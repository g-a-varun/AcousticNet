"""
events.py - a tiny publish/subscribe "event bus".

Every layer reports what it is doing ("tx", "rx", "ack", "timeout", "progress" ...)
by calling bus.emit(kind, **details).  Whoever is interested subscribes:
    - ConsoleLogger  prints a readable line in the terminal
    - CsvLogger      writes every event to a CSV file (your experiment evidence)
    - the dashboard  streams them to the browser

Why?  The protocol code never needs to know whether a dashboard is open.
"""
import csv
import json
import threading
import time


class EventBus:
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)
        return fn

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def emit(self, kind: str, **data):
        ev = {"kind": kind, "wall": time.time(), **data}
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:       # a broken subscriber must never break the protocol
                pass
        return ev


class ConsoleLogger:
    QUIET = {"spectrum", "level", "chunk", "chunk_rx", "chunk_got", "status", "ber_frame", "mac_success"}

    def __init__(self, bus: EventBus, verbose: bool = False):
        self.verbose = verbose
        self._lock = threading.Lock()
        bus.subscribe(self)

    def __call__(self, ev):
        k = ev["kind"]
        if k in self.QUIET:
            return
        t = ev.get("t")
        stamp = f"[{t:8.2f}s]" if isinstance(t, (int, float)) else "[        ]"
        if k == "tx":
            line = f"TX  {ev['type']:<4} seq={ev['seq']:<3} -> {ev['dst']:<3} {ev['len']:>3} B  {ev['dur']:.2f}s on air" \
                   + ("  (retransmission)" if ev.get("retx") else "")
        elif k == "rx":
            ok = "CRC OK  " if ev["crc_ok"] else "CRC FAIL"
            line = f"RX  {ev['type']:<4} seq={ev['seq']:<3} <- {ev['src']:<3} {ev['len']:>3} B  {ok} " \
                   f"SNR {ev['snr']:4.1f} dB  quality {ev['quality']:.2f}" + \
                   (f"  FEC fixed {ev['fec_fixed']}" if ev.get("fec_fixed") else "")
        elif k == "rx_garbled":
            line = f"RX  ???  chirp heard but {ev.get('reason')} (collision or noise)"
        elif k == "file_done":
            line = (f"FILE SAVED {ev['path']}  ({ev['size']} bytes)  SHA-256 "
                    + ("MATCHES - every bit arrived intact" if ev["ok"] else "MISMATCH - file corrupted"))
        elif k == "message":
            line = f"MESSAGE from {ev['src']}: {ev['text']}"
        elif k == "ping":
            line = (f"PING #{ev['seq']}: no reply" if ev.get("rtt") is None else
                    f"PING #{ev['seq']}: round trip {ev['rtt']:.2f} s  ({ev['airtime']:.2f} s on air + "
                    f"{ev['latency_and_processing']:.2f} s audio latency/processing)")
        elif k == "chunk":
            return
        elif not self.verbose and k in ("rx_own",):
            return
        else:
            rest = {a: b for a, b in ev.items() if a not in ("kind", "wall", "t")}
            line = f"{k.upper():<9} " + " ".join(f"{a}={b}" for a, b in rest.items())
        with self._lock:
            print(f"{stamp} {line}", flush=True)


class CsvLogger:
    def __init__(self, bus: EventBus, path: str):
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.f)
        self.w.writerow(["wall_time", "t", "kind", "details_json"])
        self._lock = threading.Lock()
        bus.subscribe(self)

    def __call__(self, ev):
        if ev["kind"] in ("spectrum", "level"):
            return
        with self._lock:
            rest = {a: b for a, b in ev.items() if a not in ("kind", "wall", "t")}
            self.w.writerow([f"{ev['wall']:.3f}", ev.get("t", ""), ev["kind"], json.dumps(rest, default=str)])
            self.f.flush()

    def close(self):
        self.f.close()
