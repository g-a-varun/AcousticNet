"""Adaptive speed: the rate controller's decisions, and whole transfers over a changing channel."""
import os
from types import SimpleNamespace

from acousticnet.phy import Modem
from acousticnet.rate import RateController
from acousticnet.virtual import run_virtual_transfer


def _fake_modem(thresholds=(10.0, 12.0, 14.0, 16.0, 18.0)):
    """a speed table with round-number SNR thresholds, so the rules are easy to follow"""
    table = [SimpleNamespace(min_snr_db=t, rate_bps=50 * 2 ** i, label=lambda i=i: f"MCS{i}")
             for i, t in enumerate(thresholds)]
    return SimpleNamespace(mcs_table=table, available_mcs=list(range(len(thresholds))))


def test_controller_steps():
    rc = RateController(_fake_modem(), start=1, margin_db=1.0)   # needs 11, 13, 15, 17, 19 dB
    rc.on_burst(4, 0, 15.5)
    assert rc.current == 1                      # room for ONE faster speed: one clean burst is not enough
    rc.on_burst(4, 0, 15.5)
    assert rc.current == 2                      # two clean bursts -> one step up
    rc.on_burst(4, 0, 25.0)
    assert rc.current == 4                      # very strong signal -> jump straight to the top
    for _ in range(10):
        rc.on_burst(4, 0, 25.0)
    assert rc.current == 4                      # tops out at the fastest speed
    rc.on_burst(4, 2, 25.0)
    assert rc.current == 3                      # heavy loss -> one step down (ARF)
    rc.on_burst(4, 0, 12.0)
    assert rc.current == 0                      # SNR says far too fast -> jump down at once
    rc.on_burst(4, 0, 25.0)
    assert rc.current == 4
    rc.on_timeout()
    assert rc.current == 3                      # one silent round: maybe bad luck, one step down
    rc.on_timeout()
    assert rc.current == 0                      # two in a row: the channel collapsed -> most robust
    rc.on_timeout()
    assert rc.current == 0                      # never below the slowest speed
    rc.on_burst(4, 0, 25.0)
    rc.on_timeout()
    rc.on_burst(4, 0, 17.5)                     # an answer in between resets the timeout count
    rc.on_timeout()
    assert rc.current == 2


def test_real_speed_table_is_sane():
    m = Modem()
    thr = [m.mcs_table[k].min_snr_db for k in sorted(m.available_mcs)]
    assert thr == sorted(thr)                   # a faster speed never needs LESS signal
    rates = [m.mcs_table[k].rate_bps for k in sorted(m.available_mcs)]
    assert rates == sorted(rates)


def test_adaptive_beats_base_speed_on_a_good_channel():
    data = os.urandom(1200)
    base, got1 = run_virtual_transfer(data, "sr", 4, rate=None, seed=1, snr_fn=lambda t: 30.0)
    auto, got2 = run_virtual_transfer(data, "sr", 4, rate="auto", seed=1, snr_fn=lambda t: 30.0)
    assert got1 == data and got2 == data
    assert auto["elapsed_s"] < base["elapsed_s"] / 3


def test_adaptive_survives_a_noisy_spell():
    """SNR drops from 30 dB to 13 dB for a while: only the robust speed still works."""
    m = Modem()

    def snr(t):
        return 13.0 if 40 < t < 140 else 30.0

    def loss(mcs, s):
        need = m.mcs_table[mcs].min_snr_db
        return 0.02 if s >= need + 1 else 0.9

    data = os.urandom(4000)
    st, got = run_virtual_transfer(data, "sr", 4, rate="auto", seed=3, snr_fn=snr, mcs_loss=loss)
    assert got == data
    hist = st["rate_history"]
    peak = hist.index(max(hist))
    assert max(hist) >= 3 and min(hist[peak:]) <= 2      # went fast, then fell back during the spell
    assert hist[-1] >= 3                                 # and sped up again afterwards
