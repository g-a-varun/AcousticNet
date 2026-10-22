"""
link.py - Node: one laptop on the acoustic network.

A Node glues together:
    AudioBackend   (speaker + mic, real or simulated)
    Modem          (bits <-> sound)
    StreamReceiver (finds frames in the mic stream, runs in its own thread)
and offers a small, clean API that the upper layers (ARQ, file transfer, MAC experiments)
use without caring about sound at all:

    node.send(frame)                 put a frame on the air (blocks until played)
    node.recv(timeout, accept=...)   wait for a matching frame
    node.channel_busy()              carrier sense: is anyone talking right now?
    node.now() / node.sleep(dt)      clock based on audio samples

experiments/virtual_link.py offers the very same API without any audio, so the ARQ code
can be tested thousands of times faster.  That is the "VirtualChannel" idea.
"""
import threading
import time
from collections import deque

import numpy as np

from .config import PhyConfig, DEFAULT_PHY, LinkConfig, DEFAULT_LINK, BROADCAST
from .events import EventBus
from .frame import Frame, TYPE_NAMES
from .phy import Modem
from .receiver import StreamReceiver


class Node:
    def __init__(self, addr: int, backend, phy: PhyConfig = DEFAULT_PHY, fec: bool = False,
                 bus: EventBus = None, link: LinkConfig = DEFAULT_LINK, rt_latency_s: float = None,
                 noise_floor: float = None, cs_threshold_db: float = 8.0, promiscuous: bool = False):
        self.addr = addr
        self.backend = backend
        self.modem = Modem(phy)
        # control frames (ACK, PING, ...) always go at the robust base speed;
        # --fec makes even that the FEC-protected MCS 0
        self.default_mcs = 0 if fec else 1
        self.fec = fec
        self.bus = bus or EventBus()
        self.link_cfg = link
        self.rt_latency_s = rt_latency_s if rt_latency_s is not None else link.default_rt_latency_s
        self.noise_floor = noise_floor
        self.cs_threshold_db = cs_threshold_db
        self.promiscuous = promiscuous            # keep frames addressed to others too (sink / sniffer)
        self.receiver = StreamReceiver(self.modem, backend.rx, self._on_frame, self._on_rx_event)
        self.handlers = []                        # fn(frame) -> True if it consumed the frame
        self.raw_listeners = []                   # fn(frame) for EVERY decoded frame, even CRC failures
        self.ignore = set()                       # link addresses to pretend we cannot hear (demo "wall")
        self._inbox = deque(maxlen=200)
        self._cond = threading.Condition()
        self._running = False
        self._thread = None
        self.stats = {"tx": 0, "rx": 0, "rx_crc_fail": 0, "rx_own": 0, "garbled": 0}

    # ------------------------------------------------------------ lifecycle --
    def start(self):
        if self.backend.stream is None:
            self.backend.start()
        self._running = True
        self._thread = threading.Thread(target=self._rx_loop, name=f"rx-{self.addr}", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        self.backend.stop()
        if self._thread:
            self._thread.join(timeout=2)

    def _rx_loop(self):
        seen = 0
        batch = int(0.1 * self.fs)                   # wake up for every ~100 ms of new audio
        while self._running:
            self.backend.rx.wait_for(seen + batch, 0.1)
            seen = self.backend.rx.end
            try:
                self.receiver.process()
            except Exception as e:                       # never let the receiver thread die
                self.bus.emit("error", where="receiver", error=repr(e), t=self.now())

    # --------------------------------------------------------------- clock --
    @property
    def fs(self):
        return self.backend.fs

    def now(self) -> float:
        return self.backend.now()

    def sleep(self, seconds: float):
        self.backend.sleep(seconds)

    def frame_time(self, payload_len: int, mcs: int = None) -> float:
        return self.modem.frame_duration(payload_len, self.default_mcs if mcs is None else mcs)

    def ack_timeout(self, ack_len: int = 6) -> float:
        """How long to wait for an ACK after our last frame finished playing:
        both laptops' audio latency (there and back) + the ACK's own airtime + slack."""
        return 2 * self.rt_latency_s + self.frame_time(ack_len) + self.link_cfg.processing_margin_s

    # ------------------------------------------------------------------ TX --
    def send(self, frame: Frame, at: int = None, wait: bool = True, mcs: int = None, **info):
        frame.src = self.addr
        m = mcs if mcs is not None else (frame.mcs if frame.mcs is not None else self.default_mcs)
        samples = self.modem.frame_waveform(frame, m)
        job = self.backend.transmit(samples, at)
        self.stats["tx"] += 1
        self.bus.emit("tx", t=round(self.now(), 3), type=frame.name, seq=frame.seq, dst=frame.dst,
                      len=len(frame.payload), dur=round(len(samples) / self.fs, 3), poll=bool(frame.flags & 1),
                      mcs=m, **info)
        if wait:
            while not job.wait(0.2):
                if self.backend.stream is None:
                    break
        return job

    # ------------------------------------------------------------------ RX --
    def _on_rx_event(self, kind, **kw):
        if kind == "rx_garbled":
            self.stats["garbled"] += 1
        self.bus.emit(kind, t=round(self.now(), 3), **kw)

    def _on_frame(self, f: Frame):
        if f.src == self.addr:                    # we hear ourselves - ignore
            self.stats["rx_own"] += 1
            return
        if f.src in self.ignore:                  # emulated "out of range" (see relay demo)
            return
        if f.crc_ok:
            self.stats["rx"] += 1
        else:
            self.stats["rx_crc_fail"] += 1
        self.bus.emit("rx", t=round(f.rx_end / self.fs, 3), type=f.name, seq=f.seq, src=f.src, dst=f.dst,
                      len=len(f.payload), crc_ok=f.crc_ok, snr=round(f.snr_db, 1),
                      quality=round(f.quality, 3), fec_fixed=f.fec_fixed, mcs=f.mcs)
        for fn in list(self.raw_listeners):
            try:
                fn(f)
            except Exception as e:
                self.bus.emit("error", where="raw_listener", error=repr(e))
        if not f.crc_ok:
            return                                # upper layers only ever see good frames
        if f.dst not in (self.addr, BROADCAST) and not self.promiscuous:
            return
        self.deliver_local(f)

    def deliver_local(self, f: Frame, skip=None):
        """hand a good frame to the handlers / inbox (the router also uses this for frames that
        reached us through a relay)"""
        for h in list(self.handlers):
            if h is skip:
                continue
            try:
                if h(f):
                    return
            except Exception as e:
                self.bus.emit("error", where="handler", error=repr(e))
        with self._cond:
            self._inbox.append(f)
            self._cond.notify_all()

    def recv(self, timeout: float, accept=None):
        """Wait up to `timeout` audio-seconds for a frame for which accept(frame) is True.

        A timeout is only declared once the receiver thread has actually LOOKED at all the audio
        up to the deadline.  On a slow or busy laptop the receiver can fall a little behind;
        without this rule an ACK that already arrived (but was not decoded yet) would be counted
        as lost and trigger needless retransmissions."""
        deadline = self.now() + timeout
        deadline_idx = int(deadline * self.fs) + 3 * self.modem.chirp_len + StreamReceiver.MARGIN
        wall_limit = None
        with self._cond:
            while True:
                for i, f in enumerate(self._inbox):
                    if accept is None or accept(f):
                        del self._inbox[i]
                        return f
                if not self._running:
                    return None
                if self.now() >= deadline:
                    if self.receiver.processed_until >= deadline_idx:
                        return None
                    if wall_limit is None:
                        wall_limit = time.monotonic() + 15.0        # never wait forever
                    elif time.monotonic() > wall_limit:
                        return None
                self._cond.wait(0.02)

    def catch_up(self, wall_timeout: float = 20.0) -> bool:
        """Block until the receiver thread has examined all audio recorded so far.
        Use before counting results, so a busy laptop never under-counts."""
        target = self.backend.rx.end + 3 * self.modem.chirp_len + StreamReceiver.MARGIN
        self.backend.wait_index(target, wall_timeout)
        limit = time.monotonic() + wall_timeout
        while self.receiver.processed_until < target and time.monotonic() < limit and self._running:
            time.sleep(0.02)
        return self.receiver.processed_until >= target

    def flush(self):
        with self._cond:
            self._inbox.clear()

    # ------------------------------------------------------- carrier sense --
    def measure_noise_floor(self, seconds: float = 1.0) -> float:
        """listen while nobody talks; remember how loud 'silence' is in our band"""
        self.sleep(seconds)
        n = int(0.03 * self.fs)
        x = self.backend.rx.read_last(int(seconds * self.fs))
        levels = [self.modem.band_level(x[i:i + n]) for i in range(0, len(x) - n, n)]
        self.noise_floor = float(np.median(levels)) if levels else 1e-9
        return self.noise_floor

    def channel_level_db(self, window_s: float = 0.03) -> float:
        x = self.backend.rx.read_last(int(window_s * self.fs))
        lvl = self.modem.band_level(x)
        floor = self.noise_floor or 1e-9
        return 10 * np.log10((lvl + 1e-15) / (floor + 1e-15))

    def channel_busy(self, window_s: float = 0.03) -> bool:
        """CSMA's 'listen before talk': True if the band is clearly louder than silence"""
        if self.noise_floor is None:
            self.measure_noise_floor(0.5)
        return self.channel_level_db(window_s) > self.cs_threshold_db
