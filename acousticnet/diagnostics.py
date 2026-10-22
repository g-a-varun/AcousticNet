"""
diagnostics.py - "is this laptop ready?"  Run before every demo:  python -m acousticnet selftest

Five checks, each one teaches you something about the hardware:
  1. silence        how loud is the room + mic when nobody talks (noise floor)
  2. latency        play a chirp, find it in the recording: round-trip audio delay of THIS laptop
                    (needed for ACK timeouts and for slot alignment in Slotted ALOHA)
  3. tone test      play 1400 Hz and 2600 Hz for 0.8 s each.  If the level DROPS while the tone
                    is still playing, the operating system is "helping" (noise suppression / auto
                    gain) and will eat our tones -> switch those features off
  4. frame loopback send a real frame from our own speaker to our own mic and decode it
  5. same with Hamming FEC

Results are saved to acousticnet_calibration.json and loaded automatically by every command.
"""
import json
import os
import time

import numpy as np

from .frame import Frame, T_MSG

CAL_FILE = "acousticnet_calibration.json"


def load_calibration(path: str = CAL_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_calibration(data: dict, path: str = CAL_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _db(x):
    return 10 * np.log10(x + 1e-15)


def measure_noise(backend, modem, seconds=1.0):
    backend.sleep(seconds)
    x = backend.rx.read_last(int(seconds * backend.fs))
    n = int(0.03 * backend.fs)
    levels = [modem.band_level(x[i:i + n]) for i in range(0, len(x) - n, n)]
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    return {"noise_floor": float(np.median(levels)) if levels else 1e-12,
            "rms_dbfs": round(20 * np.log10(rms + 1e-9), 1),
            "peak": round(float(np.max(np.abs(x))) if len(x) else 0.0, 3)}


def measure_latency(backend, modem, tries=3):
    """round-trip (speaker -> mic) latency in seconds, or [] if our own chirp is never heard"""
    fs = backend.fs
    pad = int(0.05 * fs)
    out = []
    for _ in range(tries):
        job = backend.transmit(np.concatenate([np.zeros(pad), modem.cfg.amp * modem.chirp]))
        job.wait(10)
        backend.sleep(0.8)
        if job.start is None:
            continue
        rec = backend.rx.read(job.start, backend.rx.end)
        ncc = modem.chirp_ncc(modem.bandpass(rec))
        if len(ncc) == 0 or ncc.max() < modem.cfg.detect_threshold:
            continue
        k = int(np.argmax(ncc))
        out.append((k - pad) / fs)
        backend.sleep(0.2)
    return out


def tone_test(backend, modem, freq, seconds=0.8, noise_floor=1e-12):
    fs = backend.fs
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = modem.cfg.amp * np.sin(2 * np.pi * freq * t)
    r = int(0.01 * fs)
    x[:r] *= np.linspace(0, 1, r)
    x[-r:] *= np.linspace(1, 0, r)
    job = backend.transmit(x)
    job.wait(10)
    backend.sleep(0.6)
    rec = backend.rx.read(job.start, backend.rx.end)
    # find where the tone is in the recording, then measure its level in 50 ms slices
    w = int(0.05 * fs)
    ref = np.exp(-2j * np.pi * freq * np.arange(w) / fs)
    energies = np.array([np.abs(np.dot(rec[i:i + w], ref)) ** 2 / w for i in range(0, len(rec) - w, w)])
    if len(energies) == 0 or energies.max() <= 0:
        return {"freq": freq, "heard": False}
    on = energies > energies.max() * 0.1
    idx = np.flatnonzero(on)
    seg = energies[idx[0]: idx[-1] + 1]
    if len(seg) < 6:
        return {"freq": freq, "heard": False}
    first, last = np.mean(seg[1:4]), np.mean(seg[-4:-1])
    level = float(np.mean(seg))
    clipped = float(np.max(np.abs(rec))) > 0.98
    return {"freq": freq, "heard": True,
            "level_dbfs": round(float(_db(2 * level / w)), 1),
            "snr_db": round(float(_db(level / w) - _db(noise_floor)), 1),
            "drop_db": round(float(_db(first) - _db(last)), 1),
            "clipped": clipped}


def frame_loopback(backend, modem, fec=False, mcs=None):
    payload = b"AcousticNet self-test 0123456789"
    mcs = (0 if fec else 1) if mcs is None else mcs
    job = backend.transmit(modem.frame_waveform(Frame(T_MSG, 0, 0, 7, payload), mcs))
    job.wait(30)
    backend.sleep(0.8)
    rec = backend.rx.read(job.start, backend.rx.end)
    f = modem.decode_buffer(rec)
    if f is None:
        return {"decoded": False}
    return {"decoded": True, "crc_ok": f.crc_ok and f.payload == payload, "snr_db": round(f.snr_db, 1),
            "quality": round(f.quality, 3), "fec_fixed": f.fec_fixed}


def run_selftest(backend, modem, out=print, cal_path: str = CAL_FILE, save: bool = True) -> dict:
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "fs": backend.fs, "baud": modem.cfg.baud}
    warnings = []
    out("AcousticNet self-test (keep the room quiet, volume at about 70 %)")

    out("  1/5  Listening to silence for 1 s ...")
    backend.sleep(0.3)
    nz = measure_noise(backend, modem, 1.0)
    res.update(nz)
    out(f"       mic level {nz['rms_dbfs']} dBFS")
    if nz["rms_dbfs"] < -95:
        warnings.append("The microphone records pure silence. It may be muted or blocked by privacy settings.")

    out("  2/5  Measuring this laptop's audio round-trip latency ...")
    lats = measure_latency(backend, modem)
    if lats:
        rt = float(np.median(lats))
        res["rt_latency_s"] = round(rt, 4)
        out("       " + ", ".join(f"{1000 * v:.0f} ms" for v in lats) + f"  ->  {1000 * rt:.0f} ms")
    else:
        warnings.append("Could not hear our own chirp. Volume too low, or the laptop cancels its own "
                        "sound (echo cancellation). Two-laptop transfers may still work; latency "
                        "defaults to 350 ms.")
        out("       own chirp not heard")

    out("  3/5  Tone test (is the operating system filtering our tones?) ...")
    tones = [tone_test(backend, modem, f, noise_floor=nz["noise_floor"]) for f in (modem.cfg.f0, modem.cfg.f1)]
    res["tones"] = tones
    for t in tones:
        if not t["heard"]:
            out(f"       {t['freq']:.0f} Hz: not heard")
            warnings.append(f"{t['freq']:.0f} Hz tone not heard at all.")
            continue
        out(f"       {t['freq']:.0f} Hz: level {t['level_dbfs']} dBFS, SNR {t['snr_db']} dB, "
            f"level change while playing {t['drop_db']:+.1f} dB")
        if t["drop_db"] > 6:
            warnings.append(f"The {t['freq']:.0f} Hz tone faded while it was still playing ({t['drop_db']} dB). "
                            "Turn OFF noise suppression / audio enhancements / automatic gain (see README).")
        if t["clipped"]:
            warnings.append("The microphone is clipping. Lower the volume (--volume 0.5).")
        if t["snr_db"] < 15:
            warnings.append(f"Low SNR at {t['freq']:.0f} Hz. Raise the volume or reduce room noise.")

    out("  4/5  Frame loopback without FEC ...")
    fl = frame_loopback(backend, modem, fec=False)
    res["loopback_plain"] = fl
    out("       " + (f"decoded, CRC {'OK' if fl['crc_ok'] else 'FAILED'}, SNR {fl['snr_db']} dB, "
                     f"tone clarity {fl['quality'] * 100:.0f} %" if fl["decoded"] else "no frame decoded"))
    out("  5/5  Frame loopback with Hamming FEC ...")
    ff = frame_loopback(backend, modem, fec=True)
    res["loopback_fec"] = ff
    out("       " + (f"decoded, CRC {'OK' if ff['crc_ok'] else 'FAILED'}, bits fixed by FEC: {ff['fec_fixed']}"
                     if ff["decoded"] else "no frame decoded"))

    ok_plain = fl.get("decoded") and fl.get("crc_ok")
    ok_fec = ff.get("decoded") and ff.get("crc_ok")
    if not ok_plain and ok_fec:
        warnings.append("Only FEC frames survived: use --fec for this laptop pair, or lower --baud.")
    if not ok_plain and not ok_fec:
        warnings.append("Frames did not survive the loopback. Check volume, enhancements, and try --baud 50.")
    res["warnings"] = warnings
    res["ready"] = bool(ok_plain or ok_fec)
    out("")
    for w in warnings:
        out("  WARNING: " + w)
    out("  Verdict: " + ("READY" if res["ready"] else "NOT READY (see warnings)"))
    if save:
        cal = load_calibration(cal_path)
        cal.update({k: res[k] for k in ("time", "fs", "baud", "rms_dbfs") if k in res})
        if "rt_latency_s" in res:
            cal["rt_latency_s"] = res["rt_latency_s"]
        save_calibration(cal, cal_path)
        out(f"  Calibration saved to {os.path.abspath(cal_path)}")
    return res
