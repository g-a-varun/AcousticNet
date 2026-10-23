"""
bertest.py - Bit Error Rate (BER) test for the "BER vs distance" experiment.

The sender transmits N frames whose payload is pseudo-random but REPRODUCIBLE: frame k is
always generated from seed k.  The receiver regenerates the same bytes and compares bit by bit,
even for frames whose CRC failed.  That gives the true BER, not just "frame ok / not ok".

    laptop A:  python -m acousticnet ber-tx --frames 30
    laptop B:  python -m acousticnet ber-rx --frames 30 --distance 1.0
(repeat at 0.5, 1, 2, 3 m; every run appends one row to results/measured/ber_measured.csv)
"""
import csv
import os
import threading

import numpy as np

from .config import BROADCAST
from .frame import Frame, T_BER


def ber_payload(seq: int, length: int) -> bytes:
    return np.random.default_rng(1000 + seq).integers(0, 256, length, dtype=np.uint8).tobytes()


def ber_transmit(node, frames: int = 30, length: int = 32, gap_s: float = 0.6):
    for k in range(frames):
        node.send(Frame(T_BER, node.addr, BROADCAST, k % 256, ber_payload(k % 256, length)))
        node.sleep(gap_s)


def ber_receive(node, frames: int = 30, length: int = 32, distance: str = "", csv_path: str = None,
                idle_timeout: float = 15.0):
    got = {}
    lock = threading.Lock()

    def on_raw(f):
        if f.type == T_BER and len(f.payload) == length:
            with lock:
                if f.seq not in got or (f.crc_ok and not got[f.seq]["crc_ok"]):
                    want = np.unpackbits(np.frombuffer(ber_payload(f.seq, length), np.uint8))
                    have = np.unpackbits(np.frombuffer(f.payload, np.uint8))
                    got[f.seq] = {"crc_ok": f.crc_ok, "errors": int(np.sum(want != have)),
                                  "snr": f.snr_db, "fec_fixed": f.fec_fixed}
            node.bus.emit("ber_frame", t=round(node.now(), 2), seq=f.seq, crc_ok=f.crc_ok,
                          bit_errors=got[f.seq]["errors"], received=len(got), of=frames)

    node.raw_listeners.append(on_raw)
    # never give up between two frames: at --baud 25 or with --fec one frame alone lasts 14-24 s
    idle_timeout = max(idle_timeout, 2 * (node.frame_time(length) + 1.0))
    last_n, last_change = 0, node.now()
    try:
        while True:
            node.sleep(0.5)
            with lock:
                n = len(got)
            if n != last_n:
                last_n, last_change = n, node.now()
            if n >= frames or (n > 0 and node.now() - last_change > idle_timeout):
                break
            if n == 0 and node.now() - last_change > 10 * idle_timeout:
                break
    finally:
        node.raw_listeners.remove(on_raw)
    detected = len(got)
    crc_ok = sum(v["crc_ok"] for v in got.values())
    errors = sum(v["errors"] for v in got.values())
    bits = detected * length * 8
    row = {"distance_m": distance, "baud": node.modem.cfg.baud, "fec": node.fec, "frames_sent": frames,
           "frames_detected": detected, "frames_crc_ok": crc_ok,
           "frame_success_rate": round(crc_ok / frames, 4) if frames else 0,
           "bit_errors": errors, "bits_compared": bits,
           "ber": round(errors / bits, 6) if bits else None,
           "mean_snr_db": round(float(np.mean([v["snr"] for v in got.values()])), 1) if got else None,
           "fec_bits_fixed": sum(v["fec_fixed"] for v in got.values())}
    if csv_path:
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        new = not os.path.exists(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row.keys()))
            if new:
                w.writeheader()
            w.writerow(row)
    node.bus.emit("ber_result", t=round(node.now(), 2), **row)
    return row
