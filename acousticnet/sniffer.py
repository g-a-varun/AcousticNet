"""
sniffer.py - "Eve", the eavesdropper.  Shows WHY the security layer exists.

Sound is a broadcast medium: every laptop in the room hears every frame, whoever it is
addressed to.  The Sniffer listens to all of them (promiscuous mode, like Wireshark on Wi-Fi):

  * prints every frame it overhears (type, who -> whom, a peek at the payload)
  * follows each file transfer it sees, and when it has every chunk of an UNENCRYPTED file,
    quietly rebuilds the file and saves it  ->  "Eve stole secret_message.txt"
  * for an ENCRYPTED transfer it only ever sees random-looking bytes  ->  nothing to steal
    (unless it is given the key, e.g. an authorised network monitor)
  * it RECORDS every transfer it hears (sniffed/capture_*.json), so that `replay` can play the
    recording back later - the replay attack that the session id + MAC must defeat
"""
import hashlib
import json
import os
import struct

from .crypto import SecureChannel, AuthError, SESSION_BYTES
from .frame import T_DATA, F_NET
from .transfer import META_MAGIC, SECURE_MAGIC, META_FMT, parse_meta, _safe_name


def _peek(b: bytes, n: int = 28) -> str:
    """printable text if it looks like text, otherwise hex"""
    b = bytes(b[:n])
    if b and sum(32 <= c < 127 or c in (9, 10, 13) for c in b) >= 0.85 * len(b):
        return b.decode("ascii", "replace").replace("\n", " ")
    return b.hex(" ")


class Sniffer:
    def __init__(self, node, out_dir: str = "sniffed", key: str = None, bus=None):
        self.node = node
        self.bus = bus or node.bus
        self.out_dir = out_dir
        self.channel = SecureChannel(key) if key else None
        self.flows = {}
        self.stats = {"frames": 0, "stolen_files": 0, "encrypted_transfers": 0, "captures": 0}
        node.promiscuous = True
        node.raw_listeners.append(self.on_frame)

    def on_frame(self, f):
        if not f.crc_ok:
            return
        src, dst, payload = f.src, f.dst, bytes(f.payload)
        if f.flags & F_NET and len(payload) >= 3:          # routed frame: look inside
            src, dst, payload = payload[0], payload[1], payload[3:]
        self.stats["frames"] += 1
        self.bus.emit("sniff_frame", t=round(self.node.now(), 2), type=f.name, src=src, dst=dst, seq=f.seq,
                      len=len(payload), peek=_peek(payload))
        if f.type != T_DATA:
            return
        key = (src, dst)
        if f.seq == 0 and payload[:2] in (META_MAGIC, SECURE_MAGIC) and \
                (key not in self.flows or self.flows[key]["first"] != payload):
            self.flows[key] = {"first": payload, "chunks": {}, "done": False, "raw": {0: payload},
                               "capture": self._capture_path(src, dst, payload)}
            self.stats["captures"] += 1
            self._save_capture(self.flows[key], src, dst)
            self.bus.emit("sniff_capture", t=round(self.node.now(), 2), src=src, dst=dst,
                          path=self.flows[key]["capture"])
            flow = self.flows[key]
            if payload[:2] == SECURE_MAGIC:
                sid = payload[2:2 + SESSION_BYTES]
                flow["sid"] = sid
                if self.channel is None:
                    self.stats["encrypted_transfers"] += 1
                    self.bus.emit("sniff_encrypted", t=round(self.node.now(), 2), src=src, dst=dst,
                                  session=sid.hex(), peek=_peek(payload[2 + SESSION_BYTES:], 20))
                    return
                try:
                    flow["meta"] = parse_meta(self.channel.open(self.channel.nonce(sid, 0),
                                                                payload[2 + SESSION_BYTES:]))
                except AuthError:
                    flow["meta"] = None
            else:
                flow["sid"] = None
                try:
                    flow["meta"] = parse_meta(payload)
                except (ValueError, struct.error):
                    flow["meta"] = None
            return
        flow = self.flows.get(key)
        if flow and f.seq != 0 and f.seq not in flow["raw"]:
            flow["raw"][f.seq] = payload                  # the recording, encrypted or not
            self._save_capture(flow, src, dst)
        if not flow or flow["done"] or not flow.get("meta"):
            return
        flow["chunks"][f.seq] = payload
        m = flow["meta"]
        if all(i % 256 in flow["chunks"] for i in range(1, m["chunks"] + 1)):
            parts = []
            for i in range(1, m["chunks"] + 1):
                c = flow["chunks"][i % 256]
                if flow["sid"] is not None:
                    try:
                        c = self.channel.open(self.channel.nonce(flow["sid"], i), c)
                    except AuthError:
                        return
                parts.append(c)
            data = b"".join(parts)[: m["size"]]
            flow["done"] = True
            os.makedirs(self.out_dir, exist_ok=True)
            path = os.path.join(self.out_dir, "stolen_" + _safe_name(m["name"]))
            with open(path, "wb") as fh:
                fh.write(data)
            self.stats["stolen_files"] += 1
            try:
                preview = data[:300].decode("utf-8")
            except UnicodeDecodeError:
                preview = f"<{len(data)} bytes binary>"
            self.bus.emit("sniff_file", t=round(self.node.now(), 2), src=src, dst=dst, name=m["name"],
                          size=len(data), path=path, preview=preview,
                          hash_ok=hashlib.sha256(data).digest()[:8] == m["sha8"],
                          decrypted=flow["sid"] is not None)

    # ------------------------------------------------------------- recording --
    def _capture_path(self, src, dst, first):
        tag = first[2:2 + SESSION_BYTES].hex() if first[:2] == SECURE_MAGIC else hashlib.sha256(first).hexdigest()[:8]
        return os.path.join(self.out_dir, f"capture_{src}_to_{dst}_{tag}.json")

    def _save_capture(self, flow, src, dst):
        os.makedirs(self.out_dir, exist_ok=True)
        rec = {"src": src, "dst": dst, "encrypted": flow["first"][:2] == SECURE_MAGIC,
               "messages": {str(i): p.hex() for i, p in sorted(flow["raw"].items())}}
        with open(flow["capture"], "w", encoding="ascii") as fh:
            json.dump(rec, fh, indent=1)


def load_capture(path):
    """-> (src, dst, [message bytes in order]) from a sniffed/capture_*.json recording"""
    with open(path, encoding="ascii") as fh:
        rec = json.load(fh)
    msgs = rec["messages"]
    order = sorted(int(k) for k in msgs)
    n = 0
    while n in order:                       # replay the unbroken run 0, 1, 2 ... that was heard
        n += 1
    return rec["src"], rec["dst"], [bytes.fromhex(msgs[str(i)]) for i in range(n)]
