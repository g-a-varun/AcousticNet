"""Unit tests for phy.py and receiver.py using the simulated room (no microphone needed)."""
import numpy as np
import pytest

from acousticnet.config import PhyConfig
from acousticnet.phy import Modem
from acousticnet.frame import Frame, T_DATA
from acousticnet.channel import room_channel
from acousticnet.receiver import RingBuffer, StreamReceiver


def test_tones_are_orthogonal():
    """for every speed: the 'wrong' tones leak essentially nothing into a perfectly aligned symbol"""
    from acousticnet.phy import symbols_to_bits
    m = Modem()
    for idx in m.available_mcs:
        mc = m.mcs_table[idx]
        M = len(mc.tones)
        sym = np.arange(64) % M
        x = m.modulate_bits(symbols_to_bits(sym, mc.bits_per_symbol), mc)
        e = m.tone_energies(x, 0, len(sym), mc)
        right = e[np.arange(len(sym)), sym]
        wrong = e.copy()
        wrong[np.arange(len(sym)), sym] = 0
        assert np.max(wrong.max(axis=1) / right) < 1e-18, mc.label()


def test_bad_baud_is_rejected():
    with pytest.raises(ValueError):
        PhyConfig(baud=300)      # 1400/300 is not a whole number of cycles


@pytest.mark.parametrize("baud,mcs", [(50, 1), (100, 1), (100, 0), (200, 0), (100, 2), (100, 3), (100, 4)])
def test_frame_through_room(baud, mcs):
    m = Modem(PhyConfig(baud=baud))
    snr = 12 if mcs <= 1 else 20          # faster speeds need a cleaner channel (that is the point)
    for seed in range(4):
        payload = bytes(np.random.default_rng(seed).integers(0, 256, 32).tolist())
        tx = np.concatenate([np.zeros(4000), m.frame_waveform(Frame(T_DATA, 1, 2, seed, payload), mcs),
                             np.zeros(8000)])
        rx = room_channel(tx, snr_db=snr, latency_s=0.2, rt60=0.3, drr_db=8, rng=seed)
        f = m.decode_buffer(rx)
        assert f is not None and f.crc_ok and f.payload == payload and f.seq == seed and f.mcs == mcs


def test_mfsk_symbols_roundtrip():
    from acousticnet.phy import bits_to_symbols, symbols_to_bits
    bits = np.random.default_rng(1).integers(0, 2, 64).astype(np.uint8)
    for k in (1, 2, 4):
        assert np.array_equal(symbols_to_bits(bits_to_symbols(bits, k), k), bits)


def test_frame_duration_matches_waveform():
    m = Modem()
    for n in (0, 5, 32, 97):
        for mcs in m.available_mcs:
            w = m.frame_waveform(Frame(T_DATA, 1, 2, 0, bytes(n)), mcs)
            assert len(w) == m.frame_samples(n, mcs)


def test_ring_buffer_wraps():
    r = RingBuffer(1000)
    data = np.arange(2500, dtype=np.float32)
    for i in range(0, 2500, 300):
        r.write(data[i:i + 300])
    assert r.end == 2500 and r.start == 1500
    assert np.array_equal(r.read(1700, 2400), data[1700:2400])
    assert np.array_equal(r.read_last(10), data[-10:])


def test_stream_receiver_many_frames_random_blocks():
    rng = np.random.default_rng(3)
    m = Modem()
    parts, sent = [np.zeros(2000)], []
    for i in range(6):
        p = bytes(rng.integers(0, 256, int(rng.integers(0, 30))).tolist())
        sent.append(p)
        parts += [m.frame_waveform(Frame(T_DATA, 1, 2, i, p), [1, 0, 2, 3, 4, 1][i]),
                  np.zeros(int(rng.integers(0, 20000)))]
    rx = room_channel(np.concatenate(parts + [np.zeros(20000)]), snr_db=20, rng=4)
    ring, got = RingBuffer(48000 * 30), []
    rcv = StreamReceiver(m, ring, got.append)
    i = 0
    while i < len(rx):
        n = int(rng.integers(100, 5000))
        ring.write(rx[i:i + n])
        i += n
        rcv.process()
    assert [f.payload for f in got if f.crc_ok] == sent


def test_noise_only_produces_no_frames():
    m = Modem()
    ring, got = RingBuffer(48000 * 30), []
    rcv = StreamReceiver(m, ring, got.append)
    ring.write(np.random.default_rng(0).normal(0, 0.05, 48000 * 10))
    rcv.process()
    assert got == []
