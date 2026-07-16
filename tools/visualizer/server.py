"""HTTP server for the Polaris space visualizer (DEMO/PAPER, display-only).

Serves the reused Canvas-2D sphere engine and a periodically-regenerated
``graph.json`` built from the read-only dashboard snapshot. A minimal SSE
endpoint pushes ``entry``/``exit`` events derived from newly-observed fills so
the sphere animates trade activity. Nothing here touches sizing/risk/orders;
the visual is purely a window onto live paper-trading state.

Endpoints:
  GET /                    → index.html
  GET /flow                → flow.html (gate+AI pipeline river, desktop-only)
  GET /static/*            → static assets (sphere-render.js, polaris.css)
  GET /static/graph.json   → regenerated snapshot (cached, TTL refresh)
  GET /api/flow_stats      → rolling-1h gate volume + drop/AI stats (5s TTL)
  GET /stream/events       → SSE live entry/exit stream from new fills
  GET /stream/prices       → SSE per-cell live-mark push (changed cells only)

Usage: python3 -m tools.visualizer.server --db data/polaris_live.sqlite --port 8770
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import http.server
import json
import os
import re
import socketserver
import sqlite3
import subprocess

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
# Repo root for the read-only reference endpoints (buildlog/roadmap/lessons).
# tools/visualizer/server.py → parents[2] == repo root. Absolute reads only;
# every reference file is resolved against this so a stray cwd never matters.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _git_sha() -> str:
    """Short HEAD SHA of the running server's checkout, ``unknown`` on failure.

    Computed once at import (the server is restarted, not hot-reloaded, on
    deploy) so a stale-server-vs-new-code drift is detectable by comparing
    this against ``git rev-parse --short HEAD`` from the caller's shell
    (P0-1: start_dashboard.sh auto-restart-on-mismatch).
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else "unknown"
    except Exception:
        return "unknown"


_GIT_SHA = _git_sha()
_BOOT_TS = time.time()

# Module-level runtime config (set in main()).
_DB_PATH = Path("data/polaris_live.sqlite")
_SENTINEL_DB_PATH = Path("data/sentinel.sqlite")

# Phone detection for auto-routing "/" → the purpose-built single-column page.
# iPad/tablets deliberately excluded (they report desktop Safari and the 70% board
# is usable at tablet width); ?desktop=1 forces the full board on any device.
_MOBILE_UA_RE = re.compile(
    r"Mobi|Android|iPhone|iPod|IEMobile|BlackBerry|Opera Mini", re.IGNORECASE
)


def _is_mobile_ua(ua: str) -> bool:
    return bool(_MOBILE_UA_RE.search(ua))


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
    """DashboardSnapshot JSON dict (native web board) — served from bg cache.

    ``meta`` (git_sha + boot_ts) is stamped on every request rather than baked
    into the cached dict — it's server-identity, not DB-derived state, and
    must never go stale behind the 1s snapshot cache TTL.
    """
    data: dict[str, Any] | None = _snap_cache["data"]
    if data is None:
        fresh = dataclasses.asdict(collect_snapshot(_DB_PATH))
        with _snap_lock:
            if _snap_cache["data"] is None:
                _snap_cache["data"] = fresh
                _snap_cache["ts"] = time.time()
            data = _snap_cache["data"]
    return {**data, "meta": {"git_sha": _GIT_SHA, "boot_ts": _BOOT_TS}}


# ── Live venue price overlay (Jin 2026-07-11 "가격 실시간으로") ─────────────
# The bot persists marks on its own cadence (seconds+), which capped the
# /stream/prices tick rate. This DISPLAY-ONLY poller fetches OKX public spot
# tickers (one unauthenticated request/s for ALL symbols, us.okx.com) and
# overlays the freshest price onto the open-position cells; uPnL/Δ% are
# recomputed linearly from the row's entry/size. No DB write, no bot contact,
# no order/sizing surface. Disable with POLARIS_DASH_LIVE_PX=0.
_LIVE_PX_ENABLED = os.environ.get("POLARIS_DASH_LIVE_PX", "1") != "0"
_live_px: dict[str, tuple[float, float]] = {}
_live_px_lock = threading.Lock()


def _okx_px_loop() -> None:
    import urllib.request

    # default urllib UA gets 403 from OKX — a UA header is required
    req = urllib.request.Request(
        "https://us.okx.com/api/v5/market/tickers?instType=SPOT",
        headers={"User-Agent": "Mozilla/5.0 (Polaris-dash display-only)"},
    )
    while True:
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                rows = json.loads(r.read().decode()).get("data", [])
            now_m = time.time()
            with _live_px_lock:
                for t in rows:
                    px = t.get("last")
                    inst = t.get("instId")
                    if px and inst:
                        _live_px[str(inst)] = (float(px), now_m)
        except Exception:  # noqa: BLE001 — display-only, retry after backoff
            time.sleep(4)
        time.sleep(1.0)


def _price_marks() -> dict[str, dict[str, Any]]:
    """Per-open-position live-mark cells, keyed ``venue|symbol|strategy|side``.

    Display-only, READ-ONLY: reads the warm snapshot cache (``_snap_cache``,
    kept current by ``_bg_refresh_loop`` — the bot holds live WS marks so its
    open-position price/uPnL/Δ% are the freshest available) and returns ONLY the
    four price cells the board flashes. No DB query, no sizing / gate / trade
    logic. Key matches the board's per-row flash key (board_tabs renderPositions
    + board.js applyPrices). Used by the ``/stream/prices`` SSE diff loop."""
    data: dict[str, Any] | None = _snap_cache["data"]
    if data is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in data.get("positions", []) or []:
        key = "|".join(
            str(p.get(k, "")) for k in ("venue", "symbol", "strategy_id", "side")
        )
        cell = {
            "key": key,
            "last_price": p.get("last_price"),
            "upnl_usd": p.get("upnl_usd"),
            "delta_pct": p.get("delta_pct"),
            "upnl_pct": p.get("upnl_pct"),
        }
        # live overlay (display-only): freshest public OKX mark wins
        if _LIVE_PX_ENABLED and str(p.get("venue", "")).startswith("okx"):
            with _live_px_lock:
                lv = _live_px.get(str(p.get("symbol", "")))
            entry = p.get("entry_price")
            if lv and entry:
                live_price = lv[0]
                sign = -1.0 if str(p.get("side", "")).startswith("s") else 1.0
                delta = (live_price / float(entry) - 1.0) * 100.0 * sign
                size = float(p.get("size_usd") or 0.0)
                cell["last_price"] = live_price
                cell["delta_pct"] = delta
                cell["upnl_pct"] = delta
                cell["upnl_usd"] = size * delta / 100.0
        out[key] = cell
    return out


def _ticker_chart(path: str) -> dict[str, Any]:
    """``/api/ticker_chart`` payload — OHLCV bars + technical series (read-only).

    ``?list=symbols`` returns the venue-grouped selector list; otherwise
    ``?venue=&symbol=&resolution=`` returns the bars + indicator series for one
    ticker. A missing DB / unknown symbol / unknown resolution yields a graceful
    empty payload (never an exception that would break the board poll). Opens a
    fresh read-only SQLite connection per request — user-driven, not 1s-polled,
    so no cache is needed; nothing here reads or writes sizing/risk/orders.
    """
    import urllib.parse

    from polaris.storage.schema_marketdata import marketdata_db_path_for
    from tools.visualizer import chart_data

    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if not _DB_PATH.exists():
        return {"available": False, "groups": [], "bars": [], "indicators": {}}
    # Storage-split (2026-07-14): ``bars`` is marketdata-domain — a SECOND
    # read-only conn against the sibling marketdata DB feeds both the symbol
    # selector and the OHLCV series; ``conn`` (trading) still resolves
    # ``universe.name`` for the selector's human-readable labels.
    md_path = marketdata_db_path_for(_DB_PATH)
    if not md_path.exists():
        return {"available": False, "groups": [], "bars": [], "indicators": {}}
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    md_conn = sqlite3.connect(f"file:{md_path}?mode=ro", uri=True)
    try:
        if q.get("list", [""])[0] == "symbols":
            return {
                "groups": chart_data.list_chart_symbols(md_conn, names_conn=conn)
            }
        venue = q.get("venue", [""])[0]
        symbol = q.get("symbol", [""])[0]
        resolution = q.get("resolution", ["1m"])[0]
        return chart_data.build_ticker_chart(
            md_conn, venue=venue, symbol=symbol, resolution=resolution
        )
    finally:
        conn.close()
        md_conn.close()


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
    """New fills (ts_ms > since_ms) as SSE entry/exit events.

    Exit events carry ``strategy_id`` + ``r_multiple`` (the visual-wall kill
    feed's "strategy ▸ SYM +0.09R" line) — ``r_multiple`` is the SAME
    canonical risk-unit R the ticker/strategy dashboard panels use
    (``positions.pnl_r`` via ``fills.contribution_id == positions.position_id``,
    same lookup shape as ``snapshot_sections._pnl_r_by_contribution``). A fill
    with no matched closed position (legacy/smoke data) reports ``None`` —
    the client falls back to a $-only kill-feed line rather than a fake R.
    """
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT f.venue, f.instrument_id, f.strategy_id, f.side, "
                "f.pnl_usd, f.is_close, f.ts_ms, p.pnl_r "
                "FROM fills f LEFT JOIN positions p "
                "ON p.position_id = f.contribution_id AND p.status = 'closed' "
                "WHERE f.ts_ms > ? ORDER BY f.ts_ms ASC",
                (since_ms,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    events: list[dict[str, Any]] = []
    for venue, instrument_id, strategy_id, side, pnl_usd, is_close, ts_ms, pnl_r in rows:
        ticker = str(instrument_id).split(":")[-1].split("-")[0]
        direction = "long" if str(side).lower() in ("buy", "long") else "short"
        if int(is_close):
            events.append(
                {
                    "type": "exit",
                    "ticker": ticker,
                    "direction": direction,
                    "exit_type": "EXIT",
                    "strategy_id": str(strategy_id),
                    "pnl_usd": float(pnl_usd or 0.0),
                    "pnl_pct": 0.0,
                    "r_multiple": float(pnl_r) if pnl_r is not None else None,
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


# Gate-decision SSE channel — newest per-gate DECISIONS pushed between the 1s
# board polls for the "smooth realtime, no lag" feel. Volume guard mirrors the
# snapshot feed: the repetitive steady-state G6/G7 HOLD (flow_not_block) is NOT
# streamed (it would drown the meaningful G5 size / G6 ADJUST·EXIT·SWAP / G7
# ADJUST·EXIT / G8 lesson decisions) — those HOLDs are tallied in the per-gate
# summary instead. Display-only; never reads/writes trading state.
_GATE_SSE_LABELS: dict[int, str] = {
    1: "Universe", 2: "Strategy", 3: "Validator", 4: "PreEntry",
    5: "Sizer", 6: "Monitor", 7: "Exit", 8: "Reflector",
}
_GATE_SSE_TICK_CAP = 30


def _latest_gate_event_ts() -> int:
    """Max gate_events.created_ts currently in the DB, or 0 if unavailable."""
    if not _DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT MAX(created_ts) FROM gate_events").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _gate_events_since(since_s: int) -> list[dict[str, Any]]:
    """New notable gate decisions (created_ts > since_s) as SSE feed rows.

    Same enrichment as the snapshot feed (symbol/strategy back-fill + per-gate
    ``detail``), oldest→newest so the client appends in order. Repetitive G6/G7
    HOLD is excluded (volume guard). Capped per tick. Read-only."""
    if not _DB_PATH.exists():
        return []
    try:
        from polaris.scripts.dashboard.snapshot_queries import (  # local: avoid web-import cost at boot
            _payload_detail,
            _payload_strategy_symbol_reason,
        )
    except Exception:  # noqa: BLE001 — display-only stream
        return []
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                """SELECT ge.gate_id, ge.decision, ge.payload_json, ge.created_ts,
                          p.symbol, p.strategy_id, s.instrument_id, s.strategy_id
                   FROM gate_events ge
                   LEFT JOIN positions p ON p.position_id = ge.position_id
                   LEFT JOIN signals s ON s.signal_id = ge.signal_id
                   WHERE ge.decision IS NOT NULL AND ge.created_ts > ?
                     AND NOT (ge.gate_id IN (6, 7) AND ge.decision = 'HOLD')
                   ORDER BY ge.created_ts ASC
                   LIMIT ?""",
                (since_s, _GATE_SSE_TICK_CAP),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for gid_raw, dec, payload, ts, p_sym, p_strat, s_inst, s_strat in rows:
        gid = int(gid_raw or 0)
        strategy, symbol, reason = _payload_strategy_symbol_reason(payload)
        symbol = symbol or str(p_sym or s_inst or "")[:18]
        strategy = strategy or str(p_strat or s_strat or "")[:24]
        out.append(
            {
                "gate_id": gid,
                "label": _GATE_SSE_LABELS.get(gid, f"G{gid}"),
                "decision": str(dec or "").upper(),
                "strategy": strategy,
                "symbol": symbol,
                "reason": reason,
                "ts": int(ts or 0),
                "detail": _payload_detail(gid, payload),
            }
        )
    return out


def _sentinel_payload() -> dict[str, Any]:
    """Active sentinel findings + recent heartbeat (sentinel.sqlite, ro).

    Display-only window onto the W1 Sentinel sidecar output. The sentinel DB
    is SEPARATE from the live bot DB; absence simply means the sidecar has
    not run yet (``available: false`` — never an error).
    """
    if not _SENTINEL_DB_PATH.exists():
        return {"available": False, "findings": [], "runs": [], "ts": time.time()}
    try:
        conn = sqlite3.connect(f"file:{_SENTINEL_DB_PATH}?mode=ro", uri=True)
        try:
            frows = conn.execute(
                "SELECT check_id, severity, subject, summary, detail_json, "
                "first_ts, last_ts FROM sentinel_findings WHERE status='active' "
                "ORDER BY CASE severity WHEN 'critical' THEN 0 "
                "WHEN 'warn' THEN 1 ELSE 2 END, last_ts DESC LIMIT 200"
            ).fetchall()
            rrows = conn.execute(
                "SELECT ts, checks_run, findings_active, duration_ms "
                "FROM sentinel_runs ORDER BY ts DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"available": False, "findings": [], "runs": [], "ts": time.time()}
    findings = [
        {
            "check_id": str(c), "severity": str(sev), "subject": str(subj),
            "summary": str(summ), "detail_json": str(dj),
            "first_ts": int(f_ts), "last_ts": int(l_ts),
        }
        for c, sev, subj, summ, dj, f_ts, l_ts in frows
    ]
    runs = [
        {
            "ts": int(ts), "checks_run": int(cr),
            "findings_active": int(fa), "duration_ms": int(dm),
        }
        for ts, cr, fa, dm in rrows
    ]
    return {"available": True, "findings": findings, "runs": runs, "ts": time.time()}


# ── Flow page (display-only) ─────────────────────────────────────────────────
# /api/flow_stats feeds the /flow "Living Pipeline River" page: rolling-1h
# per-gate decision volume + venue breakdown, drop-lane reasons, AI-judge shadow
# mismatches/verdicts, and the signals→sized→fills conversion summary. Same
# serve-stale pattern as buildlog/weekend — the SQLite scan runs on the ref loop
# cadence, never inside a request thread. Read-only; nothing here feeds sizing/
# risk/orders.
_flow_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_flow_lock = threading.Lock()
# Jin 2026-07-10 (feat/flat-neural-map, "전부 리얼타임 싱크"): 30s -> 5s so the
# /flow page's gate/strategy counts read as live (end-to-end <=5s including the
# client poll). Re-measured 2026-07-10 post-graveyard-DROP: ~45ms/build warm
# over the live DB — a 1s cadence is <5% duty (Jin: "모든 리프래쉬 1초 기준").
_FLOW_TTL = 1.0


def _build_flow_stats() -> dict[str, Any]:
    """``flow_data.build_flow_stats`` over a fresh ?mode=ro connection."""
    from tools.visualizer import flow_data

    empty = {
        "window_sec": flow_data.FLOW_WINDOW_SEC, "stages": [], "fills": {},
        "drops": [], "shadow_mismatch_n": 0, "ai_calls_n": 0,
        "verdicts_recent": [], "summary": {},
    }
    if not _DB_PATH.exists():
        return {**empty, "ts": time.time()}
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            data = flow_data.build_flow_stats(conn, now_s=int(time.time()))
        finally:
            conn.close()
    except sqlite3.Error:
        return {**empty, "ts": time.time()}
    return {**data, "ts": time.time()}


# ── Reference endpoints (display-only) ──────────────────────────────────────
# /api/buildlog · /api/roadmap · /api/lessons read git + vault/plans/CLAUDE.md.
# These are SLOWER than the SQLite snapshot and MUST NOT run inside a request
# thread: a per-request `git log` under the board poll is exactly the kind of
# pile-up that once inflated a sub-second build to 60s and contended with the
# `_bg_refresh_loop` SQLite work. So each has its own cache + lock, is built by
# the dedicated `_ref_refresh_loop` on a slow cadence, and request handlers only
# ever serve the warm value (serve-stale). Missing files → graceful empty payload.
_buildlog_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_buildlog_lock = threading.Lock()
_BUILDLOG_TTL = 30.0

_roadmap_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_roadmap_lock = threading.Lock()
_ROADMAP_TTL = 60.0

_lessons_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_lessons_lock = threading.Lock()
_LESSONS_TTL = 60.0

_SUBJECT_TYPE_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?!?:")
# Test count = 'suite NNNN green'; the log also uses the Korean '스위트 NNNN green'
# (the newest entries), so match either spelling to surface the latest count.
_SUITE_GREEN_RE = re.compile(r"(?:suite|스위트)\s+([\d,]+)\s+green", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _commit_type(subject: str) -> str:
    """Conventional-commit prefix (feat/fix/docs/…) or 'other'."""
    m = _SUBJECT_TYPE_RE.match(subject.strip())
    return m.group(1) if m else "other"


def _build_buildlog() -> dict[str, Any]:
    """git log (last 60) + vault/log.md tail + commits/day heat + test count.

    git is invoked here (background thread only, never a request thread).
    Read-only: ``git log`` against the repo with no working-tree mutation.
    """
    commits: list[dict[str, Any]] = []
    try:
        out = subprocess.run(
            ["git", "log", "-60", "--pretty=format:%H%x1f%cI%x1f%s"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            h, date, subject = parts
            commits.append({
                "hash": h[:8], "date": date,
                "type": _commit_type(subject), "subject": subject,
            })
    except (OSError, subprocess.SubprocessError):
        commits = []

    # commits/day heat — count by calendar day (YYYY-MM-DD prefix of ISO date).
    heat: dict[str, int] = {}
    for c in commits:
        day = str(c["date"])[:10]
        if day:
            heat[day] = heat.get(day, 0) + 1
    heat_list = [{"day": d, "count": n} for d, n in sorted(heat.items())]

    # vault/log.md tail (~40 lines) + newest 'suite NNNN green' test count.
    log_tail: list[str] = []
    test_count: int | None = None
    log_path = REPO_ROOT / "vault" / "log.md"
    if log_path.exists():
        with contextlib.suppress(OSError):
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = lines[-40:]
            # Newest test count = last suite-green match scanning bottom-up.
            for raw in reversed(lines):
                m = _SUITE_GREEN_RE.search(raw)
                if m:
                    test_count = int(m.group(1).replace(",", ""))
                    break

    return {
        "commits": commits, "log_tail": log_tail, "heat": heat_list,
        "test_count": test_count, "ts": time.time(),
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front-matter → flat str map (top-level ``key: value`` only)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            k = k.strip()
            if k and not k.startswith("-"):
                fm[k] = v.strip().strip("[]").strip()
    return fm


def _first_heading_title(text: str) -> str:
    """First markdown H1 (``# …``), front-matter stripped, else ''."""
    body = _FRONTMATTER_RE.sub("", text, count=1)
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def _build_roadmap() -> dict[str, Any]:
    """structural_roadmap phases (P0-P6 + DONE/BUILD/DEBATE) + plan kanban + NEXT.

    Read-only absolute reads under REPO_ROOT; missing files → empty sections.
    """
    plans_dir = REPO_ROOT / ".claude" / "plans"

    # 1) Phase ladder from structural_roadmap — group bullets under each '## Pn …'.
    phases: list[dict[str, Any]] = []
    # Latest structural_roadmap_*.md wins (was a hardcoded 06-22 filename — the
    # ROADMAP tab silently served a 3-week-stale plan; glob kills that class).
    rm_candidates = sorted(plans_dir.glob("structural_roadmap_*.md"))
    rm_path = rm_candidates[-1] if rm_candidates else plans_dir / "structural_roadmap_2026-06-22.md"
    if rm_path.exists():
        with contextlib.suppress(OSError):
            cur: dict[str, Any] | None = None
            for raw in rm_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.rstrip()
                if line.startswith("## "):
                    cur = {"phase": line[3:].strip(), "items": []}
                    phases.append(cur)
                elif cur is not None and line.lstrip().startswith("- "):
                    item = line.lstrip()[2:].strip()
                    tags = [
                        t for t in ("DONE", "BUILD", "DEBATE", "DECISION")
                        if t in item
                    ]
                    cur["items"].append({"text": item, "tags": tags})
            phases = [p for p in phases if p["items"]]

    # 2) Plan kanban — front-matter status per plan (no front-matter → 'untracked').
    plans: list[dict[str, str]] = []
    if plans_dir.is_dir():
        for p in sorted(plans_dir.glob("*.md")):
            status = "untracked"
            with contextlib.suppress(OSError):
                fm = _parse_frontmatter(
                    p.read_text(encoding="utf-8", errors="replace")
                )
                status = fm.get("status", "untracked")
            plans.append({"name": p.stem, "status": status})

    # 3) NEXT order from loop_state.md — capture the '## ▶ NEXT 순서' block.
    next_lines: list[str] = []
    ls_path = REPO_ROOT / ".claude" / "loop_state.md"
    if ls_path.exists():
        with contextlib.suppress(OSError):
            in_next = False
            for raw in ls_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if raw.startswith("## ") and "NEXT" in raw:
                    in_next = True
                    continue
                if in_next:
                    if raw.startswith("## "):
                        break
                    if raw.strip():
                        next_lines.append(raw.strip())

    return {"phases": phases, "plans": plans, "next": next_lines, "ts": time.time()}


def _lesson_first_takeaway(text: str) -> str:
    """First non-empty bullet/paragraph after the H1 — the lesson takeaway."""
    body = _FRONTMATTER_RE.sub("", text, count=1)
    seen_h1 = False
    for raw in body.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("# "):
            seen_h1 = True
            continue
        if not seen_h1:
            continue
        if s.startswith("#"):  # next heading before any prose
            continue
        return s.lstrip("-* ").strip()
    return ""


def _collect_md_entries(dir_path: Path, kind: str) -> list[dict[str, str]]:
    """title (front-matter or H1) + first takeaway for each *.md in dir_path."""
    entries: list[dict[str, str]] = []
    if not dir_path.is_dir():
        return entries
    for p in sorted(dir_path.glob("*.md")):
        if p.name.startswith("MOC-"):  # map-of-content index, not a lesson
            continue
        with contextlib.suppress(OSError):
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            title = fm.get("title") or _first_heading_title(text) or p.stem
            entries.append({
                "kind": kind, "name": p.stem, "title": title,
                "status": fm.get("status", ""),
                "takeaway": _lesson_first_takeaway(text),
            })
    return entries


def _build_lessons() -> dict[str, Any]:
    """lessons + forensic feeds + CLAUDE.md anti-pattern wall. Read-only."""
    research = REPO_ROOT / "vault" / "50_research"
    lessons = _collect_md_entries(research / "lessons", "lesson")
    forensic = _collect_md_entries(research / "forensic", "forensic")

    # Anti-pattern wall — bullets under '## Anti-pattern' in project CLAUDE.md.
    anti: list[str] = []
    claude_md = REPO_ROOT / "CLAUDE.md"
    if claude_md.exists():
        with contextlib.suppress(OSError):
            in_sec = False
            for raw in claude_md.read_text(encoding="utf-8", errors="replace").splitlines():
                if raw.startswith("## "):
                    in_sec = raw.startswith("## Anti-pattern")
                    continue
                if in_sec and raw.lstrip().startswith("- "):
                    anti.append(raw.lstrip()[2:].strip())

    return {
        "lessons": lessons, "forensic": forensic,
        "anti_patterns": anti, "ts": time.time(),
    }


# ── AI activity (display-only) ──────────────────────────────────────────────
# /api/ai_activity — THREE separate "AI" populations that Jin flagged as
# confusingly mixed into one tab (2026-07-10):
#   1. bot_ai       — the BOT's own runtime OpenAI GPT usage: gate_events
#                      model_used + the #32 AI-judge overlay (G3/G4/G7,
#                      non-blocking) + entry_admission_shadow + the legacy
#                      dev-debates/probe-escalation feed.
#   2. harness_ai   — THIS Claude Code harness (dev-time only) — never the
#                      bot's LLM. data/runtime/model_usage_ledger.json +
#                      vault/log.md wave tail.
#   3. cowork_intel — the cowork watchlist-intel seed feed (Alpaca
#                      candidates) + its expiry status + cohort fire rate.
# The in-loop trading DECISION is still a hard-0-GPT-calls deterministic core
# (W3 AI-free cutover, commit aafb635: tick entry / G3 / G4 / G7 primary
# decisions) — the #32 judge is a NON-BLOCKING overlay that never changes
# that decision, only annotates/measures it. Pure read-only display; never
# reads/writes sizing/risk/orders. Its own cache + the ref loop.
_ai_activity_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_ai_activity_lock = threading.Lock()
_AI_ACTIVITY_TTL = 30.0

_PROBE_DB_PATH = Path("data/probes.sqlite")
_MODEL_LEDGER_PATH = REPO_ROOT / "data" / "runtime" / "model_usage_ledger.json"
_ALPACA_SEED_PATH = REPO_ROOT / "data" / "intel" / "alpaca_seed.json"
_MARKET_CONTEXT_PATH = REPO_ROOT / "data" / "intel" / "market_context.md"

# Heading classifiers for the loosely-formatted debate files. A debate may use
# English ('### GPT-Pass-1 Position', '## Final Recommendation') or Korean
# ('## 안건', '## 결정', '## GPT + Gemini — 만장일치 수렴') headings, so each
# bucket matches a set of substrings rather than one fixed title.
_DEBATE_Q_RE = re.compile(r"안건|trigger|grounding|^d\d\b|question", re.IGNORECASE)
_DEBATE_GPT_RE = re.compile(r"\bgpt\b|codex|pass-1|pass-2", re.IGNORECASE)
_DEBATE_GEMINI_RE = re.compile(r"gemini", re.IGNORECASE)
_DEBATE_DECISION_RE = re.compile(
    r"결정|타결|final recommendation|verdict|판정|conclusion|확정|수렴|합의",
    re.IGNORECASE,
)
_DATE_PREFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _debate_sections(body: str) -> list[tuple[str, str]]:
    """Split markdown body into (heading_text, section_body) by '#'-headings.

    Front-matter must already be stripped. The pre-heading preamble is captured
    under an empty heading so a file with prose before its first '##' still
    contributes that text (used as the question fallback)."""
    sections: list[tuple[str, str]] = []
    cur_head = ""
    cur_lines: list[str] = []
    for raw in body.splitlines():
        if raw.lstrip().startswith("#"):
            sections.append((cur_head, "\n".join(cur_lines).strip()))
            cur_head = raw.lstrip().lstrip("#").strip()
            cur_lines = []
        else:
            cur_lines.append(raw)
    sections.append((cur_head, "\n".join(cur_lines).strip()))
    return sections


def _summ(text: str, limit: int = 320) -> str:
    """Collapse a section body to a compact one-liner for the dense table.

    Drops blockquote/bullet markers + blank lines, joins to a single line, and
    truncates on a word boundary. Display-only — never load-bearing."""
    parts: list[str] = []
    for raw in text.splitlines():
        s = raw.strip().lstrip("->*•").strip()
        if s and not s.startswith("#"):
            parts.append(s)
    joined = " ".join(parts)
    if len(joined) <= limit:
        return joined
    cut = joined[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def _first_match_section(
    sections: list[tuple[str, str]], pat: re.Pattern[str]
) -> str:
    """First non-empty section whose heading matches ``pat`` (summarised)."""
    for head, body in sections:
        if head and pat.search(head) and body:
            return _summ(body)
    return ""


def _parse_debate(text: str, name: str) -> dict[str, str]:
    """Best-effort extraction of {topic,date,question,gpt,gemini,decision}.

    Format-tolerant: any heading/section shape that doesn't match simply yields
    an empty field rather than raising — a malformed file never fails the feed.
    """
    fm = _parse_frontmatter(text)
    body = _FRONTMATTER_RE.sub("", text, count=1)
    title = fm.get("title") or _first_heading_title(text) or name
    date = fm.get("date") or fm.get("date_created") or ""
    if not date:
        m = _DATE_PREFIX_RE.search(name)
        date = m.group(1) if m else ""

    sections = _debate_sections(body)
    question = _first_match_section(sections, _DEBATE_Q_RE)
    if not question:
        # Fallback: the pre-heading preamble (empty-heading section).
        for head, sec in sections:
            if not head and sec:
                question = _summ(sec)
                break
    gpt = _first_match_section(sections, _DEBATE_GPT_RE)
    gemini = _first_match_section(sections, _DEBATE_GEMINI_RE)
    decision = _first_match_section(sections, _DEBATE_DECISION_RE)
    return {
        "topic": title, "date": date, "question": question,
        "gpt": gpt, "gemini": gemini, "decision": decision,
    }


def _read_debates(limit: int = 8) -> list[dict[str, str]]:
    """Newest ``limit`` debate files parsed into row dicts (read-only).

    Parse every file, then sort newest-first by the parsed date (front-matter
    or filename date prefix), falling back to file mtime so an undated file
    still orders sensibly. Filename sort alone is not chronological because the
    files mix ``YYYY-MM-DD_*`` and topic-named (``topic_*.md``) conventions."""
    dbg = REPO_ROOT / "vault" / "50_research" / "debates"
    if not dbg.is_dir():
        return []
    parsed: list[tuple[str, float, dict[str, str]]] = []
    for p in dbg.glob("*.md"):
        with contextlib.suppress(OSError):
            row = _parse_debate(
                p.read_text(encoding="utf-8", errors="replace"), p.stem
            )
            mtime = p.stat().st_mtime if p.exists() else 0.0
            parsed.append((row.get("date", ""), mtime, row))
    parsed.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [row for _, _, row in parsed[:limit]]


def _read_escalations(limit: int = 40) -> list[dict[str, Any]]:
    """Probe ``ambiguous`` would-escalate rows (hardening #11 seam), read-only.

    The advisor (arbiter) is not built, so every row is status='flagged, no
    advisor yet' — the bot flagged a HOLD-dead-band decision a future AI could
    escalate, but GPT calls stay 0. The ``ambiguous`` column is added lazily by
    the probe runtime; a DB without it (or without the file) yields []."""
    if not _PROBE_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{_PROBE_DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(probe_decisions)")}
        if "ambiguous" not in cols:
            return []
        rows = conn.execute(
            "SELECT ts, mode, action, composite_lean, deadband_margin, "
            "position_id FROM probe_decisions WHERE ambiguous = 1 "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for ts, mode, action, lean, margin, pos_id in rows:
        case = (
            f"{str(mode or '')}·{str(action or 'HOLD')} "
            f"lean={float(lean or 0.0):+.2f}"
        )
        if margin is not None:
            case += f" band={float(margin):+.2f}"
        out.append({
            "ts": int(ts or 0),
            "gate": "G6 EXIT-probe",
            "case": case,
            "reason": "composite in HOLD dead band (would-escalate)",
            "status": "flagged, no advisor yet",
            "position_id": str(pos_id or "")[:22],
        })
    return out


_BOT_AI_WINDOW_S = 86400  # 24h — section 1 (bot_ai) rollup window


def _gate_events_model_used(window_s: int) -> dict[str, Any]:
    """gate_events decision volume + model_used breakdown over ``window_s``.

    Read-only 24h aggregate — the literal answer to "what actually drove
    G1-G8 decisions" (today: overwhelmingly ``python`` — the W3 AI-free
    deterministic core; polaris/core/pipeline/config.py). Missing DB/table →
    zero counts, never an exception."""
    empty: dict[str, Any] = {"total": 0, "model_used": {}}
    if not _DB_PATH.exists():
        return empty
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            cutoff = int(time.time()) - window_s
            rows = conn.execute(
                "SELECT model_used, COUNT(*) FROM gate_events "
                "WHERE created_ts > ? GROUP BY model_used",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return empty
    model_used = {str(m or "unknown"): int(n) for m, n in rows}
    return {"total": sum(model_used.values()), "model_used": model_used}


def _entry_admission_shadow_summary(
    window_s: int, recent_n: int = 8
) -> dict[str, Any]:
    """entry_admission_shadow rollup + most-recent rows over ``window_s``.

    Component C (edge-first entry admission) — SHADOW telemetry, logged only,
    never blocks the pipeline (polaris/core/economics/entry_admission_shadow.py).
    Missing DB/table → zero counts + empty recent list, never an exception."""
    empty: dict[str, Any] = {"total": 0, "admit": 0, "would_suppress": 0, "recent": []}
    if not _DB_PATH.exists():
        return empty
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            cutoff = int(time.time()) - window_s
            totals = conn.execute(
                "SELECT COUNT(*), SUM(admit), SUM(would_suppress) "
                "FROM entry_admission_shadow WHERE created_ts > ?",
                (cutoff,),
            ).fetchone()
            rows = conn.execute(
                "SELECT created_ts, symbol, strategy_id, regime, admit, "
                "would_suppress, reason FROM entry_admission_shadow "
                "WHERE created_ts > ? ORDER BY created_ts DESC LIMIT ?",
                (cutoff, recent_n),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return empty
    total_n = int(totals[0] or 0) if totals else 0
    admit_n = int(totals[1] or 0) if totals else 0
    suppress_n = int(totals[2] or 0) if totals else 0
    recent = [
        {
            "ts": int(ts or 0), "symbol": str(sym or ""),
            "strategy_id": str(strat or ""), "regime": str(regime or ""),
            "admit": bool(admit), "would_suppress": bool(sup),
            "reason": str(reason or ""),
        }
        for ts, sym, strat, regime, admit, sup, reason in rows
    ]
    return {
        "total": total_n, "admit": admit_n, "would_suppress": suppress_n,
        "recent": recent,
    }


# ── #32 AI judge — runtime-log-derived call stats (display-only) ───────────
# The judge (GPT, a non-blocking overlay on G3/G4/G7 — polaris/core/pipeline/
# agents/ai_judge.py) logs verdict/escalation/latency via
# ``logger.info("[ai-judge] ...")``. By construction (apply_entry_verdict /
# apply_exit_verdict copy model_used/latency_ms/tokens from the DETERMINISTIC
# result, never the judge's own JudgeOutcome) none of that lands in
# gate_events — the shadow verdict itself lives in gate_shadow_events.
# technical_flags as a ``judge:<VERDICT>`` tag with no latency column. The
# runtime log is the ONLY durable place a judge call's verdict+latency pair
# is recorded, so this is a best-effort text scan (background ref-loop only,
# never a request thread). Missing/rotated-away log → all-zero, never raises.
_JUDGE_LOG_TAIL_BYTES = 8_000_000  # ample per file for a day of INFO logging
_JUDGE_LINE_RE = re.compile(
    r"^(?P<ts>\S+) .*\[ai-judge\].*?"
    r"(?:verdict=(?P<verdict>\w+)\s+)?"
    r"\(det=\w+\)\s+escalation=(?P<escalation>\w+)"
    r"(?:\s+latency=(?P<latency>\d+)ms|\s+wall_clock_ms=(?P<wall_ms>\d+))?"
)


def _parse_log_ts(raw: str) -> float:
    """``polaris.logging_config`` ISO-UTC-ms line prefix → epoch seconds, or
    0.0 on any parse failure."""
    try:
        return (
            dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=dt.UTC)
            .timestamp()
        )
    except ValueError:
        return 0.0


def _tail_bytes(path: Path, max_bytes: int) -> list[str]:
    """Seek-based tail (last ``max_bytes``) of ``path`` → decoded lines.

    [] on any OSError (missing file / permission) — never raises."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            block = min(size, max_bytes)
            fh.seek(size - block)
            data = fh.read(block)
        return data.decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _judge_log_lines(window_s: int, log_path: Path | None) -> list[str]:
    """Tail of the live bot log covering ``window_s``, oldest-first.

    ``log_path`` defaults to ``_resolve_bot_log()`` (the live bot's newest
    runtime log; injectable so tests never touch the real filesystem glob).
    The daily 07:30 auto-restart rotates the previous log to
    ``<name>.<suffix>`` alongside it (CLAUDE.md Quick reference); when the
    current file's own oldest kept line is still inside the window, this also
    reads the newest such rotated sibling so a 24h window isn't silently
    truncated to 'since last restart'."""
    log = log_path if log_path is not None else _resolve_bot_log()
    if log is None or not log.exists():
        return []
    lines = _tail_bytes(log, _JUDGE_LOG_TAIL_BYTES)
    cutoff = time.time() - window_s
    oldest_ts = 0.0
    for ln in lines[:5]:  # line 0 may be seek-truncated; try a few
        t = _parse_log_ts(ln.split(" ", 1)[0])
        if t > 0:
            oldest_ts = t
            break
    if oldest_ts == 0.0 or oldest_ts > cutoff:
        with contextlib.suppress(OSError):
            rotated = sorted(
                (p for p in log.parent.glob(f"{log.name}.*") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if rotated:
                lines = _tail_bytes(rotated[0], _JUDGE_LOG_TAIL_BYTES) + lines
    return lines


def _judge_call_stats(
    window_s: int, *, log_path: Path | None = None
) -> dict[str, Any]:
    """#32 AI-judge call stats over ``window_s`` — verdict distribution,
    escalation/salvage counts, average latency. See the module note above
    ``_JUDGE_LINE_RE`` for why this reads the runtime log rather than
    gate_events. ``log_path`` is test-injectable; production omits it."""
    lines = _judge_log_lines(window_s, log_path)
    cutoff = time.time() - window_s
    verdicts: dict[str, int] = {}
    escalations: dict[str, int] = {}
    latencies: list[int] = []
    calls = 0
    for line in lines:
        if "[ai-judge]" not in line:
            continue
        m = _JUDGE_LINE_RE.match(line)
        if not m:
            continue
        ts = _parse_log_ts(m.group("ts"))
        if ts and ts < cutoff:
            continue
        calls += 1
        verdict = m.group("verdict")
        if verdict:
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
        esc = m.group("escalation")
        if esc:
            escalations[esc] = escalations.get(esc, 0) + 1
        lat_raw = m.group("latency") or m.group("wall_ms")
        if lat_raw:
            latencies.append(int(lat_raw))
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    return {
        "window_h": window_s // 3600,
        "calls": calls,
        "verdicts": verdicts,
        "escalations": escalations,
        "salvage_count": escalations.get("gpt_ok_salvaged", 0),
        "avg_latency_ms": round(avg_latency, 1) if avg_latency is not None else None,
        "latency_sample_n": len(latencies),
        "log_available": bool(lines),
    }


# ── Harness AI (Claude Code, dev-time) — display-only ───────────────────────
# THIS is the "AI you (Claude) use" half of Jin's 2026-07-10 complaint —
# entirely separate from bot_ai above. Sourced from the model-usage ledger the
# harness itself maintains (data/runtime/model_usage_ledger.json — same file
# vault/40_ops/model-usage-stats.md is periodically snapshotted from) + the
# vault/log.md wave-append tail. Never touches sizing/risk/orders.
def _harness_model_usage_top(n: int = 4) -> list[dict[str, Any]]:
    """Top-``n`` Claude harness models by total turns (main+side), aggregated
    across every session in the usage ledger. Fail-safe: missing/corrupt file
    → [] (dev-side telemetry, never an exception)."""
    if not _MODEL_LEDGER_PATH.exists():
        return []
    try:
        raw = json.loads(_MODEL_LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    agg: dict[str, dict[str, Any]] = {}
    for models in raw.values():
        if not isinstance(models, dict):
            continue
        for model, dates in models.items():
            if not isinstance(dates, dict):
                continue
            a = agg.setdefault(model, {"turns": 0, "out_tok": 0, "last_seen": ""})
            for date, stats in dates.items():
                if not isinstance(stats, dict):
                    continue
                a["turns"] += int(stats.get("turns", 0) or 0) + int(
                    stats.get("side_turns", 0) or 0
                )
                a["out_tok"] += int(stats.get("out_tok", 0) or 0)
                if date > a["last_seen"]:
                    a["last_seen"] = date
    rows = [
        {"model": m, "turns": v["turns"], "out_tok": v["out_tok"], "last_seen": v["last_seen"]}
        for m, v in agg.items()
    ]
    rows.sort(key=lambda r: int(r["turns"]), reverse=True)
    return rows[:n]


def _harness_recent_waves(n: int = 3, *, log_path: Path | None = None) -> list[str]:
    """Last ``n`` wave-log lines (``- YYYY-MM-DD ...`` entries) from
    vault/log.md — the 1-line-per-wave append mandate (CLAUDE.md Workflow #7).
    Boot/digest marker lines (no leading '- ') are excluded. [] if absent.
    ``log_path`` is test-injectable; production omits it."""
    if log_path is None:
        log_path = REPO_ROOT / "vault" / "log.md"
    if not log_path.exists():
        return []
    with contextlib.suppress(OSError):
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        waves = [ln for ln in lines if ln.lstrip().startswith("- ")]
        return waves[-n:]
    return []


def _build_harness_ai() -> dict[str, Any]:
    """Claude harness (dev-time) usage — NOT the bot's runtime LLM. Read-only."""
    return {
        "label": "Claude harness (dev) — not the bot's LLM",
        "models": _harness_model_usage_top(4),
        "recent_waves": _harness_recent_waves(3),
    }


# ── Cowork intel feed — display-only ────────────────────────────────────────
# The cowork watchlist-intel seed feed (contract: cowork/watchlist_intel/
# CONTRACT.md) — symbol candidates with a thesis_tag + score, consumed by
# polaris/core/universe/intel_seed.py as a rank-uplift signal (never a hard
# gate). Surfaces the feed's OWN expiry ts with an EXPIRED/ACTIVE badge (an
# expired feed is not re-fetched or hidden here — display-only truth) + the
# cohort's actual fire rate (positions.seed_tag — the ACTUAL wiring point per
# polaris/storage/schema.py) so a stale feed reads honest, not silently fresh.
def _parse_iso_ts(raw: str) -> float | None:
    """Best-effort ISO-8601 'Z' timestamp → epoch seconds, or None."""
    if not raw:
        return None
    try:
        return (
            dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=dt.UTC)
            .timestamp()
        )
    except ValueError:
        return None


def _cohort_fired_count() -> int:
    """# of positions opened from a cowork-intel seed_tag cohort. 0 is a
    valid, HONEST reading (the feed may have expired before any cohort symbol
    fired) — never hidden as an error. Missing DB/table → 0."""
    if not _DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE seed_tag != ''"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _build_cowork_intel() -> dict[str, Any]:
    """Cowork watchlist-intel feed (Alpaca seed candidates) — read-only."""
    if not _ALPACA_SEED_PATH.exists():
        return {"available": False, "status": "NO_FEED"}
    try:
        raw = json.loads(_ALPACA_SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "status": "NO_FEED"}
    if not isinstance(raw, dict):
        return {"available": False, "status": "NO_FEED"}
    generated_at = str(raw.get("generated_at", ""))
    expiry_ts = str(raw.get("expiry_ts", ""))
    expiry_epoch = _parse_iso_ts(expiry_ts)
    status = "UNKNOWN"
    if expiry_epoch is not None:
        status = "EXPIRED" if expiry_epoch < time.time() else "ACTIVE"
    candidates = raw.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    rows = [c for c in candidates if isinstance(c, dict)]
    rows.sort(key=lambda c: float(c.get("score", 0.0) or 0.0), reverse=True)
    trimmed = [
        {
            "symbol": str(c.get("symbol", "")), "venue": str(c.get("venue", "")),
            "thesis_tag": str(c.get("thesis_tag", "")),
            "score": float(c.get("score", 0.0) or 0.0),
        }
        for c in rows[:8]
    ]
    mkt_head: list[str] = []
    if _MARKET_CONTEXT_PATH.exists():
        with contextlib.suppress(OSError):
            mkt_head = [
                ln for ln in _MARKET_CONTEXT_PATH.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if ln.strip()
            ][:3]
    return {
        "available": True, "status": status,
        "generated_at": generated_at, "expiry_ts": expiry_ts,
        "candidates": trimmed, "cohort_fired": _cohort_fired_count(),
        "market_context_head": mkt_head,
    }


def _build_ai_activity() -> dict[str, Any]:
    """3-section AI tab payload (Jin 2026-07-10 — bot AI / Claude harness AI /
    cowork intel were all mixed into one undifferentiated tab):

    1. bot_ai       — runtime OpenAI GPT: gate_events model_used + the #32
                      judge overlay + entry_admission_shadow + the legacy
                      dev-debates/probe-escalation feed (kept, unchanged).
    2. harness_ai   — THIS Claude Code harness (dev-time). Never the bot's LLM.
    3. cowork_intel — the cowork watchlist-intel seed feed + expiry status.

    Every sub-builder is independently fail-safe (missing file/table/log →
    zero/empty, never an exception) so one absent source never blanks the
    other two sections."""
    return {
        "bot_ai": {
            "window_h": _BOT_AI_WINDOW_S // 3600,
            "in_loop_ai_calls": 0,
            "gate_events": _gate_events_model_used(_BOT_AI_WINDOW_S),
            "judge": _judge_call_stats(_BOT_AI_WINDOW_S),
            "entry_admission_shadow": _entry_admission_shadow_summary(_BOT_AI_WINDOW_S),
            "debates": _read_debates(8),
            "probe_escalations": _read_escalations(40),
        },
        "harness_ai": _build_harness_ai(),
        "cowork_intel": _build_cowork_intel(),
        "ts": time.time(),
    }


# ── Weekend ops board (display-only) ────────────────────────────────────────
# /api/weekend serves the four-section weekend operations board: ① last-7d
# real-fee-net settlement, ② the weekend_shadow_orders would-be P&L (resolved
# OFFLINE against stored bars) + maker-fill-shadow status, ③ Monday-readiness
# session-open countdown + bar freshness, ④ the recent weekend research notes.
# The would-be resolution scans the forward bars for every shadow order (≈1.4s on
# the live DB), so — like buildlog/lessons — it MUST NOT run inside a request
# thread under the 1s board poll. It has its own slow cache + the ref loop; the
# request handler only ever serves the warm value. The DB read is ?mode=ro and
# nothing here touches sizing/risk/orders. Weekend data moves slowly (shadow
# orders accrue over the weekend; the 7d window is coarse) → a 60s TTL is ample.
_weekend_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_weekend_lock = threading.Lock()
_WEEKEND_TTL = 60.0

# Recent weekend research notes (section ④) — vault/50_research/*weekend*.md +
# the capital-weekend FX note, surfaced by filename-substring so a new note is
# picked up automatically. Read-only display chrome.
_WEEKEND_NOTE_SUBSTR = ("weekend", "alpaca_weekend")


def _build_weekend() -> dict[str, Any]:
    """Four-section weekend ops payload + recent research notes. Read-only.

    Opens a fresh ?mode=ro connection (trading) + a SECOND ?mode=ro connection
    against the sibling marketdata DB (storage-split, 2026-07-14 — sections
    ②/③ read ``bars``/``weekend_shadow_orders``/``maker_fill_shadow``), builds
    the four sections via ``weekend_data.build_weekend`` (settlement / shadow /
    readiness), then attaches the recent weekend research notes for the
    housekeeping queue. A missing DB or a builder failure degrades to an
    empty-but-shaped payload (never an exception)."""
    from polaris.storage.schema_marketdata import marketdata_db_path_for
    from tools.visualizer import weekend_data

    now_ts = int(time.time())
    payload: dict[str, Any] = {
        "ts_now": now_ts, "is_weekend": weekend_data.is_weekend_utc(now_ts),
        "settlement": {"rows": [], "best": None, "worst": None,
                       "total_net_usd": 0.0},
        "shadow": {"strategies": [], "order_n": 0, "maker_fill_shadow_n": 0,
                   "maker_persist_wired": False},
        "readiness": {"session_opens": weekend_data.next_session_opens(now_ts),
                      "bar_freshness": []},
        "funding_strategy": "weekend_funding_capitulation_maker",
        "notes": _weekend_notes(),
        "ts": time.time(),
    }
    if not _DB_PATH.exists():
        return payload
    md_path = marketdata_db_path_for(_DB_PATH)
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        try:
            md_conn = (
                sqlite3.connect(f"file:{md_path}?mode=ro", uri=True)
                if md_path.exists() else None
            )
            try:
                built = weekend_data.build_weekend(
                    conn, now_ts=now_ts, md_conn=md_conn
                )
            finally:
                if md_conn is not None:
                    md_conn.close()
        finally:
            conn.close()
    except sqlite3.Error:
        return payload
    payload.update(built)
    payload["notes"] = _weekend_notes()
    payload["ts"] = time.time()
    return payload


def _weekend_notes() -> list[dict[str, str]]:
    """Recent weekend research notes (title + first takeaway), newest-first.

    Reuses the ``_collect_md_entries`` reader over vault/50_research, keeping only
    files whose name carries a weekend substring. Read-only; empty when absent."""
    research = REPO_ROOT / "vault" / "50_research"
    entries = _collect_md_entries(research, "research")
    notes = [
        e for e in entries
        if any(s in e["name"].lower() for s in _WEEKEND_NOTE_SUBSTR)
    ]
    notes.sort(key=lambda e: e["name"], reverse=True)
    return notes[:8]


def _serve_ref(
    cache: dict[str, Any], lock: threading.Lock, builder: Any
) -> dict[str, Any]:
    """Serve-stale read for a reference cache; cold-start builds inline once.

    Mirrors ``_fresh_graph``: the warm value (kept current by ``_ref_refresh_loop``)
    is returned instantly. Only a cold cache (pre-warm) builds inline, under the
    lock, so concurrent first requests don't each run git/fs.
    """
    data: dict[str, Any] | None = cache["data"]
    if data is not None:
        return data
    with lock:
        if cache["data"] is None:
            cache["data"] = builder()
            cache["ts"] = time.time()
        return cache["data"]


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
        if self.path.startswith("/stream/prices"):
            self._serve_price_sse()
            return
        if self.path.startswith("/stream/events"):
            self._serve_sse()
            return
        # iPhone single-column page (display-only, polls /api/snapshot). Path-only
        # match so a trailing slash / query is tolerated; serves the static file.
        if self.path == "/m" or self.path.startswith("/m?") or self.path == "/m/":
            self.path = "/mobile.html"
            super().do_GET()
            return
        # Gate+AI pipeline flow page (display-only, desktop-only — same path-only
        # match as /m). Polls /api/flow_stats + subscribes /stream/events.
        if self.path == "/flow" or self.path.startswith("/flow?") or self.path == "/flow/":
            self.path = "/flow.html"
            super().do_GET()
            return
        # Phone auto-route: a bare visit to "/" from a mobile browser gets the
        # single-column page — the WebGL globe + 70% board are never served to a
        # phone. ?desktop=1 forces the full board. index.html also does a
        # client-side narrow-viewport guard (catches desktop-UA phones).
        path_only = self.path.split("?", 1)[0]
        if (
            path_only in ("/", "/index.html")
            and "desktop=1" not in self.path
            and _is_mobile_ua(self.headers.get("User-Agent", ""))
        ):
            self.path = "/mobile.html"
            super().do_GET()
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
        if self.path.startswith("/api/ticker_chart"):
            try:
                self._json(_ticker_chart(self.path))
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"ticker_chart err: {exc}")
            return
        if self.path.startswith("/api/sentinel"):
            try:
                self._json(_sentinel_payload())
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"sentinel err: {exc}")
            return
        if self.path.startswith("/api/dashboard"):
            try:
                self._json({"rows": _fresh_dashboard(), "ts": time.time()})
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"dashboard err: {exc}")
            return
        if self.path.startswith("/api/buildlog"):
            try:
                self._json(
                    _serve_ref(_buildlog_cache, _buildlog_lock, _build_buildlog)
                )
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"buildlog err: {exc}")
            return
        if self.path.startswith("/api/roadmap"):
            try:
                self._json(
                    _serve_ref(_roadmap_cache, _roadmap_lock, _build_roadmap)
                )
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"roadmap err: {exc}")
            return
        if self.path.startswith("/api/lessons"):
            try:
                self._json(
                    _serve_ref(_lessons_cache, _lessons_lock, _build_lessons)
                )
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"lessons err: {exc}")
            return
        if self.path.startswith("/api/ai_activity"):
            try:
                self._json(
                    _serve_ref(
                        _ai_activity_cache, _ai_activity_lock, _build_ai_activity
                    )
                )
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"ai_activity err: {exc}")
            return
        if self.path.startswith("/api/weekend"):
            try:
                self._json(
                    _serve_ref(_weekend_cache, _weekend_lock, _build_weekend)
                )
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"weekend err: {exc}")
            return
        if self.path.startswith("/api/flow_stats"):
            try:
                self._json(_serve_ref(_flow_cache, _flow_lock, _build_flow_stats))
            except Exception as exc:  # display-only: never crash the loop
                self.send_error(500, f"flow_stats err: {exc}")
            return
        super().do_GET()

    def _serve_price_sse(self) -> None:
        """Per-cell live-mark PUSH stream — emits ONLY the open-position price
        cells whose value moved since the last frame, as they change.

        A fast internal loop (~250ms) reads the warm snapshot cache's open marks
        (``_price_marks`` — read-only; the bot's live WS marks are already the
        freshest value in that cache) and diffs each ``venue|symbol|strategy|side``
        key against the previously-sent value. Only changed keys are pushed
        (``data: {prices:[{key,last_price,upnl_usd,delta_pct,upnl_pct}]}``) so the
        board flashes just those cells the instant they move — no full-table poll,
        no snapshot/sizing/gate/trade mutation. Cheap: a dict slice + compare. The
        first frame sends the full current set so a fresh client paints once."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            super(http.server.SimpleHTTPRequestHandler, self).end_headers()
            sent: dict[str, dict[str, Any]] = {}
            first = True
            idle = 0
            while True:
                marks = _price_marks()
                changed: list[dict[str, Any]] = []
                for key, cell in marks.items():
                    prev = sent.get(key)
                    if first or prev != cell:
                        changed.append(cell)
                        sent[key] = cell
                # drop keys for positions that closed (so a re-open re-sends).
                for key in list(sent.keys()):
                    if key not in marks:
                        del sent[key]
                if changed:
                    payload = json.dumps({"prices": changed})
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    idle = 0
                else:
                    idle += 1
                    if idle >= 60:  # ~15s heartbeat keeps the connection warm
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        idle = 0
                first = False
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001 — best-effort stream
            pass

    def _serve_sse(self) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "keep-alive")
            super(http.server.SimpleHTTPRequestHandler, self).end_headers()
            cursor = _latest_fill_ts()
            gate_cursor = _latest_gate_event_ts()
            while True:
                events = _fills_since(cursor)
                gate_events = _gate_events_since(gate_cursor)
                wrote = False
                if events:
                    cursor = max(int(e["ts"]) for e in events)
                    payload = json.dumps({"events": events})
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    wrote = True
                if gate_events:
                    gate_cursor = max(int(e["ts"]) for e in gate_events)
                    gpayload = json.dumps({"gate_events": gate_events})
                    self.wfile.write(f"data: {gpayload}\n\n".encode())
                    wrote = True
                if wrote:
                    self.wfile.flush()
                else:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                time.sleep(1)
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


def _ref_refresh_loop() -> None:
    """Single owner of the reference caches (buildlog / roadmap / lessons).

    Kept SEPARATE from ``_bg_refresh_loop`` so git/fs work never blocks (or
    contends with) the 1s SQLite snapshot build. Each cache rebuilds only past
    its own TTL; request threads only ever serve the warm value. Cadence is the
    shortest TTL — flow_stats' 5s (Jin 2026-07-10, feat/flat-neural-map
    realtime-sync mandate) since 2026-07-10; buildlog/roadmap/lessons (30-60s)
    skip until their own TTL is due."""
    builders = (
        (_buildlog_cache, _buildlog_lock, _build_buildlog, _BUILDLOG_TTL),
        (_roadmap_cache, _roadmap_lock, _build_roadmap, _ROADMAP_TTL),
        (_lessons_cache, _lessons_lock, _build_lessons, _LESSONS_TTL),
        (_ai_activity_cache, _ai_activity_lock, _build_ai_activity,
         _AI_ACTIVITY_TTL),
        (_weekend_cache, _weekend_lock, _build_weekend, _WEEKEND_TTL),
        (_flow_cache, _flow_lock, _build_flow_stats, _FLOW_TTL),
    )
    while True:
        now = time.time()
        for cache, lock, builder, ttl in builders:
            if cache["data"] is not None and now - cache["ts"] < ttl:
                continue
            try:
                fresh = builder()
            except Exception:  # noqa: BLE001 — display-only, keep stale value
                continue
            with lock:
                cache["data"] = fresh
                cache["ts"] = time.time()
        time.sleep(min(_BUILDLOG_TTL, _ROADMAP_TTL, _LESSONS_TTL, _FLOW_TTL))


def main() -> None:
    global _DB_PATH, _SENTINEL_DB_PATH, _PROBE_DB_PATH
    parser = argparse.ArgumentParser(description="Polaris space visualizer server")
    parser.add_argument("--db", default="data/polaris_live.sqlite")
    parser.add_argument("--sentinel-db", default="data/sentinel.sqlite")
    parser.add_argument("--probe-db", default="data/probes.sqlite")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    _DB_PATH = Path(args.db)
    _SENTINEL_DB_PATH = Path(args.sentinel_db)
    _PROBE_DB_PATH = Path(args.probe_db)

    # Load .env so the read-only Alpaca account probe can see the paper keys.
    _load_dotenv()

    # Prewarm so the first browser fetch is instant.
    with contextlib.suppress(Exception):
        _fresh_graph()
    threading.Thread(target=_bg_refresh_loop, daemon=True).start()
    threading.Thread(target=_ref_refresh_loop, daemon=True).start()
    if _LIVE_PX_ENABLED:
        threading.Thread(target=_okx_px_loop, daemon=True).start()

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
