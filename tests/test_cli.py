"""Runs the real command-line commands (`recv`, `send`, `ping`) exactly as typed on two laptops,
but with a fake `sounddevice` whose streams are wired into one SimAir virtual room."""
import sys
import threading
import types

import numpy as np
import pytest

from acousticnet import cli
from acousticnet.audio import SimAir


@pytest.fixture
def shared_room(monkeypatch):
    air = SimAir(speed=5.0, seed=5)
    spots = iter([(0.0, 0.0), (1.2, 0.0), (0.0, 1.2), (1.2, 1.2)])

    class Stream:
        def __init__(self, samplerate, blocksize, dtype, channels, latency, device, callback):
            self.cb = callback
            self.last_in = np.zeros((air.block, 1), np.float32)
            opener = air.add_laptop(f"laptop{id(self)}", next(spots), 0.06, 0.06)
            self.inner = opener(self._on_input, self._on_output, lambda s: None)
            self.time_scale = self.inner.time_scale

        def _on_input(self, mono):
            self.last_in = mono[:, None]

        def _on_output(self, n):
            out = np.zeros((n, 1), np.float32)
            self.cb(self.last_in[:n], out, n, None, None)
            return out[:, 0]

        def start(self):
            self.inner.start()

        def stop(self):
            self.inner.stop()

        def close(self):
            self.inner.close()

    mod = types.ModuleType("sounddevice")
    mod.Stream = Stream
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    yield air
    air.close()


def test_send_and_recv_commands(shared_room, tmp_path):
    src = tmp_path / "notes.txt"
    src.write_bytes(b"Sent with: python -m acousticnet send notes.txt\n" * 3)
    cal = str(tmp_path / "cal.json")
    out = tmp_path / "inbox"
    rc = {}
    t = threading.Thread(target=lambda: rc.update(recv=cli.main(
        ["recv", "--once", "--no-dashboard", "--out", str(out), "--cal", cal])), daemon=True)
    t.start()
    rc["send"] = cli.main(["send", str(src), "--no-dashboard", "--no-wait", "--cal", cal, "--arq", "gbn"])
    t.join(120)
    assert rc["send"] == 0 and rc.get("recv") == 0
    assert (out / "notes.txt").read_bytes() == src.read_bytes()


def test_ping_command(shared_room, tmp_path, capsys):
    cal = str(tmp_path / "cal.json")
    stop = {}
    t = threading.Thread(target=lambda: stop.update(r=cli.main(
        ["recv", "--once", "--no-dashboard", "--out", str(tmp_path), "--cal", cal])), daemon=True)
    t.start()
    assert cli.main(["ping", "--count", "2", "--no-dashboard", "--cal", cal]) == 0
    assert "average round trip" in capsys.readouterr().out


def test_sniff_and_replay_commands(shared_room, tmp_path, capsys):
    """Three laptops: Bob (`recv --key`), Eve (`sniff`) and Alice (`send --key`).  Eve records the
    encrypted transfer, cannot read it, and her `replay` of the recording - played back after the
    transfer is over, as an attacker would - is refused by the session log."""
    import glob
    import os
    import time
    cal = str(tmp_path / "cal.json")
    sniffed = tmp_path / "sniffed"
    bob = tmp_path / "bob"
    threading.Thread(target=lambda: cli.main(["sniff", "--no-dashboard", "--out", str(sniffed), "--cal", cal]),
                     daemon=True).start()
    threading.Thread(target=lambda: cli.main(["recv", "--no-dashboard", "--out", str(bob),
                                              "--cal", cal, "--key", "k1"]), daemon=True).start()
    secret = tmp_path / "exam.txt"
    secret.write_bytes(b"the answers are 1-B 2-D 3-A\n" * 4)
    assert cli.main(["send", str(secret), "--no-dashboard", "--no-wait", "--cal", cal, "--key", "k1"]) == 0
    assert (bob / "exam.txt").read_bytes() == secret.read_bytes()
    captures = glob.glob(str(sniffed / "capture_1_to_2_*.json"))
    assert captures and not glob.glob(str(sniffed / "stolen_*"))       # recorded, but nothing readable
    # wait until Bob has committed the session and stopped lingering over the first transfer, so the
    # replay is seen as a replay (within the linger window it is indistinguishable from a resend)
    deadline = time.time() + 30
    while time.time() < deadline and secret.read_bytes() and not os.path.exists(bob / ".acousticnet_sessions"):
        time.sleep(0.2)
    time.sleep(6)
    rc = cli.main(["replay", captures[0], "--no-dashboard", "--no-wait", "--cal", cal])
    assert rc == 3 and "replay attack refused" in capsys.readouterr().out
