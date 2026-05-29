"""Meta-labeling (#10) — triple-barrier label collection.

Scope (Jin mandate 2026-05-29): **label collection only.** Cold-start
sample-count is insufficient for a 2nd-stage act/skip model, so this module
*records* a triple-barrier label for every closed trade and never gates,
cuts, or dampens anything. AGGRESSIVE bias is fully preserved — no defensive
throttle, no position limit, no sizing reduction. When enough labels have
accumulated a separate 2nd-stage model can train on the ``meta_labels`` table.

Triple-barrier (López de Prado meta-labeling) terminal classification:
- ``tp``      — realized R reached the upper (profit) barrier.
- ``sl``      — realized R reached the lower (loss) barrier.
- ``timeout`` — the trade was closed (gate/horizon) before touching either
  horizontal barrier (the vertical barrier).

Each label also carries the realized profit ``pnl_sign`` (+1/0/-1) and the
realized ``r`` multiple so the future model has both the categorical barrier
and the continuous target.

Pure + deterministic label generation (``compute_triple_barrier_label``);
``persist_meta_label`` is a thin SQLite writer. Both fail-safe at the call
site (the close-path helper wraps this so a label failure never aborts a
committed close).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_SL_R",
    "DEFAULT_TP_R",
    "TripleBarrierLabel",
    "compute_triple_barrier_label",
    "persist_meta_label",
]

# Default horizontal-barrier R targets. A close at/beyond +TP_R counts as a
# take-profit touch; at/beyond -SL_R counts as a stop-loss touch. These are
# label-bucketing thresholds only — they do NOT influence sizing or exits.
DEFAULT_TP_R: Final[float] = 1.0
DEFAULT_SL_R: Final[float] = 1.0


@dataclass(frozen=True, slots=True)
class TripleBarrierLabel:
    """Terminal triple-barrier label for one closed trade (collection only)."""

    barrier: str  # "tp" | "sl" | "timeout"
    pnl_sign: int  # +1 / 0 / -1
    r: float  # realized R multiple at close
    hit_horizontal: bool  # True for tp/sl, False for timeout
    holding_bars: int
    expected_holding_bars: int
    horizon_fraction: float  # holding_bars / expected_holding_bars (0.0 if N/A)


def compute_triple_barrier_label(
    *,
    pnl_r: float,
    won: bool,
    holding_bars: int,
    expected_holding_bars: int,
    tp_r: float = DEFAULT_TP_R,
    sl_r: float = DEFAULT_SL_R,
) -> TripleBarrierLabel:
    """Classify a closed trade into a triple-barrier label (pure).

    ``tp_r`` / ``sl_r`` are positive R magnitudes. The realized ``pnl_r`` is
    already cost/ATR-adjusted upstream. ``won`` is accepted for sign-of-record
    but the sign is derived from ``pnl_r`` directly so the label stays
    self-consistent with the continuous target.
    """
    if pnl_r > 0.0:
        pnl_sign = 1
    elif pnl_r < 0.0:
        pnl_sign = -1
    else:
        pnl_sign = 0

    if pnl_r >= tp_r:
        barrier = "tp"
        hit_horizontal = True
    elif pnl_r <= -abs(sl_r):
        barrier = "sl"
        hit_horizontal = True
    else:
        barrier = "timeout"
        hit_horizontal = False

    horizon_fraction = (
        holding_bars / expected_holding_bars if expected_holding_bars > 0 else 0.0
    )

    return TripleBarrierLabel(
        barrier=barrier,
        pnl_sign=pnl_sign,
        r=float(pnl_r),
        hit_horizontal=hit_horizontal,
        holding_bars=int(holding_bars),
        expected_holding_bars=int(expected_holding_bars),
        horizon_fraction=float(horizon_fraction),
    )


def persist_meta_label(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    strategy_id: str,
    venue: str,
    ticker: str,
    regime: str,
    session: str,
    label: TripleBarrierLabel,
    now_ts: int,
) -> str:
    """Insert (or replace) a meta-label row keyed by ``trade_id``.

    Idempotent on ``trade_id`` (UNIQUE) — re-labeling the same trade overwrites
    the prior row rather than duplicating it.
    """
    row = conn.execute(
        "SELECT label_id FROM meta_labels WHERE trade_id = ?", (trade_id,)
    ).fetchone()
    label_id = str(row[0]) if row is not None else uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO meta_labels
            (label_id, trade_id, strategy_id, venue, ticker, regime, session,
             barrier, pnl_sign, r, hit_horizontal, holding_bars,
             expected_holding_bars, horizon_fraction, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_id) DO UPDATE SET
            label_id = excluded.label_id,
            strategy_id = excluded.strategy_id,
            venue = excluded.venue,
            ticker = excluded.ticker,
            regime = excluded.regime,
            session = excluded.session,
            barrier = excluded.barrier,
            pnl_sign = excluded.pnl_sign,
            r = excluded.r,
            hit_horizontal = excluded.hit_horizontal,
            holding_bars = excluded.holding_bars,
            expected_holding_bars = excluded.expected_holding_bars,
            horizon_fraction = excluded.horizon_fraction,
            created_ts = excluded.created_ts
        """,
        (
            label_id,
            trade_id,
            strategy_id,
            venue,
            ticker,
            regime,
            session,
            label.barrier,
            label.pnl_sign,
            label.r,
            1 if label.hit_horizontal else 0,
            label.holding_bars,
            label.expected_holding_bars,
            label.horizon_fraction,
            now_ts,
        ),
    )
    return label_id
