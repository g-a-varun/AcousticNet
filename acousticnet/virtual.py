"""
virtual.py - a fake link with NO audio, for testing ARQ and file transfer very fast.

VirtualNet gives every participant a VirtualLink with exactly the same methods as a real
Node (send, recv, now, sleep, frame_time, ack_timeout).  Frames take as long to "transmit"
as they would on air, arrive after a latency, collide if two overlap in time, and get lost
with a chosen probability.

Time is VIRTUAL: it only moves forward when every participant is waiting, and then it jumps
straight to the next interesting moment.  A 10-minute acoustic transfer is simulated in well
under a second, and the protocol code can't tell the difference.

Analogy: a chess clock that only ticks when both players are thinking - but instead of
ticking, it jumps straight to whoever needs to move next.
"""
import dataclasses
import math
import threading

import numpy as np

from .config import DEFAULT_PHY, DEFAULT_LINK, BROADCAST
from .events import EventBus
from .phy import Modem


class VirtualClock:
    def __init__(self, participants: int):
        self.t = 0.0
        self.cond = threading.Condition()
        self.active = participants
        self._waiting = {}

    def wait(self, wake_fn):
        """block until wake_fn() -> (ready, next_time) says ready; caller must NOT hold cond"""
        me = threading.get_ident()
        with self.cond:
            while True:
                ready, _ = wake_fn()
                if ready:
                    self._waiting.pop(me, None)
                    return
                self._waiting[me] = wake_fn
                if len(self._waiting) >= self.active:
                    nxt = min(fn()[1] for fn in self._waiting.values())
                    if math.isinf(nxt):
                        raise RuntimeError("virtual deadlock: everybody waits forever")
                    self.t = max(self.t, nxt)
                    self._waiting.clear()
                    self.cond.notify_all()
                    continue
                self.cond.wait()

    def leave(self):
        """a participant thread finished"""
        with self.cond:
            self.active -= 1
            self._waiting.pop(threading.get_ident(), None)
            if self.active and len(self._waiting) >= self.active:
                self._waiting.clear()
                self.cond.notify_all()
                # the remaining waiters re-evaluate; the last one to re-register advances time


class VirtualNet:
    def __init__(self, participants: int = 2, loss: float = 0.0, ber: float = 0.0,
                 latency_s: float = 0.2, phy=DEFAULT_PHY, fec: bool = False, seed: int = 0,
                 link_cfg=DEFAULT_LINK, snr_fn=None, mcs_loss=None, loss_fn=None):
        """snr_fn(t) -> SNR in dB the receiver REPORTS at time t (default 30 dB);
        mcs_loss(mcs, snr) -> probability that a frame at that speed is lost (default: none);
        loss_fn(mcs, t, payload_len) -> same, but computed from time (for experiments)"""
        self.clock = VirtualClock(participants)
        self.loss, self.ber, self.latency = loss, ber, latency_s
        self.snr_fn = snr_fn or (lambda t: 30.0)
        self.mcs_loss = mcs_loss
        self.loss_fn = loss_fn
        self.modem = Modem(phy)
        self.fec = fec
        self.link_cfg = link_cfg
        self.rng = np.random.default_rng(seed)
        self.links = {}
        self.transmissions = []          # (start, end, src)

    def link(self, addr: int, bus: EventBus = None) -> "VirtualLink":
        lk = VirtualLink(self, addr, bus or EventBus())
        self.links[addr] = lk
        return lk

    def _overlaps(self, start, end, src):
        return any(s < end and start < e and who != src for s, e, who in self.transmissions)


class VirtualLink:
    def __init__(self, net: VirtualNet, addr: int, bus: EventBus):
        self.net, self.addr, self.bus = net, addr, bus
        self.fec = net.fec
        self.default_mcs = 0 if net.fec else 1
        self.modem = net.modem
        self.link_cfg = net.link_cfg
        self.rt_latency_s = net.latency
        self._inbox = []                 # (deliver_time, start, end, frame, lost)
        self.handlers = []
        self.stats = {"tx": 0, "rx": 0}

    # --- clock ---
    def now(self):
        return self.net.clock.t

    def sleep(self, seconds):
        clk = self.net.clock
        t_end = clk.t + seconds
        clk.wait(lambda: (clk.t >= t_end - 1e-12, t_end))

    def frame_time(self, payload_len, mcs=None):
        return self.net.modem.frame_duration(payload_len, self.default_mcs if mcs is None else mcs)

    def ack_timeout(self, ack_len=6):
        return 2 * self.net.latency + self.frame_time(ack_len) + self.link_cfg.processing_margin_s

    # --- TX ---
    def send(self, frame, at=None, wait=True, mcs=None, **info):
        net, clk = self.net, self.net.clock
        frame.src = self.addr
        m = mcs if mcs is not None else (frame.mcs if frame.mcs is not None else self.default_mcs)
        with clk.cond:
            start = clk.t
            dur = self.frame_time(len(frame.payload), m)
            end = start + dur
            net.transmissions = [x for x in net.transmissions if x[1] > start - 60] + [(start, end, self.addr)]
            nbits = 8 * (len(frame.payload) + 9)
            p_err = 1 - (1 - net.ber) ** nbits if net.ber else 0.0
            snr = float(net.snr_fn(start))
            p_mcs = net.mcs_loss(m, snr) if net.mcs_loss else 0.0
            if net.loss_fn:
                p_mcs = max(p_mcs, net.loss_fn(m, start, len(frame.payload)))
            for addr, other in net.links.items():
                if addr == self.addr:
                    continue
                lost = (net.rng.random() < net.loss) | (net.rng.random() < p_err) | (net.rng.random() < p_mcs)
                copy = dataclasses.replace(frame, mcs=m, snr_db=snr)      # what the receiver "hears"
                other._inbox.append((end + net.latency, start, end, copy, lost))
            self.stats["tx"] += 1
        self.bus.emit("tx", t=round(start, 3), type=frame.name, seq=frame.seq, dst=frame.dst,
                      len=len(frame.payload), dur=round(dur, 3), mcs=m, **info)
        if wait:
            clk.wait(lambda: (clk.t >= end - 1e-12, end))

    # --- RX ---
    def _pop_ready(self, accept):
        t = self.net.clock.t
        for k, (td, s, e, f, lost) in enumerate(self._inbox):
            if td > t + 1e-12:
                continue
            if lost or self.net._overlaps(s, e, f.src) or (f.dst not in (self.addr, BROADCAST)):
                self._inbox.pop(k)
                return "dropped"
            if accept is None or accept(f):
                self._inbox.pop(k)
                return f
        return None

    def recv(self, timeout, accept=None):
        clk = self.net.clock
        deadline = clk.t + timeout
        result = []

        def wake():
            if result:                       # already got one (other threads may call us too)
                return True, clk.t
            while True:
                r = self._pop_ready(accept)
                if r == "dropped":
                    continue
                if r is not None:
                    result.append(r)
                    return True, clk.t
                break
            if clk.t >= deadline - 1e-12:
                return True, clk.t
            pending = [td for td, *_ in self._inbox if td > clk.t + 1e-12]
            return False, min([deadline] + pending)

        clk.wait(wake)
        if result:
            f = result[0]
            self.stats["rx"] += 1
            self.bus.emit("rx", t=round(clk.t, 3), type=f.name, seq=f.seq, src=f.src, dst=f.dst,
                          len=len(f.payload), crc_ok=True, snr=round(f.snr_db, 1), quality=1.0, fec_fixed=0,
                          mcs=f.mcs)
            return f
        return None

    def done(self):
        self.net.clock.leave()


def run_virtual_transfer(data: bytes, mode: str = "sr", window: int = 4, loss: float = 0.0,
                         ber: float = 0.0, latency_s: float = 0.3, chunk: int = None, seed: int = 0,
                         fec: bool = False, phy=DEFAULT_PHY, out_dir: str = None, rate=None,
                         snr_fn=None, mcs_loss=None, bus=None, key: str = None, rx_key: str = None,
                         loss_fn=None, rx_bus=None):
    """Send `data` from link 1 to link 2 over a VirtualNet.  Returns (sender_stats, received_bytes).
    rate: None (base speed), an MCS number (fixed speed) or "auto" (adaptive speed)."""
    import tempfile
    from .transfer import send_file, receive_file
    net = VirtualNet(2, loss=loss, ber=ber, latency_s=latency_s, seed=seed, fec=fec, phy=phy,
                     snr_fn=snr_fn, mcs_loss=mcs_loss, loss_fn=loss_fn)
    a, b = net.link(1, bus), net.link(2, rx_bus)
    out_dir = out_dir or tempfile.mkdtemp(prefix="an_virtual_")
    box = {}

    def rx():
        try:
            # linger (like TCP's TIME_WAIT) as long as the sender may keep polling: max_tries rounds,
            # so a lost final ACK can always be repeated
            box["r"] = receive_file(b, out_dir, linger=30 * (b.ack_timeout() + b.frame_time(0)),
                                    idle_timeout=600, key=rx_key if rx_key is not None else key)
        finally:
            b.done()

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    try:
        stats = send_file(a, 2, data, "virtual.bin", mode, window, chunk, rate=rate, key=key)
    finally:
        a.done()
    th.join(20)
    got = None
    if box.get("r"):
        with open(box["r"][0], "rb") as fh:
            got = fh.read()
    return stats, got
