"""Layer 2 — Pipeline payload builders for the G1->G8 chain (Day 6 plumbing).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 model split, per-gate
  inputs/outputs)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (8-gate state machine)

Each gate has a well-defined input contract. Day 5's smoke loop ran G3
(Validator) only — downstream gates were exercised via unit tests. Day 6
plumbing wires real payloads end-to-end so a single ``GateOrchestrator.run``
walks G3 -> G4 -> G5 -> G6 -> G7 -> G8 with production-shaped data.

Builder responsibilities (per spec):

  G3 input (Validator)         build_validator_payload
      raw_signal + cell_routing summary + ticker baseline + recent_trades
  G4 input (Pre-Entry Watcher) build_watcher_payload
      validated_signal + tick_window + fast-path hints (cell_quartile,
      spread_bps, baseline_p50, recent_reject, listing_age, session window)
  G5 input (Entry Sizer)       build_sizer_payload
      SignalIntent + StrategyRiskState + PortfolioState (dataclass carriers)
  G6 input (Position Monitor)  build_monitor_payload
      position dict + unrealized_pnl_r + max_loss_r (+ optional swap_candidate)
  G7 input (Adaptive Exit)     build_exit_payload
      widen_proposal { side, current/proposed_stop_price, entry_price,
                       unrealized_pnl_r, max_loss_r, overrides_used,
                       seconds_since_last_override, initial_stop_price }

Pure functions. No I/O. Callers (smoke, ignite, tests) compose them
sequentially: each payload is layered into ``GateContext.payload`` so the
orchestrator's ``_stamp_payload`` carries G(N) outputs into G(N+1) inputs
naturally.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Final

from polaris.core.cell_matrix import (
    CellKeyP0,
    classify_quartile,
    fetch_cell_stat,
)
from polaris.core.data.baseline import read_baseline_state
from polaris.core.pipeline._payload_db import _safe_query
from polaris.core.pipeline._sizer_payload import (
    build_sizer_payload,
    default_strategy_risk_state,
)
from polaris.strategies.base import RawSignal

__all__ = [
    "ActivePosition",
    "CELL_POOL_MIN_N_EFF",
    "PNL_R_USD_DENOM",
    "TickWindowEntry",
    "build_exit_payload",
    "build_monitor_payload",
    "build_sizer_payload",
    "build_validator_payload",
    "build_watcher_payload",
    "default_strategy_risk_state",
    "load_active_positions",
    "load_recent_same_symbol_trades",
]

# Quartile-eligibility minimum effective sample size for cell pool (ADR-006
# warmup: cells with n_eff < 5 are still warming up and must not enter the
# quartile classification pool — using them would let single-trade outliers
# move the boundaries). Mirrored by ``polaris.scripts.dashboard_v0`` for the
# cell distribution panel so both views agree on which cells are "eligible".
CELL_POOL_MIN_N_EFF: Final[float] = 5.0

# USD-per-R heuristic divisor used by `load_recent_same_symbol_trades` to
# project a $-PnL into an R-multiple when the underlying R-unit is unknown
# (Day 6 smoke baseline = $50 risk per trade). Replaced by the per-trade
# `risk_usd` once the sizing engine persists it (P1 Layer 5 R6).
PNL_R_USD_DENOM: Final[float] = 50.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _cell_routing_summary(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy: str,
    symbol: str,
    regime: str,
    now_ts: int,
) -> dict[str, Any]:
    """Compact cell summary for G3 prompt + G4 fast-path hint.

    Always returns finite values — callers use the ``quartile`` field for
    fast-path eligibility (top quartile gate) and ``score`` / ``n_eff``
    for prompt context. Missing cell -> ``mid`` (anti-collapse default).
    """
    key = CellKeyP0(exchange=venue, strategy=strategy, ticker=symbol, regime=regime)
    cell = fetch_cell_stat(conn, key)
    if cell is None:
        return {
            "quartile": "mid",
            "score": 0.0,
            "n_eff": 0.0,
            "wins_eff": 0.0,
            "avg_pnl_r": 0.0,
        }
    # Build pool from sibling cells with at least baseline n_eff (ADR-006
    # warmup floor — see ``CELL_POOL_MIN_N_EFF``).
    pool_rows = _safe_query(
        conn,
        "SELECT score FROM cell_matrix_p0 WHERE n_eff >= ?",
        (CELL_POOL_MIN_N_EFF,),
    )
    pool = [float(r[0]) for r in pool_rows]
    quartile = classify_quartile(cell.score, eligible_scores=pool)
    return {
        "quartile": str(quartile),
        "score": float(cell.score),
        "n_eff": float(cell.n_eff),
        "wins_eff": float(cell.wins_eff),
        "avg_pnl_r": float(cell.avg_pnl_r),
        "last_closed_ts": int(cell.last_closed_ts),
    }


def _baseline_summary(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
) -> dict[str, Any]:
    """Read 3 P0 baselines (atr / size / volume); empty dict on miss."""
    out: dict[str, Any] = {}
    for metric in ("atr", "size", "volume"):
        bv = read_baseline_state(conn, instrument_id=instrument_id, metric=metric)
        if bv is not None:
            out[metric] = {
                "p50": bv.p50,
                "p75": bv.p75,
                "n": bv.sample_count,
                "lookback_sec": bv.lookback_sec,
            }
    return out


def load_recent_same_symbol_trades(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Pull most-recent same-(venue,symbol) closed trades from ``fills``.

    Used by G3 (validator prompt) — `fills` rows with ``is_close=1`` give
    the model immediate post-trade context. Empty list when the table /
    rows are missing (Day 6 cold-start safe).
    """
    rows = _safe_query(
        conn,
        """
        SELECT ts_ms, side, fill_price, pnl_usd, fee_usd, slippage_bps
        FROM fills
        WHERE venue = ? AND instrument_id LIKE ? AND is_close = 1
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (venue, f"%{symbol}%", int(limit)),
    )
    return [
        {
            "ts": int(r[0]) // 1000,
            "side": r[1],
            "exit_price": float(r[2]),
            "pnl_usd": float(r[3]),
            "fee_usd": float(r[4]),
            "slippage_bps": float(r[5]),
            "won": float(r[3]) > 0.0,
            "pnl_r": float(r[3]) / PNL_R_USD_DENOM,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# G3 — Signal Validator payload
# ---------------------------------------------------------------------------


def build_validator_payload(
    *,
    raw_signal: RawSignal,
    venue: str,
    symbol: str,
    instrument_id: str,
    regime: str,
    conn: sqlite3.Connection,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Compose the G3 input payload (raw signal + cell + baseline + recent)."""
    ts = int(now_ts if now_ts is not None else time.time())
    cell_summary = _cell_routing_summary(
        conn,
        venue=venue,
        strategy=raw_signal.strategy_id,
        symbol=symbol,
        regime=regime,
        now_ts=ts,
    )
    baseline_summary = _baseline_summary(conn, instrument_id=instrument_id)
    recent_trades = load_recent_same_symbol_trades(
        conn, venue=venue, symbol=symbol, limit=5
    )
    return {
        "signal_id": raw_signal.signal_id,
        "raw_signal": {
            "strategy_id": raw_signal.strategy_id,
            "symbol": raw_signal.symbol,
            "side": raw_signal.side,
            "strength": raw_signal.strength,
            "sizing_hint": raw_signal.sizing_hint,
            "ttl_bars": raw_signal.ttl_bars,
            "thesis_tag": raw_signal.thesis_tag,
        },
        "cell_routing": cell_summary,
        "baseline": baseline_summary,
        "recent_trades": recent_trades,
        # G4 hints carried forward.
        "cell_quartile": cell_summary.get("quartile", "mid"),
    }


# ---------------------------------------------------------------------------
# G4 — Pre-Entry Watcher payload (validator must have stamped validated_signal)
# ---------------------------------------------------------------------------


class TickWindowEntry(dict[str, Any]):
    """Lightweight typed alias — bid/ask/mid + ts in a dict shape G4 expects."""


def build_watcher_payload(
    *,
    spread_bps: float,
    baseline_p50_spread_bps: float,
    listing_age_hours: float,
    recent_reject_in_6h: bool = False,
    session_open_shock_window: bool = False,
    tick_window: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose G4 input payload (fast-path hints + tick window).

    The validated_signal field is stamped by G3 and propagated by the
    orchestrator's ``_stamp_payload`` — caller does not duplicate it here.
    """
    return {
        "spread_bps": float(spread_bps),
        "baseline_p50_spread_bps": float(baseline_p50_spread_bps),
        "listing_age_hours": float(listing_age_hours),
        "recent_reject_in_6h": bool(recent_reject_in_6h),
        "session_open_shock_window": bool(session_open_shock_window),
        "tick_window": tick_window or [],
    }


# ---------------------------------------------------------------------------
# G6 — Position Monitor payload
# ---------------------------------------------------------------------------


def build_monitor_payload(
    *,
    position: dict[str, Any],
    unrealized_pnl_r: float,
    max_loss_r: float = 1.0,
    swap_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose G6 payload (position + R-multiples + optional swap candidate)."""
    payload: dict[str, Any] = {
        "position": dict(position),
        "unrealized_pnl_r": float(unrealized_pnl_r),
        "max_loss_r": float(max_loss_r),
    }
    if swap_candidate is not None:
        payload["swap_candidate"] = dict(swap_candidate)
    return payload


# ---------------------------------------------------------------------------
# G7 — Adaptive Exit payload (widen proposal)
# ---------------------------------------------------------------------------


def build_exit_payload(
    *,
    side: str,
    current_stop_price: float,
    proposed_stop_price: float,
    entry_price: float,
    unrealized_pnl_r: float,
    max_loss_r: float = 1.0,
    overrides_used: int = 0,
    seconds_since_last_override: int = 31,
    initial_stop_price: float | None = None,
) -> dict[str, Any]:
    """Compose G7 widen proposal payload.

    ``seconds_since_last_override`` defaults above the 30 s cooldown so the
    first call after entry is eligible (matches L2 spec Q9). Caller sets
    smaller values to test cooldown enforcement.
    """
    proposal: dict[str, Any] = {
        "side": str(side),
        "current_stop_price": float(current_stop_price),
        "proposed_stop_price": float(proposed_stop_price),
        "entry_price": float(entry_price),
        "unrealized_pnl_r": float(unrealized_pnl_r),
        "max_loss_r": float(max_loss_r),
        "overrides_used": int(overrides_used),
        "seconds_since_last_override": int(seconds_since_last_override),
    }
    if initial_stop_price is not None:
        proposal["initial_stop_price"] = float(initial_stop_price)
    return {
        "widen_proposal": proposal,
        "current_stop_price": float(current_stop_price),
    }


# ---------------------------------------------------------------------------
# Convenience — load the active "position" dict G6/G7 expect from `positions`
# ---------------------------------------------------------------------------


class ActivePosition(dict[str, Any]):
    """Position dict shape expected by G6 (subset of the persisted columns)."""


def load_active_positions(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[ActivePosition]:
    """Read ``positions`` rows where status not in {closed, cancelled}."""
    rows = _safe_query(
        conn,
        """
        SELECT position_id, venue, symbol, underlying_group_id, strategy_id,
               entry_strategy_id, active_strategy_id, side, qty, status,
               opened_ts, swap_count
        FROM positions
        WHERE status NOT IN ('closed','cancelled')
        ORDER BY opened_ts DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    out: list[ActivePosition] = []
    for r in rows:
        ap = ActivePosition()
        ap.update(
            position_id=r[0],
            venue=r[1],
            symbol=r[2],
            underlying_group_id=r[3],
            strategy=r[4],
            entry_strategy_id=r[5],
            active_strategy_id=r[6],
            side=r[7],
            qty=float(r[8]),
            status=r[9],
            opened_ts=int(r[10]),
            swap_count=int(r[11]),
            correlation_group=str(r[3]),  # heuristic — group by underlying
        )
        out.append(ap)
    return out
