"""
scenarios.py - scripted demos inside the simulated room (SimAir).  No microphone needed.

    python -m acousticnet sim-demo                      basic: file transfer + optional noise burst
    python -m acousticnet sim-demo --scenario speed     adaptive speed: a "fan" switches on and off
    python -m acousticnet sim-demo --scenario security  Eve steals a plain file, fails on an encrypted
                                                        one; a replayed recording and a wrong key are refused
    python -m acousticnet sim-demo --scenario relay     A and B are separated by a wall; R relays,
                                                        routing tables converge, traceroute, file transfer

Every scenario runs the REAL protocol code; only the sound card and the room are simulated.
The dashboard shows the most interesting laptop of each scenario.
"""
import os
import threading
import time

from .arq import ArqSender, LinkFailed
from .audio import SimAir, AudioBackend
from .config import PhyConfig
from .crypto import SecureChannel
from .link import Node
from .network import Router, NetLink, traceroute
from .sniffer import Sniffer, load_capture
from .transfer import send_file, receive_file, make_messages

DEFAULT_TEXT = (b"Hello from AcousticNet!\nThis text crossed the room as sound: 1400 Hz means 0, 2600 Hz means 1.\n"
                b"CRC-16 catches damaged frames, and Selective Repeat resends only what was lost.\n")
SECRET = (b"Exam answer key - do not share!\nQ1: CRC-16  Q2: Selective Repeat  Q3: 44.4 % for 3 stations.\n"
          b"Anyone in the room can hear sound. Only encryption keeps this private.\n")


def _story(bus, text):
    print(f"\n=== {text} ===", flush=True)
    bus.emit("story", text=text)


def _keep_open(args, dash):
    if dash and not args.no_wait:
        print("Demo finished. Dashboard stays open; Ctrl+C to quit.")
        try:
            while True:
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass


def _data(args, default=DEFAULT_TEXT):
    if getattr(args, "file", None):
        with open(args.file, "rb") as fh:
            return fh.read(), os.path.basename(args.file)
    return default, "hello.txt"


def _rate(args):
    r = getattr(args, "rate", "auto")
    return None if r in (None, "base") else ("auto" if r == "auto" else int(r))


# ----------------------------------------------------------------------------
def basic(args, make_bus, make_dash):
    data, name = _data(args)
    phy = PhyConfig(baud=args.baud)
    air = SimAir(speed=args.speed, seed=args.seed)
    bus = make_bus()
    a = Node(1, AudioBackend(air.add_laptop("sender", (0, 0), 0.08, 0.08)), phy, fec=args.fec, bus=bus,
             rt_latency_s=0.16).start()
    b = Node(2, AudioBackend(air.add_laptop("receiver", (args.distance, 0), 0.07, 0.07)), phy, fec=args.fec,
             bus=bus, rt_latency_s=0.14).start()
    dash = make_dash(bus, b, "Simulated room: node 1 sends to node 2", {"noise": lambda: air.make_noise(1.0, 0.8)})
    box, done = {}, threading.Event()
    # The receiver lingers (like TCP TIME_WAIT) in case a clap destroys its LAST ACK and the sender has to
    # ask again; once the sender has that ACK the demo tells the receiver to stop, so it still ends promptly.
    th = threading.Thread(target=lambda: box.update(r=receive_file(b, args.out, bus=bus, key=args.key,
                                                                   stop_event=done)), daemon=True)
    th.start()
    if args.noise_at:
        def noise():
            a.sleep(args.noise_at)
            print("*** simulated noise burst (someone claps) ***")
            air.make_noise(1.0, 0.8)
        threading.Thread(target=noise, daemon=True).start()
    try:
        if dash:
            time.sleep(args.wait_dashboard)
        b.measure_noise_floor(0.5)
        send_file(a, 2, data, name, args.arq, args.window, args.chunk, bus, rate=_rate(args), key=args.key)
        done.set()
        th.join(60)
        _keep_open(args, dash)
    finally:
        for n in (a, b):
            n.stop()
        air.close()
        if dash:
            dash.stop()
    r = box.get("r")
    return 0 if r and r[1] else 4


# ----------------------------------------------------------------------------
def speed(args, make_bus, make_dash):
    """adaptive speed: quiet room -> fastest mode; a fan switches on -> the link slows down;
    the fan switches off -> it speeds up again"""
    phy = PhyConfig(baud=args.baud)
    air = SimAir(speed=args.speed, seed=args.seed)
    bus = make_bus()
    quiet, noisy = 0.004, 0.25
    a = Node(1, AudioBackend(air.add_laptop("sender", (0, 0), 0.08, 0.08, quiet)), phy, bus=bus,
             rt_latency_s=0.16).start()
    b = Node(2, AudioBackend(air.add_laptop("receiver", (1.0, 0), 0.07, 0.07, quiet)), phy, bus=bus,
             rt_latency_s=0.14).start()
    dash = make_dash(bus, b, "Simulated room: adaptive speed", {"noise": lambda: air.make_noise(1.0, 0.8)})
    text = (b"Adaptive speed demo: watch the speed panel and the tone lanes. " * 2 + b"\n") * 24
    data, _ = _data(args, default=text)
    box, done = {}, threading.Event()
    th = threading.Thread(target=lambda: box.update(r=receive_file(b, args.out, bus=bus, stop_event=done)),
                          daemon=True)
    th.start()

    def weather():
        a.sleep(args.fan_on)
        _story(bus, "a noisy fan switches on next to the receiver")
        air.set_noise("receiver", noisy)
        a.sleep(args.fan_off - args.fan_on)
        _story(bus, "the fan switches off again")
        air.set_noise("receiver", quiet)
    threading.Thread(target=weather, daemon=True).start()
    try:
        if dash:
            time.sleep(args.wait_dashboard)
        b.measure_noise_floor(0.5)
        st = send_file(a, 2, data, "speed_demo.txt", "sr", 4, None, bus, rate="auto")
        print(f"speeds used (MCS: frames): {st['frames_per_mcs']}   history: {st.get('rate_history')}")
        done.set()
        th.join(60)
        _keep_open(args, dash)
    finally:
        for n in (a, b):
            n.stop()
        air.close()
        if dash:
            dash.stop()
    return 0


# ----------------------------------------------------------------------------
def security(args, make_bus, make_dash):
    key = args.key or "vit-cn-lab"
    phy = PhyConfig(baud=args.baud)
    air = SimAir(speed=args.speed, seed=args.seed)
    bus = make_bus()
    a = Node(1, AudioBackend(air.add_laptop("alice", (0, 0), 0.08, 0.08)), phy, bus=bus, rt_latency_s=0.16).start()
    b = Node(2, AudioBackend(air.add_laptop("bob", (1.0, 0), 0.07, 0.07)), phy, bus=bus, rt_latency_s=0.14).start()
    e = Node(250, AudioBackend(air.add_laptop("eve", (0.5, 1.2), 0.07, 0.07)), phy, bus=bus, rt_latency_s=0.14,
             promiscuous=True).start()
    eve = Sniffer(e, os.path.join(args.out, "eve"), bus=bus)
    dash = make_dash(bus, e, "Simulated room: Eve is listening", None)
    rate = _rate(args)
    try:
        if dash:
            time.sleep(args.wait_dashboard)
        e.measure_noise_floor(0.5)

        _story(bus, "1. Alice sends the file WITHOUT encryption")
        done = threading.Event()
        th = threading.Thread(target=lambda: receive_file(b, args.out, bus=bus, stop_event=done), daemon=True)
        th.start()
        send_file(a, 2, SECRET, "answer_key.txt", "sr", 4, None, bus, rate=rate)
        done.set()
        th.join(60)

        _story(bus, "2. Alice sends the same file ENCRYPTED (Bob has the key, Eve does not)")
        channel = SecureChannel(key)
        msgs, sid = make_messages(SECRET, "answer_key_v2.txt", 64 if rate == "auto" else 32, channel)
        stop = threading.Event()

        def bob_loop():
            while not stop.is_set():
                receive_file(b, args.out, bus=bus, linger=3, key=key, idle_timeout=20, stop_event=stop)
        th = threading.Thread(target=bob_loop, daemon=True)
        th.start()
        mcs = 3 if rate == "auto" else rate
        ArqSender(a, 2, "sr", 4, bus=bus, data_mcs=mcs).send_all(msgs)
        a.sleep(4)

        _story(bus, "3. Eve plays back her RECORDING of that transfer, pretending to be Alice (replay attack)")
        rec = eve.flows.get((1, 2), {}).get("capture")
        _, _, recorded = load_capture(rec) if rec else (None, None, msgs)
        print(f"Eve recorded {len(recorded)} of the {len(msgs)} messages -> {rec}")
        e.addr = 1                                  # addresses are not authenticated: Eve can claim to be Alice
        try:
            ArqSender(e, 2, "sr", 4, bus=bus, data_mcs=mcs, max_tries=4).send_all(recorded)
            print("replay was accepted?!")
        except LinkFailed as ex:
            print("Bob refused:", ex)
        finally:
            e.addr = 250
        a.sleep(4)

        _story(bus, "4. Eve forges a file with a guessed key")
        try:
            send_file(e, 2, b"Fake answer key: everything is C.\n", "answer_key.txt", "sr", 4, None, bus,
                      rate=rate, key="password123")
            print("forgery was accepted?!")
        except LinkFailed as ex:
            print("Bob refused:", ex)
        stop.set()
        _keep_open(args, dash)
    finally:
        for n in (a, b, e):
            n.stop()
        air.close()
        if dash:
            dash.stop()
    return 0


# ----------------------------------------------------------------------------
def relay(args, make_bus, make_dash):
    data, name = _data(args)
    phy = PhyConfig(baud=args.baud)
    air = SimAir(speed=args.speed, seed=args.seed)
    air_names = ("A", "R", "B")
    bus = make_bus()
    a = Node(1, AudioBackend(air.add_laptop("A", (0, 0), 0.08, 0.08)), phy, bus=bus, rt_latency_s=0.16).start()
    r = Node(3, AudioBackend(air.add_laptop("R", (2.2, 0), 0.07, 0.07)), phy, bus=bus, rt_latency_s=0.14).start()
    b = Node(2, AudioBackend(air.add_laptop("B", (4.4, 0), 0.07, 0.07)), phy, bus=bus, rt_latency_s=0.14).start()
    air.add_wall(air_names[0], air_names[2], 45)             # A and B cannot hear each other
    ra, rr, rb = Router(a, bus).start(), Router(r, bus).start(), Router(b, bus).start()
    la, lb = NetLink(a, ra), NetLink(b, rb)
    dash = make_dash(bus, r, "Simulated room: R relays between A and B (wall in between)", None)
    box, done = {}, threading.Event()
    try:
        if dash:
            time.sleep(args.wait_dashboard)
        _story(bus, "routers discover neighbours and exchange distance vectors")
        th = threading.Thread(target=lambda: box.update(res=receive_file(lb, args.out, bus=bus, key=args.key,
                                                                         stop_event=done)), daemon=True)
        th.start()
        ra.wait_route(2, 120)
        _story(bus, f"A has a route to B: next hop {ra.dv.lookup(2)}, cost {ra.dv.cost(2)} hops")
        _story(bus, "traceroute from A to B")
        path = traceroute(la, 2, bus=bus)
        print("path: 1 -> " + " -> ".join("*" if h is None else str(h) for h in path))
        _story(bus, "A sends a file to B through R")
        send_file(la, 2, data, name, args.arq, args.window, args.chunk, bus, rate=_rate(args), key=args.key)
        done.set()
        th.join(90)
        print("relay statistics:", rr.stats)
        _keep_open(args, dash)
    finally:
        for n in (a, r, b):
            n.stop()
        air.close()
        if dash:
            dash.stop()
    res = box.get("res")
    return 0 if res and res[1] else 4


SCENARIOS = {"basic": basic, "speed": speed, "security": security, "relay": relay}
