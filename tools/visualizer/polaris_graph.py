"""Polaris space-visualizer graph builder (DEMO/PAPER, display-only).

Turns the read-only dashboard snapshot + the dynamic universe table into the
``graph.json`` schema consumed by ``static/sphere-render.js`` (the Canvas-2D
sphere engine reused from Jin's earlier project, untouched here).

This module is purely a *view*: it reads SQLite and produces a dict. It never
sizes, orders, throttles, or gates anything — the visual has zero effect on the
trading pipeline. Aggressive bias is preserved because nothing here touches
sizing or risk.

Tiers populated from live Polaris data:
  tier0  'pos'   open positions          (snapshot.positions + live_trades)
  tier3  'reg'   regime context          (snapshot.regime_bars + stats.regime)
  tier5  'strat' strategies              (snapshot.strategy_stats)
  tier8  'mkt'   universe symbols        (universe table → galaxy_universe)
Other tiers (7/9/10/11/12/13) are rendered as small placeholders or empty
lists; the engine tolerates empty tiers.
"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from polaris.scripts.dashboard.snapshot import collect_snapshot

# 9 cluster definitions (id, label, color, tier) — color palette matches the
# render engine's CLUSTERS table. Order is display-only.
_CLUSTERS: list[dict[str, Any]] = [
    {"id": "pos", "label": "live positions", "color": "#87d7ff", "tier": 0},
    {"id": "exit", "label": "exit patterns", "color": "#ff87d7", "tier": 1},
    {"id": "reg", "label": "regime context", "color": "#d7d787", "tier": 3},
    {"id": "strat", "label": "strategies", "color": "#ff9f87", "tier": 5},
    {"id": "watch", "label": "signal watch", "color": "#87ffd7", "tier": 7},
    {"id": "mkt", "label": "market shell", "color": "#ffaf87", "tier": 8},
    {"id": "obs", "label": "observ", "color": "#afd7af", "tier": 9},
    {"id": "action", "label": "actions", "color": "#d7afff", "tier": 10},
    {"id": "exit_tally", "label": "exit tally", "color": "#ff87af", "tier": 13},
]


def _phase(i: int) -> float:
    """Deterministic render-only orbital phase in [0, 2pi)."""
    return round((i * 0.37) % (2 * math.pi), 3)


def _direction(side: str) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s or "long"


def _short_venue(venue: str) -> str:
    return (venue or "okx").lower()[:3]


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
        # n_24h is a render-only "activity" magnitude; scale 24h USD volume
        # into a small integer bucket. Display-only, never a sizing input.
        vol = float(vol_24h or 0.0)
        n_24h = int(min(500, max(1, vol / 1_000_000.0)))
        out.append(
            {
                "ticker": str(symbol),
                "exchange": _short_venue(str(venue)),
                "asset_group": str(asset_class or "crypto"),
                "n_24h": n_24h,
            }
        )
    return out


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
        # intensity / size_mul are render-only glow + scale hints.
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
    """tier5 'strat' nodes from strategy_stats."""
    nodes: list[dict[str, Any]] = []
    for j, s in enumerate(snap.strategy_stats):
        i = base_i + j
        active = s.open_n > 0 or s.closed_n > 0
        nodes.append(
            {
                "id": f"strat_{s.strategy_id}",
                "label": s.strategy_id,
                "ticker": None,
                "intensity": round(min(1.0, 0.3 + s.open_n * 0.15), 4),
                "size_mul": round(min(1.5, max(0.7, s.pf or 1.0)), 4),
                "trades_24h": s.closed_n,
                "asset_group": "crypto",
                "cluster": "strat",
                "tier": 5,
                "state": "firing" if s.open_n > 0 else ("lit" if active else "dormant"),
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

    pos_nodes, live_trades = _pos_nodes_and_trades(snap, regime="neutral")
    reg_nodes, regime = _regime_nodes(snap, base_i=len(pos_nodes))
    # Backfill live_trades regime with the dominant regime label.
    for tr in live_trades:
        tr["regime"] = regime
    strat_nodes = _strat_nodes(snap, base_i=len(pos_nodes) + len(reg_nodes))
    mkt_nodes = _mkt_nodes(
        universe, base_i=len(pos_nodes) + len(reg_nodes) + len(strat_nodes),
    )

    nodes = pos_nodes + reg_nodes + strat_nodes + mkt_nodes

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
