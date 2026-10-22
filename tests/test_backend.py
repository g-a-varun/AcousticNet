"""Tests the REAL sound-card code path (open_sounddevice + AudioBackend + self-test) by
injecting a fake `sounddevice` module: a loopback "speaker -> mic" with delay and noise that
calls our callback exactly like PortAudio would."""
import sys
import threading
import time
import types

import numpy as np
import pytest

from acousticnet.audio import AudioBackend, open_sounddevice
from acousticnet.config import PhyConfig
from acousticnet.diagnostics import run_selftest, measure_latency
from acousticnet.phy import Modem

FAKE_DELAY_S = 0.123


class _FakeStream:
    refuse_mono_output = False
    speed = 8.0

    def __init__(self, samplerate, blocksize, dtype, channels, latency, device, callback):
        out_ch = channels[1] if isinstance(channels, tuple) else channels
        if self.refuse_mono_output and out_ch == 1:
            raise RuntimeError("Invalid number of channels")
        self.fs, self.bs, self.out_ch, self.cb = samplerate, blocksize, out_ch, callback
        self.delay = np.zeros(int(FAKE_DELAY_S * samplerate))
        self.rng = np.random.default_rng(0)
        self._run = False

    def start(self):
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run:
            outdata = np.zeros((self.bs, self.out_ch), dtype=np.float32)
            sig = np.concatenate([self.delay, np.zeros(self.bs)])
            indata = (0.5 * sig[: self.bs] + self.rng.normal(0, 0.003, self.bs)).astype(np.float32)[:, None]
            self.cb(indata, outdata, self.bs, None, None)
            self.delay = np.concatenate([sig[self.bs:len(self.delay)], outdata[:, 0]])[-len(self.delay):]
            time.sleep(self.bs / self.fs / self.speed)

    def stop(self):
        self._run = False

    def close(self):
        self._run = False


@pytest.fixture
def fake_sd(monkeypatch):
    mod = types.ModuleType("sounddevice")
    mod.Stream = _FakeStream
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    _FakeStream.refuse_mono_output = False
    return mod


def test_latency_measurement_finds_the_fake_delay(fake_sd):
    be = AudioBackend(open_sounddevice(48000, None, 1024, "high"), fs=48000).start()
    try:
        lats = measure_latency(be, Modem())
        assert lats, "own chirp not heard"
        assert abs(np.median(lats) - FAKE_DELAY_S) < 0.005
    finally:
        be.stop()


def test_stereo_fallback_when_mono_output_refused(fake_sd):
    _FakeStream.refuse_mono_output = True
    be = AudioBackend(open_sounddevice(48000, None, 1024, "high"), fs=48000).start()
    try:
        assert be.stream.out_ch == 2
        be.sleep(0.2)
        assert be.rx.end > 0
    finally:
        be.stop()


def test_selftest_ready_and_saves_calibration(fake_sd, tmp_path):
    be = AudioBackend(open_sounddevice(48000, None, 1024, "high"), fs=48000).start()
    cal = tmp_path / "cal.json"
    try:
        res = run_selftest(be, Modem(PhyConfig()), out=lambda *a: None, cal_path=str(cal))
    finally:
        be.stop()
    assert res["ready"]
    assert abs(res["rt_latency_s"] - FAKE_DELAY_S) < 0.005
    assert all(t["heard"] and t["drop_db"] < 3 for t in res["tones"])
    assert cal.exists()


def test_scheduled_transmission_starts_exactly(fake_sd):
    be = AudioBackend(open_sounddevice(48000, None, 1024, "high"), fs=48000).start()
    try:
        be.sleep(0.1)
        target = be.tx_pos + 30000
        job = be.transmit(np.ones(100, np.float32) * 0.1, at=target)
        assert job.wait(5)
        assert job.start == target and not job.late
    finally:
        be.stop()
