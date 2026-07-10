"""Polaris space-visualizer graph builder (DEMO/PAPER, display-only).

Turns the read-only dashboard snapshot + the dynamic universe table into the
``graph.json`` schema consumed by ``static/sphere-render.js`` (the Canvas-2D
sphere engine reused from Jin's earlier project, untouched here).

This module is purely a *view*: it reads SQLite and produces a dict. It never
sizes, orders, throttles, or gates anything — the visual has zero effect on the
trading pipeline. Aggressive bias is preserved because nothing here touches
sizing or risk.

Tier semantics (borrowed from mk1 ``snapshot.py`` — meaning + node fields only,
not a verbatim copy; filled with live Polaris data):
  tier0  'pos'        open positions            (snapshot.positions + live_trades)
  tier1  'exit'       exit-pattern data         (recent_trades exit_reason set)
  tier3  'reg'        regime states             (snapshot.regime_bars)
  tier5  'strat'      ALL 7 strategies          (STRATEGY_REGISTRY metadata + stats)
  tier7  'watch'      signal watch              (watchlist_focus minus open positions)
  tier8  'mkt'        market shell              (universe table)
  tier9  'obs'        system health (square)    (alerts + fault + gpt ok%)
  tier10 'action'     action queue (square)     (gate_events / order_intents)
  tier11 'orbit'      function satellites        (learners / providers / AI judges)
  tier12 'axis'       dimension axis satellites  (session × liq × crisis)
  tier13 'exit_tally' exit_type counts (square) (recent_trades exit_reason counts)

Satellites (T11/T12) carry ``orbit_kind``/``axis_kind`` + per-category
``orbit_axis``/``orbit_speed`` + ``spin_axis``/``spin_speed`` so the engine
renders them as orbiting/spinning diamonds. Every tier is always emitted with
at least a structural placeholder so the rings always render.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from polaris.core.sizing.session import resolve_venue_session
from polaris.core.streams import asset_class_allowed_for_venue
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.strategies import STRATEGY_REGISTRY
from tools.visualizer.polaris_graph_chains import (
    build_lifecycle_paths,
    build_trade_chains,
)

_LOG = logging.getLogger(__name__)

# Display-only cap for the Alpaca equity galaxy: ~12.9k tradable symbols would
# choke the render loop, so only the top-N by 24h volume reach the globe (dim
# market-shell dots). NOT a trading limit — the universe table / focus path is
# untouched. Overridable via POLARIS_GLOBE_ALPACA_MAX.
_ALPACA_GLOBE_MAX_DEFAULT = 300

# ── tier-LOD node cap (Jin 2026-06-24) ───────────────────────────────────────
# Neural-Cloud attention gradient: the globe drew EVERY watched mkt row as an
# individual node, so a growing universe (305 → 1900 watched) made setGraph +
# the per-frame ease/orbit loops iterate every node every frame → lag. Tier-LOD
# fixes this WITHOUT touching any data source: only the hottest rows (open
# positions, watch focus, tier S·A in full + a slice of tier B, plus any row
# whose pipeline actually caught a live signal) are emitted as individual
# ``mkt`` nodes, capped at ``POLARIS_GRAPH_NODE_CAP``. The long tail (tier B
# overflow + the dormant T library) is NOT dropped — it is summarised into
# per-venue ``tail_aggregate`` descriptors (count + representative density) the
# globe renders as a cheap, count-independent HAZE. graph.json node count stays
# ~cap PER VENUE no matter how large the watched universe grows. Pure view:
# nothing here gates, sizes, ranks or throttles a trade — the universe table is
# untouched.
# Jin 2026-06-25: the cap is now PER VENUE (see _mkt_nodes), so 240 lets OKX
# (~219 active) and Capital (~168 whitelisted) render their FULL tradable shell
# as individual dots while Alpaca's ~12.9k rows still fold into its own haze.
# Worst-case individual nodes ≈ 3×240 = 720, within the globe's render envelope
# (DIM_BUDGET 650 stride-LOD; designed for 1000+ nodes at 60fps).
_GRAPH_NODE_CAP_DEFAULT = 240

# tier → attention priority (lower = kept as an individual node first). Open
# positions / watch nodes are emitted separately (always individual). For the
# mkt shell: S/A are the curator's hot picks (always individual), B is partial
# (kept until the cap fills), and the dormant T tail folds into the haze.
_TIER_PRIORITY: dict[str, int] = {"S": 0, "A": 1, "B": 2}
_TIER_TAIL_PRIORITY = 5  # T tail + untiered dormant rows

# Capital weekend-shell DISPLAY floor (Jin 2026-06-28) — see _mkt_nodes. Pure
# globe render weight (never a sizing/risk/gating input): keeps the Capital venue
# shell visible on weekends when its instruments go market-closed (is_active=0,
# tiny/zero vol) instead of collapsing to near-invisible grey dust.
_SHELL_INTENSITY_FLOOR = 0.30
_SHELL_SIZE_FLOOR = 0.85

# 13 cluster definitions (id, label, color, tier) — color palette matches the
# render engine's CLUSTERS table. Order is display-only. tier 4 intentionally
# empty (engine reserves the index for frame-function compat).
_CLUSTERS: list[dict[str, Any]] = [
    {"id": "pos", "label": "live positions", "color": "#87d7ff", "tier": 0},
    {"id": "exit", "label": "exit patterns", "color": "#ff87d7", "tier": 1},
    {"id": "reg", "label": "regime context", "color": "#d7d787", "tier": 3},
    {"id": "strat", "label": "strategies", "color": "#ff9f87", "tier": 5},
    {"id": "watch", "label": "signal watch", "color": "#87ffd7", "tier": 7},
    {"id": "mkt", "label": "market shell", "color": "#ffaf87", "tier": 8},
    {"id": "obs", "label": "observ", "color": "#d7d787", "tier": 9},
    {"id": "action", "label": "actions", "color": "#d787d7", "tier": 10},
    {"id": "orbit", "label": "function satellites", "color": "#9fc7ff", "tier": 11},
    {"id": "axis", "label": "dimension axis", "color": "#ffd7c7", "tier": 12},
    {"id": "exit_tally", "label": "exit tally", "color": "#ff87af", "tier": 13},
    {"id": "probe", "label": "position probes", "color": "#8a94b0", "tier": 14},
]

# ── Satellite orbit specs (per-category axis + angular speed, rad/s) ──────────
# Borrowed semantics from mk1 SAT_ORBIT: each satellite category rotates on a
# distinct 3D plane so categories visually separate (not all spinning on Y).
_SAT_ORBIT: dict[str, dict[str, Any]] = {
    # T11 ORBIT kinds
    "learner": {"axis": [-0.7071, 0.0, 0.7071], "speed": -0.20},
    "provider": {"axis": [0.7071, 0.0, 0.7071], "speed": 0.14},
    "ai_judge": {"axis": [1.0, 0.0, 0.0], "speed": 0.32},
    # T12 AXIS kinds
    "session": {"axis": [0.5, -0.3, 0.8], "speed": -0.10},
    "liq": {"axis": [-0.5, 0.3, 0.8], "speed": 0.16},
    "crisis": {"axis": [0.7071, 0.0, -0.7071], "speed": -0.18},
    # T9/T10/T13 outer ring kinds
    "obs": {"axis": [0.8, 0.0, 0.6], "speed": -0.08},
    "action": {"axis": [-0.6, 0.3, -0.7], "speed": 0.10},
    "exit_tally": {"axis": [0.0, 1.0, 0.0], "speed": 0.12},
}

# inter-tier radii for T11 orbit kinds (matches engine ORBIT_LAT band radii).
_ORBIT_INTER_RADIUS: dict[str, float] = {
    "learner": 0.63,    # STRAT ↔ BRAIN gap inner
    "ai_judge": 0.68,   # STRAT ↔ BRAIN gap outer
    "provider": 0.87,   # WATCH ↔ MKT
}


def _phase(i: int) -> float:
    """Deterministic render-only orbital phase in [0, 2pi)."""
    return round((i * 0.37) % (2 * math.pi), 3)


def _seed_rand(node_id: str, salt: str) -> float:
    """Deterministic 0..1 hash (mirror of sphere-render.js _seedRand)."""
    s = f"{node_id or 'x'}::{salt or ''}"
    h = 2166136261
    for ch in s:
        h = ((h * 16777619) ^ ord(ch)) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


def _normalize_axis(v: list[float]) -> list[float]:
    n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if n < 1e-6:
        return [0.0, 1.0, 0.0]
    return [round(v[0] / n, 4), round(v[1] / n, 4), round(v[2] / n, 4)]


def _perturb_axis(base: list[float], node_id: str, mag: float = 0.40) -> list[float]:
    """Small deterministic perturbation so same-kind satellites differ slightly."""
    ax = base[0] + (_seed_rand(node_id, "ax") - 0.5) * mag
    ay = base[1] + (_seed_rand(node_id, "ay") - 0.5) * mag
    az = base[2] + (_seed_rand(node_id, "az") - 0.5) * mag
    return _normalize_axis([ax, ay, az])


def _jitter_speed(base: float, node_id: str) -> float:
    return round(base * (0.85 + 0.30 * _seed_rand(node_id, "speed")), 5)


def _spin_axis(node_id: str) -> list[float]:
    """Deterministic per-node spin axis (own-axis tumble, independent of orbit)."""
    cx = _seed_rand(node_id, "sx") * 2 - 1
    cy = _seed_rand(node_id, "sy") * 2 - 1
    cz = _seed_rand(node_id, "sz") * 2 - 1
    return _normalize_axis([cx, cy, cz])


def _spin_speed(state: str, node_id: str) -> float:
    jitter = _seed_rand(node_id, "spin")
    if state == "firing":
        return round(1.0 + 0.5 * jitter, 4)
    if state == "lit":
        return round(0.5 + 0.5 * jitter, 4)
    return round(0.2 + 0.3 * jitter, 4)


def _orbit_fields(kind: str, node_id: str, state: str) -> dict[str, Any]:
    """Per-node satellite motion fields (orbit_axis/speed + spin) for a category."""
    spec = _SAT_ORBIT.get(kind, {"axis": [0.0, 1.0, 0.0], "speed": 0.0})
    return {
        "orbit_axis": _perturb_axis(spec["axis"], node_id),
        "orbit_speed": _jitter_speed(float(spec["speed"]), node_id),
        "spin_axis": _spin_axis(node_id),
        "spin_speed": _spin_speed(state, node_id),
    }


def _direction(side: str) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s or "long"


def _short_venue(venue: str) -> str:
    return (venue or "okx").lower()[:3]


def _asset_group_for(asset_class: str) -> str:
    """Map strategy/universe asset_class → engine group key (GROUP_COLORS)."""
    a = (asset_class or "").lower()
    if a in ("spot", "crypto", "micro"):
        return "crypto"
    if a in ("fx", "forex"):
        return "forex"
    if a in ("index", "indices"):
        return "indices"
    if a in ("commodity", "xau", "gold"):
        return "commodity"
    if a in ("stock", "shares", "equity", "equities"):
        return "stock"
    if a == "etf":
        return "etf"
    return "crypto"


# ── universe (T8) ────────────────────────────────────────────────────────────
def _query_universe(db_path: Path) -> list[dict[str, Any]]:
    """Active (is_active=1) trading-focus subset → galaxy_universe records.

    Retained as the focus-only view (bot trading focus = the rows that light up).
    The globe shell now renders the FULL tradable universe via
    :func:`_query_universe_all`; this helper is kept intact so the focus subset
    stays available unchanged (display-only — no behavior here).
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT venue, symbol, asset_class, vol_24h_usd "
            "FROM universe WHERE is_active = 1 "
            "ORDER BY vol_24h_usd DESC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for venue, symbol, asset_class, vol_24h in rows:
        vol = float(vol_24h or 0.0)
        n_24h = int(min(500, max(1, vol / 1_000_000.0)))
        out.append(
            {
                "ticker": str(symbol),
                "exchange": _short_venue(str(venue)),
                "asset_group": _asset_group_for(str(asset_class or "crypto")),
                "n_24h": n_24h,
            }
        )
    return out


def _alpaca_globe_max() -> int:
    """Top-N Alpaca cap for the globe (env POLARIS_GLOBE_ALPACA_MAX, default 300)."""
    raw = os.environ.get("POLARIS_GLOBE_ALPACA_MAX")
    if raw is None:
        return _ALPACA_GLOBE_MAX_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _ALPACA_GLOBE_MAX_DEFAULT
    return max(0, n)


def _graph_node_cap() -> int:
    """Individual-mkt-node cap for the globe (env POLARIS_GRAPH_NODE_CAP, ~180).

    Display-only LOD budget: how many universe rows reach the globe as their own
    glowing node before the rest fold into the per-venue tail haze. Trading
    universe untouched — this only bounds the render node count.
    """
    raw = os.environ.get("POLARIS_GRAPH_NODE_CAP")
    if raw is None:
        return _GRAPH_NODE_CAP_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return _GRAPH_NODE_CAP_DEFAULT
    return max(0, n)


def _query_tier_map(db_path: Path) -> dict[str, str]:
    """(venue:symbol) → tier (S/A/B/T) from the latest watchlist_focus cycle.

    The Neural-Cloud attention gradient: tier is the curator's hotness label
    already persisted by the focus cycle. Used display-only to decide which mkt
    rows stay individual nodes vs fold into the tail haze. Read-only — nothing
    here re-ranks or re-tiers anything; it mirrors the focus table as-is.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT venue, symbol, tier FROM watchlist_focus "
            "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus) "
            "AND tier IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    for venue, symbol, tier in rows:
        out[_signal_key(str(venue), str(symbol))] = str(tier).upper()
    return out


def _query_universe_all(db_path: Path) -> list[dict[str, Any]]:
    """FULL tradable universe → galaxy_universe records (display-only).

    Distinct from :func:`_query_universe` (the is_active=1 trading-focus subset,
    preserved unchanged): this view emits **every tradable symbol per venue** so
    the globe shows the real market shell, with an ``active`` flag (is_active=1 =
    bot focus = lightup candidate) instead of dropping dormant rows.

    Per-venue scope (read-only, no behavior touched):
      - OKX     : all rows (crypto spot, ~189).
      - Capital : only stream-whitelisted asset_classes (forex/index/commodity);
                  crypto is excluded per the stream SSOT
                  (``asset_class_allowed_for_venue``), matching the bot's
                  FX/index/commodity trading intent (~201).
      - Alpaca  : every is_active=1 row (bot focus always lights up) PLUS the
                  top-N dormant rows by 24h volume, capped at
                  :func:`_alpaca_globe_max` (default 300) — a render-perf cap on
                  the dormant shell only, logged when it truncates.

    The ``is_active`` flag and venue scoping are read straight from the universe
    table; nothing here re-ranks, re-gates, or session-filters.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT venue, symbol, asset_class, vol_24h_usd, is_active "
            "FROM universe ORDER BY vol_24h_usd DESC, symbol ASC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    alpaca_cap = _alpaca_globe_max()
    alpaca_emitted = 0
    alpaca_total = 0
    out: list[dict[str, Any]] = []
    for venue, symbol, asset_class, vol_24h, is_active in rows:
        v = str(venue).lower()
        ac = str(asset_class or "crypto")
        active = int(is_active or 0) == 1
        # Capital: honor the stream asset_class whitelist SSOT (crypto excluded).
        if v.startswith("cap") and not asset_class_allowed_for_venue(v, ac):
            continue
        # Alpaca: render-perf cap on the DORMANT shell only — every active row
        # (bot focus) is always emitted so its lightup is never truncated.
        if v.startswith("alp") and not active:
            alpaca_total += 1
            if alpaca_emitted >= alpaca_cap:
                continue
            alpaca_emitted += 1
        vol = float(vol_24h or 0.0)
        n_24h = int(min(500, max(1, vol / 1_000_000.0)))
        out.append(
            {
                "ticker": str(symbol),
                "exchange": _short_venue(v),
                "asset_group": _asset_group_for(ac),
                "n_24h": n_24h,
                "is_active": int(is_active or 0),
            }
        )
    if alpaca_total > alpaca_emitted:
        _LOG.info(
            "globe: Alpaca universe capped at %d/%d by POLARIS_GLOBE_ALPACA_MAX "
            "(display-only; trading universe untouched)",
            alpaca_emitted,
            alpaca_total,
        )
    return out


def _signal_key(venue: str, symbol: str) -> str:
    """Stable join key (short-venue + cleaned ticker) shared by signal counts,
    mkt nodes and watch nodes so the REAL per-instrument signal count binds back
    onto the right globe ticker node regardless of array order. Display-only."""
    tk = str(symbol).split(":")[-1].split("-")[0]
    return f"{_short_venue(venue)}:{tk}"


def _query_signal_counts(db_path: Path) -> dict[str, int]:
    """REAL per-instrument pipeline-catch counts — 30m firing window + a 4h
    afterglow companion under ``key + "|4h"`` (display-only).

    The globe's lightup was previously driven by ``is_active`` (the universe FOCUS
    flag) with ``signal_count_30m`` hard-coded 0. This joins the real ``signals``
    table (per-strategy raw signals already persisted, ``instrument_id`` =
    ``{venue}:{symbol}``) into a per-instrument count keyed by
    :func:`_signal_key`, so a ticker glows the moment its pipeline actually caught
    a signal. Pure view — nothing here gates, sizes or throttles a trade.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        # 30m = the live "firing" window; 4h afterglow rides along so
        # low-frequency venues (Alpaca 1D strategies — Jin 2026-07-11 "알파카는
        # 신호 안뜨는데 아예?") stay visible between their sparse bar closes.
        rows = conn.execute(
            "SELECT instrument_id, "
            "SUM(CASE WHEN ts >= strftime('%s','now') - 1800 THEN 1 ELSE 0 END), "
            "COUNT(*) FROM signals "
            "WHERE ts >= strftime('%s','now') - 14400 "
            "GROUP BY instrument_id"
        ).fetchall()
    except sqlite3.Error:
        return counts
    finally:
        conn.close()
    for instrument_id, c30, c4h in rows:
        inst = str(instrument_id or "")
        if ":" in inst:
            venue, symbol = inst.split(":", 1)
        else:
            venue, symbol = "okx", inst
        key = _signal_key(venue, symbol)
        counts[key] = int(c30)
        counts[key + "|4h"] = int(c4h)
    return counts


def _query_watch_focus(db_path: Path, held: set[str]) -> list[dict[str, Any]]:
    """tier7 'watch' source — latest focus cycle symbols not already open."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT venue, symbol, focus_score, target_bucket FROM watchlist_focus "
            "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus) "
            "ORDER BY focus_rank ASC LIMIT 40"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for venue, symbol, score, bucket in rows:
        if str(symbol) in held:
            continue
        out.append(
            {
                "venue": str(venue),
                "symbol": str(symbol),
                "score": float(score or 0.0),
                "bucket": str(bucket or "satellite"),
            }
        )
    return out


def _query_actions(db_path: Path) -> dict[str, int]:
    """tier10 'action' source — recent gate decisions + pending order intents."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        for decision, c in conn.execute(
            "SELECT decision, COUNT(*) FROM gate_events "
            "WHERE created_ts >= strftime('%s','now') - 1800 AND decision IS NOT NULL "
            "GROUP BY decision"
        ).fetchall():
            counts[f"gate_{str(decision).lower()}"] = int(c)
        for status, c in conn.execute(
            "SELECT status, COUNT(*) FROM order_intents GROUP BY status"
        ).fetchall():
            counts[f"order_{str(status).lower()}"] = int(c)
    except sqlite3.Error:
        return counts
    finally:
        conn.close()
    return counts


def _query_kills_and_heartbeat(
    db_path: Path,
) -> tuple[list[dict[str, Any]], int]:
    """Globe activity source — recent G3/G4 KILL events + decision heartbeat ts.

    Read-only, display-only. Returns:
      - ``kills``: the most recent KILL gate decisions (ticker + gate + ts),
        best-effort joined to ``signals.instrument_id``. Feeds the Kill Sediment
        pass (a transient grey mote that fades outward — *measurement honesty*,
        NOT a throttle: nothing here blocks or dampens a trade).
      - ``last_decision_ts``: MAX(gate_events.created_ts), the tick heartbeat the
        globe watches to fire a one-shot conductor pulse. Snapshot-derived only —
        the SSE stream is untouched.
    """
    if not db_path.exists():
        return [], 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    kills: list[dict[str, Any]] = []
    last_ts = 0
    try:
        row = conn.execute("SELECT MAX(created_ts) FROM gate_events").fetchone()
        last_ts = int(row[0] or 0) if row else 0
        for gate_id, signal_id, instrument, ts in conn.execute(
            "SELECT ge.gate_id, ge.signal_id, s.instrument_id, ge.created_ts "
            "FROM gate_events ge LEFT JOIN signals s ON s.signal_id = ge.signal_id "
            "WHERE ge.decision = 'KILL' ORDER BY ge.created_ts DESC LIMIT 12"
        ).fetchall():
            # Two signal_id formats live in gate_events: entry-phase rows carry a
            # hex signal_id that joins to signals.instrument_id; position-phase
            # rows (G6-G8) carry a synthetic id `pos_{hash}_{venue}_{TICKER}_{ts}`
            # that embeds the instrument directly. Resolve both so every kill mote
            # binds to its REAL ticker (was always '?' — the join-NULL bug).
            ticker = _kill_ticker(str(signal_id or ""), str(instrument or ""))
            kills.append(
                {"ticker": ticker, "gate_id": int(gate_id or 0), "ts": int(ts or 0)}
            )
    except sqlite3.Error:
        return kills, last_ts
    finally:
        conn.close()
    return kills, last_ts


def _kill_ticker(signal_id: str, instrument: str) -> str:
    """Resolve a kill mote's REAL ticker from a gate_events row (display-only).

    Prefers the joined ``signals.instrument_id`` (entry-phase hex signal_id). When
    that is NULL (position-phase synthetic id), parse the embedded instrument from
    ``pos_{hash}_{venue}_{TICKER}_{ts}`` — strip the ``pos_`` head, the trailing
    ``_{ts}`` epoch, the leading hash, and the venue token, leaving the symbol
    (which itself may contain underscores, e.g. ``AUDUSD_ZERO``). Falls back to
    ``'?'`` only when nothing parses. No trading behavior here.
    """
    if instrument:
        return instrument.split(":")[-1].split("-")[0]
    sid = signal_id or ""
    if sid.startswith("pos_"):
        parts = sid[4:].split("_")
        # drop trailing epoch token if numeric
        if parts and parts[-1].isdigit():
            parts = parts[:-1]
        # drop leading hash token
        if len(parts) >= 2:
            parts = parts[1:]
        # drop a leading venue token if present (okx/capital/cap/alpaca/alp)
        if parts and parts[0].lower() in ("okx", "capital", "cap", "alpaca", "alp"):
            parts = parts[1:]
        sym = "_".join(parts)
        if sym:
            return sym.split(":")[-1].split("-")[0]
    return "?"


def _query_faults(db_path: Path) -> dict[str, int]:
    """tier9 'obs' source — recent strategy fault events by type."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: dict[str, int] = {}
    try:
        for ftype, c in conn.execute(
            "SELECT fault_type, COUNT(*) FROM strategy_fault_events "
            "WHERE event_ts >= strftime('%s','now') - 3600 GROUP BY fault_type"
        ).fetchall():
            out[str(ftype)] = int(c)
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


# ── tier builders ────────────────────────────────────────────────────────────
def _pos_nodes_and_trades(
    snap: Any, regime: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """tier0 'pos' nodes + matching live_trades from open positions."""
    nodes: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for i, p in enumerate(snap.positions):
        direction = _direction(p.side)
        ticker = p.symbol.split(":")[-1].split("-")[0] if p.symbol else p.symbol
        trade_id = f"{p.venue}_{ticker}_{int(snap.ts_now)}"
        pnl_pct = round(p.delta_pct, 4)
        intensity = round(min(1.0, abs(pnl_pct) / 5.0 + 0.25), 4)
        # Per-instrument regime + live exit-FSM state (additive, display-only).
        # The globe tints the active node by its OWN regime and marks positions
        # actively in the exit FSM (touched/protected/trailing). Source = the
        # PositionRow columns already collected (regime_lookup + exit_state);
        # nothing here gates or sizes — pure view fields the globe mirrors.
        pos_regime = str(getattr(p, "regime", "") or "neutral")
        exit_state = str(getattr(p, "exit_state", "") or "open")
        nodes.append(
            {
                "id": f"pos_{p.venue}_{ticker}",
                "label": ticker,
                "ticker": ticker,
                "direction": direction,
                "exchange": _short_venue(p.venue),
                "trade_id": trade_id,
                "strategy_id": p.strategy_id,
                "regime": pos_regime,
                "exit_state": exit_state,
                "asset_group": "crypto" if "crypto" in p.symbol else "other",
                "pnl_usd": round(p.upnl_usd, 4),
                "pnl_pct": pnl_pct,
                "size_usd": round(p.size_usd, 4),
                "intensity": intensity,
                "size_mul": round(min(1.5, max(0.5, p.cell_mult or 1.0)), 4),
                "cluster": "pos",
                "tier": 0,
                "state": "firing" if abs(pnl_pct) > 0.5 else "lit",
                "i": i,
                "phase": _phase(i),
            }
        )
        trades.append(
            {
                "trade_id": trade_id,
                "ticker": ticker,
                "strategy_id": p.strategy_id,
                "exchange": _short_venue(p.venue),
                "direction": direction,
                "entry_ts": float(snap.ts_now - p.held_sec),
                "entry_price": round(p.entry_price, 8),
                "current_price": round(p.last_price, 8),
                "size_usd": round(p.size_usd, 4),
                "asset_group": "crypto" if "crypto" in p.symbol else "other",
                "regime": pos_regime,
                "exit_state": exit_state,
                "pnl_usd": round(p.upnl_usd, 4),
                "pnl_pct": pnl_pct,
                "max_profit_pct": max(0.0, pnl_pct),
                "hold_seconds": round(p.held_sec, 1),
            }
        )
    return nodes, trades


def _exit_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier1 'exit' nodes — exit-reason patterns from recent_trades."""
    from collections import Counter

    ct = Counter(t.exit_reason or "EXIT" for t in snap.recent_trades)
    # Always render the canonical exit patterns so the ring is never empty.
    canonical = ["TP", "SL", "TRAIL", "TIME", "BEP", "SIGNAL"]
    reasons = list(dict.fromkeys(list(ct.keys()) + canonical))
    nodes: list[dict[str, Any]] = []
    for j, reason in enumerate(reasons):
        i = base_i + j
        n = int(ct.get(reason, 0))
        nodes.append(
            {
                "id": f"exit_{reason}",
                "label": f"exit_{reason}",
                "ticker": None,
                "count": n,
                "intensity": round(min(1.0, 0.3 + n / 8.0), 4),
                "size_mul": 1.2,
                "cluster": "exit",
                "tier": 1,
                "state": "firing" if n >= 3 else ("lit" if n > 0 else "dormant"),
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


def _regime_nodes(snap: Any, base_i: int) -> tuple[list[dict[str, Any]], str]:
    """tier3 'reg' nodes from regime_bars. Returns (nodes, dominant_regime)."""
    nodes: list[dict[str, Any]] = []
    dominant = "neutral"
    best = -1
    for j, rb in enumerate(snap.regime_bars):
        if rb.count > best:
            best = rb.count
            dominant = rb.regime
        i = base_i + j
        nodes.append(
            {
                "id": f"reg_regime_{rb.regime}",
                "label": f"regime_{rb.regime}",
                "ticker": None,
                "intensity": round(min(1.0, 0.3 + rb.count / 20.0), 4),
                "size_mul": 1.5,
                "cluster": "reg",
                "tier": 3,
                "state": "firing" if rb.count == best else "lit",
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes, dominant


def _strat_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier5 'strat' nodes for ALL 7 registered strategies (live stats merged)."""
    by_id = {s.strategy_id: s for s in snap.strategy_stats}
    nodes: list[dict[str, Any]] = []
    for j, (sid, cls) in enumerate(STRATEGY_REGISTRY.items()):
        meta = cls.metadata
        st = by_id.get(sid)
        open_n = st.open_n if st else 0
        closed_n = st.closed_n if st else 0
        pf = st.pf if st else 1.0
        pnl = st.pnl_usd if st else 0.0
        active = open_n > 0 or closed_n > 0
        group = _asset_group_for(meta.asset_class)
        i = base_i + j
        nodes.append(
            {
                "id": f"strat_{sid}",
                "label": sid,
                "ticker": None,
                "exchange": _short_venue(meta.venue),
                "intensity": round(min(1.0, 0.3 + open_n * 0.15), 4),
                "size_mul": round(min(1.5, max(0.7, pf or 1.0)), 4),
                "trades_24h": closed_n,
                # real slot occupancy (Jin 2026-07-11 wall slot pips): live
                # open count vs the strategy's own max_positions metadata.
                "open_n": int(open_n),
                "max_open": int(getattr(meta, "max_positions", 0) or 0),
                "pnl_usd": round(pnl, 4),
                "asset_group": group,
                "cluster": "strat",
                "tier": 5,
                "state": "firing" if open_n > 0 else ("lit" if active else "dormant"),
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


def _probe_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier14 'probe' nodes — the FULL position-probe taxonomy (Jin 2026-07-10
    "안 쓰는 것도 다 띄워야": every advisor type gets a node; dormant when it
    has no recent reading). Activity = snapshot.probe_events (G6-attached
    advisor readings), so brightness is real recency, never fabricated."""
    taxonomy = ("profit_taking", "loss_defense", "technical")
    recent: dict[str, int] = {k: 0 for k in taxonomy}
    latest: dict[str, int] = {k: 0 for k in taxonomy}
    now = int(snap.ts_now)
    for ev in getattr(snap, "probe_events", []) or []:
        name = str(ev.get("name") or "")
        if name in recent and now - int(ev.get("ts") or 0) <= 1800:
            recent[name] += 1
            latest[name] = max(latest[name], int(ev.get("ts") or 0))
    nodes: list[dict[str, Any]] = []
    for j, name in enumerate(taxonomy):
        n = recent[name]
        i = base_i + j
        nodes.append(
            {
                "id": f"probe_{name}",
                "label": name,
                "ticker": None,
                "intensity": round(min(1.0, 0.25 + n * 0.08), 4),
                "size_mul": 1.0,
                "readings_30m": n,
                "cluster": "probe",
                "tier": 14,
                "state": "firing" if n > 0 else "dormant",
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


def _watch_nodes(
    watch: list[dict[str, Any]], signal_counts: dict[str, int], base_i: int,
) -> list[dict[str, Any]]:
    """tier7 'watch' nodes — signal-watch focus symbols not yet open.

    Same REAL-signal glow rule as the mkt shell: a watch node with a live 30m
    signal catch is ``firing``; the core-bucket flag is only a dim fallback.
    """
    nodes: list[dict[str, Any]] = []
    for j, w in enumerate(watch):
        i = base_i + j
        ticker = w["symbol"].split(":")[-1].split("-")[0]
        is_core = w["bucket"] == "core"
        sig_n = int(signal_counts.get(_signal_key(w["venue"], w["symbol"]), 0))
        state = "firing" if sig_n > 0 else ("lit" if is_core else "dormant")
        nodes.append(
            {
                "id": f"watch_{w['venue']}_{ticker}",
                "label": ticker,
                "ticker": ticker,
                "exchange": _short_venue(w["venue"]),
                "score": round(w["score"], 4),
                "intensity": round(min(1.0, 0.3 + abs(w["score"]) / 2.0), 4),
                "size_mul": 1.0,
                "signal_count_30m": sig_n,
                "cluster": "watch",
                "tier": 7,
                "state": state,
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


def _mkt_priority(
    tier: str, active: bool, sig_n: int,
) -> int:
    """Attention priority of a universe row (lower = kept as an individual node).

    Display-only LOD ordering for tier-LOD. A row whose pipeline actually caught
    a live signal (``sig_n > 0``) is always promoted to the very front so a real
    catch is never folded into the haze. Otherwise: tier S/A/B by the curator's
    tier label, then active-but-untiered focus rows, then the dormant T tail.
    """
    if sig_n > 0:
        return -1
    if tier in _TIER_PRIORITY:
        return _TIER_PRIORITY[tier]
    if active:
        return 3  # focus row with no tier label yet — above the dormant tail
    return _TIER_TAIL_PRIORITY


def _mkt_nodes(
    universe: list[dict[str, Any]],
    signal_counts: dict[str, int],
    tier_map: dict[str, str],
    node_cap: int,
    base_i: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """tier8 'mkt' market-shell nodes + the per-venue tail-haze aggregate.

    GLOW SOURCE (Jin 2026-06-23): the REAL per-instrument 30m signal count drives
    the lightup — a node with ``signal_count_30m > 0`` is ``firing`` (its pipeline
    actually caught a signal). ``is_active`` (universe FOCUS) is a secondary dim
    cue only.

    TIER-LOD (Jin 2026-06-24): the Neural-Cloud attention gradient. Only the
    hottest rows reach the globe as individual nodes — open positions / watch
    nodes (emitted elsewhere), plus the top ``node_cap`` mkt rows by attention
    priority (live-signal catches first, then tier S·A in full + a slice of tier
    B, then active focus rows). The long tail (tier B overflow + the dormant T
    library) is summarised per venue into ``tail_aggregate`` descriptors the
    globe renders as a cheap, count-independent haze. graph.json node count stays
    ~cap regardless of how large the watched universe grows. Display-only —
    nothing gated/sized/re-ranked here; the universe table is untouched.

    PER-VENUE budget (Jin 2026-06-25): ``node_cap`` applies INDEPENDENTLY per
    exchange, not as one global pool. A single global cap let Alpaca's ~12.9k
    rows (148 tier-S + 314 tier-A) win every priority slot and starve OKX's 219
    active rows down to ~4 individual nodes (the rest folded into haze, so the
    globe showed only ~5 OKX dots). With a per-venue cap each venue's own shell
    fills before folding to its haze, so OKX/Capital render their full tradable
    universe while Alpaca's huge tail is still bounded per venue.

    Returns ``(individual_nodes, tail_aggregate)``.
    """
    # Stable-sort universe rows by attention priority (the universe list is
    # already vol-desc, so equal-priority rows keep their volume order). The
    # first ``node_cap`` rows PER VENUE become individual nodes; the rest feed
    # that venue's haze.
    enriched: list[tuple[int, dict[str, Any], int, str, bool, int]] = []
    for u in universe:
        key = _signal_key(u["exchange"], u["ticker"])
        active = int(u.get("is_active", 0)) == 1
        sig_n = int(signal_counts.get(key, 0))
        sig_4h = int(signal_counts.get(key + "|4h", 0))
        tier = tier_map.get(key, "")
        enriched.append(
            (_mkt_priority(tier, active, max(sig_n, min(1, sig_4h))), u, sig_n, tier, active, sig_4h)
        )
    order = sorted(range(len(enriched)), key=lambda k: enriched[k][0])

    nodes: list[dict[str, Any]] = []
    # per-venue tail accumulator: count + summed n_24h (→ representative density).
    tail: dict[str, dict[str, float]] = {}
    # per-venue emitted-node counter → the cap is applied independently per venue.
    venue_emitted: dict[str, int] = {}
    for k in order:
        _prio, u, sig_n, tier, active, sig_4h = enriched[k]
        ex = str(u["exchange"])
        if venue_emitted.get(ex, 0) >= node_cap:
            # Fold into the per-venue tail haze (count + density proxy). A live
            # signal catch is never here (priority -1 keeps it individual).
            slot = tail.setdefault(ex, {"count": 0.0, "n24_sum": 0.0, "active": 0.0})
            slot["count"] += 1.0
            slot["n24_sum"] += float(u["n_24h"])
            if active:
                slot["active"] += 1.0
            continue
        venue_emitted[ex] = venue_emitted.get(ex, 0) + 1
        i = base_i + len(nodes)
        ticker = str(u["ticker"]).split(":")[-1].split("-")[0]
        state = "firing" if sig_n > 0 else ("lit" if active else "dormant")
        intensity = round(min(1.0, 0.1 + u["n_24h"] / 500.0), 4)
        size_mul = round(min(1.0, 0.6 + u["n_24h"] / 1000.0), 4)
        # ── Capital weekend-shell visibility floor (Jin 2026-06-28, display-only) ──
        # On weekends Capital's venue marketStatus turns ~all its instruments
        # is_active=0 → state="dormant", and their forex/commodity/index 24h vol is
        # tiny or zero, so the vol-driven intensity/size collapse to the bottom
        # (~0.102 / 0.6). The client then folds them into the cheap dim point-cloud
        # which (for shell_floor nodes) reads these fields — without a floor they
        # render as near-invisible grey dust and the whole Capital galaxy looks
        # "empty/off". We stamp a ``shell_floor`` hint + lift the DISPLAY weight to
        # a visible minimum so the venue shell still shows ("asleep but present").
        # Scoped to Capital dormant only → OKX/Alpaca untouched; the node STAYS
        # dormant (no glow, no active spoof) — market-closed is the truth, only its
        # on-globe visibility changes. Pure render weight: nothing gated/sized/
        # re-ranked, the universe table is never read or mutated here.
        shell_floor = state == "dormant" and str(u["exchange"]).lower().startswith("cap")
        if shell_floor:
            intensity = max(intensity, _SHELL_INTENSITY_FLOOR)
            size_mul = max(size_mul, _SHELL_SIZE_FLOOR)
        nodes.append(
            {
                "id": f"mkt_{u['exchange']}_{u['ticker']}",
                "label": ticker,
                "ticker": ticker,
                "exchange": u["exchange"],
                "asset_group": u["asset_group"],
                "active": active,
                "tier_label": tier or None,
                "intensity": intensity,
                "size_mul": size_mul,
                "shell_floor": shell_floor,
                "signal_count_30m": sig_n,
                "signal_count_4h": sig_4h,
                "cluster": "mkt",
                "tier": 8,
                "state": state,
                "i": i,
                "phase": _phase(i),
            }
        )

    tail_aggregate: list[dict[str, Any]] = []
    for ex, slot in tail.items():
        cnt = int(slot["count"])
        if cnt <= 0:
            continue
        # representative density 0..1 for haze brightness: mean 24h-volume proxy
        # of the tail rows (display weight only, never a sizing/risk input).
        density = round(min(1.0, 0.12 + (slot["n24_sum"] / cnt) / 500.0), 4)
        tail_aggregate.append(
            {
                "exchange": ex,
                "count": cnt,
                "active": int(slot["active"]),
                "density": density,
            }
        )
    return nodes, tail_aggregate


def _obs_nodes(snap: Any, faults: dict[str, int], base_i: int) -> list[dict[str, Any]]:
    """tier9 'obs' square satellites — system health (alerts + faults + gpt ok)."""
    from collections import Counter

    alert_ct = Counter(a.level for a in snap.alerts)
    gpt_calls = sum(g.calls_per_h for g in snap.gpt_stats)
    items: list[tuple[str, int, str]] = [
        ("health_errors", alert_ct.get("ERROR", 0) + alert_ct.get("CRITICAL", 0), "obs"),
        ("health_warnings", alert_ct.get("WARNING", 0) + alert_ct.get("WARN", 0), "obs"),
        ("health_fault_exception", faults.get("exception", 0), "obs"),
        ("health_fault_reject", faults.get("reject", 0), "obs"),
        ("health_gpt_calls_h", int(gpt_calls), "obs"),
        ("health_ok", 1 if not snap.alerts else 0, "obs"),
    ]
    nodes: list[dict[str, Any]] = []
    for j, (label, cnt, kind) in enumerate(items):
        i = base_i + j
        node_id = f"obs_{label}"
        degraded = cnt > 0 and label != "health_ok" and label != "health_gpt_calls_h"
        state = "firing" if degraded else "lit"
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "ticker": None,
                "count": cnt,
                "intensity": round(min(1.0, 0.4 + cnt / 10.0), 4),
                "size_mul": 1.0,
                "cluster": "obs",
                "tier": 9,
                "shape": "square",
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields(kind, node_id, state),
            }
        )
    return nodes


def _action_nodes(actions: dict[str, int], base_i: int) -> list[dict[str, Any]]:
    """tier10 'action' square satellites — gate decisions + order intents."""
    if not actions:
        actions = {"gate_idle": 0}
    nodes: list[dict[str, Any]] = []
    for j, (label, cnt) in enumerate(sorted(actions.items())):
        i = base_i + j
        node_id = f"action_{label}"
        state = "firing" if cnt >= 5 else ("lit" if cnt > 0 else "dormant")
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "ticker": None,
                "count": cnt,
                "intensity": round(min(1.0, 0.4 + cnt / 20.0), 4),
                "size_mul": 1.0,
                "cluster": "action",
                "tier": 10,
                "shape": "square",
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields("action", node_id, state),
            }
        )
    return nodes


def _orbit_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier11 'orbit' function satellites — learners + AI providers/judges."""
    nodes: list[dict[str, Any]] = []
    j = 0

    # learners (live) — hourly learner network slots
    for ln in snap.learners:
        i = base_i + j
        node_id = f"orbit_learner_{ln.learner_id}_{ln.key}".replace(":", "_")
        active = (ln.n_eff or 0) > 0
        state = "firing" if abs(ln.delta_1h or 0.0) > 0.01 else ("lit" if active else "dormant")
        nodes.append(
            {
                "id": node_id,
                "label": f"{ln.learner_id}:{ln.key}",
                "ticker": None,
                "cluster": "orbit",
                "tier": 11,
                "orbit_kind": "learner",
                "inter_radius": _ORBIT_INTER_RADIUS["learner"],
                "value": round(ln.value, 4),
                "delta_1h": round(ln.delta_1h, 4),
                "intensity": round(min(1.0, 0.4 + abs(ln.delta_1h or 0.0)), 4),
                "size_mul": 1.0,
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields("learner", node_id, state),
            }
        )
        j += 1

    # AI providers (gpt models) — data-feed satellites
    for g in snap.gpt_stats:
        i = base_i + j
        node_id = f"orbit_provider_{g.model}"
        state = "firing" if g.calls_per_h >= 100 else ("lit" if g.calls_per_h > 0 else "dormant")
        nodes.append(
            {
                "id": node_id,
                "label": f"prov_{g.model}",
                "ticker": None,
                "cluster": "orbit",
                "tier": 11,
                "orbit_kind": "provider",
                "inter_radius": _ORBIT_INTER_RADIUS["provider"],
                "calls_per_h": int(g.calls_per_h),
                "intensity": round(min(1.0, 0.4 + g.calls_per_h / 1000.0), 4),
                "size_mul": 1.0,
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields("provider", node_id, state),
            }
        )
        j += 1

    # AI judges (per-gate AI roles) — the decision functions as satellites
    for judge in ("entry_judge", "exit_advise", "validator", "post_trade_reflector"):
        i = base_i + j
        node_id = f"orbit_judge_{judge}"
        state = "lit"
        nodes.append(
            {
                "id": node_id,
                "label": judge,
                "ticker": None,
                "cluster": "orbit",
                "tier": 11,
                "orbit_kind": "ai_judge",
                "inter_radius": _ORBIT_INTER_RADIUS["ai_judge"],
                "intensity": 0.5,
                "size_mul": 1.0,
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields("ai_judge", node_id, state),
            }
        )
        j += 1

    return nodes


def _axis_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier12 'axis' dimension satellites — session × liquidity × crisis."""
    crisis_count = next(
        (rb.count for rb in snap.regime_bars if rb.regime == "crisis"), 0
    )
    components: list[tuple[str, str, str]] = [
        ("session_asia", "session", "lit"),
        ("session_eu", "session", "lit"),
        ("session_us", "session", "lit"),
        ("liq_small", "liq", "dormant"),
        ("liq_mid", "liq", "lit"),
        ("liq_large", "liq", "lit"),
        ("crisis_l1", "crisis", "firing" if crisis_count > 0 else "dormant"),
        ("crisis_l2", "crisis", "dormant"),
        ("crisis_l3", "crisis", "dormant"),
    ]
    nodes: list[dict[str, Any]] = []
    for j, (label, kind, state) in enumerate(components):
        i = base_i + j
        node_id = f"axis_{label}"
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "ticker": None,
                "cluster": "axis",
                "tier": 12,
                "axis_kind": kind,
                "intensity": 0.5,
                "size_mul": 1.0,
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields(kind, node_id, state),
            }
        )
    return nodes


def _exit_tally_nodes(snap: Any, base_i: int) -> list[dict[str, Any]]:
    """tier13 'exit_tally' square outer ring — exit_reason counts."""
    from collections import Counter

    ct = Counter(t.exit_reason or "EXIT" for t in snap.recent_trades)
    if not ct:
        ct = Counter({"NONE": 0})
    nodes: list[dict[str, Any]] = []
    for j, (reason, cnt) in enumerate(sorted(ct.items())):
        i = base_i + j
        node_id = f"exit_tally_{reason}"
        state = "firing" if cnt >= 3 else ("lit" if cnt > 0 else "dormant")
        nodes.append(
            {
                "id": node_id,
                "label": f"tally_{reason}",
                "ticker": None,
                "count": int(cnt),
                "intensity": round(min(1.0, 0.4 + cnt / 8.0), 4),
                "size_mul": 1.0,
                "cluster": "exit_tally",
                "tier": 13,
                "shape": "square",
                "state": state,
                "i": i,
                "phase": _phase(i),
                **_orbit_fields("exit_tally", node_id, state),
            }
        )
    return nodes


def _recent_closes(snap: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in snap.recent_trades:
        ticker = t.symbol.split(":")[-1].split("-")[0] if t.symbol else t.symbol
        out.append(
            {
                "ticker": ticker,
                "direction": _direction(t.side_close),
                "pnl_usd": round(t.pnl_usd, 4),
                "pnl_pct": round(t.r_units, 4),
                "exit_type": t.exit_reason or "EXIT",
                "exit_ts": float(t.ts_close),
                "hold_seconds": round(t.held_sec, 1),
                "exchange": _short_venue(t.venue),
            }
        )
    return out


def _gate_funnel(snap: Any) -> list[dict[str, Any]]:
    """Globe Gate-Activity Ring source — per-gate decision VOLUME (display-only).

    Reshapes the snapshot's ``gate_decisions`` (the per-gate decision summary that
    replaced the pass/kill funnel — flow_not_block makes a pass/kill ratio
    structurally ~100% / zero-information) into compact {gate_id,label,total}
    segments. The globe draws one arc segment per gate around the conductor sized
    by decision volume — Jin's literal "활동 그래픽화". Nothing gated/sized here.
    ``pass_n`` is kept = volume / ``kill_n`` = 0 for the existing ring shader; the
    bot never blocks, so there is no red taper to draw.
    """
    out: list[dict[str, Any]] = []
    for g in getattr(snap, "gate_decisions", []) or []:
        n = int(getattr(g, "n", 0))
        out.append(
            {
                "gate_id": int(g.gate_id),
                "label": str(g.label),
                "pass_n": n,
                "kill_n": 0,
                "total": n,
            }
        )
    return out


def _galaxy_activity(snap: Any) -> list[dict[str, Any]]:
    """Per-galaxy (venue) regime + normalized activity (display-only).

    Merges two already-collected snapshot lists the globe ignored:
      - ``regime_states`` → a dominant regime label per venue (highest-confidence
        group) for the Regime Tide background wash.
      - ``streams`` → open_n / daily_trades / exposed_usd per venue, each
        normalized 0..1 for the Stream Breath halo glow (busy venue = brighter).

    Pure view: normalization is cosmetic (glow intensity), never a sizing/risk
    input. The trading pipeline never reads this.
    """
    # dominant regime per venue: pick the highest-confidence group's regime.
    best_regime: dict[str, tuple[float, str]] = {}
    for rs in snap.regime_states:
        v = _short_venue(rs.venue)
        conf = float(rs.confidence or 0.0)
        if v not in best_regime or conf > best_regime[v][0]:
            best_regime[v] = (conf, str(rs.regime or "neutral"))

    # normalization denominators (max across venues, ≥1 to avoid div-by-zero).
    max_open = max((s.open_positions_n for s in snap.streams), default=0) or 1
    max_trades = max((s.daily_trades for s in snap.streams), default=0) or 1
    max_exposed = max((abs(s.exposed_usd) for s in snap.streams), default=0.0) or 1.0

    out: list[dict[str, Any]] = []
    for s in snap.streams:
        v = _short_venue(s.venue)
        regime = best_regime.get(v, (0.0, "neutral"))[1]
        out.append(
            {
                "venue": v,
                "regime": regime,
                "open_n": int(s.open_positions_n),
                "trades": int(s.daily_trades),
                "exposed_usd": round(float(s.exposed_usd), 2),
                # 0..1 activity for halo glow (display-only weight, not a multiplier)
                "open_norm": round(s.open_positions_n / max_open, 4),
                "trades_norm": round(s.daily_trades / max_trades, 4),
                "exposed_norm": round(abs(s.exposed_usd) / max_exposed, 4),
            }
        )
    return out


# ── /flow console v2 (Jin 2026-07-11 "제대로 하자") ──────────────────────────
# gate_flow_1h is the ONLY net-new SQL this block issues — everything else is a
# zero-cost field copy off the already-computed collect_snapshot() result. A
# module-level 5s cache (independent of the 1s graph-refresh cadence) keeps the
# read to 1 query/5s instead of 1/1s (idx_gate_events_dash covers it either way,
# but there is no reason to pay it 5x as often as it can possibly change
# anything a human is reading). Read-only; never gates/sizes/throttles a trade.
_gate_flow_cache: dict[str, Any] = {"ts": 0.0, "rows": []}
_GATE_FLOW_TTL = 5.0


def _query_gate_flow_1h(db_path: Path) -> dict[int, int]:
    now = time.time()
    if now - _gate_flow_cache["ts"] >= _GATE_FLOW_TTL:
        rows: list[Any] = []
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT gate_id, COUNT(*) AS n FROM gate_events "
                    "WHERE created_ts >= ? GROUP BY gate_id",
                    (int(time.time()) - 3600,),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            finally:
                conn.close()
        _gate_flow_cache["ts"] = now
        _gate_flow_cache["rows"] = rows
    return {int(gid): int(n or 0) for gid, n in _gate_flow_cache["rows"]}


def _console_block(snap: Any, db_path: Path) -> dict[str, Any]:
    """Console v2 read-model — powers ``static/wall_console_readouts.js``'s
    corner panels (equity/AI/cell-ledger/exit-FSM/register/regime-matrix).

    Pure field-copy off ``snap`` (collect_snapshot already computed every
    number here) plus the one cached ``gate_flow_1h`` query above. None-guarded
    throughout: a missing/older-schema field degrades to a zero/empty reading
    the client renders as "—", never a crash or a fabricated number.
    """
    since_reset = snap.virtual_since_reset
    core = {
        "equity_now": round(float(snap.equity_now), 2),
        "peak_equity": round(float(snap.peak_equity), 2),
        "starting_capital": round(float(snap.starting_capital), 2),
        "real_fee_net": round(float(snap.equity_now_real_fee_net), 2),
        "virtual_since_reset": dataclasses.asdict(since_reset) if since_reset else None,
    }

    now_s = int(snap.ts_now or time.time())
    sessions = {v: resolve_venue_session(v, now_s) for v in ("okx", "capital", "alpaca")}

    streams = [
        {
            "venue": _short_venue(s.venue),
            "marks_label": s.marks_label,
            "age_sec": int(s.marks_age_sec),
            "exposed": round(float(s.exposed_usd), 2),
            "upnl": round(float(s.upnl_usd), 2),
            "equity_usd": round(float(s.virtual_equity_usd), 2),
            "drawdown_pct": round(float(s.drawdown_pct), 2),
        }
        for s in snap.streams
    ]

    gcalls = sum(int(g.calls_per_h) for g in snap.gpt_stats)
    gok_num = sum(g.ok_pct * g.calls_per_h for g in snap.gpt_stats)
    gpt = {
        "calls_per_h": gcalls,
        "ok_pct": round(gok_num / gcalls, 1) if gcalls else 100.0,
        "err_n": sum(int(g.err_n) for g in snap.gpt_stats),
        "cost_per_h": round(sum(g.cost_per_h_usd for g in snap.gpt_stats), 4),
    }

    def _cell(row: Any) -> dict[str, Any]:
        return {
            "exchange": row.exchange, "strategy": row.strategy, "ticker": row.ticker,
            "regime": row.regime, "n_eff": round(float(row.n_eff), 2),
            "score": round(float(row.score), 3), "mult": round(float(row.mult), 3),
        }

    exit_tally: dict[str, int] = {}
    for t in snap.recent_trades:
        r = t.exit_reason or "EXIT"
        exit_tally[r] = exit_tally.get(r, 0) + 1

    exit_block = {
        "fsm_states": dict(snap.exit_surface.fsm_states),
        "g6_decisions": dict(snap.exit_surface.g6_decisions),
        "g7_decisions": dict(snap.exit_surface.g7_decisions),
        "tally": exit_tally,
    }

    # regimes: aggregated to (venue x asset-class) — snap.regime_states is
    # PER-SYMBOL (``group_id`` = "equity:AAPL" / "crypto:AAVE" / ...; ~1200+
    # rows live on a real DB, one per Alpaca-tracked ticker alone), so a raw
    # per-row dump would both bloat the 1s graph.json payload and make the
    # readouts' venue×asset-class grid try to draw a 1000+-column strip
    # (build round-1 self-critique screenshot caught this — the raw grid
    # collided with the existing 4 regime satellite nodes AND would have run
    # off-screen). Collapsed here into the same small asset-class buckets
    # the L4 cell matrix already groups by (crypto/forex/indices/commodity/
    # equity — see _asset_group_for), one row per (venue, class): mean
    # confidence + the class's most-common regime label.
    from collections import Counter as _Counter
    from collections import defaultdict as _defaultdict

    reg_groups: dict[tuple[str, str], list[Any]] = _defaultdict(list)
    for r in snap.regime_states:
        cls = str(r.group_id or "").split(":", 1)[0] or "other"
        reg_groups[(_short_venue(r.venue), cls)].append(r)
    regimes = [
        {
            "venue": v, "group_id": cls,
            "regime": _Counter(str(x.regime or "chop") for x in rows).most_common(1)[0][0],
            "confidence": round(sum(float(x.confidence or 0.0) for x in rows) / len(rows), 3),
            "n": len(rows),
        }
        for (v, cls), rows in sorted(reg_groups.items())
    ]

    return {
        "core": core,
        "sessions": sessions,
        "streams": streams,
        "gpt": gpt,
        "cell_top": [_cell(r) for r in snap.cell_top],
        "cell_bottom": [_cell(r) for r in snap.cell_bottom],
        "exit": exit_block,
        "regimes": regimes,
        "gate_flow_1h": _query_gate_flow_1h(db_path),
    }


def build_graph(
    db_path: str | Path = "data/polaris_live.sqlite",
    snapshot: Any = None,
) -> dict[str, Any]:
    """Build the full graph.json payload (display-only). Never raises on
    missing data — empty lists are valid for every section.

    ``snapshot`` lets a caller (the server's single bg refresh thread) pass a
    pre-built ``collect_snapshot`` result so the expensive snapshot is computed
    once per cycle and fanned out to all read-models instead of rebuilt here.
    """
    path = Path(db_path)
    snap = snapshot if snapshot is not None else collect_snapshot(path)
    # Full tradable universe for the market shell (display-only). The trading
    # focus subset (_query_universe, is_active=1) is preserved unchanged; the
    # globe shows the whole shell and lights up the active rows.
    universe = _query_universe_all(path)
    held = {p.symbol for p in snap.positions}
    watch = _query_watch_focus(path, held)
    actions = _query_actions(path)
    faults = _query_faults(path)
    signal_counts = _query_signal_counts(path)
    tier_map = _query_tier_map(path)
    kills, last_decision_ts = _query_kills_and_heartbeat(path)

    pos_nodes, live_trades = _pos_nodes_and_trades(snap, regime="neutral")
    bi = len(pos_nodes)
    exit_nodes = _exit_nodes(snap, base_i=bi)
    bi += len(exit_nodes)
    reg_nodes, regime = _regime_nodes(snap, base_i=bi)
    bi += len(reg_nodes)
    # Backfill live_trades regime with the dominant regime label ONLY when the
    # position carries no per-instrument regime (the globe prefers the real
    # per-position regime now emitted above; dominant is the fallback).
    for tr in live_trades:
        if not tr.get("regime") or tr.get("regime") == "neutral":
            tr["regime"] = regime
    strat_nodes = _strat_nodes(snap, base_i=bi)
    bi += len(strat_nodes)
    watch_nodes = _watch_nodes(watch, signal_counts, base_i=bi)
    bi += len(watch_nodes)
    probe_nodes = _probe_nodes(snap, base_i=bi)
    bi += len(probe_nodes)
    mkt_nodes, mkt_tail = _mkt_nodes(
        universe, signal_counts, tier_map, _graph_node_cap(), base_i=bi
    )
    bi += len(mkt_nodes)
    obs_nodes = _obs_nodes(snap, faults, base_i=bi)
    bi += len(obs_nodes)
    action_nodes = _action_nodes(actions, base_i=bi)
    bi += len(action_nodes)
    orbit_nodes = _orbit_nodes(snap, base_i=bi)
    bi += len(orbit_nodes)
    axis_nodes = _axis_nodes(snap, base_i=bi)
    bi += len(axis_nodes)
    tally_nodes = _exit_tally_nodes(snap, base_i=bi)

    nodes = (
        pos_nodes
        + exit_nodes
        + reg_nodes
        + strat_nodes
        + watch_nodes
        + probe_nodes
        + mkt_nodes
        + obs_nodes
        + action_nodes
        + orbit_nodes
        + axis_nodes
        + tally_nodes
    )

    closes = _recent_closes(snap)
    # Glove pathway restore (was hard-coded []): real chains + lifecycle paths
    # built from the live trades (open positions) + recent closes, referencing
    # the node ids already emitted above. Display-only — drawn by sphere-render
    # (drawTradeChains + highlightLifecycle); no trading behavior touched.
    trade_chains = build_trade_chains(live_trades, nodes, regime=regime)
    lifecycle_paths = build_lifecycle_paths(
        live_trades, closes, nodes, regime=regime
    )

    firing = sum(1 for n in nodes if n.get("state") == "firing")
    lit = sum(1 for n in nodes if n.get("state") == "lit")
    tiers = {n["tier"] for n in nodes}
    clusters_present = {n["cluster"] for n in nodes}

    stats = {
        "regime": regime,
        "tick": int(snap.ts_now),
        "node_count": len(nodes),
        "firing_rate": round(firing / len(nodes), 4) if nodes else 0.0,
        "firing": firing,
        "lit": lit,
        "cluster_count": len(clusters_present),
        "tier_count": len(tiers),
        "open_count": len(live_trades),
        "ts": int(time.time()),
    }

    # ── globe activity bindings (display-only, P4) ───────────────────────────
    # Four small arrays derived from the snapshot the globe previously ignored,
    # consumed by globe-funnel.js. Each is a *view* — none gate/size/throttle.
    learner_pulse = round(
        max((abs(ln.delta_1h or 0.0) for ln in snap.learners), default=0.0), 4
    )

    # ── Polaris core (celestial-chart centre) — additive, display-only ───────
    # The celestial navigation globe (globe-core.js) draws Polaris at the chart
    # centre, glowing green/red by the real-fee-net equity vs starting capital.
    # We surface those equity scalars HERE so the globe keeps its single
    # graph.json fetch (no second /api/snapshot poll, no trading-path coupling).
    # VIRTUAL ACCOUNT fields (Jin 2026-07-07 mandate) added alongside the LEGACY
    # ones so wall_spine_hud.js's bottom-ticker can match the same
    # virtual_account_enabled branch board.js/mobile.js already use — Σ
    # streams[].virtual_equity_usd / virtual_seed_usd (no per-venue global field
    # exists on DashboardSnapshot; virtual_daily_pnl_usd IS a global scalar).
    virtual_equity_usd = sum(
        (s.virtual_equity_usd for s in snap.streams), 0.0
    )
    virtual_seed_usd = sum((s.virtual_seed_usd for s in snap.streams), 0.0)
    polaris_core = {
        "equity_now": round(float(snap.equity_now), 2),
        "equity_now_real_fee_net": round(float(snap.equity_now_real_fee_net), 2),
        "starting_capital": round(float(snap.starting_capital), 2),
        "peak_equity": round(float(snap.peak_equity), 2),
        "ts_now": int(snap.ts_now),
        "virtual_account_enabled": bool(snap.virtual_account_enabled),
        "virtual_equity_usd": round(float(virtual_equity_usd), 2),
        "virtual_seed_usd": round(float(virtual_seed_usd), 2),
        "virtual_daily_pnl_usd": round(float(snap.virtual_daily_pnl_usd), 2),
    }

    # probe -> position live links (Jin 2026-07-10 "프로브 연결"): which
    # positions each advisor actually read in the last 30m — real events only.
    pos_ids = {n["id"] for n in pos_nodes}
    probe_links: list[dict[str, str]] = []
    seen_links: set[str] = set()
    now_s = int(snap.ts_now)
    for ev in getattr(snap, "probe_events", []) or []:
        name = str(ev.get("name") or "")
        if name not in ("profit_taking", "loss_defense", "technical"):
            continue
        if now_s - int(ev.get("ts") or 0) > 1800:
            continue
        base = str(ev.get("ticker") or "").split("-")[0].split("_")[0]
        pid = f"pos_{_short_venue(str(ev.get('venue') or ''))}_{base}"
        key = f"probe_{name}>{pid}"
        if pid in pos_ids and key not in seen_links:
            seen_links.add(key)
            probe_links.append({"probe": f"probe_{name}", "pos": pid})

    return {
        "nodes": nodes,
        "clusters": _CLUSTERS,
        "probe_links": probe_links,
        "live_trades": live_trades,
        "recent_closes": closes,
        "galaxy_universe": universe,
        "tail_aggregate": mkt_tail,
        "trade_chains": trade_chains,
        "lifecycle_paths": lifecycle_paths,
        "console": _console_block(snap, path),
        "gate_funnel": _gate_funnel(snap),
        "galaxy_activity": _galaxy_activity(snap),
        "kills": kills,
        "last_decision_ts": last_decision_ts,
        "learner_pulse": learner_pulse,
        "polaris_core": polaris_core,
        "stats": stats,
    }
