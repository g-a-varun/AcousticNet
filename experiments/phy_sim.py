"""
phy_sim.py - how robust is the physical layer?  (waveform-level simulation)

Every trial builds a real frame with acousticnet.phy, passes it through a model of a room
(echo + noise + latency, acousticnet/channel.py) and decodes it with the real receiver code.

Experiment A  "noise":  frame success rate vs in-band SNR, for 50/100/200 baud, FEC off/on,
                        in a mildly echoey room (RT60 0.3 s, direct-to-reverb ratio 10 dB).
Experiment B  "echo":   frame success rate vs how echoey the room is (DRR), at a comfortable
                        SNR of 20 dB.  Shows why slower symbols survive echo better.
Experiment C  "mcs":    frame success of every speed (MCS 0-4) vs SNR, plus the header SNR the
                        receiver measures; suggest_thresholds() turns that into config.MCS_MIN_SNR.
                        (`python experiments/phy_sim.py --thresholds` re-prints them from the CSV.)

Output: results/sim/phy_sim.csv
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from acousticnet.channel import room_channel              # noqa: E402
from acousticnet.config import PhyConfig                  # noqa: E402
from acousticnet.frame import Frame, T_DATA               # noqa: E402
from acousticnet.phy import Modem                         # noqa: E402

PAYLOAD = 32


def trial_batch(baud, fec, snr_db, drr_db, rt60, trials, seed, mcs=None):
    m = Modem(PhyConfig(baud=baud))
    mcs = (0 if fec else 1) if mcs is None else mcs
    ok, errs, bits = 0, 0, 0
    hdr_snr = []
    for t in range(trials):
        rng = np.random.default_rng(seed * 1000 + t)
        payload = rng.integers(0, 256, PAYLOAD, dtype=np.uint8).tobytes()
        tx = np.concatenate([np.zeros(3000), m.frame_waveform(Frame(T_DATA, 1, 2, t % 256, payload), mcs),
                             np.zeros(6000)])
        rx = room_channel(tx, snr_db=snr_db, latency_s=0.1, rt60=rt60, drr_db=drr_db, rng=rng)
        f = m.decode_buffer(rx)
        bits += PAYLOAD * 8
        if f is None or len(f.payload) != PAYLOAD:
            errs += PAYLOAD * 4                     # frame lost: count as 50 % bit errors (coin flips)
            continue
        hdr_snr.append(f.snr_db)
        e = int(np.sum(np.unpackbits(np.frombuffer(f.payload, np.uint8)) !=
                       np.unpackbits(np.frombuffer(payload, np.uint8))))
        errs += e
        ok += int(f.crc_ok and e == 0)
    dur = m.frame_duration(PAYLOAD, mcs)
    return {"baud": baud, "fec": fec, "mcs": mcs, "snr_db": snr_db, "drr_db": drr_db, "rt60_s": rt60,
            "trials": trials, "frames_ok": ok, "fsr": ok / trials, "ber": errs / bits, "frame_s": round(dur, 3),
            "goodput_bps": round(ok / trials * PAYLOAD * 8 / dur, 2),
            "header_snr_db": round(float(np.median(hdr_snr)), 2) if hdr_snr else ""}


MCS_SNRS = (-6, -3, 0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30)


def mcs_experiment(trials=40):
    """Experiment C "mcs": every speed vs in-band SNR (mild echo).  Also records the header SNR the
    receiver MEASURES - the number the rate controller sees - so thresholds can be set from it."""
    rows = []
    for mcs in (0, 1, 2, 3, 4):
        for snr in MCS_SNRS:
            r = trial_batch(100, mcs == 0, snr, 10.0, 0.3, trials, seed=7000 + mcs * 100 + snr + 50, mcs=mcs)
            r["experiment"] = "mcs"
            rows.append(r)
            print(f"mcs {mcs} snr {snr:4}: FSR {r['fsr']:.2f}  header SNR {r['header_snr_db']}", flush=True)
    return rows


def suggest_thresholds(rows, target=0.75):
    """The SNR thresholds in config.py (MCS_MIN_SNR) come from here: for every speed, the in-band
    SNR where frame success first reaches `target`, translated into the header SNR the receiver
    would MEASURE at that point (pooled over all speeds - the header is always sent the same way).
    Thresholds are then made non-decreasing: a faster speed never gets a lower bar."""
    rows = [r for r in rows if r["experiment"] == "mcs"]
    snrs = sorted({float(r["snr_db"]) for r in rows})
    hdr = []
    for s_ in snrs:
        v = [float(r["header_snr_db"]) for r in rows if float(r["snr_db"]) == s_ and r["header_snr_db"] != ""]
        hdr.append(np.median(v) if v else np.nan)
    ok = ~np.isnan(hdr)
    snr_ok, hdr_ok = np.array(snrs)[ok], np.maximum.accumulate(np.array(hdr)[ok])
    out = []
    for m in sorted({int(r["mcs"]) for r in rows}):
        rr = sorted([r for r in rows if int(r["mcs"]) == m], key=lambda r: float(r["snr_db"]))
        x = np.array([float(r["snr_db"]) for r in rr])
        y = np.maximum.accumulate(np.array([float(r["fsr"]) for r in rr]))
        k = int(np.argmax(y >= target))
        cross = x[0] if k == 0 else x[k - 1] + (target - y[k - 1]) * (x[k] - x[k - 1]) / (y[k] - y[k - 1])
        out.append(round(float(np.interp(cross, snr_ok, hdr_ok)) * 2) / 2)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1])
    return tuple(out)


def main(out_csv=None, trials=40):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_csv = out_csv or os.path.join(root, "results", "sim", "phy_sim.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    rows = []
    for baud in (50, 100, 200):
        for fec in (False, True):
            for snr in (-12, -9, -6, -3, 0, 3, 6, 10, 15):
                r = trial_batch(baud, fec, snr, 10.0, 0.3, trials, seed=baud * 100 + (snr + 50) * 7 + int(fec))
                r["experiment"] = "noise"
                rows.append(r)
                print(f"noise  baud {baud:3} fec {int(fec)} snr {snr:4}: FSR {r['fsr']:.2f}", flush=True)
    for baud in (50, 100, 200):
        for fec in (False, True):
            for drr in (12, 8, 6, 4, 2, 0, -3):
                r = trial_batch(baud, fec, 20.0, drr, 0.6, max(24, trials * 3 // 4), seed=90000 + baud * 100 + (drr + 50) * 5 + int(fec))
                r["experiment"] = "echo"
                rows.append(r)
                print(f"echo   baud {baud:3} fec {int(fec)} drr {drr:4}: FSR {r['fsr']:.2f}", flush=True)
    rows += mcs_experiment(trials)
    print("suggested MCS_MIN_SNR for config.py:", suggest_thresholds(rows))
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["experiment"] + [k for k in rows[0] if k != "experiment"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    if "--thresholds" in sys.argv:                 # just re-read the CSV and print the thresholds
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "sim", "phy_sim.csv")
        print(suggest_thresholds(list(csv.DictReader(open(path, encoding="utf-8")))))
    else:
        main()
