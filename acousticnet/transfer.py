"""
transfer.py - the APPLICATION layer: send a whole file, acoustic ping, link probe.

File transfer = a list of messages delivered reliably by arq.py:
    message 0      META  : file name, size, number of chunks, first 8 bytes of SHA-256
    message 1..n   chunks of the file

The receiver re-assembles the chunks, recomputes SHA-256 and only says "FILE OK" if the hash
matches - end-to-end proof that every single bit crossed the air correctly.

Adaptive speed (rate="auto"): first a PING/PONG "link probe" measures the SNR in both
directions; that picks the starting speed and the chunk size (faster speed = bigger chunks).
During the transfer the RateController (rate.py) keeps adjusting the speed burst by burst.

Security (key="passphrase"): META and every chunk are encrypted and authenticated with
crypto.SecureChannel.  Encrypted META = "AE" + session id + sealed(META).  The receiver refuses
wrong keys, modified data, unencrypted files (when it has a key) and replayed sessions.

After finishing, the receiver keeps answering for a while ("linger") in case its last ACK was
lost and the sender asks again.  TCP's TIME_WAIT state exists for the same reason.
"""
import hashlib
import os
import struct
import threading

from .arq import ArqSender, ArqReceiver, LinkFailed, ALERT_TEXT
from .config import CHUNK_FOR_MCS
from .crypto import SecureChannel, AuthError, SESSION_BYTES
from .frame import Frame, T_DATA, T_ACK, T_PING, T_PONG, T_MSG
from .rate import RateController

META_MAGIC = b"AN"              # plain transfer
SECURE_MAGIC = b"AE"            # encrypted transfer
META_FMT = ">2sHI8s"            # magic, n_chunks, size, sha256[:8]  -> 16 bytes, then name


def make_messages(data: bytes, name: str, chunk: int, channel: SecureChannel = None):
    chunks = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
    sha8 = hashlib.sha256(data).digest()[:8]
    name_b = os.path.basename(name).encode("utf-8")[:40]
    meta = struct.pack(META_FMT, META_MAGIC, len(chunks), len(data), sha8) + name_b
    if channel is None:
        return [meta] + chunks, None
    sid = channel.new_session()
    sealed = [SECURE_MAGIC + sid + channel.seal(channel.nonce(sid, 0), meta)]
    sealed += [channel.seal(channel.nonce(sid, i), c) for i, c in enumerate(chunks, start=1)]
    return sealed, sid


def parse_meta(payload: bytes):
    n = struct.calcsize(META_FMT)
    magic, n_chunks, size, sha8 = struct.unpack(META_FMT, payload[:n])
    if magic != META_MAGIC:
        raise ValueError("not a META message")
    return {"chunks": n_chunks, "size": size, "sha8": sha8,
            "name": payload[n:].decode("utf-8", "replace") or "received.bin"}


# ------------------------------------------------------------------- probe --
def probe(link, peer: int, tries: int = 2, bus=None):
    """PING the peer; the PONG says at what SNR the PING was heard, and we measure the PONG.
    Returns the worse of the two directions (dB), or None if the peer did not answer."""
    bus = bus or link.bus
    for i in range(tries):
        link.send(Frame(T_PING, link.addr, peer, 200 + i, b"prob"))
        pong = link.recv(link.ack_timeout(5) + 1.0,
                         accept=lambda f: f.type == T_PONG and f.src == peer and f.dst == link.addr and f.seq == 200 + i)
        if pong is not None:
            there = pong.payload[-1] / 4.0 if len(pong.payload) >= 5 else pong.snr_db
            snr = min(there, pong.snr_db)
            bus.emit("probe", t=round(link.now(), 3), snr_there=round(there, 1), snr_back=round(pong.snr_db, 1),
                     snr=round(snr, 1))
            return snr
    bus.emit("probe", t=round(link.now(), 3), snr=None)
    return None


# ------------------------------------------------------------------- send --
def send_file(link, peer: int, data: bytes, name: str, mode: str = "sr", window: int = 4,
              chunk: int = None, bus=None, rate=None, key: str = None):
    """rate: None = base speed, an MCS number = fixed speed, "auto" = adaptive speed.
    key:  passphrase for encryption + authentication (both laptops must use the same one)."""
    bus = bus or link.bus
    controller, data_mcs = None, None
    if rate == "auto":
        snr = probe(link, peer, bus=bus)
        controller = RateController(link.modem, start=link.default_mcs, bus=bus, clock=link.now)
        if snr is not None:
            controller.cur = controller.best_for_snr(snr)       # starting speed from the probe
            controller.history = [controller.cur]
        start = controller.current
        chunk = chunk or CHUNK_FOR_MCS[start]
    elif rate is not None:
        data_mcs = int(rate)
        chunk = chunk or CHUNK_FOR_MCS[data_mcs]
    chunk = chunk or link.link_cfg.chunk_size
    channel = SecureChannel(key) if key else None
    msgs, sid = make_messages(data, name, chunk, channel)
    start_mcs = controller.current if controller else (data_mcs if data_mcs is not None else link.default_mcs)
    bus.emit("transfer_start", t=round(link.now(), 3), name=os.path.basename(name), size=len(data),
             chunks=len(msgs), mode=mode, window=window, chunk=chunk, rate=str(rate or "base"),
             mcs=start_mcs, encrypted=bool(channel), session=sid.hex() if sid else None,
             est_frame_s=round(link.frame_time(chunk, start_mcs), 2))
    sender = ArqSender(link, peer, mode, window, max_tries=link.link_cfg.max_tries, bus=bus,
                       rate=controller, data_mcs=data_mcs)
    try:
        stats = sender.send_all(msgs)
    except LinkFailed as e:
        bus.emit("transfer_failed", t=round(link.now(), 3), reason=str(e))
        raise
    stats["file_goodput_bps"] = round(len(data) * 8 / stats["elapsed_s"], 2) if stats["elapsed_s"] else 0
    stats["chunk"] = chunk
    if controller:
        stats["rate_history"] = controller.history
    bus.emit("transfer_done", t=round(link.now(), 3), **stats)
    return stats


# ---------------------------------------------------------------- receive --
def _safe_name(name: str) -> str:
    """never let a received name escape the output folder (no ../../ tricks)"""
    base = os.path.basename(name.replace("\\", "/")).strip() or "received.bin"
    return "".join(c for c in base if c.isalnum() or c in "._- ") or "received.bin"


class _SessionLog:
    """remembers completed encrypted sessions, so a replayed recording is refused"""

    def __init__(self, out_dir):
        self.path = os.path.join(out_dir, ".acousticnet_sessions")

    def seen(self, sid: bytes) -> bool:
        try:
            with open(self.path, encoding="ascii") as fh:
                return sid.hex() in fh.read().split()
        except OSError:
            return False

    def add(self, sid: bytes):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="ascii") as fh:
            fh.write(sid.hex() + "\n")


def receive_file(link, out_dir: str = "received", bus=None, idle_timeout: float = None,
                 linger: float = None, stop_event: threading.Event = None, key: str = None):
    """Wait for one file.  Returns (path, ok, meta), or None if refused / timed out.
    Answers pings while waiting."""
    bus = bus or link.bus
    linger = link.link_cfg.linger_s if linger is None else linger
    channel = SecureChannel(key) if key else None
    sessions = _SessionLog(out_dir)
    state = {"meta": None, "chunks": {}, "sid": None, "refused": None, "first": None}

    def refuse(code, detail=""):
        if state["refused"] is None:
            state["refused"] = code
            rcv.alert = code                        # answer the sender with an ALERT, not an ACK
            bus.emit("security_reject", t=round(link.now(), 3), reason=ALERT_TEXT[code] + detail)

    def deliver(idx, payload):
        if state["refused"]:
            return
        if idx == 0:
            if payload[:2] == SECURE_MAGIC:
                sid = payload[2:2 + SESSION_BYTES]
                if channel is None:
                    return refuse(1, " (start the receiver with --key)")
                if sessions.seen(sid):
                    return refuse(3, f" (session {sid.hex()})")
                try:
                    payload = channel.open(channel.nonce(sid, 0), payload[2 + SESSION_BYTES:])
                except AuthError:
                    return refuse(2)
                state["sid"] = sid
            elif channel is not None:
                return refuse(4)
            try:
                state["meta"] = parse_meta(payload)
            except Exception:
                bus.emit("error", where="transfer", error="bad META message")
                return
            m = state["meta"]
            bus.emit("transfer_meta", t=round(link.now(), 3), name=m["name"], size=m["size"],
                     chunks=m["chunks"] + 1, encrypted=state["sid"] is not None)
        else:
            if state["sid"] is not None:
                try:
                    payload = channel.open(channel.nonce(state["sid"], idx), payload)
                except AuthError:
                    return refuse(5, f" (chunk {idx})")
            state["chunks"][idx] = payload
            bus.emit("chunk_rx", t=round(link.now(), 3), idx=idx, bytes=len(payload),
                     got=len(state["chunks"]), of=(state["meta"] or {}).get("chunks", 0))

    rcv = ArqReceiver(link, deliver, bus=bus)
    finished_at, result = None, None
    last_activity = link.now()
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        f = link.recv(1.0, accept=lambda f: f.type in (T_DATA, T_ACK, T_PING, T_MSG) and f.dst in (link.addr, 0xFF))
        now = link.now()
        if f is not None:
            if f.type == T_PING:
                last_activity = now
                answer_ping(link, f)
                continue
            if f.type == T_MSG:
                bus.emit("message", t=round(now, 3), src=f.src, text=f.payload.decode("utf-8", "replace"))
                continue
            start = f.type == T_DATA and f.seq == 0 and len(f.payload) >= 2 \
                and f.payload[:2] in (META_MAGIC, SECURE_MAGIC)
            if start and state["first"] is None:
                state["first"] = bytes(f.payload)
            elif start and f.payload != state["first"] and result is not None:
                # we already finished a file and are only lingering; a DIFFERENT transfer is
                # starting (a genuine next file, or a replay).  Do NOT cumulative-ACK it as the
                # finished one - hand off to a fresh receiver (which will refuse a replay).
                break
            if rcv.peer is not None and f.src != rcv.peer and state["meta"] is None \
                    and state["refused"] is None and result is None:
                # we had locked onto a sender whose transfer we never saw start (a late or stray
                # fragment): forget it and follow the new sender instead
                rcv = ArqReceiver(link, deliver, bus=bus)
                state["chunks"], state["sid"], state["first"] = {}, None, None
            rcv.handle(f)
            if f.src == rcv.peer:
                last_activity = now               # only OUR sender keeps the session alive
        m = state["meta"]
        if result is None and state["refused"] is None and m and len(state["chunks"]) >= m["chunks"]:
            data = b"".join(state["chunks"][i] for i in range(1, m["chunks"] + 1))[: m["size"]]
            ok = hashlib.sha256(data).digest()[:8] == m["sha8"]
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, _safe_name(m["name"]))
            with open(path, "wb") as fh:
                fh.write(data)
            if state["sid"] is not None:
                sessions.add(state["sid"])
            preview = data[:400].decode("utf-8", "replace") if _is_text(data) else f"<{len(data)} bytes binary>"
            bus.emit("file_done", t=round(now, 3), path=path, ok=ok, size=len(data), preview=preview,
                     name=m["name"], encrypted=state["sid"] is not None)
            result = (path, ok, m)
            finished_at = now
        if state["refused"] is not None and now - last_activity > min(linger, 3 * link.ack_timeout()):
            break                                 # refused: answer ALERTs while they keep trying, then move on
        if finished_at is not None and now - finished_at > linger:
            break
        if result is None and idle_timeout and now - last_activity > idle_timeout:
            break
    return result


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# -------------------------------------------------------------------- ping --
def answer_ping(link, f: Frame):
    """PONG = the PING's payload + the SNR (x4) at which we heard the PING"""
    snr_q = bytes([max(0, min(255, int(round(f.snr_db * 4))))])
    link.send(Frame(T_PONG, link.addr, f.src, f.seq, bytes(f.payload[:4]) + snr_q))


def ping(link, peer: int, count: int = 3, bus=None):
    """Acoustic ping.  RTT = from start of our PING until the PONG is fully heard."""
    bus = bus or link.bus
    results = []
    for i in range(count):
        t_start = link.now()
        link.send(Frame(T_PING, link.addr, peer, i, b"ping"))
        pong = link.recv(link.ack_timeout(5) + 1.0,
                         accept=lambda f: f.type == T_PONG and f.src == peer and f.dst == link.addr and f.seq == i)
        if pong is None:
            bus.emit("ping", t=round(link.now(), 3), seq=i, rtt=None)
            results.append(None)
        else:
            rtt = link.now() - t_start
            air = link.frame_time(4) + link.frame_time(5)
            bus.emit("ping", t=round(link.now(), 3), seq=i, rtt=round(rtt, 3), airtime=round(air, 3),
                     latency_and_processing=round(rtt - air, 3), snr=round(pong.snr_db, 1),
                     snr_there=round(pong.payload[-1] / 4.0, 1) if len(pong.payload) >= 5 else None)
            results.append(rtt)
        link.sleep(0.5)
    return results
