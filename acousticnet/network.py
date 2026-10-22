"""
network.py - the NETWORK layer: distance-vector routing, forwarding, TTL and traceroute.

Problem: laptop A and laptop B are too far apart (or a wall is in between) to hear each other,
but both can hear laptop R in the middle.  R must RELAY:   A  ))) R )))  B

1. Neighbour discovery   every frame a laptop decodes cleanly tells it "I can hear X".
2. Distance vector       each router broadcasts its table [(destination, cost, next hop), ...]
   (Bellman-Ford, RIP)   when the room is quiet.  A neighbour that offers destination D at cost c
                         gives us D at cost c+1 via that neighbour.  Cost = number of hops.
3. Poison reverse        if my best route to D goes THROUGH you, I tell you my cost to D is
                         infinity (16), so you never route D back through me.  This stops the
                         famous "count-to-infinity" problem when a link dies.
4. Timeouts              a neighbour not heard for 4 periods is declared dead: routes through it
                         become infinity and a triggered update tells everybody at once.
   Jitter                every advert time is randomised a lot.  Two laptops that cannot hear
                         each other (a "hidden terminal" pair) would otherwise answer the same
                         advert at the same moment, collide at the relay, and - because their timers
                         then stay in step - keep colliding forever ("synchronisation of periodic
                         routing messages", Floyd & Jacobson 1993).
   Quiet when busy       while a laptop is carrying real traffic it postpones its periodic adverts:
                         the data frames themselves prove that neighbours are alive, and an advert
                         from a hidden terminal would only collide with somebody's data at the relay.
5. Forwarding            routed frames carry a 3-byte network header [net_src, net_dst, TTL]
                         inside the payload (flag F_NET).  The link-layer header says only the
                         NEXT hop.  A relay waits until the room is quiet (half-duplex!) and then
                         re-sends the whole burst to the next hop - store-and-forward.
6. TTL                   decremented at every relay; at 0 the frame is dropped and a TTLX
                         ("time exceeded") goes back to the sender - exactly what ICMP does, and
                         what traceroute uses to discover the path one hop at a time.

NetLink wraps a Node so the ARQ/file-transfer code works unchanged across several hops: it
looks up the next hop for every frame and stretches the ACK timeout by the number of hops.
"""
import dataclasses
import random
import threading
from dataclasses import dataclass

from .config import BROADCAST
from .frame import Frame, T_ROUTE, T_TTLX, T_PING, T_PONG, F_NET

INF = 16
DEFAULT_TTL = 8


@dataclass
class Route:
    dest: int
    next_hop: int
    cost: int
    updated: float


class DVTable:
    """Pure distance-vector logic (no audio) - also used by experiments/routing_sim.py."""

    def __init__(self, me: int, poison_reverse: bool = True):
        self.me = me
        self.poison_reverse = poison_reverse
        self.routes = {me: Route(me, me, 0, 0.0)}

    def neighbor_up(self, n: int, now: float) -> bool:
        r = self.routes.get(n)
        if r is None or r.cost != 1 or r.next_hop != n:
            self.routes[n] = Route(n, n, 1, now)
            return True
        r.updated = now
        return False

    def neighbor_down(self, n: int, now: float) -> bool:
        changed = False
        for r in self.routes.values():
            if r.next_hop == n and r.dest != self.me and r.cost < INF:
                r.cost, r.updated = INF, now
                changed = True
        return changed

    def advert(self):
        return [(r.dest, min(r.cost, INF), r.next_hop) for r in self.routes.values()]

    def on_advert(self, n: int, entries, now: float) -> bool:
        changed = self.neighbor_up(n, now)
        for d, c, nh in entries:
            if d in (self.me, n):
                continue
            if self.poison_reverse and nh == self.me:
                c = INF                                   # "don't route it back through me"
            new = min(INF, c + 1)
            r = self.routes.get(d)
            if r is None:
                if new < INF:
                    self.routes[d] = Route(d, n, new, now)
                    changed = True
            elif r.next_hop == n:                         # news from our current next hop: believe it
                if r.cost != new:
                    r.cost = new
                    changed = True
                r.updated = now
            elif new < r.cost:                            # a better path
                self.routes[d] = Route(d, n, new, now)
                changed = True
        return changed

    def lookup(self, dest: int):
        r = self.routes.get(dest)
        return r.next_hop if r is not None and r.cost < INF else None

    def cost(self, dest: int) -> int:
        r = self.routes.get(dest)
        return r.cost if r is not None else INF

    def collect_garbage(self, now: float, hold: float):
        for d in [d for d, r in self.routes.items() if r.cost >= INF and now - r.updated > hold]:
            del self.routes[d]

    def snapshot(self):
        return [{"dest": r.dest, "next_hop": r.next_hop, "cost": r.cost}
                for r in sorted(self.routes.values(), key=lambda r: r.dest)]


class Router:
    def __init__(self, node, bus=None, period_s: float = 20.0, poison_reverse: bool = True,
                 min_snr_db: float = 6.0, forward: bool = True, idle_s: float = 0.6):
        self.node = node
        self.bus = bus or node.bus
        self.dv = DVTable(node.addr, poison_reverse)
        self.period = period_s
        self.timeout = 4 * period_s
        self.min_snr_db = min_snr_db
        self.forward = forward
        self.idle_s = idle_s
        self.neighbors = {}
        self.queue = []
        self.stats = {"forwarded": 0, "adverts": 0, "ttl_expired": 0, "no_route": 0}
        self._last_traffic = -1e9
        self._last_data = -1e9                   # last time we sent / heard / forwarded real traffic
        self._next_advert = None
        self._trigger_at = None
        self._seq = 0
        self._lock = threading.Lock()
        self._running = False
        self._route_cond = threading.Condition()
        node.raw_listeners.append(self._overheard)
        node.handlers.insert(0, self._on_frame)

    # ------------------------------------------------------------ lifecycle --
    def start(self):
        self._running = True
        self._next_advert = self.node.now() + random.uniform(0.5, 8.0)
        threading.Thread(target=self._loop, name=f"router-{self.node.addr}", daemon=True).start()
        return self

    def stop(self):
        self._running = False

    def _changed(self, reason):
        now = self.node.now()
        if self._trigger_at is None:
            self._trigger_at = now + random.uniform(1.0, 6.0)   # triggered update, widely jittered
        self.bus.emit("route_table", t=round(now, 2), me=self.node.addr, reason=reason, table=self.dv.snapshot(),
                      neighbors=sorted(self.neighbors))
        with self._route_cond:
            self._route_cond.notify_all()

    # -------------------------------------------------------------- receive --
    def _overheard(self, f: Frame):
        """any clean frame from X proves we can hear X: neighbour discovery"""
        now = self.node.now()
        self._last_traffic = now
        if not f.crc_ok or f.snr_db < self.min_snr_db or f.src == BROADCAST:
            return
        if f.type != T_ROUTE:
            self._last_data = now
        with self._lock:
            self.neighbors[f.src] = now
            if self.dv.neighbor_up(f.src, now):
                self._changed(f"can hear node {f.src}")

    def _on_frame(self, f: Frame) -> bool:
        me = self.node.addr
        if f.type == T_ROUTE:
            entries = [tuple(f.payload[i:i + 3]) for i in range(0, len(f.payload) - 2, 3)]
            with self._lock:
                if self.dv.on_advert(f.src, entries, self.node.now()):
                    self._changed(f"advert from node {f.src}")
            return True
        if not (f.flags & F_NET) or len(f.payload) < 3:
            return False
        net_src, net_dst, ttl = f.payload[0], f.payload[1], f.payload[2]
        if net_dst == me:                                         # arrived: hand it up
            inner = dataclasses.replace(f, src=net_src, dst=me, payload=bytes(f.payload[3:]),
                                        flags=f.flags & ~F_NET, link_src=f.src)
            self.node.deliver_local(inner, skip=self._on_frame)
            return True
        if not self.forward:
            return True
        ttl -= 1
        if ttl <= 0:                                              # like ICMP "time exceeded"
            self.stats["ttl_expired"] += 1
            self.bus.emit("ttl_expired", t=round(self.node.now(), 2), src=net_src, dst=net_dst, seq=f.seq)
            threading.Thread(target=self.send_routed, daemon=True, args=(
                Frame(T_TTLX, me, net_src, f.seq, bytes([f.type, f.seq])), net_src)).start()
            return True
        with self._lock:
            nh = self.dv.lookup(net_dst)
        if nh is None:
            self.stats["no_route"] += 1
            self.bus.emit("no_route", t=round(self.node.now(), 2), dst=net_dst)
            return True
        fwd = Frame(f.type, me, nh, f.seq, bytes([net_src, net_dst, ttl]) + bytes(f.payload[3:]), f.flags,
                    mcs=f.mcs)
        with self._lock:
            self.queue.append(fwd)
        self.bus.emit("forward_queued", t=round(self.node.now(), 2), type=f.name, seq=f.seq, src=net_src,
                      dst=net_dst, next_hop=nh)
        return True

    # ----------------------------------------------------------------- send --
    def wait_route(self, dest: int, timeout_s: float) -> bool:
        end = self.node.now() + timeout_s
        while self.node.now() < end and self._running:
            with self._lock:
                if self.dv.lookup(dest) is not None:
                    return True
            with self._route_cond:
                self._route_cond.wait(0.1)
        with self._lock:
            return self.dv.lookup(dest) is not None

    def send_routed(self, frame: Frame, dest: int, ttl: int = DEFAULT_TTL, wait: bool = True, mcs=None, **info):
        with self._lock:
            nh = self.dv.lookup(dest)
        if nh is None:
            self.stats["no_route"] += 1
            self.bus.emit("no_route", t=round(self.node.now(), 2), dst=dest)
            return None
        out = Frame(frame.type, self.node.addr, nh, frame.seq, bytes([self.node.addr, dest, ttl]) + bytes(frame.payload),
                    frame.flags | F_NET, mcs=frame.mcs)
        self._last_data = self.node.now()
        return self.node.send(out, wait=wait, mcs=mcs, net_dst=dest, next_hop=nh, **info)

    def _advertise(self):
        with self._lock:
            entries = self.dv.advert()
        payload = b"".join(bytes([d & 0xFF, min(c, INF), nh & 0xFF]) for d, c, nh in entries)
        self._seq = (self._seq + 1) % 256
        self.stats["adverts"] += 1
        self.bus.emit("route_advert", t=round(self.node.now(), 2), me=self.node.addr, entries=len(entries))
        self.node.send(Frame(T_ROUTE, self.node.addr, BROADCAST, self._seq, payload))

    # ----------------------------------------------------------------- loop --
    def _loop(self):
        node = self.node
        if node.noise_floor is None:
            node.measure_noise_floor(0.5)
        while self._running:
            node.sleep(0.1)
            now = node.now()
            with self._lock:
                dead = [n for n, t in self.neighbors.items() if now - t > self.timeout]
                for n in dead:
                    del self.neighbors[n]
                    if self.dv.neighbor_down(n, now):
                        self._changed(f"lost node {n} (not heard for {self.timeout:.0f} s)")
                self.dv.collect_garbage(now, 2 * self.period)
                queue_ready = bool(self.queue)
            quiet = now - self._last_traffic > self.idle_s and not node.backend.is_transmitting()
            if queue_ready and quiet and not node.channel_busy():
                with self._lock:
                    batch, self.queue = self.queue, []
                self._last_data = now
                for f in batch:                                    # store-and-forward the burst
                    node.send(f, mcs=f.mcs, forwarded=True)
                    self.stats["forwarded"] += 1
                    self.bus.emit("forward", t=round(node.now(), 2), me=node.addr, type=f.name, seq=f.seq,
                                  src=f.payload[0], dst=f.payload[1], next_hop=f.dst)
                continue
            triggered = self._trigger_at is not None and now >= self._trigger_at
            busy_with_data = now - self._last_data < 2 * self.period
            due = triggered or (now >= self._next_advert and not busy_with_data)
            if due and now - self._last_traffic > 1.5 and not queue_ready and not node.channel_busy():
                self._advertise()
                self._trigger_at = None
                self._next_advert = node.now() + self.period * random.uniform(0.5, 1.5)


class NetLink:
    """Looks like a Node to the upper layers, but every frame is routed (multi-hop)."""

    def __init__(self, node, router: Router, ttl: int = DEFAULT_TTL, route_wait_s: float = 60.0):
        self.node, self.router, self.ttl, self.route_wait_s = node, router, ttl, route_wait_s
        self.addr, self.bus, self.link_cfg, self.modem = node.addr, node.bus, node.link_cfg, node.modem
        self.default_mcs, self.fec = node.default_mcs, node.fec
        self.rt_latency_s = node.rt_latency_s
        self._burst_air = 0.0
        self._last_dest = None

    def now(self):
        return self.node.now()

    def sleep(self, s):
        self.node.sleep(s)

    def frame_time(self, payload_len, mcs=None):
        return self.node.frame_time(payload_len + 3, mcs)

    def hops_to(self, dest) -> int:
        c = self.router.dv.cost(dest)
        return c if c < INF else 1

    def ack_timeout(self, ack_len: int = 6) -> float:
        hops = self.hops_to(self._last_dest) if self._last_dest is not None else 1
        base = self.node.ack_timeout(ack_len + 3)
        per_hop = (self._burst_air + self.node.frame_time(ack_len + 3) + 2 * self.router.idle_s
                   + 2 * self.node.rt_latency_s + self.link_cfg.processing_margin_s)
        return base + (hops - 1) * per_hop

    def send(self, frame: Frame, at=None, wait=True, mcs=None, **info):
        dest = frame.dst
        if dest in (BROADCAST, self.addr):
            return self.node.send(frame, at=at, wait=wait, mcs=mcs, **info)
        if not self.router.wait_route(dest, self.route_wait_s):
            from .arq import LinkFailed
            raise LinkFailed(f"no route to node {dest}")
        m = mcs if mcs is not None else (frame.mcs if frame.mcs is not None else self.default_mcs)
        self._burst_air += self.frame_time(len(frame.payload), m)
        self._last_dest = dest
        return self.router.send_routed(frame, dest, ttl=self.ttl, wait=wait, mcs=m, **info)

    def recv(self, timeout, accept=None):
        f = self.node.recv(timeout, accept)
        self._burst_air = 0.0
        return f


def traceroute(netlink: NetLink, dest: int, max_hops: int = 6, bus=None):
    """Find the path to `dest` one hop at a time, by sending pings with TTL = 1, 2, 3 ..."""
    bus = bus or netlink.bus
    node, router = netlink.node, netlink.router
    if not router.wait_route(dest, netlink.route_wait_s):
        bus.emit("traceroute", t=round(node.now(), 2), src=node.addr, ttl=0, hop=None, note=f"no route to {dest}")
        return []
    path = []
    for ttl in range(1, max_hops + 1):
        seq = 100 + ttl
        resp = None
        for _ in range(3):                                   # two retries per hop (collisions happen)
            t0 = node.now()
            netlink._last_dest = dest
            netlink._burst_air = node.frame_time(4 + 3)
            router.send_routed(Frame(T_PING, node.addr, dest, seq, b"trce"), dest, ttl=ttl)
            resp = node.recv(netlink.ack_timeout(5) * ttl + 2.0, accept=lambda f, s=seq: f.dst == node.addr and (
                (f.type == T_TTLX and len(f.payload) >= 2 and f.payload[1] == s) or
                (f.type == T_PONG and f.seq == s and f.src == dest)))
            if resp is not None:
                break
        if resp is None:
            bus.emit("traceroute", t=round(node.now(), 2), src=node.addr, ttl=ttl, hop=None, rtt=None)
            path.append(None)
            continue
        rtt = node.now() - t0
        path.append(resp.src)
        bus.emit("traceroute", t=round(node.now(), 2), src=node.addr, ttl=ttl, hop=resp.src, rtt=round(rtt, 2),
                 reached=resp.type == T_PONG)
        if resp.type == T_PONG:
            break
        node.sleep(0.5)
    return path
