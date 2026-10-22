"""
arq_sim.py - Stop-and-Wait vs Go-Back-N vs Selective Repeat, measured on the REAL ARQ code.

Runs acousticnet/arq.py + transfer.py over the VirtualNet (virtual time, real airtimes of
100-baud frames, 0.3 s audio latency each way), so thousands of acoustic transfers take seconds.

Experiment A "loss":  goodput vs frame-loss probability (same probability for data and ACKs)
Experiment B "chunk": goodput vs payload size per frame at a fixed bit-error rate - small
                      frames waste time on headers, big frames get hit by errors more often.

Output: results/sim/arq_sim.csv
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from acousticnet.virtual import run_virtual_transfer      # noqa: E402

CONFIGS = [("sw", 1), ("gbn", 4), ("sr", 4), ("gbn", 8), ("sr", 8)]


def main(out_csv=None, seeds=6):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_csv = out_csv or os.path.join(root, "results", "sim", "arq_sim.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    rows = []
    data = np.random.default_rng(0).integers(0, 256, 32 * 40, dtype=np.uint8).tobytes()   # 1280 bytes
    for mode, w in CONFIGS:
        for loss in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
            g, retx, eff, el = [], [], [], []
            for s in range(seeds):
                st, got = run_virtual_transfer(data, mode, w, loss=loss, seed=100 * s + int(loss * 100))
                assert got == data, "transfer corrupted!"
                g.append(st["file_goodput_bps"]); retx.append(st["retransmissions"])
                eff.append(st["efficiency"]); el.append(st["elapsed_s"])
            rows.append({"experiment": "loss", "mode": mode, "window": w, "loss": loss, "ber": 0,
                         "chunk": 32, "goodput_bps": round(float(np.mean(g)), 2),
                         "goodput_sd": round(float(np.std(g)), 2), "retransmissions": round(float(np.mean(retx)), 1),
                         "efficiency": round(float(np.mean(eff)), 3), "elapsed_s": round(float(np.mean(el)), 1),
                         "seeds": seeds})
            print(f"loss  {mode:3} W={w}  loss={loss:.2f}: {np.mean(g):6.1f} bit/s", flush=True)
    for ber in (1e-3, 3e-3):
        for chunk in (8, 16, 32, 64, 128):
            g = []
            for s in range(seeds):
                st, got = run_virtual_transfer(data, "sr", 4, ber=ber, chunk=chunk, seed=7 * s + chunk)
                assert got == data
                g.append(st["file_goodput_bps"])
            rows.append({"experiment": "chunk", "mode": "sr", "window": 4, "loss": 0, "ber": ber,
                         "chunk": chunk, "goodput_bps": round(float(np.mean(g)), 2),
                         "goodput_sd": round(float(np.std(g)), 2), "retransmissions": "", "efficiency": "",
                         "elapsed_s": "", "seeds": seeds})
            print(f"chunk ber={ber} chunk={chunk:3}: {np.mean(g):6.1f} bit/s", flush=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    main()
