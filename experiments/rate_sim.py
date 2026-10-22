"""
rate_sim.py - is adaptive speed worth it?  Runs the REAL ARQ + rate-control code on the
VirtualNet, with frame losses taken from the measured per-speed curves (phy_sim "mcs").

Experiment A "envelope":  steady channel, SNR from -3 to 30 dB.  Goodput of every fixed speed
                          and of adaptive speed.  Adaptive should ride the top of all curves.
Experiment B "walk":      a noisy fan next to the receiver switches on and off every 45 s:
                          SNR jumps between 25 dB (fan off) and 0 dB (fan on).
                          Bytes delivered over time for fixed speeds and adaptive speed.
                          Bytes delivered over time for fixed slow, fixed fast and adaptive.

Output: results/sim/rate_sim.csv  (needs results/sim/phy_sim.csv first)
"""
import csv
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from acousticnet.arq import LinkFailed                    # noqa: E402
from acousticnet.events import EventBus                   # noqa: E402
from acousticnet.virtual import run_virtual_transfer      # noqa: E402


def load_curves(path=None):
    """-> {mcs: (inband_snr[], fsr[], header_snr[])} from phy_sim.csv"""
    path = path or os.path.join(ROOT, "results", "sim", "phy_sim.csv")
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["experiment"] == "mcs"]
    out = {}
    for m in sorted({int(r["mcs"]) for r in rows}):
        rr = sorted([r for r in rows if int(r["mcs"]) == m], key=lambda r: float(r["snr_db"]))
        snr = np.array([float(r["snr_db"]) for r in rr])
        fsr = np.array([float(r["fsr"]) for r in rr])
        hdr = np.array([float(r["header_snr_db"]) if r["header_snr_db"] else 0.0 for r in rr])
        fsr = np.maximum.accumulate(fsr)            # monotone (removes trial noise)
        out[m] = (snr, fsr, hdr)
    return out


class Channel:
    def __init__(self, curves, snr_of_t):
        self.c = curves
        self.snr_of_t = snr_of_t
        s, _, h = curves[1]
        self._s, self._h = s, np.maximum.accumulate(h)

    def reported(self, t):
        """the header SNR the receiver would measure"""
        return float(np.interp(self.snr_of_t(t), self._s, self._h))

    def loss(self, mcs, t, payload_len):
        s, fsr, _ = self.c[mcs]
        p_ok32 = float(np.interp(self.snr_of_t(t), s, fsr))
        return 1.0 - max(p_ok32, 1e-6) ** (max(payload_len, 8) / 32.0)     # longer frames fail more


STRATEGIES = [("fixed MCS1 (2-FSK)", 1), ("fixed MCS3 (16-FSK)", 3), ("fixed MCS4 (fastest)", 4), ("adaptive", "auto")]


def run(strategy, data, chan, seed):
    rx_bus = EventBus()
    got = []
    rx_bus.subscribe(lambda ev: ev["kind"] == "chunk_rx" and got.append((ev["t"], ev["bytes"])))
    tx_bus = EventBus()
    speeds = []
    tx_bus.subscribe(lambda ev: ev["kind"] == "tx" and ev["type"] == "DATA" and speeds.append((ev["t"], ev["mcs"])))
    ok, stats = True, {}
    try:
        stats, recv = run_virtual_transfer(data, "sr", 4, rate=strategy, seed=seed, snr_fn=chan.reported,
                                           loss_fn=chan.loss, bus=tx_bus, rx_bus=rx_bus)
        ok = recv == data
    except LinkFailed:
        ok = False
    return ok, stats, got, speeds


def main(out_csv=None, seeds=3):
    out_csv = out_csv or os.path.join(ROOT, "results", "sim", "rate_sim.csv")
    curves = load_curves()
    rows = []
    data = np.random.default_rng(0).integers(0, 256, 1500, dtype=np.uint8).tobytes()
    for snr in (-3, 0, 2, 4, 6, 9, 12, 18, 24, 30):
        chan = Channel(curves, lambda t, s=snr: s)
        for name, strat in STRATEGIES:
            g = []
            for seed in range(seeds):
                ok, st, _, _ = run(strat, data, chan, seed)
                g.append(st.get("file_goodput_bps", 0.0) if ok else 0.0)
            rows.append({"experiment": "envelope", "strategy": name, "snr_db": snr, "t": "", "bytes": "",
                         "mcs": "", "goodput_bps": round(float(np.mean(g)), 2)})
            print(f"envelope snr {snr:3}  {name:22}: {np.mean(g):6.1f} bit/s", flush=True)

    hi, lo, period = 25.0, 0.0, 45.0

    def walk(t):                                   # a noisy fan next to the receiver: off, on, off, ...
        return lo if int(t // period) % 2 == 1 else hi

    big = np.random.default_rng(1).integers(0, 256, 6000, dtype=np.uint8).tobytes()
    chan = Channel(curves, walk)
    rows.append({"experiment": "walk_size", "strategy": "", "snr_db": "", "t": "", "bytes": len(big), "mcs": "",
                 "goodput_bps": ""})
    for t in range(0, 600, 5):
        rows.append({"experiment": "walk_snr", "strategy": "", "snr_db": walk(t), "t": t, "bytes": "", "mcs": "",
                     "goodput_bps": ""})
    for name, strat in STRATEGIES:
        ok, st, got, speeds = run(strat, big, chan, 5)
        total = 0
        for t, nbytes in sorted(got):
            total += nbytes
            rows.append({"experiment": "walk", "strategy": name, "snr_db": round(walk(t), 2), "t": round(t, 2),
                         "bytes": min(total, len(big)), "mcs": "", "goodput_bps": ""})
        for t, m in speeds:
            rows.append({"experiment": "walk_mcs", "strategy": name, "snr_db": "", "t": round(t, 2), "bytes": "",
                         "mcs": m, "goodput_bps": ""})
        end = max([t for t, _ in got] + [0.0])
        rows.append({"experiment": "walk_result", "strategy": name, "snr_db": "", "t": round(end, 2),
                     "bytes": len(big) if ok else "", "mcs": "", "goodput_bps": int(ok)})
        print(f"walk {name:22}: {'done' if ok else 'FAILED'} at {st.get('elapsed_s', round(end, 1))} s", flush=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    main()
