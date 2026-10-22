"""
arq.py - Automatic Repeat reQuest: making an unreliable link reliable.

Three classic protocols (Forouzan ch. 11 / Module 3 of the syllabus):

  Stop-and-Wait (sw)       send 1 frame, wait for its ACK, repeat.
  Go-Back-N (gbn)          send a window of W frames; receiver only accepts frames in
                           order; one loss -> the sender re-sends that frame AND all after it.
  Selective Repeat (sr)    send a window of W frames; receiver keeps out-of-order frames;
                           one loss -> only that frame is re-sent.

The acoustic twist: sound is HALF-DUPLEX (our speaker drowns our own microphone), so
the receiver can't ACK each frame while the sender is still talking.  We therefore use
"bursts":
    sender:   [DATA 0][DATA 1][DATA 2][DATA 3 + POLL]  ... silence ...
    receiver:                                          [ACK]
The POLL flag on the last frame means "your turn, tell me what you got".  This is exactly
how Wi-Fi 802.11n Block-ACK works, and how HDLC uses its P/F bit.

ACK payloads (last byte = SNR x 4 at which the receiver heard the burst, for rate adaptation):
    Stop-and-Wait / Go-Back-N:  [next expected seq][SNR]                   (cumulative ACK)
    Selective Repeat:           [next expected seq][32-bit bitmap][SNR]    (which later frames arrived)

Timeout recovery: if no ACK comes back, the sender does not blindly re-send everything.
It sends a tiny "status poll" (an ACK-type frame with POLL set, HDLC's "RR,P") and the
receiver answers with what it has.  Then only what is really missing gets re-sent.

Stop-and-Wait is literally Go-Back-N with W = 1 (every frame carries POLL) - same code.

Sequence numbers are 8 bits (0..255) and wrap around; the window must be <= 128 for
Selective Repeat so that "new" and "old" frames with the same number can't be confused.
"""
from .frame import Frame, T_DATA, T_ACK, T_ALERT, F_POLL, F_SR

RECV_WINDOW = 32          # how far ahead the SR receiver will buffer (bitmap size)


class LinkFailed(Exception):
    pass


# ALERT frames carry a 1-byte reason code (a long text would take seconds of airtime)
ALERT_TEXT = {
    1: "the file is encrypted and the receiver has no key",
    2: "authentication failed: wrong key, or the data was modified",
    3: "replay attack refused: this session was already received",
    4: "unencrypted file refused: the receiver requires a key",
    5: "a chunk failed authentication: data was modified",
}


def _abs_seq(seq8: int, base: int) -> int:
    """turn an 8-bit sequence number into an absolute index near `base`"""
    return base + ((seq8 - base) % 256)


class ArqSender:
    def __init__(self, link, peer: int, mode: str = "sr", window: int = 4, max_tries: int = 25,
                 bus=None, rate=None, data_mcs: int = None, max_burst_s: float = 12.5):
        mode = mode.lower()
        if mode not in ("sw", "gbn", "sr"):
            raise ValueError("mode must be sw, gbn or sr")
        self.link, self.peer, self.mode = link, peer, mode
        self.window = 1 if mode == "sw" else max(1, min(int(window), RECV_WINDOW if mode == "sr" else 127))
        self.max_tries = max_tries
        self.bus = bus or link.bus
        self.rate = rate                    # RateController (adaptive speed) or None
        self.max_burst_s = max_burst_s
        self.data_mcs = data_mcs            # fixed speed for DATA frames when rate is None
        self.stats = {"data_frames": 0, "retransmissions": 0, "timeouts": 0, "acks": 0,
                      "status_polls": 0}
        self.frames_per_mcs = {}

    def _emit(self, kind, **kw):
        self.bus.emit(kind, t=round(self.link.now(), 3), **kw)

    def send_all(self, messages):
        """Reliably deliver every message (bytes) in order.  Returns a stats dict."""
        link, W, N = self.link, self.window, len(messages)
        sr = self.mode == "sr"
        acked = [False] * N
        times_sent = [0] * N
        base, tries, stall = 0, 0, 0             # tries = consecutive silent timeouts
        need_resend = True                     # False right after a status-poll answer w/o loss
        t0 = link.now()
        # throw away ACKs from this peer that are still queued from an EARLIER transfer (a
        # stale "I have everything" would otherwise end this transfer before it starts)
        while link.recv(0, accept=lambda f: f.type in (T_ACK, T_ALERT) and f.src == self.peer
                        and f.dst == link.addr) is not None:
            pass
        self._emit("arq_start", mode=self.mode, window=W, frames=N)
        burst = []
        rate_burst = None                      # the burst whose outcome rate control has not seen yet
        while base < N:
            if need_resend:
                hi = min(base + W, N)
                burst = [i for i in range(base, hi) if not (sr and acked[i])]
                mcs = self.rate.current if self.rate else self.data_mcs
                if self.rate is not None and burst:
                    # adaptive speed: keep each burst short (<= max_burst_s of airtime) so the
                    # speed can be re-decided quickly - at the slowest speed that is 1 frame
                    ft = link.frame_time(max(len(messages[i]) for i in burst), mcs)
                    burst = burst[:max(1, int(self.max_burst_s // ft))]
                mcs_used = mcs if mcs is not None else link.default_mcs
                rate_burst = list(burst)
                for k, i in enumerate(burst):
                    flags = (F_POLL if k == len(burst) - 1 else 0) | (F_SR if sr else 0)
                    retx = times_sent[i] > 0
                    self._emit("chunk", idx=i, state="retx" if retx else "sent")
                    link.send(Frame(T_DATA, link.addr, self.peer, i % 256, messages[i], flags),
                              mcs=mcs, retx=retx, idx=i)
                    times_sent[i] += 1
                    self.stats["data_frames"] += 1
                    self.frames_per_mcs[mcs_used] = self.frames_per_mcs.get(mcs_used, 0) + 1
                    if retx:
                        self.stats["retransmissions"] += 1
            ack = link.recv(link.ack_timeout(), accept=lambda f: f.src == self.peer and f.dst == link.addr and (
                f.type == T_ALERT or (f.type == T_ACK and not f.flags & F_POLL)))
            if ack is not None and ack.type == T_ALERT:
                code = ack.payload[0] if ack.payload else 0
                reason = ALERT_TEXT.get(code, f"alert code {code}")
                self._emit("alert", reason=reason)
                raise LinkFailed(f"receiver refused the transfer: {reason}")
            if ack is None:
                tries += 1
                self.stats["timeouts"] += 1
                self._emit("timeout", base=base, tries=tries)
                if tries >= self.max_tries:
                    raise LinkFailed(f"no answer after {tries} tries (base={base})")
                if self.rate is not None:
                    self.rate.on_timeout()
                # ask the receiver what it has (cheap) instead of re-sending everything
                self.stats["status_polls"] += 1
                link.send(Frame(T_ACK, link.addr, self.peer, base % 256, b"", F_POLL | (F_SR if sr else 0)),
                          status_poll=True)
                need_resend = False
                continue
            # apply this ACK and any other ACKs already waiting (a late ACK may arrive after a
            # status poll); ACK information only ever adds, so order does not matter
            newly, rbase, snr = [], base, None
            while ack is not None:
                self.stats["acks"] += 1
                if len(ack.payload) == (6 if sr else 2):
                    snr = ack.payload[-1] / 4.0
                if ack.payload:
                    rb = _abs_seq(ack.payload[0], base)
                    if rb <= base + W:                     # ignore stale / nonsense ACKs
                        rbase = max(rbase, rb)
                        for i in range(base, min(rb, N)):
                            if not acked[i]:
                                acked[i] = True
                                newly.append(i)
                        if sr and len(ack.payload) >= 5:
                            bitmap = int.from_bytes(ack.payload[1:5], "big")
                            for j in range(32):
                                i = rb + 1 + j
                                if bitmap >> (31 - j) & 1 and i < N and not acked[i]:
                                    acked[i] = True
                                    newly.append(i)
                ack = link.recv(0, accept=lambda f: f.type == T_ACK and f.src == self.peer
                                and f.dst == link.addr and not f.flags & F_POLL)
            old_base = base
            while base < N and acked[base]:
                base += 1
            if self.rate is not None and rate_burst:
                self.rate.on_burst(len(rate_burst), sum(1 for i in rate_burst if not acked[i]), snr)
                rate_burst = None
            for i in newly:
                self._emit("chunk", idx=i, state="acked")
            self._emit("ack", rbase=rbase, acked=len(newly), base=base, of=N)
            tries = 0                                   # the receiver is alive and answering
            stall = 0 if (base > old_base or newly) else stall + 1
            if stall >= 3 * self.max_tries:
                raise LinkFailed("receiver keeps reporting no progress")
            need_resend = True
        elapsed = link.now() - t0
        payload_bytes = sum(len(m) for m in messages)
        self.stats.update(elapsed_s=round(elapsed, 2), payload_bytes=payload_bytes,
                          goodput_bps=round(payload_bytes * 8 / elapsed, 2) if elapsed > 0 else 0.0,
                          efficiency=round(N / max(1, self.stats["data_frames"]), 3),
                          frames_per_mcs={str(k): v for k, v in self.frames_per_mcs.items()})
        self._emit("arq_done", **self.stats)
        return dict(self.stats)


class ArqReceiver:
    """Mode-agnostic: behaves as Go-Back-N or Selective Repeat depending on the F_SR flag
    the sender puts on its frames."""

    def __init__(self, link, on_deliver, peer: int = None, bus=None):
        self.link = link
        self.on_deliver = on_deliver        # fn(index, payload)
        self.peer = peer                    # lock onto the first sender if None
        self.bus = bus or link.bus
        self.base = 0                       # next in-order index we are waiting for
        self.buffer = {}                    # SR: out-of-order frames
        self.sr = False
        self.alert = None                   # ALERT code set by the application to refuse the transfer
        self._snrs = []                     # SNR of frames from the sender heard since the last ACK
        self._last_snr = 0.0
        self.stats = {"accepted": 0, "duplicates": 0, "discarded_out_of_order": 0, "acks_sent": 0}

    def _ack(self):
        payload = bytes([self.base % 256])
        if self.sr:
            bitmap = 0
            for j in range(32):
                if self.base + 1 + j in self.buffer:
                    bitmap |= 1 << (31 - j)
            payload += bitmap.to_bytes(4, "big")
        if self._snrs:
            self._last_snr = sorted(self._snrs)[len(self._snrs) // 2]     # median of the burst
            self._snrs = []
        payload += bytes([max(0, min(255, int(round(self._last_snr * 4))))])
        self.link.send(Frame(T_ACK, self.link.addr, self.peer, self.base % 256, payload))
        self.stats["acks_sent"] += 1

    def handle(self, f: Frame) -> bool:
        """Feed every received frame here.  Returns True if it was ARQ traffic."""
        if f.type not in (T_DATA, T_ACK) or f.dst != self.link.addr:
            return False
        if self.peer is None:
            self.peer = f.src
        if f.src != self.peer:
            return False
        if f.type == T_ACK:                          # sender's status poll
            if f.flags & F_POLL:
                self.sr = bool(f.flags & F_SR)
                self._snrs.append(f.snr_db)          # fresh news about the channel for rate control
                self._answer()
                return True
            return False
        self.sr = bool(f.flags & F_SR)
        self._snrs.append(f.snr_db)
        i = _abs_seq(f.seq, self.base)
        t = round(self.link.now(), 3)
        if self.sr:
            if i < self.base + RECV_WINDOW and i not in self.buffer:
                self.buffer[i] = f.payload
                self.stats["accepted"] += 1
                self.bus.emit("chunk_got", t=t, idx=i, in_order=(i == self.base))
                while self.base in self.buffer:
                    self.on_deliver(self.base, self.buffer.pop(self.base))
                    self.base += 1
            else:
                self.stats["duplicates"] += 1
        else:
            if i == self.base:
                self.bus.emit("chunk_got", t=t, idx=i, in_order=True)
                self.on_deliver(self.base, f.payload)
                self.base += 1
                self.stats["accepted"] += 1
            elif i < self.base + 128:
                self.stats["discarded_out_of_order"] += 1
                self.bus.emit("gbn_discard", t=t, idx=i, waiting_for=self.base)
            else:
                self.stats["duplicates"] += 1
        if f.flags & F_POLL:
            self._answer()
        return True

    def _answer(self):
        """ACK - or, if the application refused the transfer, an ALERT saying why"""
        if self.alert:
            self.link.send(Frame(T_ALERT, self.link.addr, self.peer, 0, bytes([self.alert])))
        else:
            self._ack()
