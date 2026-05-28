"""Polaris dashboard v1 — snapshot panel queries (funnel / cells / trades / etc).

Per-panel best-effort read helpers consumed by ``snapshot.collect_snapshot``:
gate funnel, cell-matrix top/bottom, regime heatmap, recent closed trades,
learner state, GPT cost, alert log, universe focus. Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC. Shared low-level helpers
and lookback constants are imported from ``snapshot_queries``.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Final

from polaris.scripts.dashboard.snapshot_models import (
    AlertRow,
    CellRow,
    ClosedTrade,
    GateRow,
    GptStat,
    LearnerSlot,
    RegimeBar,
)
from polaris.scripts.dashboard.snapshot_queries import (
    DEFAULT_R_USD,
    GATE_FUNNEL_LOOKBACK_SEC,
    GPT_LOOKBACK_SEC,
    GPT_PRICE_PER_1K,
    GPT_TOKENS_PER_CALL,
    LEARNER_DELTA_LOOKBACK_SEC,
    _safe_query,
    _symbol_from_inst,
)

# ---------------------------------------------------------------------------
# Section: gate funnel
# ---------------------------------------------------------------------------


_GATE_LABELS: Final[dict[int, str]] = {
    1: "Universe",
    2: "Strategy",
    3: "Validator",
    4: "PreEntry",
    5: "Sizer",
    6: "Monitor",
    7: "Exit",
    8: "Reflector",
}


def _gate_funnel(conn: sqlite3.Connection, *, now_s: int) -> list[GateRow]:
    rows = _safe_query(
        conn,
        """SELECT gate_id, decision, COUNT(*) FROM gate_events
           WHERE created_ts >= ?
           GROUP BY gate_id, decision
           ORDER BY gate_id""",
        (now_s - GATE_FUNNEL_LOOKBACK_SEC,),
    )
    by_gate: dict[int, dict[str, int]] = {}
    for r in rows:
        gid = int(r[0])
        dec = str(r[1] or "?").upper()
        n = int(r[2] or 0)
        by_gate.setdefault(gid, {})[dec] = n

    out: list[GateRow] = []
    for gid in range(1, 9):
        d = by_gate.get(gid, {})
        pass_n = d.get("PASS", 0) + d.get("PROCEED", 0) + d.get("HOLD", 0) + d.get(
            "SIZED", 0
        ) + d.get("REFLECTED", 0)
        kill_n = d.get("KILL", 0) + d.get("EXIT", 0) + d.get("REJECT", 0)
        total = sum(d.values())
        other = max(0, total - pass_n - kill_n)
        rate = (pass_n / total * 100.0) if total else 0.0
        out.append(
            GateRow(
                gate_id=gid,
                label=_GATE_LABELS.get(gid, f"G{gid}"),
                pass_n=pass_n,
                kill_n=kill_n,
                other_n=other,
                total=total,
                pass_rate=rate,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: cell matrix top/bottom
# ---------------------------------------------------------------------------


def _cell_top_bottom(
    conn: sqlite3.Connection,
    *,
    cell_mult: dict[tuple[str, str, str, str], float],
    n: int = 5,
) -> tuple[list[CellRow], list[CellRow], int]:
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, n_eff, score
           FROM cell_matrix_p0
           WHERE n_eff >= 20
           ORDER BY score DESC""",
    )
    if not rows:
        return [], [], 0
    eligible_n = len(rows)
    top_rows = rows[:n]
    bot_rows = list(reversed(rows[-n:]))
    if eligible_n < n * 2:
        # Avoid duplicate row showing in both top & bottom for tiny pools
        seen = {(r[0], r[1], r[2], r[3]) for r in top_rows}
        bot_rows = [r for r in bot_rows if (r[0], r[1], r[2], r[3]) not in seen]

    def _build(r: tuple[Any, ...]) -> CellRow:
        key = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        return CellRow(
            exchange=str(r[0]),
            strategy=str(r[1]),
            ticker=str(r[2]),
            regime=str(r[3]),
            n_eff=float(r[4] or 0.0),
            score=float(r[5] or 0.0),
            mult=cell_mult.get(key, 1.0),
        )

    return [_build(r) for r in top_rows], [_build(r) for r in bot_rows], eligible_n


# ---------------------------------------------------------------------------
# Section: regime heatmap
# ---------------------------------------------------------------------------


def _regime_bars(
    conn: sqlite3.Connection,
) -> tuple[list[RegimeBar], dict[tuple[str, str], str]]:
    rows = _safe_query(
        conn,
        """SELECT venue, underlying_group_id, regime FROM regime_state""",
    )
    counts: dict[str, int] = {}
    lookup: dict[tuple[str, str], str] = {}
    for v, g, regime in rows:
        regime_s = str(regime or "chop")
        counts[regime_s] = counts.get(regime_s, 0) + 1
        lookup[(str(v), str(g))] = regime_s
    ordered = ["chop", "bull_trend", "bear_trend", "crisis"]
    bars = [RegimeBar(regime=r, count=counts.get(r, 0)) for r in ordered]
    return bars, lookup


# ---------------------------------------------------------------------------
# Section: recent closed trades (trade-level pairing)
# ---------------------------------------------------------------------------


def _recent_closed_trades(
    conn: sqlite3.Connection, *, n: int = 10
) -> list[ClosedTrade]:
    """Pair close fills with their entry fills, exact when possible.

    Codex round 2 P0 fix: the production pipeline now persists every fill
    with a ``contribution_id`` that equals the originating ``position_id``
    (see ``_production_pipeline.persist_fill`` + ``_production_close``).
    We pair via that exact ID first, falling back to FIFO per
    ``(venue, instrument_id, strategy_id)`` only when ``contribution_id`` is
    NULL (legacy / smoke rows). This eliminates cross-pairing when two open
    positions share the same ``(venue, inst, strat)`` bucket.
    """
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty, size_usd, is_close,
                  contribution_id
           FROM fills
           ORDER BY ts_ms ASC""",
    )
    # Index by exact contribution_id → first open fill (entry)
    open_by_contrib: dict[str, tuple[float, int]] = {}
    open_fifo: dict[tuple[str, str, str], list[tuple[float, int]]] = {}
    closed_trades: list[ClosedTrade] = []
    for r in rows:
        venue = str(r[0])
        inst = str(r[1])
        strat = str(r[2])
        side = str(r[3])
        fill_price = float(r[4] or 0.0)
        pnl = float(r[5] or 0.0)
        ts_ms = int(r[6] or 0)
        qty = float(r[7] or 0.0)
        is_close = bool(r[9])
        contrib = r[10]
        contrib_str = str(contrib) if contrib else None
        bucket = (venue, inst, strat)
        if not is_close:
            if contrib_str and contrib_str not in open_by_contrib:
                open_by_contrib[contrib_str] = (fill_price, ts_ms)
            open_fifo.setdefault(bucket, []).append((fill_price, ts_ms))
            continue
        # CLOSE leg pairing rules (codex round 3 P0 tightening):
        #  • contribution_id present + exact open found → pair exact
        #  • contribution_id present + open MISSING     → reconstruct from PnL
        #    (do NOT FIFO-pair, that would re-introduce the cross-pair bug)
        #  • contribution_id NULL (legacy / smoke rows) → FIFO bucket fallback
        entry_px: float | None = None
        open_ts_ms: int | None = None
        if contrib_str:
            if contrib_str in open_by_contrib:
                entry_px, open_ts_ms = open_by_contrib.pop(contrib_str)
                # Drain matching FIFO entry so legacy fallbacks don't reuse it.
                fifo = open_fifo.get(bucket)
                if fifo:
                    for i, (px, ts) in enumerate(fifo):
                        if px == entry_px and ts == open_ts_ms:
                            fifo.pop(i)
                            break
            # else: deliberately do NOT FIFO-pair — fall through to reconstruct.
        else:
            fifo = open_fifo.get(bucket) or []
            if fifo:
                entry_px, open_ts_ms = fifo.pop(0)
        if entry_px is None or open_ts_ms is None:
            # Stale close: reconstruct entry from PnL
            if qty > 0 and fill_price > 0:
                sign = 1.0 if side.lower() == "sell" else -1.0
                entry_px = fill_price - (pnl / qty) * sign
            else:
                entry_px = fill_price
            held_sec = 0.0
        else:
            held_sec = max(0.0, (ts_ms - open_ts_ms) / 1000.0)
        if pnl > 0:
            reason = "TP"
        elif pnl < 0:
            reason = "SL" if held_sec < 600 else "TIME"
        else:
            reason = "FLAT"
        closed_trades.append(
            ClosedTrade(
                ts_close=ts_ms // 1000,
                venue=venue,
                symbol=_symbol_from_inst(inst),
                strategy_id=strat,
                side_close=side,
                entry_price=entry_px,
                exit_price=fill_price,
                pnl_usd=pnl,
                r_units=pnl / DEFAULT_R_USD,
                held_sec=held_sec,
                exit_reason=reason,
            )
        )
    closed_trades.sort(key=lambda t: t.ts_close, reverse=True)
    return closed_trades[:n]


# ---------------------------------------------------------------------------
# Section: learner state (3 P0)
# ---------------------------------------------------------------------------


_LEARNER_FEATURED: Final[tuple[str, ...]] = ("session_mult", "regime_mult", "max_hold")


def _learner_slots(conn: sqlite3.Connection, *, now_s: int) -> list[LearnerSlot]:
    cutoff = now_s - LEARNER_DELTA_LOOKBACK_SEC
    out: list[LearnerSlot] = []
    for lid in _LEARNER_FEATURED:
        # Pick the row with highest n_eff (most-informed key)
        rows = _safe_query(
            conn,
            """SELECT key, value, n_eff, updated_at FROM learner_state
               WHERE learner_id = ? ORDER BY n_eff DESC LIMIT 1""",
            (lid,),
        )
        if not rows:
            out.append(LearnerSlot(learner_id=lid, key="-", value=1.0, delta_1h=0.0, n_eff=0.0))
            continue
        key = str(rows[0][0])
        value = float(rows[0][1] or 1.0)
        n_eff = float(rows[0][2] or 0.0)
        # Look up snapshot ≥ 1h ago for delta
        snap = _safe_query(
            conn,
            """SELECT value FROM learner_snapshot
               WHERE learner_id = ? AND key = ? AND snapshot_ts <= ?
               ORDER BY snapshot_ts DESC LIMIT 1""",
            (lid, key, cutoff),
        )
        prev = float(snap[0][0]) if snap else value
        out.append(
            LearnerSlot(
                learner_id=lid, key=key, value=value, delta_1h=value - prev, n_eff=n_eff,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: GPT cost
# ---------------------------------------------------------------------------


def _gpt_stats(conn: sqlite3.Connection, *, now_s: int) -> list[GptStat]:
    rows = _safe_query(
        conn,
        """SELECT model_used, COUNT(*) FROM gate_events
           WHERE created_ts >= ? AND model_used IS NOT NULL
           GROUP BY model_used""",
        (now_s - GPT_LOOKBACK_SEC,),
    )
    out: list[GptStat] = []
    for model, n in rows:
        if not model:
            continue
        m = str(model)
        if m == "python":
            continue
        price = GPT_PRICE_PER_1K.get(m, GPT_PRICE_PER_1K["gpt"])
        cost_per_h = int(n) * GPT_TOKENS_PER_CALL / 1000.0 * price
        out.append(
            GptStat(
                model=m,
                calls_per_h=int(n),
                cost_per_h_usd=cost_per_h,
                cost_24h_proj_usd=cost_per_h * 24.0,
            )
        )
    out.sort(key=lambda g: g.calls_per_h, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: alert log
# ---------------------------------------------------------------------------


def _alerts(conn: sqlite3.Connection, *, n: int = 3) -> list[AlertRow]:
    out: list[AlertRow] = []
    risk = _safe_query(
        conn,
        """SELECT created_ts, event_type, strategy_id, payload_json FROM risk_events
           ORDER BY created_ts DESC LIMIT ?""",
        (n,),
    )
    for r in risk:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="WARN",
                module=str(r[2] or "risk"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    fault = _safe_query(
        conn,
        """SELECT event_ts, fault_type, strategy_id, detail_json FROM strategy_fault_events
           ORDER BY event_ts DESC LIMIT ?""",
        (n,),
    )
    for r in fault:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="ERROR",
                module=str(r[2] or "strat"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    out.sort(key=lambda a: a.ts, reverse=True)
    return out[:n]


# ---------------------------------------------------------------------------
# Section: universe
# ---------------------------------------------------------------------------


def _universe(conn: sqlite3.Connection) -> tuple[int, str]:
    # Current cycle's focus count (NOT all-time accumulated rows)
    rows = _safe_query(
        conn,
        "SELECT COUNT(*), cycle_ts FROM watchlist_focus "
        "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus) "
        "GROUP BY cycle_ts",
    )
    if rows and rows[0][0]:
        cnt, last_ts = rows[0]
        ts_str = (
            time.strftime("%H:%M:%S", time.localtime(int(last_ts))) if last_ts else "n/a"
        )
        return int(cnt), ts_str
    rows = _safe_query(conn, "SELECT COUNT(*) FROM universe WHERE is_active = 1")
    cnt = int(rows[0][0]) if rows else 0
    return cnt, "n/a"
