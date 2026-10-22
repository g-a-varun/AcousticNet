"""MAC layer: theory formulas, the event simulator against the textbook, message formats,
and one short real-code run in the virtual room."""
import os
import sys
import threading

import pytest

from acousticnet import theory as th
from acousticnet.mac import RunParams, Report, MacStation, MacSink
from acousticnet.audio import SimAir, AudioBackend
from acousticnet.link import Node
from acousticnet.config import SINK_ADDR

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments"))
import mac_sim  # noqa: E402


def test_textbook_and_finite_station_peaks():
    assert th.peak(th.aloha_pure)[1] == pytest.approx(0.184, abs=0.001)
    assert th.peak(th.aloha_slotted)[1] == pytest.approx(0.368, abs=0.001)
    assert th.peak(th.aloha_slotted_finite, 3)[1] == pytest.approx(4 / 9, abs=0.001)
    assert th.peak(th.aloha_pure_finite, 3)[1] == pytest.approx(0.246, abs=0.001)


def test_simulator_reproduces_textbook_with_many_stations():
    r = mac_sim.simulate("slotted", 1.0 / 50, N=50, out_lat=0, in_lat=0, guard=0, calib_sd=0,
                         duration=1500, seed=1)
    assert r["S_slot"] == pytest.approx(float(th.aloha_slotted(r["G_slot"])), abs=0.03)


def test_simulator_matches_three_station_theory():
    r = mac_sim.simulate("slotted", 1 / 3, N=3, duration=3000, seed=2)
    assert r["S_slot"] == pytest.approx(4 / 9, abs=0.03)


def test_messages_roundtrip():
    p = RunParams(7, "csma", 0.33, 90, 4, 250, 123, 8, 1500)
    assert RunParams.unpack(p.pack()) == p
    rep = Report(7, 10, 20, 9, 11, 1)
    assert Report.unpack(rep.pack()) == rep


def test_short_real_code_run_in_virtual_room():
    air = SimAir(speed=6.0, seed=4)
    stop = threading.Event()
    nodes = []
    try:
        for i, pos in enumerate([(1.2, 0), (-0.6, 1.0), (-0.6, -1.0)], start=1):
            n = Node(i, AudioBackend(air.add_laptop(f"s{i}", pos, 0.06, 0.06)), rt_latency_s=0.12).start()
            nodes.append(n)
            threading.Thread(target=MacStation(n).serve, args=(stop,), daemon=True).start()
        sink = Node(SINK_ADDR, AudioBackend(air.add_laptop("sink", (0, 0), 0.06, 0.06)), rt_latency_s=0.12).start()
        nodes.append(sink)
        nodes[0].sleep(1.5)
        row = MacSink(sink, [1, 2, 3]).run_one("csma", 0.3, duration_s=25, seed=3)
        assert row["reports_received"] == 3
        assert row["transmissions"] > 0 and 0 < row["successes"] <= row["transmissions"]
        assert 0 < row["S"] < 1
    finally:
        stop.set()
        for n in nodes:
            n.stop()
        air.close()
