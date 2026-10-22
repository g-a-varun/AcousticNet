"""
cli.py - every command you type:   python -m acousticnet <command> [options]

  devices        list sound cards
  selftest       check this laptop and save its calibration (run first!)
  recv           wait for files / pings / messages            (receiver laptop)
  send FILE      send a file reliably                          (sender laptop)
  ping           acoustic ping, prints round-trip time
  traceroute     find the multi-hop path to a laptop (needs routing)
  relay          be the laptop in the middle that forwards frames (routing)
  sniff          "Eve": overhear every frame, steal unencrypted files, record transfers
  replay FILE    "Eve": play a recorded transfer back (replay attack)
  say TEXT       one-shot text message (no ACK)
  ber-tx/ber-rx  bit-error-rate test for the distance experiment
  mac-station    take part in the shared-channel experiment    (each sender laptop)
  mac-sink       run the shared-channel experiment             (listener laptop)
  sim-demo       the demos inside a simulated room (no microphone needed):
                 --scenario basic | speed | security | relay
  sim-mac        the shared-channel experiment inside a simulated room
  simulate       run the offline experiments -> results/sim/*.csv
  plot           draw every graph -> results/*.png

Add --help after any command to see its options.
"""
import argparse
import importlib
import os
import sys
import threading
import time

from .config import PhyConfig, LinkConfig, SINK_ADDR, BROADCAST, SUPPORTED_BAUDS
from .events import EventBus, ConsoleLogger, CsvLogger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------- helpers --
def _device(spec):
    if spec is None:
        return None
    if "," in spec:
        return tuple(_device(s.strip()) for s in spec.split(","))
    return int(spec) if spec.strip().isdigit() else spec


def _bus(args):
    bus = EventBus()
    ConsoleLogger(bus, verbose=args.verbose)
    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        CsvLogger(bus, args.log)
    return bus


def _node(args, addr, bus, promiscuous=False):
    from .audio import AudioBackend, open_sounddevice
    from .diagnostics import load_calibration
    from .link import Node
    cal = load_calibration(args.cal)
    phy = PhyConfig(fs=args.fs, baud=args.baud)
    backend = AudioBackend(open_sounddevice(args.fs, _device(args.device), args.blocksize, args.latency),
                           fs=args.fs, volume=args.volume)
    rt = cal.get("rt_latency_s")
    node = Node(addr, backend, phy, fec=args.fec, bus=bus, rt_latency_s=rt, promiscuous=promiscuous)
    if getattr(args, "ignore", None):
        node.ignore = {int(x) for x in args.ignore.split(",") if x.strip()}
    node.start()
    if rt is None:
        print("note: no calibration found (run `selftest` first); assuming 350 ms audio latency")
    node.measure_noise_floor(0.7)
    return node


def _link(args, node, bus, forward=False):
    """plain Node, or (with --routed) a NetLink that routes over several hops"""
    if not getattr(args, "routed", False) and not forward:
        return node, None
    from .network import Router, NetLink
    router = Router(node, bus, forward=True).start()
    return NetLink(node, router), router


def _rate(value):
    return None if value in (None, "base") else ("auto" if value == "auto" else int(value))


def _dashboard(args, bus, node, role, **kw):
    if args.no_dashboard:
        return None
    from .dashboard import Dashboard
    d = Dashboard(bus, node, port=args.port, role=role, **kw)
    d.start(open_browser=not args.no_browser)
    return d


def _wait_forever(stop):
    try:
        while not stop.is_set():
            time.sleep(0.3)
    except KeyboardInterrupt:
        stop.set()


# ---------------------------------------------------------------- commands --
def cmd_devices(args):
    try:
        import sounddevice as sd
    except Exception as e:
        print("sounddevice is not usable:", e)
        return 1
    print(sd.query_devices())
    print("\nDefault (input, output):", sd.default.device)
    print("Use --device N  or  --device IN,OUT  to choose.")
    return 0


def cmd_selftest(args):
    from .audio import AudioBackend, open_sounddevice
    from .diagnostics import run_selftest
    from .phy import Modem
    backend = AudioBackend(open_sounddevice(args.fs, _device(args.device), args.blocksize, args.latency),
                           fs=args.fs, volume=args.volume).start()
    try:
        res = run_selftest(backend, Modem(PhyConfig(fs=args.fs, baud=args.baud)), cal_path=args.cal)
    finally:
        backend.stop()
    return 0 if res["ready"] else 2


def cmd_recv(args):
    from .transfer import receive_file
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    link, _ = _link(args, node, bus)
    dash = _dashboard(args, bus, node, "Receiver" + (" (encrypted)" if args.key else ""))
    print(f"Receiver ready (node {args.addr}). Waiting for files... Ctrl+C to stop.")
    try:
        while True:
            r = receive_file(link, args.out, bus=bus, key=args.key)
            if r and args.once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        if dash:
            dash.stop()
    return 0


def cmd_send(args):
    from .arq import LinkFailed
    from .transfer import send_file
    with open(args.file, "rb") as fh:
        data = fh.read()
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    link, _ = _link(args, node, bus)
    dash = _dashboard(args, bus, node, "Sender" + (" (encrypted)" if args.key else ""))
    if dash:
        time.sleep(args.wait_dashboard)
    code = 0
    try:
        st = send_file(link, args.to, data, os.path.basename(args.file), args.arq, args.window, args.chunk, bus,
                       rate=_rate(args.rate), key=args.key)
        print(f"\nDone: {len(data)} bytes in {st['elapsed_s']} s, goodput {st['file_goodput_bps']} bit/s, "
              f"{st['data_frames']} data frames ({st['retransmissions']} retransmissions, {st['timeouts']} timeouts), "
              f"frames per speed {st['frames_per_mcs']}")
        if dash and not args.no_wait:
            print("Dashboard stays open. Ctrl+C to quit.")
            _wait_forever(threading.Event())
    except LinkFailed as e:
        print("Transfer failed:", e)
        code = 3
    except KeyboardInterrupt:
        code = 130
    finally:
        node.stop()
        if dash:
            dash.stop()
    return code


def cmd_ping(args):
    from .transfer import ping
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    link, _ = _link(args, node, bus)
    try:
        r = [x for x in ping(link, args.to, args.count, bus) if x is not None]
        if r:
            air = link.frame_time(4) + link.frame_time(5)
            est = (sum(r) / len(r) - air) / 2
            print(f"\naverage round trip {sum(r) / len(r):.2f} s; airtime {air:.2f} s; "
                  f"so each laptop adds about {est * 1000:.0f} ms of audio latency + processing")
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
    return 0


def cmd_traceroute(args):
    from .network import traceroute
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    link, router = _link(args, node, bus, forward=True)
    try:
        print(f"waiting for a route to node {args.to} ...")
        path = traceroute(link, args.to, args.max_hops, bus)
        print("\npath: " + " -> ".join([str(args.addr)] + [("*" if h is None else str(h)) for h in path]))
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
    return 0


def cmd_relay(args):
    from .frame import T_PING
    from .transfer import answer_ping
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    link, router = _link(args, node, bus, forward=True)
    dash = _dashboard(args, bus, node, f"Relay (node {args.addr})")
    print(f"Relay node {args.addr}: learning routes and forwarding. Ctrl+C to stop.")
    try:
        while True:
            f = link.recv(1.0, accept=lambda f: f.type == T_PING)
            if f is not None:
                answer_ping(link, f)
    except KeyboardInterrupt:
        pass
    finally:
        print("forwarded:", router.stats)
        node.stop()
        if dash:
            dash.stop()
    return 0


def cmd_sniff(args):
    from .sniffer import Sniffer
    bus = _bus(args)
    node = _node(args, args.addr, bus, promiscuous=True)
    sn = Sniffer(node, args.out, args.key, bus)
    dash = _dashboard(args, bus, node, "Eavesdropper (Eve)")
    print("Eve is listening to every frame in the room. Ctrl+C to stop.")
    _wait_forever(threading.Event())
    print("\nEve's haul:", sn.stats)
    node.stop()
    if dash:
        dash.stop()
    return 0


def cmd_replay(args):
    """Eve plays back a transfer she recorded (a replay attack), by default pretending to be the
    original sender - addresses are not authenticated, only the encrypted content is"""
    from .arq import ArqSender, LinkFailed
    from .sniffer import load_capture
    src, dst, msgs = load_capture(args.capture)
    me = src if args.addr is None else args.addr
    bus = _bus(args)
    node = _node(args, me, bus)
    dash = _dashboard(args, bus, node, "Eve: replay attack")
    if dash:
        time.sleep(args.wait_dashboard)
    print(f"Replaying {len(msgs)} recorded messages to node {args.to or dst}, pretending to be node {me}...")
    code = 0
    try:
        ArqSender(node, args.to or dst, "sr", 4, max_tries=args.tries, bus=bus,
                  data_mcs=int(args.rate)).send_all(msgs)
        print("The receiver ACCEPTED the replay (it was not protected).")
    except KeyboardInterrupt:
        pass
    except LinkFailed as e:
        print("Replay failed:", e)
        code = 3
    try:
        if dash and not args.no_wait:
            print("Dashboard stays open. Ctrl+C to quit.")
            _wait_forever(threading.Event())
    finally:
        node.stop()
        if dash:
            dash.stop()
    return code


def cmd_say(args):
    from .frame import Frame, T_MSG
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    try:
        node.send(Frame(T_MSG, args.addr, args.to, 0, args.text.encode("utf-8")[:200]))
    finally:
        node.stop()
    return 0


def cmd_ber_tx(args):
    from .bertest import ber_transmit
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    try:
        ber_transmit(node, args.frames, args.length, args.gap)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
    return 0


def cmd_ber_rx(args):
    from .bertest import ber_receive
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    dash = _dashboard(args, bus, node, "BER receiver")
    try:
        row = ber_receive(node, args.frames, args.length, args.distance, args.csv)
        print("\nResult:", row)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        if dash:
            dash.stop()
    return 0


def cmd_mac_station(args):
    from .mac import MacStation
    bus = _bus(args)
    node = _node(args, args.addr, bus)
    dash = _dashboard(args, bus, node, f"Shared-channel station {args.addr}")
    stop = threading.Event()
    print(f"Station {args.addr} waiting for the sink. Ctrl+C to stop.")
    th = threading.Thread(target=MacStation(node, bus).serve, args=(stop,), daemon=True)
    th.start()
    _wait_forever(stop)
    node.stop()
    if dash:
        dash.stop()
    return 0


def cmd_mac_sink(args):
    from .mac import MacSink
    bus = _bus(args)
    node = _node(args, SINK_ADDR, bus)
    dash = _dashboard(args, bus, node, "Shared-channel sink")
    stations = [int(s) for s in args.stations.split(",")]
    protos = [p.strip() for p in args.protocols.split(",")]
    ps = [float(x) for x in args.p.split(",")]
    sink = MacSink(node, stations, bus)
    print(f"Sink: {len(protos) * len(ps) * args.repeats} runs of {args.duration} s, results -> {args.csv}")
    try:
        sink.sweep(protos, ps, args.duration, args.repeats, args.csv, payload_len=args.payload,
                   guard_ms=args.guard_ms)
        print("Sweep finished. Run `python -m acousticnet plot` to draw the graph.")
        if dash and not args.no_wait:
            _wait_forever(threading.Event())
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        if dash:
            dash.stop()
    return 0


def cmd_sim_demo(args):
    """scripted demos in a simulated room (see scenarios.py)"""
    from .scenarios import SCENARIOS
    return SCENARIOS[args.scenario](args, lambda: _bus(args),
                                    lambda bus, node, role, actions: _dashboard(args, bus, node, role, actions=actions))


def cmd_sim_mac(args):
    """3 stations + sink inside a simulated room"""
    from .audio import SimAir, AudioBackend
    from .link import Node
    from .mac import MacStation, MacSink
    air = SimAir(speed=args.speed, seed=args.seed)
    phy = PhyConfig(baud=args.baud)
    bus = _bus(args)
    stop = threading.Event()
    rng = __import__("numpy").random.default_rng(args.seed)
    nodes = []
    import math
    for i in range(1, args.stations + 1):
        ang = 2 * math.pi * i / args.stations
        o, inn = rng.uniform(0.05, 0.12), rng.uniform(0.05, 0.12)
        n = Node(i, AudioBackend(air.add_laptop(f"station{i}", (1.2 * math.cos(ang), 1.2 * math.sin(ang)), o, inn)),
                 phy, bus=EventBus(), rt_latency_s=o + inn).start()
        nodes.append(n)
        threading.Thread(target=MacStation(n).serve, args=(stop,), daemon=True).start()
    sink = Node(SINK_ADDR, AudioBackend(air.add_laptop("sink", (0, 0), 0.07, 0.07)), phy, bus=bus,
                rt_latency_s=0.14).start()
    dash = _dashboard(args, bus, sink, f"Simulated room: {args.stations} stations share one channel")
    try:
        if dash:
            time.sleep(args.wait_dashboard)
        sink.measure_noise_floor(0.5)
        nodes[0].sleep(1.5)
        ms = MacSink(sink, [n.addr for n in nodes], bus)
        ms.sweep([p.strip() for p in args.protocols.split(",")], [float(x) for x in args.p.split(",")],
                 args.duration, 1, args.csv)
        if dash and not args.no_wait:
            print("Finished. Dashboard stays open; Ctrl+C to quit.")
            _wait_forever(threading.Event())
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for n in nodes + [sink]:
            n.stop()
        air.close()
        if dash:
            dash.stop()
    return 0


def _experiment(name):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module(f"experiments.{name}")


def cmd_simulate(args):
    # order matters: "rate" uses the per-speed curves that "phy" writes
    which = ["phy", "rate", "arq", "mac", "errdetect", "routing"] if args.which == "all" else [args.which]
    for w in which:
        print(f"== simulating {w} ==", flush=True)
        _experiment(f"{w}_sim").main()
    return 0


def cmd_plot(args):
    _experiment("plot_results").main()
    return 0


# ------------------------------------------------------------------ parser --
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("audio / physical layer")
    g.add_argument("--baud", type=int, default=100, choices=SUPPORTED_BAUDS, help="bits per second on air (default 100)")
    g.add_argument("--fec", action="store_true", help="Hamming(7,4) forward error correction (both laptops must agree)")
    g.add_argument("--volume", type=float, default=0.8, help="output volume 0..1 (default 0.8)")
    g.add_argument("--device", default=None, help="sound device index/name, or IN,OUT")
    g.add_argument("--fs", type=int, default=48000, help="sample rate (48000 or 44100)")
    g.add_argument("--blocksize", type=int, default=2048, help="audio block size in samples")
    g.add_argument("--latency", default="high", help="'high' (safe), 'low', or seconds")
    g.add_argument("--cal", default="acousticnet_calibration.json", help="calibration file")
    n = common.add_argument_group("network / security")
    n.add_argument("--routed", action="store_true", help="use multi-hop routing (needs a `relay` laptop in between)")
    n.add_argument("--ignore", default=None, help="pretend not to hear these addresses, e.g. 1 (emulates a wall)")
    n.add_argument("--key", default=None, help="passphrase for encryption + authentication (both sides)")
    o = common.add_argument_group("output")
    o.add_argument("--no-dashboard", action="store_true", help="do not start the web dashboard")
    o.add_argument("--no-browser", action="store_true", help="start the dashboard but don't open a browser")
    o.add_argument("--port", type=int, default=8765, help="dashboard port (default 8765)")
    o.add_argument("--log", default=None, help="also write every event to this CSV file")
    o.add_argument("--verbose", action="store_true")
    o.add_argument("--no-wait", action="store_true", help="exit when done instead of keeping the dashboard open")
    o.add_argument("--wait-dashboard", type=float, default=2.0, help="seconds to wait for the browser before starting")

    p = argparse.ArgumentParser(prog="python -m acousticnet", description="AcousticNet: a data network over sound.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("devices", parents=[common], help="list sound devices")
    s.set_defaults(fn=cmd_devices)
    s = sub.add_parser("selftest", parents=[common], help="check this laptop, save calibration")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("recv", parents=[common], help="receive files (receiver laptop)")
    s.add_argument("--addr", type=int, default=2)
    s.add_argument("--out", default="received", help="folder for received files")
    s.add_argument("--once", action="store_true", help="exit after one file")
    s.set_defaults(fn=cmd_recv)

    s = sub.add_parser("send", parents=[common], help="send a file (sender laptop)")
    s.add_argument("file")
    s.add_argument("--addr", type=int, default=1)
    s.add_argument("--to", type=int, default=2)
    s.add_argument("--arq", default="sr", choices=["sw", "gbn", "sr"], help="Stop-and-Wait, Go-Back-N, Selective Repeat")
    s.add_argument("--window", type=int, default=4)
    s.add_argument("--chunk", type=int, default=None, help="payload bytes per frame (default: depends on speed)")
    s.add_argument("--rate", default="auto", choices=["auto", "base", "0", "1", "2", "3", "4"],
                   help="payload speed: auto (adaptive), base, or a fixed MCS 0-4")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("ping", parents=[common], help="acoustic ping")
    s.add_argument("--addr", type=int, default=1)
    s.add_argument("--to", type=int, default=2)
    s.add_argument("--count", type=int, default=3)
    s.set_defaults(fn=cmd_ping)

    s = sub.add_parser("traceroute", parents=[common], help="find the multi-hop path to a laptop")
    s.add_argument("--addr", type=int, default=1)
    s.add_argument("--to", type=int, default=2)
    s.add_argument("--max-hops", type=int, default=6)
    s.set_defaults(fn=cmd_traceroute)

    s = sub.add_parser("relay", parents=[common], help="forward frames between laptops that can't hear each other")
    s.add_argument("--addr", type=int, default=3)
    s.set_defaults(fn=cmd_relay)

    s = sub.add_parser("sniff", parents=[common], help="eavesdropper: overhear everything, steal unencrypted files")
    s.add_argument("--addr", type=int, default=250)
    s.add_argument("--out", default="sniffed", help="folder for stolen files")
    s.set_defaults(fn=cmd_sniff)

    s = sub.add_parser("replay", parents=[common], help="Eve plays back a recorded transfer (replay attack)")
    s.add_argument("capture", help="a sniffed/capture_*.json file written by `sniff`")
    s.add_argument("--to", type=int, default=None, help="default: the original receiver")
    s.add_argument("--addr", type=int, default=None, help="default: pretend to be the original sender")
    s.add_argument("--tries", type=int, default=6)
    s.add_argument("--rate", default="3", choices=["0", "1", "2", "3", "4"], help="speed for the replayed frames")
    s.set_defaults(fn=cmd_replay)

    s = sub.add_parser("say", parents=[common], help="send a one-shot text message")
    s.add_argument("text")
    s.add_argument("--addr", type=int, default=1)
    s.add_argument("--to", type=int, default=BROADCAST)
    s.set_defaults(fn=cmd_say)

    s = sub.add_parser("ber-tx", parents=[common], help="send BER test frames")
    s.add_argument("--addr", type=int, default=1)
    s.add_argument("--frames", type=int, default=30)
    s.add_argument("--length", type=int, default=32)
    s.add_argument("--gap", type=float, default=0.6)
    s.set_defaults(fn=cmd_ber_tx)
    s = sub.add_parser("ber-rx", parents=[common], help="measure BER")
    s.add_argument("--addr", type=int, default=2)
    s.add_argument("--frames", type=int, default=30)
    s.add_argument("--length", type=int, default=32)
    s.add_argument("--distance", default="", help="label for this run, e.g. 1.0")
    s.add_argument("--csv", default=os.path.join("results", "measured", "ber_measured.csv"))
    s.set_defaults(fn=cmd_ber_rx)

    s = sub.add_parser("mac-station", parents=[common], help="shared-channel experiment: station")
    s.add_argument("--addr", type=int, required=True, help="1, 2, 3 ...")
    s.set_defaults(fn=cmd_mac_station)
    s = sub.add_parser("mac-sink", parents=[common], help="shared-channel experiment: sink / controller")
    s.add_argument("--stations", default="1,2,3")
    s.add_argument("--protocols", default="aloha,slotted,csma")
    s.add_argument("--p", default="0.1,0.2,0.33,0.5,0.8", help="load values to sweep")
    s.add_argument("--duration", type=int, default=90, help="seconds per run")
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--payload", type=int, default=4)
    s.add_argument("--guard-ms", type=int, default=250)
    s.add_argument("--csv", default=os.path.join("results", "measured", "mac_measured.csv"))
    s.set_defaults(fn=cmd_mac_sink)

    s = sub.add_parser("sim-demo", parents=[common], help="demos in a simulated room (no microphone needed)")
    s.add_argument("--scenario", default="basic", choices=["basic", "speed", "security", "relay"])
    s.add_argument("--file", default=None)
    s.add_argument("--arq", default="sr", choices=["sw", "gbn", "sr"])
    s.add_argument("--window", type=int, default=4)
    s.add_argument("--chunk", type=int, default=None)
    s.add_argument("--rate", default="auto", choices=["auto", "base", "0", "1", "2", "3", "4"])
    s.add_argument("--fan-on", type=float, default=25.0, help="speed scenario: when the noisy fan starts (s)")
    s.add_argument("--fan-off", type=float, default=60.0, help="speed scenario: when it stops (s)")
    s.add_argument("--speed", type=float, default=1.0, help="1 = real time, 4 = four times faster")
    s.add_argument("--distance", type=float, default=1.0, help="metres between the two virtual laptops")
    s.add_argument("--noise-at", type=float, default=None, help="inject a noise burst after N seconds")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", default=os.path.join("received", "sim"))
    s.set_defaults(fn=cmd_sim_demo)

    s = sub.add_parser("sim-mac", parents=[common], help="shared-channel experiment in a simulated room")
    s.add_argument("--stations", type=int, default=3)
    s.add_argument("--protocols", default="aloha,slotted,csma")
    s.add_argument("--p", default="0.2,0.5")
    s.add_argument("--duration", type=int, default=60)
    s.add_argument("--speed", type=float, default=4.0)
    s.add_argument("--seed", type=int, default=3)
    s.add_argument("--csv", default=os.path.join("results", "sim", "mac_emulated.csv"))
    s.set_defaults(fn=cmd_sim_mac)

    s = sub.add_parser("simulate", parents=[common], help="run offline experiments")
    s.add_argument("which", nargs="?", default="all", choices=["all", "phy", "rate", "arq", "mac", "errdetect", "routing"])
    s.set_defaults(fn=cmd_simulate)
    s = sub.add_parser("plot", parents=[common], help="draw all graphs")
    s.set_defaults(fn=cmd_plot)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")      # Windows consoles + non-ASCII file names
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except RuntimeError as e:                          # friendly message for sound-card problems
        print("ERROR:", e)
        return 1
