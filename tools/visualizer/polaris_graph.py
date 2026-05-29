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

import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.strategies import STRATEGY_REGISTRY

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
    if a in ("stock", "shares"):
        return "stock"
    if a == "etf":
        return "etf"
    return "crypto"


# ── universe (T8) ────────────────────────────────────────────────────────────
def _query_universe(db_path: Path) -> list[dict[str, Any]]:
    """Active universe symbols → galaxy_universe records (display-only)."""
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
        nodes.append(
            {
                "id": f"pos_{p.venue}_{ticker}_{i}",
                "label": ticker,
                "ticker": ticker,
                "direction": direction,
                "exchange": _short_venue(p.venue),
                "trade_id": trade_id,
                "strategy_id": p.strategy_id,
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
                "regime": regime,
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


def _watch_nodes(watch: list[dict[str, Any]], base_i: int) -> list[dict[str, Any]]:
    """tier7 'watch' nodes — signal-watch focus symbols not yet open."""
    nodes: list[dict[str, Any]] = []
    for j, w in enumerate(watch):
        i = base_i + j
        ticker = w["symbol"].split(":")[-1].split("-")[0]
        is_core = w["bucket"] == "core"
        nodes.append(
            {
                "id": f"watch_{w['venue']}_{ticker}",
                "label": ticker,
                "ticker": ticker,
                "exchange": _short_venue(w["venue"]),
                "score": round(w["score"], 4),
                "intensity": round(min(1.0, 0.3 + abs(w["score"]) / 2.0), 4),
                "size_mul": 1.0,
                "signal_count_30m": 0,
                "cluster": "watch",
                "tier": 7,
                "state": "lit" if is_core else "dormant",
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


def _mkt_nodes(
    universe: list[dict[str, Any]], base_i: int,
) -> list[dict[str, Any]]:
    """tier8 'mkt' market-shell nodes from the universe."""
    nodes: list[dict[str, Any]] = []
    for j, u in enumerate(universe):
        i = base_i + j
        nodes.append(
            {
                "id": f"mkt_{u['ticker']}",
                "label": u["ticker"],
                "ticker": u["ticker"],
                "exchange": u["exchange"],
                "asset_group": u["asset_group"],
                "intensity": round(min(1.0, 0.1 + u["n_24h"] / 500.0), 4),
                "size_mul": round(min(1.0, 0.6 + u["n_24h"] / 1000.0), 4),
                "signal_count_30m": 0,
                "cluster": "mkt",
                "tier": 8,
                "state": "dormant",
                "i": i,
                "phase": _phase(i),
            }
        )
    return nodes


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


def build_graph(db_path: str | Path = "data/polaris_live.sqlite") -> dict[str, Any]:
    """Build the full graph.json payload (display-only). Never raises on
    missing data — empty lists are valid for every section."""
    path = Path(db_path)
    snap = collect_snapshot(path)
    universe = _query_universe(path)
    held = {p.symbol for p in snap.positions}
    watch = _query_watch_focus(path, held)
    actions = _query_actions(path)
    faults = _query_faults(path)

    pos_nodes, live_trades = _pos_nodes_and_trades(snap, regime="neutral")
    bi = len(pos_nodes)
    exit_nodes = _exit_nodes(snap, base_i=bi)
    bi += len(exit_nodes)
    reg_nodes, regime = _regime_nodes(snap, base_i=bi)
    bi += len(reg_nodes)
    # Backfill live_trades regime with the dominant regime label.
    for tr in live_trades:
        tr["regime"] = regime
    strat_nodes = _strat_nodes(snap, base_i=bi)
    bi += len(strat_nodes)
    watch_nodes = _watch_nodes(watch, base_i=bi)
    bi += len(watch_nodes)
    mkt_nodes = _mkt_nodes(universe, base_i=bi)
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
        + mkt_nodes
        + obs_nodes
        + action_nodes
        + orbit_nodes
        + axis_nodes
        + tally_nodes
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

    return {
        "nodes": nodes,
        "clusters": _CLUSTERS,
        "live_trades": live_trades,
        "recent_closes": _recent_closes(snap),
        "galaxy_universe": universe,
        "trade_chains": [],
        "lifecycle_paths": [],
        "exchange_pnl": [],
        "stats": stats,
    }
