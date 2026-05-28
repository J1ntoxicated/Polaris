"""Layer 4 — atomic per-trade cell-matrix update (P0 + parent3 + parent2 + shadow).

The write-side SSOT for the cell matrix. Split out of ``routing.py`` to keep
each module ≤500 LOC; ``routing`` re-exports ``update_on_trade_close`` so
``from polaris.core.cell_matrix.routing import update_on_trade_close`` keeps
working. Spec source: vault/30_components/layer-4-cell-matrix.md (Q4-Q6).
"""

from __future__ import annotations

import logging
import math
import sqlite3

from polaris.core.cell_matrix.schema import (
    CELL_DECAY_HALF_LIFE_SEC,
    TradeClose,
)
from polaris.core.cell_matrix.score import (
    compute_avg_pnl_r,
    compute_cell_score,
    decay_factor,
)

logger = logging.getLogger(__name__)


def update_on_trade_close(
    conn: sqlite3.Connection,
    trade: TradeClose,
    *,
    half_life_sec: int = CELL_DECAY_HALF_LIFE_SEC,
) -> None:
    """Update cell_matrix_p0 + parent3 + parent2 + shadow under one transaction.

    Steps for each affected row:
      1. Fetch current state (or zero-row if missing).
      2. Apply EWMA decay (elapsed = ``trade.closed_ts - last_closed_ts``).
      3. Add the new trade (n_eff += 1, pnl_r_sum_eff += pnl_r, wins_eff += won?1:0).
      4. Recompute avg_pnl_r + score.
      5. Upsert.

    Done inside an explicit ``BEGIN``/``COMMIT`` so concurrent writers cannot
    interleave the read-modify-write of the same cell.
    """
    if not math.isfinite(trade.pnl_r):
        raise ValueError("trade.pnl_r must be finite")

    conn.execute("BEGIN IMMEDIATE")
    try:
        _upsert_p0(conn, trade, half_life_sec=half_life_sec)
        _upsert_parent3(conn, trade, half_life_sec=half_life_sec)
        _upsert_parent2(conn, trade, half_life_sec=half_life_sec)
        _upsert_shadow(conn, trade, half_life_sec=half_life_sec)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    k = trade.key
    logger.info(
        "[cell] update_on_trade_close %s/%s/%s/%s pnl_r=%.3f won=%s closed_ts=%d",
        k.exchange,
        k.strategy,
        k.ticker,
        k.regime,
        trade.pnl_r,
        trade.won,
        trade.closed_ts,
    )


def _upsert_p0(
    conn: sqlite3.Connection,
    trade: TradeClose,
    *,
    half_life_sec: int,
) -> None:
    k = trade.key
    row = conn.execute(
        """
        SELECT n_eff, wins_eff, pnl_r_sum_eff, last_closed_ts
        FROM cell_matrix_p0
        WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?
        """,
        (k.exchange, k.strategy, k.ticker, k.regime),
    ).fetchone()
    n_eff, wins_eff, pnl_sum, last_ts = (row if row else (0.0, 0.0, 0.0, 0))
    elapsed = max(0.0, float(trade.closed_ts - int(last_ts)))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec) if last_ts else 1.0
    n_eff = n_eff * factor + 1.0
    wins_eff = wins_eff * factor + (1.0 if trade.won else 0.0)
    pnl_sum = pnl_sum * factor + trade.pnl_r
    avg = compute_avg_pnl_r(pnl_r_sum_eff=pnl_sum, n_eff=n_eff)
    score = compute_cell_score(avg_pnl_r=avg, n_eff=n_eff)
    conn.execute(
        """
        INSERT INTO cell_matrix_p0
            (exchange, strategy, ticker, regime,
             n_eff, wins_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(exchange, strategy, ticker, regime) DO UPDATE SET
            n_eff=excluded.n_eff,
            wins_eff=excluded.wins_eff,
            pnl_r_sum_eff=excluded.pnl_r_sum_eff,
            avg_pnl_r=excluded.avg_pnl_r,
            score=excluded.score,
            last_closed_ts=excluded.last_closed_ts
        """,
        (
            k.exchange, k.strategy, k.ticker, k.regime,
            n_eff, wins_eff, pnl_sum, avg, score, trade.closed_ts,
        ),
    )


def _upsert_parent3(
    conn: sqlite3.Connection,
    trade: TradeClose,
    *,
    half_life_sec: int,
) -> None:
    k = trade.key
    row = conn.execute(
        """
        SELECT n_eff, pnl_r_sum_eff, last_closed_ts
        FROM cell_matrix_parent3
        WHERE exchange = ? AND strategy = ? AND regime = ?
        """,
        (k.exchange, k.strategy, k.regime),
    ).fetchone()
    n_eff, pnl_sum, last_ts = (row if row else (0.0, 0.0, 0))
    elapsed = max(0.0, float(trade.closed_ts - int(last_ts)))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec) if last_ts else 1.0
    n_eff = n_eff * factor + 1.0
    pnl_sum = pnl_sum * factor + trade.pnl_r
    avg = compute_avg_pnl_r(pnl_r_sum_eff=pnl_sum, n_eff=n_eff)
    score = compute_cell_score(avg_pnl_r=avg, n_eff=n_eff)
    conn.execute(
        """
        INSERT INTO cell_matrix_parent3
            (exchange, strategy, regime, n_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(exchange, strategy, regime) DO UPDATE SET
            n_eff=excluded.n_eff,
            pnl_r_sum_eff=excluded.pnl_r_sum_eff,
            avg_pnl_r=excluded.avg_pnl_r,
            score=excluded.score,
            last_closed_ts=excluded.last_closed_ts
        """,
        (k.exchange, k.strategy, k.regime, n_eff, pnl_sum, avg, score, trade.closed_ts),
    )


def _upsert_parent2(
    conn: sqlite3.Connection,
    trade: TradeClose,
    *,
    half_life_sec: int,
) -> None:
    k = trade.key
    row = conn.execute(
        """
        SELECT n_eff, pnl_r_sum_eff, last_closed_ts
        FROM cell_matrix_parent2
        WHERE strategy = ? AND regime = ?
        """,
        (k.strategy, k.regime),
    ).fetchone()
    n_eff, pnl_sum, last_ts = (row if row else (0.0, 0.0, 0))
    elapsed = max(0.0, float(trade.closed_ts - int(last_ts)))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec) if last_ts else 1.0
    n_eff = n_eff * factor + 1.0
    pnl_sum = pnl_sum * factor + trade.pnl_r
    avg = compute_avg_pnl_r(pnl_r_sum_eff=pnl_sum, n_eff=n_eff)
    score = compute_cell_score(avg_pnl_r=avg, n_eff=n_eff)
    conn.execute(
        """
        INSERT INTO cell_matrix_parent2
            (strategy, regime, n_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy, regime) DO UPDATE SET
            n_eff=excluded.n_eff,
            pnl_r_sum_eff=excluded.pnl_r_sum_eff,
            avg_pnl_r=excluded.avg_pnl_r,
            score=excluded.score,
            last_closed_ts=excluded.last_closed_ts
        """,
        (k.strategy, k.regime, n_eff, pnl_sum, avg, score, trade.closed_ts),
    )


def _upsert_shadow(
    conn: sqlite3.Connection,
    trade: TradeClose,
    *,
    half_life_sec: int,
) -> None:
    k = trade.key
    c = trade.context
    row = conn.execute(
        """
        SELECT n_eff, pnl_r_sum_eff, last_closed_ts
        FROM cell_matrix_shadow_context
        WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?
              AND grp = ? AND session = ?
        """,
        (k.exchange, k.strategy, k.ticker, k.regime, c.group, c.session),
    ).fetchone()
    n_eff, pnl_sum, last_ts = (row if row else (0.0, 0.0, 0))
    elapsed = max(0.0, float(trade.closed_ts - int(last_ts)))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec) if last_ts else 1.0
    n_eff = n_eff * factor + 1.0
    pnl_sum = pnl_sum * factor + trade.pnl_r
    avg = compute_avg_pnl_r(pnl_r_sum_eff=pnl_sum, n_eff=n_eff)
    score = compute_cell_score(avg_pnl_r=avg, n_eff=n_eff)
    conn.execute(
        """
        INSERT INTO cell_matrix_shadow_context
            (exchange, strategy, ticker, regime, grp, session,
             n_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(exchange, strategy, ticker, regime, grp, session) DO UPDATE SET
            n_eff=excluded.n_eff,
            pnl_r_sum_eff=excluded.pnl_r_sum_eff,
            avg_pnl_r=excluded.avg_pnl_r,
            score=excluded.score,
            last_closed_ts=excluded.last_closed_ts
        """,
        (
            k.exchange, k.strategy, k.ticker, k.regime, c.group, c.session,
            n_eff, pnl_sum, avg, score, trade.closed_ts,
        ),
    )
