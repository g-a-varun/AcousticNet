"""Network layer: distance-vector logic and a real relayed transfer in the virtual room."""
import threading

from acousticnet.network import DVTable, INF, Router, NetLink, traceroute
from acousticnet.audio import SimAir, AudioBackend
from acousticnet.link import Node
from acousticnet.transfer import send_file, receive_file


def _round(tables, links, alive):
    """one synchronous exchange: every alive node advertises to its alive neighbours"""
    adverts = {n: tables[n].advert() for n in tables if n in alive}
    changed = False
    for a, b in links:
        for x, y in ((a, b), (b, a)):
            if x in alive and y in alive:
                changed |= tables[y].on_advert(x, adverts[x], 0.0)
    return changed


def test_line_converges():
    t = {n: DVTable(n) for n in (1, 3, 2)}
    links = [(1, 3), (3, 2)]
    for _ in range(4):
        _round(t, links, {1, 2, 3})
    assert t[1].lookup(2) == 3 and t[1].cost(2) == 2
    assert t[2].lookup(1) == 3 and t[2].cost(1) == 2


def _fail_and_count(poison):
    """1 - 3 - 2 line, then node 2 dies; how high does node 1's cost to 2 climb?"""
    t = {n: DVTable(n, poison_reverse=poison) for n in (1, 3, 2)}
    links = [(1, 3), (3, 2)]
    for _ in range(4):
        _round(t, links, {1, 2, 3})
    t[3].neighbor_down(2, 0.0)
    rounds = 0
    while rounds < 40:
        rounds += 1
        if not _round(t, links, {1, 3}):
            break
    return t[1].cost(2), rounds


def test_poison_reverse_prevents_count_to_infinity():
    cost, rounds = _fail_and_count(poison=True)
    assert cost == INF and rounds <= 3
    cost, rounds = _fail_and_count(poison=False)
    assert cost == INF and rounds >= 6           # slowly counts up to "infinity" (16)


def test_relayed_transfer_through_a_wall(tmp_path):
    air = SimAir(speed=6.0, seed=2)
    nodes = []
    try:
        a = Node(1, AudioBackend(air.add_laptop("A", (0, 0), 0.07, 0.07)), rt_latency_s=0.14).start()
        r = Node(3, AudioBackend(air.add_laptop("R", (2.0, 0), 0.07, 0.07)), rt_latency_s=0.14).start()
        b = Node(2, AudioBackend(air.add_laptop("B", (4.0, 0), 0.07, 0.07)), rt_latency_s=0.14).start()
        nodes = [a, r, b]
        air.add_wall("A", "B", 45)
        ra, rr, rb = Router(a).start(), Router(r).start(), Router(b).start()
        la, lb = NetLink(a, ra), NetLink(b, rb)
        box = {}
        th = threading.Thread(target=lambda: box.update(res=receive_file(lb, str(tmp_path), linger=5)), daemon=True)
        th.start()
        assert ra.wait_route(2, 120) and ra.dv.cost(2) == 2
        assert traceroute(la, 2) == [3, 2]
        data = b"two acoustic hops via a relay\n" * 4
        send_file(la, 2, data, "relayed.txt", "sr", 4)
        th.join(60)
        assert box["res"][1] and open(box["res"][0], "rb").read() == data
        assert rr.stats["forwarded"] > 0
    finally:
        for n in nodes:
            n.stop()
        air.close()
