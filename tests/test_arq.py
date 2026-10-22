"""ARQ + file-transfer tests on the VirtualNet (no audio, virtual time -> very fast)."""
import os

import pytest

from acousticnet.virtual import run_virtual_transfer


@pytest.mark.parametrize("mode,window", [("sw", 1), ("gbn", 4), ("sr", 4), ("gbn", 8), ("sr", 8)])
@pytest.mark.parametrize("loss", [0.0, 0.15, 0.35])
def test_transfer_is_exact_under_loss(mode, window, loss):
    for seed in range(3):
        data = os.urandom(250 + 41 * seed)
        stats, got = run_virtual_transfer(data, mode, window, loss=loss, seed=seed)
        assert got == data
        if loss == 0:
            assert stats["retransmissions"] == 0


def test_sequence_numbers_wrap_around():
    data = os.urandom(32 * 300 + 7)          # > 256 chunks -> seq wraps past 255
    for mode, w in (("gbn", 8), ("sr", 16)):
        _, got = run_virtual_transfer(data, mode, w, loss=0.1, seed=1)
        assert got == data


def test_selective_repeat_retransmits_less_than_go_back_n():
    data = os.urandom(32 * 40)
    gbn = sum(run_virtual_transfer(data, "gbn", 8, loss=0.2, seed=s)[0]["retransmissions"] for s in range(4))
    sr = sum(run_virtual_transfer(data, "sr", 8, loss=0.2, seed=s)[0]["retransmissions"] for s in range(4))
    assert sr < gbn


def test_windowing_beats_stop_and_wait_without_loss():
    data = os.urandom(32 * 20)
    sw = run_virtual_transfer(data, "sw", 1, seed=0)[0]["goodput_bps"]
    sr = run_virtual_transfer(data, "sr", 8, seed=0)[0]["goodput_bps"]
    assert sr > sw * 1.2


def test_empty_file():
    _, got = run_virtual_transfer(b"", "sr", 4, seed=0)
    assert got == b""
