"""Layer 5 triple-block lookup + snapshot-restore (read/replay helpers).

Module-level functions used by the sizing / pre-entry pipeline and operator
rollback path. Split out of ``base.py`` to keep each module ≤500 LOC; ``base``
re-exports them so ``from polaris.core.learners.base import evaluate_triple_block``
keeps working. ADR-007 §triple specific block; spec §Q3 for disk rollback.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from polaris.core.learners._primitives import (
    LEARNER_SNAPSHOT_DIR,
    TRIPLE_BLOCK_DURATION_SEC,
    TRIPLE_BLOCK_EXPECTANCY_THRESHOLD,
    TRIPLE_BLOCK_NEFF_THRESHOLD,
    TRIPLE_BLOCK_SIZE_MULT,
    TRIPLE_BLOCK_WR_THRESHOLD,
    ClosedTrade,
    TripleBlockEntry,
)

logger = logging.getLogger(__name__)


def maybe_emit_triple_block(
    conn: sqlite3.Connection,
    *,
    learner_id: str,
    trade: ClosedTrade,
    ts: int,
) -> None:
    """ADR-007 §triple specific block.

    Triggered from regime_mult only (single source-of-truth for blocks).
    Learners with ``learner_id != 'regime_mult'`` are a no-op.
    """
    if learner_id != "regime_mult":
        return
    # n_eff / wr / expectancy from the just-updated row
    triple_key = f"{trade.ticker}|{trade.strategy_id}|{trade.regime}"
    row = conn.execute(
        "SELECT n_eff, wins_eff, pnl_r_sum_eff FROM learner_state "
        "WHERE learner_id = 'triple_stats' AND key = ?",
        (triple_key,),
    ).fetchone()
    if row is None:
        n_eff, wins, pnl_sum = 0.0, 0.0, 0.0
    else:
        n_eff, wins, pnl_sum = float(row[0]), float(row[1]), float(row[2])
    n_eff += 1.0
    wins += 1.0 if trade.won else 0.0
    pnl_sum += trade.pnl_r
    conn.execute(
        "INSERT INTO learner_state "
        "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        " pending_delta, updated_at) "
        "VALUES ('triple_stats', ?, 1.0, ?, ?, ?, 0.0, ?) "
        "ON CONFLICT(learner_id, key) DO UPDATE SET "
        " n_eff = excluded.n_eff, wins_eff = excluded.wins_eff, "
        " pnl_r_sum_eff = excluded.pnl_r_sum_eff, "
        " updated_at = excluded.updated_at",
        (triple_key, n_eff, wins, pnl_sum, ts),
    )
    if n_eff < TRIPLE_BLOCK_NEFF_THRESHOLD:
        return
    wr = wins / n_eff if n_eff > 0 else 0.0
    expectancy = pnl_sum / n_eff if n_eff > 0 else 0.0
    if wr <= TRIPLE_BLOCK_WR_THRESHOLD and expectancy <= TRIPLE_BLOCK_EXPECTANCY_THRESHOLD:
        until = ts + TRIPLE_BLOCK_DURATION_SEC
        # Level discipline (logging-observability §3): a triple BLOCK is NORMAL
        # learning flow (a losing cell down-weighted), not a degraded/fault
        # condition — log at INFO, never WARNING (WARNING inflated the dashboard
        # error/warn count with expected learner decisions). Behaviour unchanged.
        logger.info(
            "[learner %s] triple BLOCK %s|%s|%s wr=%.2f exp=%.2f size_mult=%.2f until=%d",
            learner_id,
            trade.ticker,
            trade.strategy_id,
            trade.regime,
            wr,
            expectancy,
            TRIPLE_BLOCK_SIZE_MULT,
            until,
        )
        conn.execute(
            "INSERT INTO learner_blocks "
            "(ticker, strategy_id, regime, size_mult, reason, source_learner, "
            " blocked_until_ts, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, strategy_id, regime) DO UPDATE SET "
            " size_mult = excluded.size_mult, "
            " reason = excluded.reason, "
            " source_learner = excluded.source_learner, "
            " blocked_until_ts = excluded.blocked_until_ts, "
            " created_ts = excluded.created_ts",
            (
                trade.ticker,
                trade.strategy_id,
                trade.regime,
                TRIPLE_BLOCK_SIZE_MULT,
                f"wr={wr:.2f},exp={expectancy:.2f}",
                learner_id,
                until,
                ts,
            ),
        )


def evaluate_triple_block(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    strategy_id: str,
    regime: str,
    now_ts: int,
) -> TripleBlockEntry | None:
    """Return active block for ``(ticker, strategy_id, regime)`` or ``None``.

    Auto-unblock: a row whose ``blocked_until_ts <= now_ts`` is treated as
    expired (and lazily not returned). Eviction is handled by the scheduler
    so concurrent readers don't race each other.
    """
    row = conn.execute(
        "SELECT ticker, strategy_id, regime, size_mult, reason, source_learner, "
        " blocked_until_ts, created_ts FROM learner_blocks "
        "WHERE ticker = ? AND strategy_id = ? AND regime = ?",
        (ticker, strategy_id, regime),
    ).fetchone()
    if row is None:
        return None
    if int(row[6]) <= int(now_ts):
        return None
    return TripleBlockEntry(
        ticker=row[0],
        strategy_id=row[1],
        regime=row[2],
        size_mult=float(row[3]),
        reason=row[4],
        source_learner=row[5],
        blocked_until_ts=int(row[6]),
        created_ts=int(row[7]),
    )


def evict_expired_triple_blocks(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Delete rows whose ``blocked_until_ts <= now_ts``. Returns count."""
    cur = conn.execute(
        "DELETE FROM learner_blocks WHERE blocked_until_ts <= ?", (int(now_ts),)
    )
    return int(cur.rowcount or 0)


def restore_snapshot_from_disk(
    conn: sqlite3.Connection,
    *,
    snapshot_ts: int,
    learner_id: str,
    snapshot_dir: Path = LEARNER_SNAPSHOT_DIR,
) -> int:
    """Manual-apply rollback path (spec §Q3 — operator-driven).

    Reads ``<snapshot_dir>/<snapshot_ts>.<learner_id>.json`` and replays the
    rows into ``learner_state`` for the given learner. Returns row count.
    Raises :class:`FileNotFoundError` if the manifest is missing.
    """
    manifest_path = snapshot_dir / f"{snapshot_ts}.{learner_id}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(str(manifest_path))
    payload = json.loads(manifest_path.read_text())
    rows = payload.get("rows", [])
    ts = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "DELETE FROM learner_state WHERE learner_id = ?", (learner_id,)
        )
        for k in rows:
            conn.execute(
                "INSERT INTO learner_state "
                "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
                " pending_delta, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0.0, ?)",
                (
                    learner_id,
                    k["key"],
                    float(k["value"]),
                    float(k["n_eff"]),
                    float(k["wins_eff"]),
                    float(k["pnl_r_sum_eff"]),
                    ts,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows)
