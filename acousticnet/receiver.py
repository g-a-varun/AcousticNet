"""
receiver.py - listens to the never-ending microphone stream and pulls frames out of it.

RingBuffer      : remembers the last ~90 s of microphone audio.  Every sample has an
                  ABSOLUTE index (sample number since start), which doubles as our clock.
StreamReceiver  : walks forward through the ring buffer:
                     SEARCH  -> look for the chirp (normalised matched filter)
                     DECODE  -> sync word, header, body  (phy.Modem.decode_at)
                     EMIT    -> hand the decoded Frame to the node
                  If a frame is only half recorded, it waits and tries again later.

Analogy: someone reading a ticker tape.  They scan for the special start mark,
then read the message after it, then continue scanning where the message ended.
"""
import threading

import numpy as np

from .phy import Modem


class RingBuffer:
    """Thread-safe circular buffer of float32 audio with absolute sample indices."""

    def __init__(self, capacity: int):
        self.capacity = int(capacity)
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self.end = 0                      # absolute index of next sample to be written
        self._lock = threading.Lock()
        self.cond = threading.Condition(self._lock)

    @property
    def start(self) -> int:
        """oldest absolute index still stored"""
        return max(0, self.end - self.capacity)

    def write(self, block: np.ndarray):
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        n = len(block)
        if n == 0:
            return
        with self.cond:
            if n >= self.capacity:
                block = block[-self.capacity:]
                self.end += n - self.capacity
                n = self.capacity
            i = self.end % self.capacity
            first = min(n, self.capacity - i)
            self._buf[i:i + first] = block[:first]
            if first < n:
                self._buf[: n - first] = block[first:]
            self.end += n
            self.cond.notify_all()

    def read(self, a: int, b: int) -> np.ndarray:
        """samples with absolute indices [a, b)  (clipped to what is stored)"""
        with self._lock:
            a = max(a, self.start)
            b = min(b, self.end)
            if b <= a:
                return np.zeros(0, dtype=np.float32)
            i, j = a % self.capacity, b % self.capacity
            if i < j:
                return self._buf[i:j].copy()
            return np.concatenate([self._buf[i:], self._buf[:j]])     # wraps around

    def read_last(self, n: int) -> np.ndarray:
        e = self.end
        return self.read(e - n, e)

    def wait_for(self, index: int, timeout: float) -> bool:
        """block until absolute index has been written (or timeout seconds pass)"""
        with self.cond:
            if self.end >= index:
                return True
            self.cond.wait(timeout)
            return self.end >= index


class StreamReceiver:
    MARGIN = 1024            # extra samples kept around a chunk for the band-pass filter
    CHUNK_S = 1.0            # how much new audio to scan at a time

    def __init__(self, modem: Modem, ring: RingBuffer, on_frame, on_event=None):
        self.m = modem
        self.ring = ring
        self.on_frame = on_frame
        self.on_event = on_event or (lambda *a, **k: None)
        self.pos = 0                         # absolute index: search from here
        self.pending = None                  # (chirp_abs, needed_abs) of a half-arrived frame
        self.processed_until = 0             # every frame ending before ~this index has been handled
        self._last_avail = 0
        self.stats = {"frames": 0, "crc_fail": 0, "header_fail": 0, "sync_fail": 0}

    def _finish(self, status, res, c):
        """handle a decode_at() result; returns True if scanning may continue"""
        m = self.m
        if status == "more":
            self.pending = (c, res)
            return False
        self.pending = None
        if status == "fail":
            key = "header_fail" if "header" in res else "sync_fail"
            self.stats[key] += 1
            if key == "header_fail":
                self.on_event("rx_garbled", reason=res, at=c)
            self.pos = c + m.sps                  # skip past this chirp
            return True
        frame = res
        self.stats["frames"] += 1
        if not frame.crc_ok:
            self.stats["crc_fail"] += 1
        self.pos = frame.rx_end
        self.on_frame(frame)
        return True

    def process(self):
        """Consume everything currently available.  Call repeatedly (receiver thread)."""
        self._process()
        # _process only returns once it cannot make progress, so everything up to the audio that
        # was available in its last pass has been examined
        self.processed_until = self._last_avail

    def _process(self):
        m, ring = self.m, self.ring
        L = m.chirp_len
        chunk = int(self.CHUNK_S * m.fs)
        while True:
            avail = ring.end
            self._last_avail = avail
            if self.pending is not None:
                c, needed = self.pending
                if avail < needed:
                    return
                if c - m.sps < ring.start:            # fell out of the buffer (should not happen)
                    self.pending, self.pos = None, ring.start
                    continue
                status, res = m.decode_at(ring.read(c - m.sps, avail), c - m.sps, c, avail)
                if not self._finish(status, res, c):
                    return
                continue
            pos = max(self.pos, ring.start + self.MARGIN)
            # a chirp start k can only be judged once samples up to k + L + MARGIN exist.
            # Only NEW audio is scanned each time (incremental), so this stays cheap.
            k_end = min(avail - L - self.MARGIN, pos + chunk)      # judge starts in [pos, k_end)
            if k_end <= pos:
                return
            seg_a = pos - self.MARGIN
            ncc = m.chirp_ncc(m.bandpass(ring.read(seg_a, k_end + L + self.MARGIN)))
            region = ncc[self.MARGIN: self.MARGIN + (k_end - pos)]
            hits = np.flatnonzero(region >= m.cfg.detect_threshold)
            if hits.size == 0:
                self.pos = k_end
                continue
            first = pos + int(hits[0])
            # the true chirp start is the strongest peak within one chirp length of the
            # first threshold crossing; wait until that whole stretch has been recorded
            if first + 2 * L + self.MARGIN > avail:
                self.pos = first
                return
            ncc2 = m.chirp_ncc(m.bandpass(ring.read(first - self.MARGIN, first + 2 * L + self.MARGIN)))
            c = first + int(np.argmax(ncc2[self.MARGIN: self.MARGIN + L]))
            self.pos = c
            status, res = m.decode_at(ring.read(c - m.sps, avail), c - m.sps, c, avail)
            if not self._finish(status, res, c):
                return
