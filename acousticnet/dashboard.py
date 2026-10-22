"""
dashboard.py - live web dashboard (works fully offline: Wi-Fi is OFF during the demo!).

A tiny web server built only from Python's standard library:
    GET  /          -> web/index.html (the page)
    GET  /events    -> Server-Sent Events: a never-ending stream of JSON events
    GET  /history   -> the last few hundred events (so a page refresh does not lose the log)
    POST /action/x  -> run a named action (only the simulator offers one: "noise")

It subscribes to the EventBus and also samples the microphone ~12 times per second to draw
the spectrogram ("waterfall").  Open http://127.0.0.1:8765 in any browser.
"""
import json
import os
import queue
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _jsonable(ev):
    return json.dumps(ev, default=lambda o: o.item() if hasattr(o, "item") else str(o))


class Dashboard:
    SPEC_MIN_HZ = 700          # show only the band we use, so the tone lanes are thick
    SPEC_MAX_HZ = 4000

    def __init__(self, bus, node=None, port: int = 8765, role: str = "", info: dict = None,
                 actions: dict = None):
        self.bus, self.node, self.port, self.role = bus, node, port, role
        self.info = info or {}
        self.actions = actions or {}
        self.clients = set()
        self.history = deque(maxlen=400)
        self._lock = threading.Lock()
        self._running = False
        self._floor_db = None
        self.httpd = None
        bus.subscribe(self._on_event)

    # ---------------------------------------------------------------- events --
    def _broadcast(self, ev, keep=True):
        msg = _jsonable(ev)
        if keep:
            self.history.append(msg)
        with self._lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def _on_event(self, ev):
        self._broadcast(ev, keep=ev.get("kind") not in ("spectrum", "status"))

    def hello(self):
        node = self.node
        d = {"kind": "hello", "role": self.role, "actions": list(self.actions), **self.info}
        if node is not None:
            c = node.modem.cfg
            d.update(addr=node.addr, baud=c.baud, f0=c.f0, f1=c.f1, fec=node.fec,
                     rt_latency_ms=round(node.rt_latency_s * 1000), spec_min_hz=self.SPEC_MIN_HZ,
                     spec_max_hz=self.SPEC_MAX_HZ)
        return d

    # --------------------------------------------------------- sampling loop --
    def _sampler(self):
        """One spectrogram column per BIT (hop = samples per symbol), so single bits are visible."""
        node = self.node
        fs = node.fs
        hop = node.modem.sps
        nfft = 1024
        win = np.hanning(hop)
        f = np.fft.rfftfreq(nfft, 1 / fs)
        band = (f >= self.SPEC_MIN_HZ) & (f <= self.SPEC_MAX_HZ)
        rx = node.backend.rx
        pos = rx.end
        last_status = 0.0
        while self._running:
            time.sleep(0.08)
            end = rx.end
            if end - pos > fs:                       # fell behind: skip ahead
                pos = end - fs // 4
            ncols = (end - pos) // hop
            if ncols <= 0:
                continue
            x = rx.read(pos, pos + ncols * hop)
            if len(x) < ncols * hop:
                pos = end
                continue
            pos += ncols * hop
            X = np.abs(np.fft.rfft(x.reshape(ncols, hop) * win, nfft, axis=1))[:, band]
            db = 20 * np.log10(X + 1e-9)
            floor = float(np.percentile(db, 30))
            self._floor_db = floor if self._floor_db is None else 0.97 * self._floor_db + 0.03 * floor
            vals = (np.clip((db - self._floor_db - 10) / 38.0, 0, 1) * 255).astype(int)
            rms = float(np.sqrt(np.mean(x ** 2)))
            self._broadcast({"kind": "spectrum", "cols": vals.tolist(),
                             "rms_db": round(20 * np.log10(rms + 1e-9), 1),
                             "tx": bool(node.backend.is_transmitting())}, keep=False)
            now = time.monotonic()
            if now - last_status > 0.5:
                last_status = now
                st = dict(node.stats)
                st.update(node.receiver.stats)
                ch = node.channel_level_db() if node.noise_floor else None
                self._broadcast({"kind": "status", "t": round(node.now(), 2), "stats": st,
                                 "glitches": node.backend.glitches,
                                 "channel_db": None if ch is None else round(float(ch), 1)}, keep=False)

    # ------------------------------------------------------------ http server --
    def _handler(self):
        dash = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    with open(os.path.join(WEB_DIR, "index.html"), "rb") as fh:
                        return self._send(200, fh.read(), "text/html; charset=utf-8")
                if self.path == "/history":
                    body = "[" + ",".join([_jsonable(dash.hello())] + list(dash.history)) + "]"
                    return self._send(200, body.encode(), "application/json")
                if self.path == "/events":
                    q = queue.Queue(maxsize=500)
                    with dash._lock:
                        dash.clients.add(q)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    try:
                        self.wfile.write(f"data: {_jsonable(dash.hello())}\n\n".encode())
                        while dash._running:
                            try:
                                msg = q.get(timeout=10)
                                self.wfile.write(f"data: {msg}\n\n".encode())
                            except queue.Empty:
                                self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        with dash._lock:
                            dash.clients.discard(q)
                    return
                self._send(404, b"not found", "text/plain")

            def do_POST(self):
                name = self.path.rsplit("/", 1)[-1]
                fn = dash.actions.get(name)
                if fn is None:
                    return self._send(404, b"no such action", "text/plain")
                threading.Thread(target=fn, daemon=True).start()
                dash.bus.emit("action", name=name)
                self._send(200, b"ok", "text/plain")

        return H

    def start(self, open_browser: bool = True):
        self._running = True
        handler = self._handler()
        for port in range(self.port, self.port + 20):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                self.port = port
                break
            except OSError:
                continue
        else:
            raise RuntimeError("no free port for the dashboard")
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="dashboard", daemon=True).start()
        if self.node is not None:
            threading.Thread(target=self._sampler, name="spectrum", daemon=True).start()
        url = f"http://127.0.0.1:{self.port}/"
        print(f"Dashboard: {url}", flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return url

    def stop(self):
        self._running = False
        if self.httpd:
            self.httpd.shutdown()
