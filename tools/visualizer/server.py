"""HTTP server — serves visualizer + /events endpoint for live polling.

Endpoints:
  GET /                    → index.html (Neural Cloud, only page)
  GET /events              → JSON feed (live events + recent closes + stats)
  GET /stream/events       → SSE live event stream
  GET /static/*            → static files
  GET /static/graph.json   → auto-regenerated galaxy snapshot

Cloud-only mode (Jin 2026-04-27): dashboard/intel/trades pages archived to
tools/visualizer/_archive/. Routes /dashboard /intel /extra removed; any legacy
hit on those paths returns 404 (clean break).

Usage: python3 -m tools.visualizer.server
Browser: http://localhost:8765
"""
import http.server
import json
import socketserver
import threading
import time
from pathlib import Path

PORT = 8765
ROOT = Path(__file__).parent

# Jin 2026-04-28 — graph.json snapshot cache. build_galaxy() ~134ms 동기 작업.
# 매 fetch (60s) 마다 server thread 차단 + SSE 응답 지연 = 랙 원인.
# 5초 TTL cache + lock 으로 burst fetch (re-render / multi-tab) 보호.
_snapshot_cache = {"data": None, "ts": 0.0}
_snapshot_lock = threading.Lock()
_SNAPSHOT_TTL = 30.0  # build_galaxy 첫 cold ~3s — browser timeout 막기 위해 30s.


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _json(self, payload):
        data = json.dumps(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    # Jin 2026-04-27: static file 도 no-cache (browser jump fix)
    # Override end_headers to inject Cache-Control on every static response
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def do_GET(self):
        # P4: SSE stream — events only (no closes/stats — those are polling)
        if self.path.startswith("/stream/events"):
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                import time as _t
                from tools.visualizer.events import tail_log
                # Per-client cursor (no global state)
                client_seen = set()
                while True:
                    events = tail_log()
                    new = []
                    for ev in events:
                        key = f"{ev['type']}_{ev['ticker']}_{ev['ts']}"
                        if key not in client_seen:
                            client_seen.add(key)
                            new.append(ev)
                    if len(client_seen) > 500:
                        client_seen = set(list(client_seen)[-300:])
                    if new:
                        payload = json.dumps({"events": new})
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                    else:
                        # heartbeat every 10s
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    _t.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try: self.send_error(500, f"sse err: {e}")
                except Exception: pass
            return
        if self.path.startswith("/events"):
            try:
                from tools.visualizer.events import feed
                self._json(feed())
            except Exception as e:
                self.send_error(500, f"events err: {e}")
            return
        # Auto-regenerate galaxy snapshot before serving graph.json.
        # Jin 2026-04-28: cache TTL 5s — burst fetch (multi-tab / sub-second
        # reload) 가 build_galaxy 134ms 동기 작업을 반복 호출 안 함. lock 으로
        # cache miss 동시 호출 1회로 union (thundering-herd 방지).
        if self.path.startswith("/static/graph.json"):
            try:
                from tools.visualizer.snapshot import build_galaxy
                now = time.time()
                graph = None
                with _snapshot_lock:
                    cached = _snapshot_cache["data"]
                    cached_ts = _snapshot_cache["ts"]
                    if cached is not None and (now - cached_ts) < _SNAPSHOT_TTL:
                        graph = cached
                if graph is None:
                    # Cache miss — rebuild outside lock (only one thread will
                    # actually compute since lock above already returned None
                    # for this call; subsequent threads compute too but cache
                    # write is idempotent and last-writer-wins).
                    fresh = build_galaxy()
                    with _snapshot_lock:
                        _snapshot_cache["data"] = fresh
                        _snapshot_cache["ts"] = time.time()
                    graph = fresh
                self._json(graph)
                return
            except Exception as e:
                self.send_error(500, f"snapshot err: {e}")
            return
        # Cloud-only mode (Jin 2026-04-27): legacy pages archived → 404
        if self.path.rstrip("/") in ("/dashboard", "/intel", "/trades", "/extra") \
                or self.path in ("/dashboard.html", "/intel.html", "/trades.html"):
            self.send_error(404, "page archived (cloud-only mode)")
            return
        super().do_GET()

    def log_message(self, *_):  # silent
        pass


def _prewarm_cache():
    """Build initial snapshot before serving — avoids 3s cold-start
    timeouts on the first browser fetch."""
    try:
        from tools.visualizer.snapshot import build_galaxy
        t0 = time.time()
        with _snapshot_lock:
            _snapshot_cache["data"] = build_galaxy()
            _snapshot_cache["ts"] = time.time()
        print(f"   pre-warm cache built in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"   pre-warm failed: {e}")


def _bg_refresh_loop():
    """Background refresher — every 20s rebuilds the snapshot so cache
    is always fresh ahead of the next /static/graph.json fetch (60s
    interval from frontend)."""
    while True:
        time.sleep(20)
        try:
            from tools.visualizer.snapshot import build_galaxy
            data = build_galaxy()
            with _snapshot_lock:
                _snapshot_cache["data"] = data
                _snapshot_cache["ts"] = time.time()
        except Exception:
            pass


def main():
    import os
    os.chdir(ROOT.parent.parent)  # project root for sqlite path resolution
    _prewarm_cache()
    threading.Thread(target=_bg_refresh_loop, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        print(f"🧠 Invasion Live Visualizer — http://localhost:{PORT}")
        print(f"   /events polled by frontend every 1s")
        print(f"   Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
