"""
audio.py - the bridge between our protocol code and a sound card (real or simulated).

AudioBackend
    Owns one full-duplex audio stream (speaker + microphone at the same time).
    The sound card calls our _callback() every ~40 ms with:
        indata  = what the mic just recorded   -> appended to self.rx (RingBuffer)
        outdata = what the speaker should play <- taken from the transmit queue
    Because input and output advance together, the sample counter is our CLOCK.
    now() = samples recorded so far / fs.  All timers in the project use it, so the
    exact same code runs at real speed on laptops and faster-than-real-time in the
    simulator.

open_sounddevice(...)
    Opens a real stream with the `sounddevice` library (PortAudio).

SimAir / SimAir.open_stream(...)
    A virtual room: several simulated laptops placed at x,y positions (metres).
    Sound gets quieter with distance, takes time to travel (343 m/s), echoes once off a
    wall, and every laptop has its own audio latency and mic noise.  It calls the SAME
    _callback() a real sound card would, so the simulator tests the real code path.
"""
import threading
import time
from collections import deque

import numpy as np

from .receiver import RingBuffer

SPEED_OF_SOUND = 343.0


class TxJob:
    """one queued transmission"""

    def __init__(self, samples, at=None):
        self.samples = samples
        self.at = at                # requested start (TX sample index) or None = as soon as possible
        self.start = None           # actual start TX index
        self.end = None
        self.late = False           # True if it could not start at the requested index
        self.done = threading.Event()
        self.cancelled = False

    def wait(self, timeout=None):
        return self.done.wait(timeout)


class AudioBackend:
    def __init__(self, open_stream, fs: int = 48000, volume: float = 1.0, buffer_s: float = 90.0,
                 name: str = "audio"):
        self.fs = fs
        self.volume = float(volume)
        self.name = name
        self._open_stream = open_stream
        self.rx = RingBuffer(int(buffer_s * fs))
        self.tx_pos = 0                 # absolute index of next output sample
        self._txq = deque()
        self._lock = threading.Lock()
        self.stream = None
        self.glitches = 0               # input overflows / output underflows reported by driver
        self.time_scale = 1.0           # wall seconds per audio second (simulator sets < 1)
        self._transmitting_until = -1

    # -- lifecycle ------------------------------------------------------------
    def start(self):
        self.stream = self._open_stream(self._on_input, self._on_output, self._on_status)
        self.time_scale = getattr(self.stream, "time_scale", 1.0)
        self.stream.start()
        return self

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        with self._lock:
            for job in self._txq:
                job.cancelled = True
                job.done.set()
            self._txq.clear()

    # -- callbacks (called from the audio thread: keep them fast!) -------------
    def _on_status(self, status):
        self.glitches += 1

    def _on_input(self, mono):
        self.rx.write(mono)

    def _on_output(self, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        pos = self.tx_pos
        with self._lock:
            i = 0
            while i < n and self._txq:
                job = self._txq[0]
                if job.start is None:
                    if job.at is None or job.at <= pos + i:
                        job.late = job.at is not None and job.at < pos + i
                        job.start = pos + i
                    else:
                        job.start = job.at
                if job.start >= pos + n:
                    break                               # scheduled for a later block
                i = max(i, job.start - pos)
                off = pos + i - job.start
                take = min(n - i, len(job.samples) - off)
                out[i:i + take] = job.samples[off:off + take]
                i += take
                if off + take >= len(job.samples):
                    job.end = job.start + len(job.samples)
                    self._transmitting_until = job.end
                    self._txq.popleft()
                    job.done.set()
        self.tx_pos = pos + n
        return out

    # -- API used by the Node ----------------------------------------------------
    def transmit(self, samples: np.ndarray, at: int = None) -> TxJob:
        """queue sound; at = TX sample index to start at (None = as soon as possible)"""
        job = TxJob(np.clip(np.asarray(samples, np.float32) * self.volume, -1, 1), at)
        with self._lock:
            self._txq.append(job)
        return job

    def is_transmitting(self) -> bool:
        with self._lock:
            return bool(self._txq) and self._txq[0].start is not None or self.tx_pos < self._transmitting_until

    def now(self) -> float:
        return self.rx.end / self.fs

    def wait_index(self, index: int, timeout_wall: float = None) -> bool:
        """block until the mic clock reaches `index` samples"""
        deadline = None if timeout_wall is None else time.monotonic() + timeout_wall
        while self.rx.end < index:
            if self.stream is None:
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False
            self.rx.wait_for(index, 0.05)
        return True

    def sleep(self, seconds: float):
        self.wait_index(self.rx.end + int(seconds * self.fs))

    def sleep_until(self, t_seconds: float):
        self.wait_index(int(t_seconds * self.fs))


# =============================================================================
# Real sound card
# =============================================================================

class _DualStream:
    """Fallback when one full-duplex stream can't be opened: separate in / out streams."""

    def __init__(self, sd, fs, device, blocksize, latency, on_input, on_output, on_status):
        dev_in, dev_out = device if isinstance(device, (tuple, list)) else (device, device)

        def cb_in(indata, frames, t, status):
            if status:
                on_status(status)
            on_input(indata[:, 0].copy())

        def cb_out(outdata, frames, t, status):
            if status:
                on_status(status)
            outdata[:] = on_output(frames)[:, None]

        self.i = sd.InputStream(samplerate=fs, blocksize=blocksize, dtype="float32", channels=1,
                                latency=latency, device=dev_in, callback=cb_in)
        out_err = None
        for ch in (1, 2):
            try:
                self.o = sd.OutputStream(samplerate=fs, blocksize=blocksize, dtype="float32",
                                         channels=ch, latency=latency, device=dev_out, callback=cb_out)
                break
            except Exception as e:     # some outputs refuse mono
                out_err = e
        else:
            raise out_err

    def start(self):
        self.i.start()
        self.o.start()

    def stop(self):
        self.i.stop()
        self.o.stop()

    def close(self):
        self.i.close()
        self.o.close()


def open_sounddevice(fs: int = 48000, device=None, blocksize: int = 2048, latency="high"):
    """Returns an `open_stream` function for AudioBackend that uses the real sound card.
    device: None (system default), an index, a name, or a (input, output) pair."""

    def opener(on_input, on_output, on_status):
        try:
            import sounddevice as sd
        except OSError as e:
            raise RuntimeError("PortAudio library not found. On Linux: sudo apt install libportaudio2") from e
        except ImportError as e:
            raise RuntimeError("Install sounddevice:  pip install sounddevice") from e

        def cb(indata, outdata, frames, t, status):
            if status:
                on_status(status)
            outdata[:] = on_output(frames)[:, None]
            on_input(indata[:, 0].copy())

        errors = []
        for ch in (1, (1, 2)):                    # some outputs refuse mono, try stereo
            try:
                return sd.Stream(samplerate=fs, blocksize=blocksize, dtype="float32", channels=ch,
                                 latency=latency, device=device, callback=cb)
            except Exception as e:
                errors.append(e)
        try:
            return _DualStream(sd, fs, device, blocksize, latency, on_input, on_output, on_status)
        except Exception as e:
            errors.append(e)
        raise RuntimeError("Could not open the sound card: " + " | ".join(str(e) for e in errors))

    return opener


# =============================================================================
# Simulated room
# =============================================================================

class _Port:
    def __init__(self, name, pos, out_latency, in_latency, noise_rms, fs):
        self.name = name
        self.pos = np.asarray(pos, dtype=float)
        self.out_lat = int(out_latency * fs)
        self.in_lat = int(in_latency * fs)
        self.noise_rms = noise_rms
        self.hist = RingBuffer(fs * 10)          # what this laptop's speaker emitted (TX index)
        self.on_input = self.on_output = None
        self.active = False
        self.offset = 0                         # air index at which this port's counters were 0


class _SimStream:
    def __init__(self, air, port):
        self.air, self.port = air, port
        self.time_scale = 1.0 / air.speed if air.speed > 0 else 0.0

    def start(self):
        self.air._activate(self.port)

    def stop(self):
        self.port.active = False

    def close(self):
        self.port.active = False


class SimAir:
    """Virtual room shared by simulated laptops (see module docstring)."""

    def __init__(self, fs: int = 48000, block: int = 1024, speed: float = 1.0, seed: int = 0,
                 echo_delay_s: float = 0.006, echo_gain: float = 0.3, ref_gain: float = 0.25):
        self.fs, self.block, self.speed = fs, block, speed
        self.rng = np.random.default_rng(seed)
        self.echo_delay = int(echo_delay_s * fs)
        self.echo_gain = echo_gain
        self.ref_gain = ref_gain                # loudness heard at 1 metre
        self.ports = []
        self.t = 0                              # air time (samples)
        self._bursts = []                       # (start, samples) interference, e.g. a clap
        self._walls = {}                        # frozenset({name_a, name_b}) -> extra loss in dB
        self._lock = threading.Lock()
        self._thread = None
        self._running = False

    def add_laptop(self, name: str, pos=(0.0, 0.0), out_latency_s: float = 0.08,
                   in_latency_s: float = 0.08, noise_rms: float = 0.01):
        """returns an open_stream function for AudioBackend"""
        port = _Port(name, pos, out_latency_s, in_latency_s, noise_rms, self.fs)
        with self._lock:
            self.ports.append(port)

        def opener(on_input, on_output, on_status):
            port.on_input, port.on_output = on_input, on_output
            return _SimStream(self, port)
        return opener

    def make_noise(self, seconds: float = 1.0, level: float = 0.3):
        """inject a loud broadband burst (someone clapping / talking) into the room now"""
        n = int(seconds * self.fs)
        burst = self.rng.normal(0, level, n) * np.hanning(n) ** 0.3
        with self._lock:
            self._bursts.append((self.t + self.block, burst))

    def set_noise(self, name: str, noise_rms: float):
        """change how noisy one laptop's surroundings are (e.g. someone switches on a fan)"""
        for p in self.ports:
            if p.name == name:
                p.noise_rms = noise_rms

    def add_wall(self, name_a: str, name_b: str, loss_db: float = 40.0):
        """put a wall between two laptops: their sound is attenuated by loss_db"""
        self._walls[frozenset((name_a, name_b))] = loss_db

    def gain_delay(self, listener: _Port, emitter: _Port):
        d = max(0.3, float(np.linalg.norm(listener.pos - emitter.pos)))    # own speaker ~30 cm away
        g = self.ref_gain / d
        wall = self._walls.get(frozenset((listener.name, emitter.name)))
        if wall:
            g *= 10 ** (-wall / 20)
        return g, int(d / SPEED_OF_SOUND * self.fs)

    def _activate(self, port):
        with self._lock:
            port.offset = self.t
            port.active = True
        if self._thread is None:
            self._running = True
            self._thread = threading.Thread(target=self._run, name="SimAir", daemon=True)
            self._thread.start()

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    @staticmethod
    def _read_padded(ring: RingBuffer, a: int, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float64)
        lo, hi = max(a, ring.start), min(a + n, ring.end)
        if hi > lo:
            out[lo - a: hi - a] = ring.read(lo, hi)
        return out

    def _run(self):
        B = self.block
        t_wall0 = time.monotonic()
        t_air0 = self.t
        while self._running:
            with self._lock:
                ports = [p for p in self.ports if p.active]
                bursts = list(self._bursts)
            # 1) every speaker produces its next block
            for p in ports:
                p.hist.write(p.on_output(B))
            # 2) every microphone hears everybody (delayed, attenuated, echoed) + noise
            for li in ports:
                mic = np.zeros(B)
                for em in ports:
                    g, prop = self.gain_delay(li, em)
                    # emitter TX index whose sound reaches this mic block
                    src = (self.t - li.in_lat - prop - em.out_lat) - em.offset
                    mic += g * self._read_padded(em.hist, src, B)
                    mic += g * self.echo_gain * self._read_padded(em.hist, src - self.echo_delay, B)
                for start, burst in bursts:
                    a = self.t - li.in_lat - start
                    if -B < a < len(burst):
                        lo, hi = max(0, a), min(len(burst), a + B)
                        mic[lo - a: hi - a] += burst[lo:hi]
                mic += self.rng.normal(0, li.noise_rms, B)
                li.on_input(np.clip(mic, -1, 1).astype(np.float32))
            with self._lock:
                self.t += B
                self._bursts = [(s, b) for s, b in self._bursts if self.t - s < len(b) + self.fs]
            # 3) pace the simulation
            if self.speed > 0:
                target = t_wall0 + (self.t - t_air0) / (self.fs * self.speed)
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
