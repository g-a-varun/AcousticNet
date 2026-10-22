"""
mac.py - the MAC-layer experiment: many laptops sharing ONE acoustic channel.

Roles
    MacStation  (each sender laptop)  waits for a START command, then transmits short frames
                according to ALOHA, Slotted ALOHA or CSMA for `duration` seconds.
    MacSink     (one listener laptop) is the "base station": it starts each run over sound,
                counts the frames it decodes correctly, then polls every station for how many
                frames it tried to send, and writes one CSV row per run.
    A full sweep (3 protocols x several loads) runs unattended - start the stations, start
    the sink, go and get tea.

Protocols (p = load knob; each station offers about p frames per frame-time)
    aloha    every 1/8 frame-time: with probability p/8 transmit now (if not already talking)
    slotted  time is cut into slots (frame + guard).  At each slot start: transmit with prob. p
    csma     like aloha, but a new frame is only sent if the channel sounds idle; if busy,
             wait a random 0.5-1.5 frame-times and listen again (non-persistent CSMA)

Slot alignment = GSM "timing advance".  Every laptop hears the START frame end at the same
moment.  Each laptop knows its own round-trip audio latency rt (from `calibrate`), so to make
its sound hit the air exactly at a slot boundary it schedules the samples rt seconds EARLY.

Why the sink polls for reports over sound: it keeps the whole experiment cable-free and
Wi-Fi-free, and is itself a third MAC style ("polling / controlled access", Forouzan 12.2).
"""
import struct
import threading
import time
from dataclasses import dataclass, asdict

import numpy as np

from .config import BROADCAST, SINK_ADDR
from .frame import Frame, T_CTRL, T_MAC

PROTOCOLS = {"aloha": 0, "slotted": 1, "csma": 2}
PROTO_NAMES = {v: k for k, v in PROTOCOLS.items()}

_START = b"S"
_REPORT_REQ = b"R"
_REPORT = b"r"
_START_FMT = ">cBBHHBHHBH"      # kind, run, proto, p*10000, duration, payload, guard_ms, seed, mini, lead_ms
_REPORT_FMT = ">cBHHHHH"        # kind, run, arrivals, senses, transmissions, busy_defers, late


@dataclass
class RunParams:
    run_id: int
    protocol: str
    p: float
    duration_s: int = 90
    payload_len: int = 4
    guard_ms: int = 250
    seed: int = 1
    mini: int = 8
    lead_ms: int = 1500

    def pack(self) -> bytes:
        return struct.pack(_START_FMT, _START, self.run_id & 0xFF, PROTOCOLS[self.protocol],
                           int(round(self.p * 10000)), self.duration_s, self.payload_len,
                           self.guard_ms, self.seed, self.mini, self.lead_ms)

    @staticmethod
    def unpack(b: bytes) -> "RunParams":
        k, run, proto, p, dur, plen, guard, seed, mini, lead = struct.unpack(_START_FMT, b[:struct.calcsize(_START_FMT)])
        return RunParams(run, PROTO_NAMES[proto], p / 10000, dur, plen, guard, seed, mini, lead)


@dataclass
class Report:
    run_id: int
    arrivals: int = 0         # new frames generated
    senses: int = 0           # CSMA: times the channel was checked (Kleinrock's G counts these)
    transmissions: int = 0    # frames actually put on air
    busy_defers: int = 0      # CSMA: found busy, backed off
    late: int = 0             # slotted: could not start exactly on the slot boundary

    def pack(self) -> bytes:
        c = lambda v: min(int(v), 65535)
        return struct.pack(_REPORT_FMT, _REPORT, self.run_id & 0xFF, c(self.arrivals), c(self.senses),
                           c(self.transmissions), c(self.busy_defers), c(self.late))

    @staticmethod
    def unpack(b: bytes) -> "Report":
        _, run, a, s, t, d, l = struct.unpack(_REPORT_FMT, b[:struct.calcsize(_REPORT_FMT)])
        return Report(run, a, s, t, d, l)


def _is_ctrl(kind: bytes):
    return lambda f: f.type == T_CTRL and f.payload[:1] == kind


# =============================================================================
class MacStation:
    LOOKAHEAD_S = 0.15        # wake up this early to schedule a slotted transmission

    def __init__(self, node, bus=None):
        self.node = node
        self.bus = bus or node.bus
        self.reports = {}

    def serve(self, stop_event: threading.Event = None):
        """Wait for START / REPORT requests forever (or until stop_event is set)."""
        node = self.node
        if node.noise_floor is None:
            node.measure_noise_floor(1.0)
        self.bus.emit("mac_ready", t=round(node.now(), 2), addr=node.addr,
                      rt_latency_ms=round(node.rt_latency_s * 1000))
        while stop_event is None or not stop_event.is_set():
            f = node.recv(1.0, accept=lambda f: f.type == T_CTRL)
            if f is None:
                continue
            kind = f.payload[:1]
            if kind == _START:
                self.run(RunParams.unpack(f.payload), f)
            elif kind == _REPORT_REQ and f.dst == node.addr:
                run = f.payload[1]
                rep = self.reports.get(run, Report(run))
                node.sleep(0.2)
                node.send(Frame(T_CTRL, node.addr, f.src, run, rep.pack()))

    def run(self, P: RunParams, start_frame: Frame) -> Report:
        node, fs = self.node, self.node.fs
        T = node.frame_time(P.payload_len)
        T_s = int(T * fs)
        rt = int(node.rt_latency_s * fs)
        t0 = start_frame.rx_end + int(P.lead_ms / 1000 * fs)        # local mic-clock index
        end = t0 + P.duration_s * fs
        rng = np.random.default_rng(P.seed * 7919 + node.addr)
        rep = Report(P.run_id)
        self.reports[P.run_id] = rep
        slotted = P.protocol == "slotted"
        step = T_s + int(P.guard_ms / 1000 * fs) if slotted else max(1, T_s // P.mini)
        prob = P.p if slotted else P.p / P.mini
        payload = bytes([P.run_id & 0xFF]) + bytes(max(0, P.payload_len - 1))
        self.bus.emit("mac_run", t=round(node.now(), 2), run=P.run_id, protocol=P.protocol, p=P.p,
                      frame_s=round(T, 3), step_s=round(step / fs, 3))
        busy_until = 0          # local index until which our own previous frame occupies us
        pending = False         # CSMA: a frame is waiting to be sent
        retry_at = 0
        seq = 0
        # If this laptop noticed START late (busy CPU), never "catch up" by firing slots that are
        # already in the past - skip ahead to the first step that is still in the future.
        now = node.backend.rx.end
        ahead = rt + int(self.LOOKAHEAD_S * fs) if slotted else 0
        k = max(0, -(-(now + ahead - t0) // step))
        if k:
            self.bus.emit("mac_late_start", t=round(node.now(), 2), run=P.run_id, skipped_steps=int(k))
        while True:
            x = t0 + k * step
            k += 1
            if x >= end:
                break
            if slotted:
                node.backend.wait_index(x - rt - int(self.LOOKAHEAD_S * fs))
                if x - rt >= busy_until and rng.random() < prob:
                    rep.arrivals += 1
                    job = node.send(Frame(T_MAC, node.addr, SINK_ADDR, seq, payload), at=x - rt,
                                    wait=False, run=P.run_id)
                    rep.transmissions += 1
                    seq += 1
                    busy_until = x - rt + T_s
                    self._count_late(job, rep)
                continue
            node.backend.wait_index(x)
            now = node.backend.rx.end
            if P.protocol == "aloha":
                if now >= busy_until and rng.random() < prob:
                    rep.arrivals += 1
                    node.send(Frame(T_MAC, node.addr, SINK_ADDR, seq, payload), wait=False, run=P.run_id)
                    rep.transmissions += 1
                    seq += 1
                    busy_until = now + T_s + rt
            else:   # csma, non-persistent
                if not pending and now >= busy_until and rng.random() < prob:
                    pending = True
                    rep.arrivals += 1
                    retry_at = now
                if pending and now >= busy_until and now >= retry_at:
                    rep.senses += 1
                    if node.channel_busy():
                        rep.busy_defers += 1
                        retry_at = now + int(rng.uniform(0.5, 1.5) * T_s)
                    else:
                        node.send(Frame(T_MAC, node.addr, SINK_ADDR, seq, payload), wait=False, run=P.run_id)
                        rep.transmissions += 1
                        seq += 1
                        pending = False
                        busy_until = now + T_s + rt
        node.backend.wait_index(max(busy_until, end))
        self.bus.emit("mac_run_done", t=round(node.now(), 2), **asdict(rep))
        return rep

    def _count_late(self, job, rep):
        def check():
            job.wait(30)
            if job.late:
                rep.late += 1
        threading.Thread(target=check, daemon=True).start()


# =============================================================================
class MacSink:
    def __init__(self, node, stations, bus=None):
        self.node = node
        self.stations = list(stations)
        self.bus = bus or node.bus
        self.run_counter = 0
        self._lock = threading.Lock()
        self._live = {}
        node.handlers.append(self._on_frame)

    def _on_frame(self, f: Frame) -> bool:
        if f.type != T_MAC:
            return False
        with self._lock:
            run = f.payload[0] if f.payload else -1
            d = self._live.setdefault(run, {})
            d[f.src] = d.get(f.src, 0) + 1
            total = sum(d.values())
        self.bus.emit("mac_success", t=round(f.rx_end / self.node.fs, 2), run=run, src=f.src, seq=f.seq,
                      total=total)
        return True

    def run_one(self, protocol: str, p: float, duration_s: int = 90, payload_len: int = 4,
                guard_ms: int = 250, seed: int = None, mini: int = 8, lead_ms: int = 1500) -> dict:
        node = self.node
        self.run_counter = (self.run_counter + 1) % 256
        run = self.run_counter
        P = RunParams(run, protocol, p, duration_s, payload_len, guard_ms,
                      seed if seed is not None else int(time.time()) % 60000, mini, lead_ms)
        garbled0 = node.stats["garbled"] + node.stats["rx_crc_fail"]
        with self._lock:
            self._live[run] = {}
        self.bus.emit("mac_start", t=round(node.now(), 2), run=run, protocol=protocol, p=p,
                      duration=duration_s, stations=self.stations)
        node.send(Frame(T_CTRL, node.addr, BROADCAST, run, P.pack()))
        T = node.frame_time(payload_len)
        node.sleep(lead_ms / 1000 + duration_s + T + 2 * node.rt_latency_s + 1.0)
        node.catch_up()                      # make sure every frame of the run has been decoded
        with self._lock:
            succ = dict(self._live.get(run, {}))
        garbled = node.stats["garbled"] + node.stats["rx_crc_fail"] - garbled0

        reports = {}
        for st in self.stations:
            for attempt in range(4):
                node.send(Frame(T_CTRL, node.addr, st, run, _REPORT_REQ + bytes([run])))
                f = node.recv(node.ack_timeout(12) + 0.5,
                              accept=lambda f, st=st: _is_ctrl(_REPORT)(f) and f.src == st and f.payload[1] == run)
                if f is not None:
                    reports[st] = Report.unpack(f.payload)
                    break
        tx = sum(r.transmissions for r in reports.values())
        senses = sum(r.senses for r in reports.values())
        ok = sum(succ.values())
        frames_per_run = duration_s / T
        row = {
            "run": run, "protocol": protocol, "p": p, "n_stations": len(self.stations),
            "duration_s": duration_s, "frame_s": round(T, 4), "guard_ms": guard_ms,
            "successes": ok, "transmissions": tx, "senses": senses, "garbled": garbled,
            "reports_received": len(reports),
            "S": round(ok / frames_per_run, 4),
            "G_tx": round(tx / frames_per_run, 4),
            "G_attempts": round((senses if protocol == "csma" else tx) / frames_per_run, 4),
            "late_slots": sum(r.late for r in reports.values()),
            # slotted ALOHA is compared with theory PER SLOT (a slot = frame + guard time)
            "S_slot": round(ok / max(1, int(duration_s / (T + guard_ms / 1000))), 4) if protocol == "slotted" else round(ok / frames_per_run, 4),
            "G_slot": round(tx / max(1, int(duration_s / (T + guard_ms / 1000))), 4) if protocol == "slotted" else round(tx / frames_per_run, 4),
            "per_station_success": ";".join(f"{k}:{v}" for k, v in sorted(succ.items())),
        }
        self.bus.emit("mac_result", t=round(node.now(), 2), **row)
        return row

    def sweep(self, protocols, ps, duration_s=90, repeats=1, csv_path=None, **kw):
        import csv
        import os
        rows = []
        writer, fh = None, None
        if csv_path:
            os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
            new = not os.path.exists(csv_path)
            fh = open(csv_path, "a", newline="", encoding="utf-8")
        try:
            for _ in range(repeats):
                for proto in protocols:
                    for p in ps:
                        row = self.run_one(proto, p, duration_s, **kw)
                        rows.append(row)
                        if fh:
                            if writer is None:
                                writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
                                if new:
                                    writer.writeheader()
                            writer.writerow(row)
                            fh.flush()
                        self.node.sleep(2.0)
        finally:
            if fh:
                fh.close()
        return rows
