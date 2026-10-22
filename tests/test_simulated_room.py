"""End-to-end tests: real Node + modem + receiver + ARQ over the SimAir virtual room.
This runs the SAME code that runs on laptops; only the sound card is simulated."""
import threading

import numpy as np

from acousticnet.audio import SimAir, AudioBackend
from acousticnet.config import PhyConfig
from acousticnet.frame import Frame, T_MSG
from acousticnet.link import Node
from acousticnet.transfer import send_file, receive_file, ping, answer_ping
from acousticnet.frame import T_PING


def _pair(speed=10.0, dist=1.5, phy=PhyConfig(), fec=False):
    air = SimAir(speed=speed, seed=2)
    a = Node(1, AudioBackend(air.add_laptop("A", (0, 0))), phy=phy, fec=fec, rt_latency_s=0.16).start()
    b = Node(2, AudioBackend(air.add_laptop("B", (dist, 0))), phy=phy, fec=fec, rt_latency_s=0.16).start()
    return air, a, b


def test_single_frame_and_clock(tmp_path):
    air, a, b = _pair()
    try:
        a.sleep(0.2)
        a.send(Frame(T_MSG, 0, 2, 1, b"hi"))
        f = b.recv(3.0)
        assert f is not None and f.payload == b"hi" and f.src == 1
        assert f.snr_db > 15
    finally:
        a.stop(); b.stop(); air.close()


def test_file_transfer_with_noise_burst(tmp_path):
    air, a, b = _pair()
    data = b"The quick brown fox jumps over the lazy dog. 0123456789\n" * 3
    box = {}
    th = threading.Thread(target=lambda: box.update(r=receive_file(b, str(tmp_path), linger=5)), daemon=True)
    th.start()

    def clap():
        a.sleep(8.0)
        air.make_noise(1.0, 0.4)
    threading.Thread(target=clap, daemon=True).start()
    try:
        stats = send_file(a, 2, data, "fox.txt", "sr", 4)
        th.join(30)
        path, ok, meta = box["r"]
        assert ok and open(path, "rb").read() == data
        assert stats["retransmissions"] >= 1          # the clap really destroyed something
    finally:
        a.stop(); b.stop(); air.close()


def test_ping(tmp_path):
    # 5x real time (not 10x): at 10x a 0.2 s scheduling hiccup on a busy machine already adds
    # 2 s of "audio time" to one round trip.  Like real ping tools, judge the best round trip.
    air, a, b = _pair(speed=5.0)
    b.handlers.append(lambda f: f.type == T_PING and (threading.Thread(target=answer_ping, args=(b, f)).start() or True))
    try:
        rtts = ping(a, 2, count=3)
        assert all(r is not None for r in rtts)
        airtime = a.frame_time(4) + a.frame_time(5)          # PING (4 bytes) + PONG (5 bytes)
        assert airtime < min(rtts) < airtime + 2.0
    finally:
        a.stop(); b.stop(); air.close()


def test_fec_mode_transfer(tmp_path):
    air, a, b = _pair(phy=PhyConfig(baud=200), fec=True)
    data = bytes(range(100))
    box = {}
    th = threading.Thread(target=lambda: box.update(r=receive_file(b, str(tmp_path), linger=4)), daemon=True)
    th.start()
    try:
        send_file(a, 2, data, "bytes.bin", "gbn", 4)
        th.join(30)
        assert box["r"][1] and open(box["r"][0], "rb").read() == data
    finally:
        a.stop(); b.stop(); air.close()


def test_carrier_sense():
    air, a, b = _pair()
    try:
        b.measure_noise_floor(0.5)
        assert not b.channel_busy()
        job = a.send(Frame(T_MSG, 0, 2, 0, bytes(30)), wait=False)
        b.sleep(1.0)
        assert b.channel_busy()
        job.wait()
        b.sleep(1.0)
        assert not b.channel_busy()
    finally:
        a.stop(); b.stop(); air.close()
