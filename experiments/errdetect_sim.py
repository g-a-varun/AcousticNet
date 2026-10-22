"""
errdetect_sim.py - why CRC?  Parity vs Internet checksum vs CRC-8 vs CRC-16.

We take a 34-byte message (like our frame body), flip bits on purpose, and count how often
each detector FAILS to notice (the corrupted message still "checks out").

Experiment A "burst":  a burst of length L (first and last bit flipped, bits in between random).
                       Theory: a CRC with r check bits catches EVERY burst of length <= r.
Experiment B "random": k bits flipped at random positions anywhere.

Output: results/sim/errdetect_sim.csv
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from acousticnet.coding import crc8, crc16_ccitt, internet_checksum, parity_bit   # noqa: E402

MSG_BYTES = 34
DETECTORS = {
    "parity (1 bit)": parity_bit,
    "Internet checksum (16 bit)": internet_checksum,
    "CRC-8": crc8,
    "CRC-16": crc16_ccitt,
}


def _flip(msg: bytes, positions) -> bytes:
    b = bytearray(msg)
    for p in positions:
        b[p // 8] ^= 0x80 >> (p % 8)
    return bytes(b)


def run(kind, size, trials, rng):
    nbits = MSG_BYTES * 8
    undetected = {k: 0 for k in DETECTORS}
    for _ in range(trials):
        msg = rng.integers(0, 256, MSG_BYTES, dtype=np.uint8).tobytes()
        if kind == "burst":
            start = int(rng.integers(0, nbits - size + 1))
            inner = [start + i for i in range(1, size - 1) if rng.random() < 0.5]
            pos = [start] + inner + ([start + size - 1] if size > 1 else [])
        else:
            pos = rng.choice(nbits, size=size, replace=False).tolist()
        bad = _flip(msg, pos)
        for name, fn in DETECTORS.items():
            if fn(bad) == fn(msg):
                undetected[name] += 1
    return undetected


def main(out_csv=None, trials=20000):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_csv = out_csv or os.path.join(root, "results", "sim", "errdetect_sim.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    rng = np.random.default_rng(1)
    rows = []
    for kind, sizes in (("burst", (2, 3, 4, 8, 9, 12, 16, 17, 20, 24, 32)), ("random", (1, 2, 3, 4, 5, 6, 8))):
        for size in sizes:
            und = run(kind, size, trials, rng)
            for name, n in und.items():
                rows.append({"experiment": kind, "size": size, "detector": name, "trials": trials,
                             "undetected": n, "undetected_rate": n / trials})
            print(f"{kind:6} {size:2}: " + "  ".join(f"{k.split(' ')[0]}={v}" for k, v in und.items()), flush=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    main()
