"""HTTP server for the Polaris space visualizer (DEMO/PAPER, display-only).

Serves the reused Canvas-2D sphere engine and a periodically-regenerated
``graph.json`` built from the read-only dashboard snapshot. A minimal SSE
endpoint pushes ``entry``/``exit`` events derived from newly-observed fills so
the sphere animates trade activity. Nothing here touches sizing/risk/orders;
the visual is purely a window onto live paper-trading state.

Endpoints:
  GET /                    → index.html
  GET /static/*            → static assets (sphere-render.js, polaris.css)
  GET /static/graph.json   → regenerated snapshot (cached, TTL refresh)
  GET /stream/events       → SSE live entry/exit stream from new fills

Usage: python3 -m tools.visualizer.server --db data/polaris_live.sqlite --port 8770
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import http.server
import json
import os
import socketserver
import sqlite3

# The ANSI palette decides colour support once at import via sys.stdout.isatty().
# The server's stdout is a pipe (not a TTY) → it would import the palette with
# colour DISABLED, and the browser dashboard overlay would receive plain
# uncoloured rows. Force isatty() True for the duration of the colour-sensitive
# polaris imports so render_dashboard_v2 emits SGR codes; dashboard-overlay.js
# converts SGR → HTML in the browser. Display-only; stdout is restored after.
import sys as _sys
import threading
import time
from pathlib import Path
from typing import Any


class _ForceTTYStdout:
    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.__stdout__, name)

    def isatty(self) -> bool:
        return True


_saved_stdout = _sys.stdout
_sys.stdout = _ForceTTYStdout()
try:
    from polaris.scripts.dashboard.snapshot import collect_snapshot
    from polaris.scripts.dashboard_v2 import render_dashboard_v2
    from tools.visualizer.polaris_graph import build_graph
finally:
    _sys.stdout = _saved_stdout

ROOT = Path(__file__).parent

# Module-level runtime config (set in main()).
_DB_PATH = Path("data/polaris_live.sqlite")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load ``.env`` into ``os.environ`` (existing keys win), best-effort.

    Mirrors ``production_paper_loop._load_dotenv``. The server is started
    detached (no shell-exported env), so without this the read-only Alpaca
    account probe in ``snapshot_queries`` would never see the paper keys and the
    Alpaca lane would render $0. Display-only; never used for orders. Secrets are
    placed in the process env only and are never logged.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

# graph.json snapshot cache — build is a few-ms SQLite read; a short TTL keeps
# multi-tab / sub-second reloads from re-querying on every fetch.
_snapshot_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_snapshot_lock = threading.Lock()
_SNAPSHOT_TTL = 1.0  # 1s — match the board's 1s poll for live refresh


def _fresh_graph() -> dict[str, Any]:
    # Serve-stale-while-revalidate: the single _bg_refresh_loop owns (re)building,
    # so a request thread NEVER triggers build_graph (that pile-up under the 1s/2s
    # polls is what inflated a sub-second build to 60s). Return the warm cache
    # instantly; only build inline on a cold start (cache still None pre-warm).
    data: dict[str, Any] | None = _snapshot_cache["data"]
    if data is not None:
        return data
    fresh = build_graph(_DB_PATH)
    with _snapshot_lock:
        if _snapshot_cache["data"] is None:
            _snapshot_cache["data"] = fresh
            _snapshot_cache["ts"] = time.time()
        return _snapshot_cache["data"]


_dash_cache: dict[str, Any] = {"rows": None, "ts": 0.0}
_dash_lock = threading.Lock()


def _fresh_dashboard() -> list[str]:
    """dashboard_v2 ANSI rows — served from the bg-refreshed cache (serve-stale)."""
    rows: list[str] | None = _dash_cache["rows"]
    if rows is not None:
        return rows
    fresh = render_dashboard_v2(collect_snapshot(_DB_PATH))
    with _dash_lock:
        if _dash_cache["rows"] is None:
            _dash_cache["rows"] = fresh
            _dash_cache["ts"] = time.time()
        return _dash_cache["rows"]


_snap_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_snap_lock = threading.Lock()


def _fresh_snapshot() -> dict[str, Any]:
    """DashboardSnapshot JSON dict (native web board) — served from bg cache."""
    data: dict[str, Any] | None = _snap_cache["data"]
    if data is not None:
        return data
    fresh = dataclasses.asdict(collect_snapshot(_DB_PATH))
    with _snap_lock:
        if _snap_cache["data"] is None:
            _snap_cache["data"] = fresh
            _snap_cache["ts"] = time.time()
        return _snap_cache["data"]


def _resolve_bot_log() -> Path | None:
    """Newest LIVE bot runtime log under data/paper/.

    Globs every ``*.log`` (the live bot may write any name, e.g.
    ``production_overhaul_*.log``) and picks the most-recently-modified, excluding
    non-bot logs (server/dashboard stdout, stderr, diag). The previous glob only
    matched ``collect_*``/``polaris_runtime*``, so it tailed a stale dead-process
    log while the live bot wrote elsewhere — the dashboard log pane showed no live
    activity (Jin: logs must surface at every point)."""
    paper = Path("data/paper")
    if not paper.is_dir():
        return None
    cands = [
        p for p in paper.glob("*.log")
        if not p.name.endswith(("_stdout.log", "dashboard.log", "dashboard_e4.log"))
        and "diag" not in p.name and "stderr" not in p.name
    ]
    cands.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return cands[0] if cands else None


def _tail_botlog(n: int = 320) -> list[str]:
    """Last n lines of the live bot log (seek-based tail, read-only).

    Larger tail (default 320) so the left-column log pane is a genuine
    debugging surface — all levels preserved, the client colorizes
    ERROR/WARN/INFO. ``_resolve_bot_log`` always returns the NEWEST runtime log
    so a fresh session is picked up automatically. Block grows with ``n`` so a
    deeper tail is not truncated mid-window.
    """
    log = _resolve_bot_log()
    if log is None or not log.exists():
        return []
    try:
        with log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            # ~600B/line heuristic with headroom so n lines always fit the read.
            block = min(size, max(400_000, n * 600))
            fh.seek(size - block)
            data = fh.read(block)
        lines = data.decode("utf-8", "replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def _latest_fill_ts() -> int:
    """Max fill ts_ms currently in the DB, or 0 if unavailable."""
    if not _DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT MAX(ts_ms) FROM fills").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _fills_since(since_ms: int) -> list[dict[str, Any]]:
    """New fills (ts_ms > since_ms) as SSE entry/exit events."""
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT venue, instrument_id, strategy_id, side, pnl_usd, "
                "is_close, ts_ms FROM fills WHERE ts_ms > ? ORDER BY ts_ms ASC",
                (since_ms,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    events: list[dict[str, Any]] = []
    for venue, instrument_id, strategy_id, side, pnl_usd, is_close, ts_ms in rows:
        ticker = str(instrument_id).split(":")[-1].split("-")[0]
        direction = "long" if str(side).lower() in ("buy", "long") else "short"
        if int(is_close):
            events.append(
                {
                    "type": "exit",
                    "ticker": ticker,
                    "direction": direction,
                    "exit_type": "EXIT",
                    "pnl_usd": float(pnl_usd or 0.0),
                    "pnl_pct": 0.0,
                    "exchange": str(venue)[:3].lower(),
                    "ts": int(ts_ms),
                }
            )
        else:
            events.append(
                {
                    "type": "entry",
                    "ticker": ticker,
                    "direction": direction,
                    "strategy_id": str(strategy_id),
                    "exchange": str(venue)[:3].lower(),
                    "ts": int(ts_ms),
                }
            )
    return events


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _json(self, payload: Any) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 (stdlib casing)
        if self.path.startswith("/stream/events"):
            self._serve_sse()
            return
        if self.path.startswith("/static/graph.json"):
            try:
                self._json(_fresh_graph())
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"snapshot err: {exc}")
            return
        if self.path.startswith("/api/botlog"):
            try:
                self._json({"lines": _tail_botlog(320), "ts": time.time()})
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"botlog err: {exc}")
            return
        if self.path.startswith("/api/snapshot"):
            try:
                self._json(_fresh_snapshot())
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"snapshot err: {exc}")
            return
        if self.path.startswith("/api/dashboard"):
            try:
                self._json({"rows": _fresh_dashboard(), "ts": time.time()})
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"dashboard err: {exc}")
            return
        super().do_GET()

    def _serve_sse(self) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            super(http.server.SimpleHTTPRequestHandler, self).end_headers()
            cursor = _latest_fill_ts()
            while True:
                events = _fills_since(cursor)
                if events:
                    cursor = max(int(e["ts"]) for e in events)
                    payload = json.dumps({"events": events})
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                else:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 — best-effort stream
            pass

    def log_message(self, *_: Any) -> None:  # silent
        pass


def _bg_refresh_loop() -> None:
    """Single owner of the expensive snapshot build. Computes ``collect_snapshot``
    ONCE per cycle and fans it out to all three read-model caches (graph json /
    snapshot dict / dashboard_v2 rows) so request threads only ever serve a warm
    cache. This removes the concurrent-rebuild pile-up that, under the board's 1s
    poll + globe's 2s poll, turned a sub-second build into 60s+ of lock/CPU/SQLite
    contention. Cadence ≈ build_time + 1s → near-live; never blocks a request."""
    while True:
        try:
            snap = collect_snapshot(_DB_PATH)
            graph = build_graph(_DB_PATH, snapshot=snap)
            snap_dict = dataclasses.asdict(snap)
            dash_rows = render_dashboard_v2(snap)
            now = time.time()
            with _snapshot_lock:
                _snapshot_cache["data"] = graph
                _snapshot_cache["ts"] = now
            with _snap_lock:
                _snap_cache["data"] = snap_dict
                _snap_cache["ts"] = now
            with _dash_lock:
                _dash_cache["rows"] = dash_rows
                _dash_cache["ts"] = now
        except Exception:  # noqa: BLE001 — display-only
            pass
        time.sleep(1.0)


def main() -> None:
    global _DB_PATH
    parser = argparse.ArgumentParser(description="Polaris space visualizer server")
    parser.add_argument("--db", default="data/polaris_live.sqlite")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    _DB_PATH = Path(args.db)

    # Load .env so the read-only Alpaca account probe can see the paper keys.
    _load_dotenv()

    # Prewarm so the first browser fetch is instant.
    with contextlib.suppress(Exception):
        _fresh_graph()
    threading.Thread(target=_bg_refresh_loop, daemon=True).start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", args.port), Handler) as httpd:
        print(f"Polaris space visualizer — http://localhost:{args.port}")
        print(f"  db={_DB_PATH}  (DEMO/PAPER, display-only)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
