"""
channel.py - mathematical models of "the air between two laptops".

Used by the simulator and the experiments, never by the real-hardware path.
    add_noise ............ white noise at a chosen in-band SNR
    room_ir .............. synthetic room echo (direct sound + decaying reverb tail)
    apply_ir ............. convolve a signal with that echo
    room_channel ......... all of the above + latency + attenuation, in one call

In-band SNR = signal power / noise power measured only inside 700-4000 Hz,
the band our receiver actually listens to.  That makes the number meaningful:
noise outside the band is filtered away anyway.
"""
import numpy as np

from .config import PhyConfig, DEFAULT_PHY


def add_noise(x: np.ndarray, snr_db: float, cfg: PhyConfig = DEFAULT_PHY, rng=None,
              ref_power: float = None) -> np.ndarray:
    rng = np.random.default_rng(rng)
    x = np.asarray(x, dtype=np.float64)
    if ref_power is None:
        active = x[np.abs(x) > 1e-6]
        ref_power = float(np.mean(active ** 2)) if len(active) else 1e-6
    inband_fraction = (cfg.band_hi - cfg.band_lo) / (cfg.fs / 2)
    noise_var = ref_power / (10 ** (snr_db / 10)) / inband_fraction
    return x + rng.normal(0.0, np.sqrt(noise_var), len(x))


def room_ir(fs: int = 48000, rt60: float = 0.3, drr_db: float = 10.0, rng=None) -> np.ndarray:
    """Direct path (1.0) + exponentially decaying random tail.
    rt60   = time for echoes to fall by 60 dB (classroom ~0.4-0.8 s, small room ~0.3 s)
    drr_db = direct-to-reverberant energy ratio (close mic ~ +10 dB, far mic ~ 0 dB)"""
    rng = np.random.default_rng(rng)
    n = int(rt60 * fs)
    t = np.arange(n) / fs
    tail = rng.normal(0, 1, n) * 10 ** (-3 * t / rt60)      # -60 dB at rt60
    tail[: int(0.002 * fs)] = 0                              # first reflection after 2 ms
    tail *= np.sqrt(10 ** (-drr_db / 10) / (np.sum(tail ** 2) + 1e-20))
    ir = tail
    ir[0] = 1.0
    return ir


def apply_ir(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = len(x) + len(ir) - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[:n]
    return y[: len(x)]


def room_channel(tx: np.ndarray, snr_db: float = 20.0, latency_s: float = 0.15, gain: float = 0.3,
                 rt60: float = 0.3, drr_db: float = 10.0, cfg: PhyConfig = DEFAULT_PHY,
                 rng=None) -> np.ndarray:
    """speaker -> room -> microphone, for offline tests"""
    rng = np.random.default_rng(rng)
    tx = np.asarray(tx, dtype=np.float64)
    d = int(latency_s * cfg.fs)
    y = np.concatenate([np.zeros(d), gain * tx])
    if rt60 > 0:
        y = apply_ir(y, room_ir(cfg.fs, rt60, drr_db, rng))
    active = gain * tx[np.abs(tx) > 1e-6]
    ref = float(np.mean(active ** 2)) if len(active) else 1e-6
    return add_noise(y, snr_db, cfg, rng, ref_power=ref)
