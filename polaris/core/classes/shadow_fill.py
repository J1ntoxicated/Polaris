"""pts-classes (group WIRE) — pessimistic shadow fill recording point.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — a PROVE/BENCH intent routed to shadow keeps
signaling/learning; this module only RECORDS the hypothetical outcome into
``strategy_class.shadow_ring`` for the reentry ladder to later read
(no_block_filter_architecture / flow_not_block). No sizing/9-stack/-1.0R rail
touch — purely classifier-side bookkeeping.

Fill-price model (prove_then_scale_classes_2026-07-03.md 유효반박 #3, exact
spec): a LIMIT order is assumed to trade through by 1 tick (worse than the
honest mid); a MARKET order is assumed to slip ``max(1 tick, 5bp)`` — on
BOTH legs (entry + the eventual exit), plus the REAL taker fee on both legs.
This is strictly worse than an optimistic (mid-price, zero-slippage) fill,
which is the whole point (an optimistic shadow fill was the loser-reentry-
churn defect this design exists to cure).

Outcome proxy (WIRE's own explicit assembly choice — no forward price feed is
available synchronously at signal time to simulate a real future exit, and
building a full bar-driven shadow-position MTM resolver is a separate, much
larger feature not listed in any lettered group's build-plan file list):
the projected edge is read from the ALREADY-EXISTING, already-vetted
``learner_posterior`` R-multiple mean (``mu`` — Layer 5's own posterior
estimate for this exact (venue, strategy, ticker, regime) cell) rather than
inventing a new edge estimate. A cold/missing posterior defaults to 0.0
(neutral — NEVER fabricates a win), so ``shadow_score = mu - pessimistic_cost``
is always at least as pessimistic as the honest posterior mean, and never
manufactures ladder-climbing evidence out of nothing. This proxy is a
deliberate simplification — flagged as a follow-up candidate for a dedicated
bar-driven shadow-position resolver if a more precise model is wanted later.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from typing import Final, Literal

from polaris.core.economics.fees import real_fee_bps

logger = logging.getLogger(__name__)

__all__ = ["pessimistic_round_trip_cost_r", "record_shadow_fill"]

OrderStyle = Literal["limit", "market"]

# 1-tick trade-through proxy, expressed as a fraction of notional — no venue
# registered here carries a per-instrument tick table at this layer, so this
# reuses the SAME generic small-fraction floor the project already applies
# elsewhere for a "1 tick"-scale move (0.01% = 1 bp, a conservative minimum
# tick-equivalent for the venues currently registered).
_ONE_TICK_FRAC: Final[float] = 0.0001
_MARKET_SLIP_FRAC: Final[float] = 0.0005  # 5 bp


def pessimistic_round_trip_cost_r(
    *, venue: str, order_style: OrderStyle, atr_pct: float
) -> float:
    """Pessimistic round-trip transaction cost, expressed in R (fraction of
    the position's own ATR-based 1R) so it nets directly against a score_F-
    scale (R-multiple-like) edge estimate.

    Both legs (entry + exit) pay: ``max(1 tick, 5bp)`` slippage for a market
    order, ``1 tick`` trade-through for a limit order, PLUS the real taker
    fee. A non-positive/non-finite ``atr_pct`` floors to ``_ONE_TICK_FRAC`` so
    this never divides by zero (fails toward a LARGER, more pessimistic cost,
    never toward an unbounded one).
    """
    slip_frac = _ONE_TICK_FRAC if order_style == "limit" else max(
        _ONE_TICK_FRAC, _MARKET_SLIP_FRAC
    )
    fee_frac = real_fee_bps(venue) / 10_000.0
    round_trip_cost_frac = 2.0 * (slip_frac + fee_frac)
    r_denom = atr_pct if atr_pct > 0.0 else _ONE_TICK_FRAC
    return round_trip_cost_frac / r_denom


def _fetch_posterior_mu(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, ticker: str, regime: str
) -> float:
    try:
        row = conn.execute(
            "SELECT mu FROM learner_posterior "
            "WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?",
            (venue, strategy_id, ticker, regime),
        ).fetchone()
    except sqlite3.Error:
        return 0.0
    return float(row[0]) if row is not None else 0.0


def record_shadow_fill(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
    ticker: str,
    regime: str,
    order_style: OrderStyle,
    atr_pct: float,
) -> None:
    """Append one pessimistic shadow-fill score contribution to
    ``strategy_class.shadow_ring`` (JSON ring, trimmed to the row's own
    ``window_w``). Fail-open — a recording failure must never unwind the
    sizing decision that already routed to shadow; a missing
    ``strategy_class`` row (not yet bootstrapped) is a silent no-op.
    """
    mu_r = _fetch_posterior_mu(
        conn, venue=venue, strategy_id=strategy_id, ticker=ticker, regime=regime
    )
    pessimistic_cost_r = pessimistic_round_trip_cost_r(
        venue=venue, order_style=order_style, atr_pct=atr_pct
    )
    shadow_score = mu_r - pessimistic_cost_r
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT shadow_ring, window_w FROM strategy_class "
            "WHERE venue = ? AND strategy_id = ?",
            (venue, strategy_id),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return
        try:
            ring = json.loads(row[0]) if row[0] else []
            ring = list(ring) if isinstance(ring, list) else []
        except (TypeError, ValueError):
            ring = []
        window_w = int(row[1])
        ring.append(shadow_score)
        if len(ring) > window_w:
            ring = ring[-window_w:]
        conn.execute(
            "UPDATE strategy_class SET shadow_ring = ? "
            "WHERE venue = ? AND strategy_id = ?",
            (json.dumps(ring), venue, strategy_id),
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[pts-classes] shadow_ring record failed for %s/%s: %r",
            venue, strategy_id, exc,
        )
