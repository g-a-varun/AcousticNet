"""
phy.py - the PHYSICAL layer: turning bits into sound and sound back into bits.

  Transmit:  preroll + chirp + guard
             + sync & header as 2-FSK at the BASE rate (1400 Hz = 0, 2600 Hz = 1)
             + payload as M-FSK at the speed (MCS) written in the header
  Receive:   find chirp (matched filter) -> lock symbol timing on the sync word ->
             read the header -> now we know the payload's speed and length -> read it

Analogy: the chirp is a whistle that rises in pitch, "wheeeep!".  It is easy to spot even
in a noisy room, and the exact moment it starts tells the receiver where every following
symbol begins - like a conductor's baton tap before the orchestra starts.

M-FSK analogy: 2-FSK is a piano with 2 keys (each note = 1 bit).  16-FSK is a piano with
16 keys: each note now says one of 16 things = 4 bits, in the same time.
"""
import numpy as np

from .config import PhyConfig, DEFAULT_PHY, build_mcs_table
from .frame import (Frame, SYNC_PLAIN, SYNC_FEC, SYNC_BITS, encode_frame_parts, header_bit_count,
                    body_symbol_count, parse_header, parse_body)


def _popcount16(v: int) -> int:
    return bin(v & 0xFFFF).count("1")


def bits_to_symbols(bits, k: int) -> np.ndarray:
    """group k bits (MSB first) into one symbol value 0..2^k-1"""
    b = np.asarray(bits, dtype=np.int64).reshape(-1, k)
    return (b * (1 << np.arange(k - 1, -1, -1))).sum(axis=1)


def symbols_to_bits(sym, k: int) -> np.ndarray:
    sym = np.asarray(sym, dtype=np.int64)
    return ((sym[:, None] >> np.arange(k - 1, -1, -1)) & 1).astype(np.uint8).reshape(-1)


class Modem:
    def __init__(self, cfg: PhyConfig = DEFAULT_PHY):
        self.cfg = cfg
        self.fs = cfg.fs
        self.sps = cfg.sps                         # base-rate samples per symbol
        self.mcs_table = build_mcs_table(cfg)
        # speeds this sample rate can do exactly (e.g. 200 baud needs fs divisible by 200)
        self.available_mcs = [m.index for m in self.mcs_table if cfg.fs % m.baud == 0]
        self._refs = {}
        for m in self.mcs_table:
            if m.index in self.available_mcs:
                sps = cfg.fs // m.baud
                n = np.arange(sps)
                # one reference vector per tone: dot(symbol, ref) = how much of that tone
                self._refs[m.index] = np.exp(-2j * np.pi * np.outer(n, m.tones) / self.fs)
        self.base_mcs = self.mcs_table[1]
        self.chirp = self._make_chirp()
        self.chirp_len = len(self.chirp)
        self._chirp_norm = float(np.linalg.norm(self.chirp))
        self.guard = cfg.guard_syms * self.sps
        self.preroll = int(round(cfg.preroll_s * self.fs))
        self.chirp_to_data = self.chirp_len + self.guard     # chirp start -> first sync symbol

    def mcs(self, index):
        if index is None:
            return self.base_mcs
        if index not in self.available_mcs:
            raise ValueError(f"MCS {index} not available at fs={self.fs}")
        return self.mcs_table[index]

    def mcs_sps(self, m) -> int:
        return self.fs // m.baud

    # ------------------------------------------------------------------ TX --
    def _make_chirp(self) -> np.ndarray:
        c = self.cfg
        n = int(round(c.chirp_s * self.fs))
        t = np.arange(n) / self.fs
        k = (c.chirp_hi - c.chirp_lo) / c.chirp_s
        x = np.sin(2 * np.pi * (c.chirp_lo * t + 0.5 * k * t * t))
        taper = max(1, int(0.05 * n))                      # 10 % Tukey taper: soft start/end
        w = np.ones(n)
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(taper) / taper)
        w[:taper], w[-taper:] = ramp, ramp[::-1]
        return x * w

    def _freq_track(self, symbols, m) -> np.ndarray:
        return np.repeat(np.asarray(m.tones)[np.asarray(symbols, dtype=np.int64)], self.mcs_sps(m))

    def modulate_bits(self, bits, m=None) -> np.ndarray:
        """continuous-phase M-FSK (default: base-rate 2-FSK)"""
        m = m or self.base_mcs
        freq = self._freq_track(bits_to_symbols(bits, m.bits_per_symbol), m)
        return np.sin(2 * np.pi * np.cumsum(freq) / self.fs)

    def _ramp(self, x: np.ndarray, r: int) -> np.ndarray:
        if r > 1 and len(x) > 2 * r:
            w = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
            x = x.copy()
            x[:r] *= w
            x[-r:] *= w[::-1]
        return x

    def waveform_from_parts(self, base_bits, body_bits, m) -> np.ndarray:
        """preroll + chirp + guard + base part (2-FSK) + body (M-FSK), phase-continuous"""
        t = np.arange(self.preroll) / self.fs
        r = int(min(self.cfg.ramp_s * self.fs, 0.2 * self.mcs_sps(m), 0.2 * self.sps))
        pre = self._ramp(0.3 * np.sin(2 * np.pi * self.cfg.f0 * t), r)
        freq = np.concatenate([self._freq_track(bits_to_symbols(base_bits, 1), self.base_mcs),
                               self._freq_track(bits_to_symbols(body_bits, m.bits_per_symbol), m)])
        data = self._ramp(np.sin(2 * np.pi * np.cumsum(freq) / self.fs), r)
        x = np.concatenate([pre, self.chirp, np.zeros(self.guard), data])
        return (self.cfg.amp * x).astype(np.float32)

    def frame_waveform(self, frame: Frame, mcs=None) -> np.ndarray:
        m = self.mcs(mcs if mcs is not None else frame.mcs)
        base, body = encode_frame_parts(frame, m)
        return self.waveform_from_parts(base, body, m)

    def frame_samples(self, payload_len: int, mcs=None) -> int:
        m = self.mcs(mcs)
        return (self.preroll + self.chirp_to_data + (SYNC_BITS + header_bit_count(m.fec)) * self.sps
                + body_symbol_count(payload_len, m) * self.mcs_sps(m))

    def frame_duration(self, payload_len: int, mcs=None) -> float:
        """seconds on air for a frame with this payload size at this speed"""
        return self.frame_samples(payload_len, mcs) / self.fs

    # ------------------------------------------------------------- helpers --
    def bandpass(self, x: np.ndarray) -> np.ndarray:
        """FFT band-pass (band_lo..band_hi) with soft 150 Hz edges; removes hum and hiss."""
        x = np.asarray(x, dtype=np.float64)
        n = len(x)
        if n < 8:
            return x
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(n, 1 / self.fs)
        lo, hi, e = self.cfg.band_lo, self.cfg.band_hi, 150.0
        msk = np.clip((f - (lo - e)) / e, 0, 1) * np.clip(((hi + e) - f) / e, 0, 1)
        return np.fft.irfft(X * msk, n)

    def chirp_ncc(self, xf: np.ndarray) -> np.ndarray:
        """Normalised cross-correlation of (band-passed) xf with the chirp.
        out[k] ~ 1.0 when a clean chirp starts at xf[k], ~0 for noise."""
        L = self.chirp_len
        if len(xf) < L:
            return np.zeros(0)
        nfft = 1 << (len(xf) + L - 2).bit_length()
        full = np.fft.irfft(np.fft.rfft(xf, nfft) * np.fft.rfft(self.chirp[::-1], nfft), nfft)
        corr = full[L - 1: len(xf)]          # 'valid' part: index k = chirp starting at k
        e = np.concatenate([[0.0], np.cumsum(xf * xf)])
        win = e[L:] - e[:-L]
        return corr / (self._chirp_norm * np.sqrt(np.maximum(win, 0)) + 1e-9)

    def tone_energies(self, x: np.ndarray, start: int, nsym: int, m=None):
        """-> array (nsym, M): energy of every tone of MCS m in every symbol slot"""
        m = m or self.base_mcs
        sps = self.mcs_sps(m)
        seg = np.asarray(x[start: start + nsym * sps], dtype=np.float64).reshape(nsym, sps)
        return np.abs(seg @ self._refs[m.index]) ** 2

    def demod_symbols(self, x, start, nsym, m=None):
        """-> (symbols, quality 0..1, snr_db).  Symbol = loudest tone.  quality = how much the
        loudest tone beats the runner-up; snr = loudest vs the median of the other tones."""
        e = self.tone_energies(x, start, nsym, m)
        sym = np.argmax(e, axis=1)
        srt = np.sort(e, axis=1)
        top, second = srt[:, -1], srt[:, -2]
        quality = float(np.mean((top - second) / (top + second + 1e-20)))
        others = np.median(srt[:, :-1], axis=1) if e.shape[1] > 2 else second
        snr_db = float(np.clip(10 * np.log10(np.median(top / (others + 1e-20)) + 1e-12), 0, 60))
        return sym, quality, snr_db

    def demod(self, x, start, nbits):
        """base-rate 2-FSK bits -> (bits, quality, snr_db)"""
        sym, q, snr = self.demod_symbols(x, start, nbits, self.base_mcs)
        return sym.astype(np.uint8), q, snr

    def band_level(self, x: np.ndarray) -> float:
        """mean power inside the signal band (used for carrier sense / noise floor)"""
        x = np.asarray(x, dtype=np.float64)
        if len(x) < 64:
            return 0.0
        X = np.fft.rfft(x * np.hanning(len(x)))
        f = np.fft.rfftfreq(len(x), 1 / self.fs)
        band = (f >= self.cfg.band_lo) & (f <= self.cfg.band_hi)
        return float(np.sum(np.abs(X[band]) ** 2) / len(x))

    # ----------------------------------------------------------------- RX --
    def decode_at(self, x: np.ndarray, x0: int, chirp_abs: int, avail_abs: int):
        """Try to decode the frame whose chirp starts at absolute sample chirp_abs.

        x holds absolute samples [x0, x0 + len(x)).  avail_abs = first sample not yet recorded.
        Returns one of:
            ("more", needed_abs)   wait until that many samples exist, then call again
            ("fail", reason)       not a decodable frame (noise, collision, bad header)
            ("ok", Frame)          decoded (check frame.crc_ok!)
        """
        sps = self.sps
        nominal = chirp_abs + self.chirp_to_data
        half = sps // 2
        need = nominal + half + (SYNC_BITS + header_bit_count(True)) * sps
        if need > avail_abs:
            return "more", need

        # 1) symbol-timing search: which offset decodes a sync word most cleanly?
        best = None
        step = max(1, sps // 16)
        for off in range(-half, half + 1, step):
            s = nominal + off - x0
            if s < 0:
                continue
            bits, q, _ = self.demod(x, s, SYNC_BITS)
            v = 0
            for b in bits:
                v = (v << 1) | int(b)
            for sync, fec in ((SYNC_PLAIN, False), (SYNC_FEC, True)):
                key = (_popcount16(v ^ sync), -q)
                if best is None or key < best[0]:
                    best = (key, s, fec)
        if best is None or best[0][0] > self.cfg.max_sync_errors:
            return "fail", "no sync word"
        (sync_err, _), s, hfec = best

        # 2) header (always base rate)
        hb = header_bit_count(hfec)
        hbits, _, _ = self.demod(x, s + SYNC_BITS * sps, hb)
        hdr, fixed_h = parse_header(hbits, hfec)
        if hdr is None:
            return "fail", "header CRC-8 failed"
        if hdr["mcs"] not in self.available_mcs:
            return "fail", f"unknown speed MCS {hdr['mcs']}"
        m = self.mcs_table[hdr["mcs"]]

        # 3) payload at the announced speed, starting right where the header ended
        body_start = s + (SYNC_BITS + hb) * sps
        nsym = body_symbol_count(hdr["len"], m)
        end_abs = x0 + body_start + nsym * self.mcs_sps(m)
        if end_abs > avail_abs or end_abs > x0 + len(x):
            return "more", end_abs
        sym, q, _ = self.demod_symbols(x, body_start, nsym, m)
        payload, ok, fixed_b = parse_body(symbols_to_bits(sym, m.bits_per_symbol), hdr, m)
        _, _, snr = self.demod(x, s, SYNC_BITS + hb)          # channel quality from the header
        return "ok", Frame(type=hdr["type"], flags=hdr["flags"], src=hdr["src"], dst=hdr["dst"],
                           seq=hdr["seq"], payload=payload, mcs=m.index, crc_ok=ok, fec=m.fec,
                           fec_fixed=fixed_h + fixed_b, rx_start=chirp_abs, rx_end=end_abs,
                           snr_db=snr, quality=q, sync_errors=sync_err)

    def decode_buffer(self, rec: np.ndarray):
        """One-shot helper for a recording that contains (at most) one frame.
        Returns Frame or None.  Used by self-test and offline experiments."""
        xf = self.bandpass(rec)
        ncc = self.chirp_ncc(xf)
        if len(ncc) == 0 or ncc.max() < self.cfg.detect_threshold:
            return None
        c = int(np.argmax(ncc))
        status, res = self.decode_at(rec, 0, c, len(rec))
        return res if status == "ok" else None
