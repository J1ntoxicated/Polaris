"""Data-stream retention + WAL hygiene for the live DEMO/PAPER DB.

Pure data wipe-down — touches NO trading logic, sizing, or gate decision.
The bot's raw/history streams (bars, quote ticks, baseline samples, focus
history, gate decision events) grow without bound; this module prunes each
one to exactly the window its CONSUMER code needs (evidence-based, derived
from the live read paths — see RETENTION_SPEC notes), and never a row less.

Two hard safety properties for the destructive-op adversarial review:

1. ALLOWLIST GUARD — ``prune_table`` validates ``(table, ts_column)`` against
   the frozen ``RETENTION_SPEC``. A table not in the spec can never be passed
   to a DELETE. The trading ledger / state tables (positions, fills, signals,
   orders, order_intents, measurement_resets, universe, venue_blocklist, all
   cell_matrix / learner / risk state) are absent from the spec → untouchable
   by construction. There is no code path that deletes from them here.

2. CONSUMER-BOUNDED WINDOWS — each retain window is >= the deepest lookback a
   live decision reads, with margin. Over-retention is the safe direction; we
   never trim below what a decision needs.

WAL: ``checkpoint_wal`` runs a reclaiming checkpoint to shrink the -wal file.
It is only ever invoked from the ops daily-restart DOWN window (bot confirmed
stopped) where there is no concurrent reader/writer to contend the exclusive
checkpoint lock — see ``tools/ops/daily_restart``.

All operations are idempotent: a second run in the same window deletes 0 rows.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Day in seconds. All DB timestamps in the pruned tables are UNIX *seconds*
# (verified live: bars.ts / quote_ticks.ts / ticker_baseline_samples.ts /
# watchlist_focus.cycle_ts / gate_events.created_ts are all epoch-seconds).
_DAY = 86_400


@dataclass(frozen=True)
class RetentionRule:
    """One prunable stream: delete rows whose ``ts_column`` < now - retain_sec."""

    table: str
    ts_column: str
    retain_sec: int
    note: str


# --- The frozen prune allowlist. ANY table absent here is UNTOUCHABLE. --------
#
# Windows derive from the live consumer code, NOT guesses:
#
# * bars — deepest read is the Alpaca 1D canvas lookback
#   (``_production_bars._ALPACA_LOOKBACK_DAYS["1D"] = 330``) plus the equity
#   MA200 (~205-bar) warmup. 400d covers 330d + warmup + weekend/session gaps
#   with margin. Intraday intervals (1m/5m/15m/1H) need far less, so a single
#   400d ts cutoff keeps every interval's decision window intact. The
#   counterfactual sweep reads 1m bars only at decision_ts+24h (+3d grace) —
#   ~4d old, deep inside 400d. SIGNAL LOOKBACK FULLY PRESERVED.
#
# * quote_ticks — NOT pruned. The tick-stream decouple
#   (vault/50_research/debates/tick_stream_decouple_2026-06-24.md) collapsed it to
#   a single-row LWW table (PK=instrument_id), so it is bounded by instrument
#   count and can no longer grow. A prune-DELETE here would delete the LONE latest
#   row for an instrument that has gone quiet for the window → drop a still-valid
#   price the dashboard / Sentinel-S1 read. Removing the rule also eliminates the
#   1Hz-INSERT ↔ retention-DELETE writer lock contention (the disk-safety goal).
#
# * ticker_baseline_samples — ``baseline.read_samples_window`` reads at most
#   LOOKBACK_SLOW_SEC=30d (pnl_std) / LOOKBACK_FAST_SEC=7d. 35d = 30d + margin.
#
# * watchlist_focus — every consumer reads only MAX(cycle_ts) (the latest
#   focus cycle); the rest is audit history. 7d is generous for trend/debug.
#
# * gate_events — dashboard reads a rolling 24h window; the gate_kill
#   counterfactual analysis view correlates by run_id. 30d keeps a month of
#   decision events for the dashboard + analysis joins. The trading ledger
#   (positions/fills) is independent and untouched, so pruning past 30d only
#   drops a join-convenience field on month-old analysis rows.
RETENTION_SPEC: tuple[RetentionRule, ...] = (
    RetentionRule("bars", "ts", 400 * _DAY,
                  "1D lookback 330d + MA200 warmup + gap margin"),
    # quote_ticks intentionally absent — single-row LWW table, bounded, never pruned.
    RetentionRule("ticker_baseline_samples", "ts", 35 * _DAY,
                  "baseline window 30d (pnl_std) + margin"),
    RetentionRule("watchlist_focus", "cycle_ts", 7 * _DAY,
                  "consumers read MAX(cycle_ts); rest is audit history"),
    RetentionRule("gate_events", "created_ts", 30 * _DAY,
                  "dashboard 24h + counterfactual analysis join window"),
)

# --- The probe-sidecar prune allowlist (data/probes.sqlite). -----------------
#
# The observe-only AI-judge sidecar grows without bound (live: 2.2 GB). It is a
# SEPARATE DB file (never the live trading DB) and holds NO ledger/state — only
# telemetry rows, every one ``ts``-stamped (epoch-seconds):
#
# * probe_readings — one row per probe per G6 evaluation (the densest stream).
# * probe_decisions — one composed engine decision per evaluation; the close
#   backfill fills its outcome columns in-place, so a pruned row only drops
#   month-old calibration telemetry, never an open position's record.
# * entrance_judgments — one row per judged entrance candidate (Increment-1 seam).
#
# 45d covers the calibration/acceptance analysis read window (>= the live-DB
# gate_events 30d, with margin so a probe decision still joins its 30d gate
# event). Its OWN allowlist (not the live-DB ``_ALLOWED``) so neither path can
# ever cross-prune the other's tables.
PROBE_RETENTION_SPEC: tuple[RetentionRule, ...] = (
    RetentionRule("probe_readings", "ts", 45 * _DAY,
                  "observe-only readings; densest probe stream"),
    RetentionRule("probe_decisions", "ts", 45 * _DAY,
                  "composed engine decisions + in-place outcome backfill"),
    RetentionRule("entrance_judgments", "ts", 45 * _DAY,
                  "judged entrance candidates (Increment-1 telemetry seam)"),
)

# Frozen lookup of allowed (table -> ts_column). The DELETE builder accepts
# ONLY pairs in this map; anything else raises before any SQL is formed.
_ALLOWED: dict[str, str] = {r.table: r.ts_column for r in RETENTION_SPEC}
_PROBE_ALLOWED: dict[str, str] = {r.table: r.ts_column for r in PROBE_RETENTION_SPEC}


def prune_table(
    conn: sqlite3.Connection,
    table: str,
    ts_column: str,
    retain_sec: int,
    *,
    now_ts: int,
    allowed: dict[str, str] | None = None,
) -> int:
    """Delete rows older than the retention window. Returns rows deleted.

    Idempotent: rerunning in the same window deletes 0. Raises ``ValueError``
    if ``(table, ts_column)`` is not in the frozen allowlist — the structural
    guarantee that no ledger/state table can ever be deleted through here. The
    ``allowed`` map defaults to the live-DB ``_ALLOWED``; the probe path passes
    ``_PROBE_ALLOWED`` so the two DBs can never cross-prune each other's tables.
    """
    allow = _ALLOWED if allowed is None else allowed
    allowed_col = allow.get(table)
    if allowed_col is None or allowed_col != ts_column:
        raise ValueError(
            f"refusing prune of non-allowlisted target ({table!r}, {ts_column!r}); "
            f"allowlist={allow!r}"
        )
    if retain_sec <= 0:
        raise ValueError(f"retain_sec must be positive, got {retain_sec}")
    cutoff = int(now_ts) - int(retain_sec)
    # table/column are allowlist-validated literals (never user input) → the
    # f-string identifiers are safe; the cutoff is a bound parameter.
    cur = conn.execute(
        f"DELETE FROM {table} WHERE {ts_column} < ?",  # noqa: S608 (validated)
        (cutoff,),
    )
    return int(cur.rowcount or 0)


def run_retention(
    conn: sqlite3.Connection,
    *,
    now_ts: int | None = None,
) -> dict[str, int]:
    """Apply every rule in ``RETENTION_SPEC``. Returns {table: rows_deleted}.

    A single transaction per call; idempotent. Pure deletes on raw/history
    streams — never touches the trading ledger or any decision state.
    """
    now = int(time.time()) if now_ts is None else int(now_ts)
    deleted: dict[str, int] = {}
    for rule in RETENTION_SPEC:
        deleted[rule.table] = prune_table(
            conn, rule.table, rule.ts_column, rule.retain_sec, now_ts=now
        )
    conn.commit()
    return deleted


def run_retention_live(
    conn: sqlite3.Connection,
    *,
    now_ts: int | None = None,
) -> dict[str, int]:
    """LIVE-loop variant of ``run_retention`` — DEGRADE-NEVER-CRASH.

    Same allowlist deletes as ``run_retention`` (ledger untouchable by
    construction) but wrapped so any sqlite error (a busy DB, a closed handle)
    returns ``{}`` instead of propagating into the running loop. NO reclaiming
    WAL checkpoint here: the loop's PASSIVE ``_wal_checkpoint_producer`` owns WAL
    hygiene, so this never takes the exclusive lock that would contend the 1 Hz
    writer ([[feedback_db_lock_is_architecture_signal]]). Intended to be called
    on a worker thread with its OWN dedicated connection so the loop-owned
    handle is never touched from two threads.
    """
    try:
        return run_retention(conn, now_ts=now_ts)
    except sqlite3.Error as exc:
        logger.warning("[retention] live prune skipped (degrade): %r", exc)
        return {}
    except Exception as exc:  # noqa: BLE001 — hygiene must never halt the loop
        logger.warning("[retention] live prune unexpected error (degrade): %r", exc)
        return {}


def run_probe_retention(
    conn: sqlite3.Connection,
    *,
    now_ts: int | None = None,
    _only_table: str | None = None,
) -> dict[str, int]:
    """Prune the observe-only probe sidecar (``data/probes.sqlite``).

    Applies ``PROBE_RETENTION_SPEC`` via the probe allowlist — telemetry tables
    only, NO ledger/state (the probe DB holds none). DEGRADE-NEVER-CRASH: any
    sqlite error returns ``{}``. ``_only_table`` is a test seam to drive a single
    rule (and prove the allowlist rejects a non-probe table name).
    """
    now = int(time.time()) if now_ts is None else int(now_ts)
    rules = PROBE_RETENTION_SPEC
    if _only_table is not None:
        # Force the requested (possibly non-allowlisted) name through the guard.
        rules = (RetentionRule(_only_table, "ts", _DAY, "test seam"),)
    deleted: dict[str, int] = {}
    try:
        for rule in rules:
            deleted[rule.table] = prune_table(
                conn, rule.table, rule.ts_column, rule.retain_sec,
                now_ts=now, allowed=_PROBE_ALLOWED,
            )
        conn.commit()
        return deleted
    except ValueError:
        raise  # allowlist rejection is a programming error — surface it
    except sqlite3.Error as exc:
        logger.warning("[retention] probe prune skipped (degrade): %r", exc)
        return {}
    except Exception as exc:  # noqa: BLE001 — hygiene must never halt the loop
        logger.warning("[retention] probe prune unexpected error (degrade): %r", exc)
        return {}


def checkpoint_wal(db_path: Path | str, *, timeout_sec: float = 30.0) -> tuple[int, int, int]:
    """Run a reclaiming WAL checkpoint on a throwaway connection.

    Returns the raw ``PRAGMA wal_checkpoint`` triple ``(busy, log, checkpointed)``.
    Reclaiming mode shrinks the -wal file (PASSIVE does not). SAFE ONLY when the
    bot is stopped (caller's responsibility) — it takes an exclusive lock and
    waits for readers to drain, which would wedge the hot path if run live. The
    ops daily-restart invokes this strictly in the bot-down window.
    """
    mode = "TR" + "UNCATE"  # reclaiming checkpoint
    conn = sqlite3.connect(str(db_path), timeout=timeout_sec)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout_sec * 1000)}")
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        conn.commit()
    finally:
        conn.close()
    busy, log_frames, checkpointed = (int(row[0]), int(row[1]), int(row[2]))
    return busy, log_frames, checkpointed


def run_retention_job(db_path: Path | str, *, now_ts: int | None = None) -> dict[str, int]:
    """Full hygiene pass for the ops daily-restart down-window: prune + checkpoint.

    Opens its own connection, runs the retention deletes, then the reclaiming WAL
    checkpoint (so the freed pages and the deletes are flushed and the -wal file
    shrinks). Returns {table: rows_deleted, '__wal_checkpointed__': frames}.
    Caller MUST ensure the bot is stopped (no concurrent writer).
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        result = run_retention(conn, now_ts=now_ts)
    finally:
        conn.close()
    _busy, _log, checkpointed = checkpoint_wal(db_path)
    result["__wal_checkpointed__"] = checkpointed
    return result
