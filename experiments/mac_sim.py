"""
mac_sim.py - fast discrete-event simulation of the MAC experiment.

It copies the exact decision rules of acousticnet/mac.py (MacStation.run) but replaces sound
with simple time intervals, so one hour of channel time takes ~0.1 s.  It is used to:
  1. validate itself against textbook theory with many stations (N = 50 -> classic curves),
  2. predict what 3 real laptops should measure (N = 3, real frame time, real latencies),
  3. show how CSMA collapses as audio latency grows (the "a" sweep).

Timing model per laptop i (see README "Why latency matters"):
    in_i  = microphone latency, out_i = speaker latency, rt_i = in_i + out_i
    ALOHA / CSMA: a decision happens; sound reaches the air out_i later.
    CSMA senses audio that is in_i old.  So the "blind window" is ~ in_i + out_i + 30 ms.
    Slotted: start is scheduled rt early (timing advance); residual error = calibration error.

Run:  python experiments/mac_sim.py           -> results/sim/mac_sim.csv
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from acousticnet.phy import Modem          # noqa: E402

FRAME_S = Modem().frame_duration(4)       # 4-byte MAC frame at 100 baud (~1.29 s)


def _collisions(emis):
    """emis: list of (start, end, station) -> boolean array 'success' in the same order"""
    if not emis:
        return np.zeros(0, bool)
    arr = np.array([(s, e) for s, e, _ in emis])
    order = np.argsort(arr[:, 0])
    st, en = arr[order, 0], arr[order, 1]
    ok = np.ones(len(st), bool)
    run_max = -np.inf
    for k in range(len(st)):
        if st[k] < run_max:
            ok[k] = False
        if k + 1 < len(st) and st[k + 1] < en[k]:
            ok[k] = False
        run_max = max(run_max, en[k])
    out = np.empty(len(st), bool)
    out[order] = ok
    return out


def simulate(protocol, p, N=3, T=FRAME_S, guard=0.25, mini=8, duration=3600.0, out_lat=None,
             in_lat=None, calib_sd=0.005, cs_window=0.03, seed=0):
    rng = np.random.default_rng(seed)
    out_lat = np.full(N, 0.08) if out_lat is None else np.broadcast_to(np.asarray(out_lat, float), (N,))
    in_lat = np.full(N, 0.08) if in_lat is None else np.broadcast_to(np.asarray(in_lat, float), (N,))
    rt = out_lat + in_lat
    emis, senses, defers = [], 0, 0

    if protocol == "slotted":
        slot = T + guard
        nslots = int(duration / slot)
        err = rng.normal(0, calib_sd, N)
        for k in range(nslots):
            for i in range(N):
                if rng.random() < p:
                    s = k * slot + err[i]
                    emis.append((s, s + T, i))
    else:
        dt = T / mini
        steps = int(duration / dt)
        busy_until = np.zeros(N)
        pending = np.zeros(N, bool)
        retry_at = np.zeros(N)
        # decision events: station i decides at k*dt + in_i (its clock lags the air by in_i)
        times = (np.arange(steps)[:, None] * dt + in_lat[None, :]).ravel()
        who = np.tile(np.arange(N), steps)
        order = np.argsort(times, kind="stable")
        recent = []                                    # recent emissions, for carrier sense
        for idx in order:
            t, i = times[idx], who[idx]
            if protocol == "aloha":
                if t >= busy_until[i] and rng.random() < p / mini:
                    s = t + out_lat[i]
                    emis.append((s, s + T, i))
                    busy_until[i] = t + T + rt[i]
                continue
            # csma, non-persistent
            if not pending[i] and t >= busy_until[i] and rng.random() < p / mini:
                pending[i] = True
                retry_at[i] = t
            if pending[i] and t >= busy_until[i] and t >= retry_at[i]:
                senses += 1
                w1 = t - in_lat[i]                     # the newest audio this laptop has
                w0 = w1 - cs_window
                busy = any(s < w1 and w0 < e and j != i for s, e, j in recent)
                if busy:
                    defers += 1
                    retry_at[i] = t + rng.uniform(0.5, 1.5) * T
                else:
                    s = t + out_lat[i]
                    emis.append((s, s + T, i))
                    recent.append((s, s + T, i))
                    if len(recent) > 4 * N:
                        recent = recent[-4 * N:]
                    pending[i] = False
                    busy_until[i] = t + T + rt[i]

    emis = [e for e in emis if 0 <= e[0] < duration]
    ok = _collisions(emis)
    frames = duration / T
    row = {"protocol": protocol, "p": p, "N": N, "frame_s": round(T, 4), "duration_s": duration,
           "transmissions": len(emis), "successes": int(ok.sum()), "senses": senses,
           "S": ok.sum() / frames, "G_tx": len(emis) / frames,
           "G_attempts": (senses if protocol == "csma" else len(emis)) / frames,
           "a": float(np.mean(rt) + cs_window) / T}
    if protocol == "slotted":
        nslots = int(duration / (T + guard))
        row["S_slot"] = ok.sum() / nslots
        row["G_slot"] = len(emis) / nslots
    else:
        row["S_slot"], row["G_slot"] = row["S"], row["G_tx"]
    return row


def default_sweep():
    return [0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0]


def main(out_csv=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_csv = out_csv or os.path.join(root, "results", "sim", "mac_sim.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    rows = []
    # (1) N = 3 like the lab: realistic latencies (50-150 ms per direction)
    lat_rng = np.random.default_rng(42)
    out3, in3 = lat_rng.uniform(0.05, 0.15, 3), lat_rng.uniform(0.05, 0.15, 3)
    for proto in ("aloha", "slotted", "csma"):
        ps = default_sweep() if proto != "slotted" else [0.05, 0.1, 0.2, 0.3, 0.33, 0.4, 0.5, 0.6, 0.8, 1.0]
        for p in ps:
            r = simulate(proto, p, N=3, out_lat=out3, in_lat=in3, duration=7200, seed=int(p * 1000))
            r["scenario"] = "lab_N3"
            rows.append(r)
    # (2) N = 50, zero latency, no guard -> must reproduce the classic infinite-N curves
    for proto in ("aloha", "slotted", "csma"):
        for G in [0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]:
            p = G / 50
            r = simulate(proto, p, N=50, out_lat=0.0, in_lat=0.0, guard=0.0, mini=10,
                         cs_window=0.01 * FRAME_S, calib_sd=0.0, duration=2500, seed=int(G * 100))
            r["scenario"] = "validate_N50"
            rows.append(r)
    # (3) CSMA vs audio latency: sweep round-trip latency, for 3 laptops and for 10
    for n_st, ps in ((3, (0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.3, 1.8)), (10, (0.05, 0.1, 0.2, 0.3, 0.45, 0.6))):
        for rt_ms in (0, 50, 100, 200, 300, 400, 600):
            for p in ps:
                r = simulate("csma", p, N=n_st, out_lat=rt_ms / 2000, in_lat=rt_ms / 2000, duration=3000,
                             seed=rt_ms + int(p * 100) + n_st)
                r["scenario"] = f"latency_N{n_st}_{rt_ms}ms"
                r["rt_ms"] = rt_ms
                rows.append(r)
    keys = sorted({k for r in rows for k in r})
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    main()
