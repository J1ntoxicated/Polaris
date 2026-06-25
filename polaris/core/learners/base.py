"""Layer 5 — BaseLearner ABC + clip helpers + adaptive_learner_attack constants.

Spec source: vault/30_components/layer-5-learner-network.md (Q1-Q6).
ADR-007: 4 principles — generous default / temporary block / specific triple / toggle.

Pure functions + protocol surface only. SQLite I/O lives in
``polaris.core.learners.{session,regime,max_hold}`` (per-learner) and
``polaris.core.learners.scheduler`` (orchestration).

Constants, result dataclasses, and clip helpers live in ``_primitives``; the
triple-block lookup, block emission, and snapshot-restore free functions live
in ``triple_block``. Both are re-exported here so existing ``learners.base``
import paths keep working.

Design notes:
- Incremental stats per close → ``record_trade_close()``.
- Hourly commit → ``commit_hourly()`` writes ``pending_delta`` into ``value`` and
  resets pending. Caller cron is the scheduler.
- adaptive_learner_attack triple block: SQLite-resident, 1h auto-unblock,
  ``size_mult=0.3`` (entry allowed — 4 principles: generous default).
- Toggle fallback: ``enabled=False`` → caller must use hardcoded default.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.core.learners._primitives import (
    DEFAULT_MAX_HOLD_FALLBACK_BARS,
    LEARNER_DELTA_HOURLY_CAP,
    LEARNER_INDIVIDUAL_MULT_CLIP,
    LEARNER_MIN_NEFF_FOR_DELTA,
    LEARNER_PRODUCT_CLIP,
    LEARNER_SNAPSHOT_DIR,
    LEARNER_SNAPSHOT_KEEP,
    NEUTRAL_MULT,
    TRIPLE_BLOCK_DURATION_SEC,
    TRIPLE_BLOCK_SIZE_MULT,
    WR_DEMOTE_THRESHOLD,
    WR_PROMOTE_THRESHOLD,
    ClosedTrade,
    HourlyCommitReport,
    LearnerMultResult,
    TripleBlockEntry,
    clip_hourly_delta,
    clip_individual_mult,
    clip_product_mult,
    resolve_final_size_mult,
)
from polaris.core.learners.triple_block import (
    evaluate_triple_block,
    evict_expired_triple_blocks,
    maybe_emit_triple_block,
    restore_snapshot_from_disk,
)
from polaris.datastream import emit as datastream_emit

logger = logging.getLogger(__name__)


class BaseLearner(ABC):
    """Abstract base for incremental-stats + hourly-commit learners.

    Subclasses **must** implement:
      - ``learner_id`` (class attribute / property).
      - ``key_for(trade)`` → string key for ``learner_state``.
      - ``observe(trade)`` → update sufficient stats (called by ``update``).
      - ``compute_value_from_stats(stats)`` → derive ``value`` for ``get_mult``.

    The base class supplies the SQLite plumbing: row read/write, per-hour
    delta clip, snapshot + fallback handling, and the
    ``adaptive_learner_attack`` toggle.
    """

    learner_id: str = "base"
    enabled: bool = True

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._pending_disk_snapshot: tuple[int, Mapping[str, Any]] | None = None

    # -- subclass hooks ---------------------------------------------------

    @abstractmethod
    def key_for(self, trade: ClosedTrade) -> str:
        """Return composite key for this trade in ``learner_state.key``."""

    @abstractmethod
    def observe(
        self,
        prior: dict[str, float],
        trade: ClosedTrade,
    ) -> dict[str, float]:
        """Fold ``trade`` into the running stats dict.

        ``prior`` keys: ``n_eff``, ``wins_eff``, ``pnl_r_sum_eff``,
        ``pending_delta``, ``value``. Returns a new dict.
        """

    @abstractmethod
    def compute_value_from_stats(self, stats: dict[str, float]) -> float:
        """Derive the *committed* ``value`` (multiplier) from sufficient stats.

        Called during ``commit_hourly``. Subclasses encode the WR / bucket /
        threshold logic here.
        """

    def fallback_value(self, key: str) -> float:
        """Sparse / disabled fallback. Default = 1.0 (neutral)."""
        return NEUTRAL_MULT

    # -- core operations --------------------------------------------------

    def update(self, trade: ClosedTrade, *, now_ts: int | None = None) -> None:
        """Incremental observe (called per trade close)."""
        if not self.enabled:
            logger.debug(
                "[learner %s] update skipped — disabled (trade_id=%s)",
                self.learner_id,
                trade.trade_id,
            )
            return
        ts = int(now_ts if now_ts is not None else time.time())
        key = self.key_for(trade)
        prior = self._fetch_state(key)
        new_stats = self.observe(prior, trade)
        self._upsert_state(key, new_stats, ts=ts)
        # DATA → datastream sink: a per-trade-close learner observe is structured
        # measurement (firehose at scale), not an operator signal. Hourly commit
        # + rollback below STAY on the runtime log (low-frequency, operational).
        datastream_emit(
            "learner/update",
            learner_id=self.learner_id,
            key=key,
            trade_id=trade.trade_id,
            pnl_r=round(trade.pnl_r, 4),
            won=trade.won,
            n_eff_prior=round(prior.get("n_eff", 0.0), 1),
            n_eff_new=round(new_stats.get("n_eff", 0.0), 1),
            wins_eff_prior=round(prior.get("wins_eff", 0.0), 1),
            wins_eff_new=round(new_stats.get("wins_eff", 0.0), 1),
        )
        # adaptive_learner_attack triple block evaluation (specific axis only)
        self._maybe_emit_triple_block(trade, ts)

    def get_mult(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str = "asia",
    ) -> LearnerMultResult:
        """Read-only multiplier lookup (used by sizing pipeline)."""
        if not self.enabled:
            return LearnerMultResult(
                value=self.fallback_value(""),
                source="disabled",
            )
        key = self._key_for_lookup(
            ticker=ticker, strategy_id=strategy_id, regime=regime, session=session
        )
        row = self.conn.execute(
            "SELECT value, n_eff FROM learner_state WHERE learner_id = ? AND key = ?",
            (self.learner_id, key),
        ).fetchone()
        if row is None:
            return LearnerMultResult(
                value=self.fallback_value(key), source="fallback_neutral"
            )
        value, n_eff = float(row[0]), float(row[1])
        if n_eff < LEARNER_MIN_NEFF_FOR_DELTA:
            return LearnerMultResult(
                value=self.fallback_value(key), source="sparse", n_eff=n_eff
            )
        return LearnerMultResult(value=clip_individual_mult(value), source="live", n_eff=n_eff)

    def commit_hourly(self, *, now_ts: int | None = None) -> HourlyCommitReport:
        """Atomic hourly delta commit — recomputes ``value`` from stats.

        Process per row:
          1. Skip rows where ``n_eff < LEARNER_MIN_NEFF_FOR_DELTA``.
          2. Recompute the candidate value from current stats.
          3. ``delta = candidate - current_value`` (clipped to ±LEARNER_DELTA_HOURLY_CAP).
          4. Write committed value, reset ``pending_delta``.

        Atomicity (codex Day 4 R1 P1 fix): every read AND write happens inside
        the same ``BEGIN IMMEDIATE`` / ``COMMIT`` envelope, including the
        post-commit snapshot. ``BEGIN IMMEDIATE`` takes the writer lock up
        front, so concurrent ``record_trade_close`` writers wait — no
        read-modify-write race window between the candidate read and the
        update, and the snapshot reflects exactly the state this transaction
        committed.
        """
        ts = int(now_ts if now_ts is not None else time.time())
        report = HourlyCommitReport(learner_id=self.learner_id, keys_updated=0)
        if not self.enabled:
            return report
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute(
                "SELECT key, value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
                "FROM learner_state WHERE learner_id = ?",
                (self.learner_id,),
            ).fetchall()
            for r in rows:
                key = r[0]
                stats = {
                    "value": float(r[1]),
                    "n_eff": float(r[2]),
                    "wins_eff": float(r[3]),
                    "pnl_r_sum_eff": float(r[4]),
                    "pending_delta": float(r[5]),
                }
                if stats["n_eff"] < LEARNER_MIN_NEFF_FOR_DELTA:
                    continue
                candidate = self.compute_value_from_stats(stats)
                if not math.isfinite(candidate):
                    continue
                raw_delta = candidate - stats["value"]
                delta = clip_hourly_delta(raw_delta)
                new_value = clip_individual_mult(stats["value"] + delta)
                self.conn.execute(
                    "UPDATE learner_state SET value = ?, pending_delta = 0.0, "
                    "updated_at = ? WHERE learner_id = ? AND key = ?",
                    (new_value, ts, self.learner_id, key),
                )
                report.keys_updated += 1
                report.deltas_applied[key] = delta
            # snapshot inside the same transaction so it reflects commit state
            report.snapshot_ts = self._write_snapshot(now_ts=ts)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        # Disk snapshot (SQLite hot backup + JSON manifest) runs outside the
        # transaction. The in-DB row snapshot above is the rollback SSOT; the disk
        # pair is operator-facing and DISABLED by default — the gate lives INSIDE
        # ``_flush_disk_snapshot`` so it covers EVERY caller (here + the per-learner
        # direct calls e.g. max_hold), which a caller-site gate here would miss.
        self._flush_disk_snapshot()
        logger.info(
            "[learner %s] hourly commit — keys_updated=%d snapshot_ts=%s",
            self.learner_id,
            report.keys_updated,
            report.snapshot_ts,
        )
        return report

    def rollback(self, snapshot_ts: int) -> int:
        """Restore this learner from a JSON snapshot row.

        Returns the number of keys restored. Raises if the snapshot is missing.
        """
        logger.warning(
            "[learner %s] rollback to snapshot_ts=%s",
            self.learner_id,
            snapshot_ts,
        )
        row = self.conn.execute(
            "SELECT payload_json FROM learner_snapshot "
            "WHERE snapshot_ts = ? AND learner_id = ?",
            (snapshot_ts, self.learner_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"snapshot {snapshot_ts} for learner {self.learner_id} not found"
            )
        payload = json.loads(row[0])
        keys = payload.get("rows", [])
        ts = int(time.time())
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "DELETE FROM learner_state WHERE learner_id = ?", (self.learner_id,)
            )
            for k in keys:
                self.conn.execute(
                    "INSERT INTO learner_state "
                    "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
                    " pending_delta, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.learner_id,
                        k["key"],
                        float(k["value"]),
                        float(k["n_eff"]),
                        float(k["wins_eff"]),
                        float(k["pnl_r_sum_eff"]),
                        0.0,
                        ts,
                    ),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return len(keys)

    # -- helpers ----------------------------------------------------------

    def _fetch_state(self, key: str) -> dict[str, float]:
        row = self.conn.execute(
            "SELECT value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
            "FROM learner_state WHERE learner_id = ? AND key = ?",
            (self.learner_id, key),
        ).fetchone()
        if row is None:
            return {
                "value": NEUTRAL_MULT,
                "n_eff": 0.0,
                "wins_eff": 0.0,
                "pnl_r_sum_eff": 0.0,
                "pending_delta": 0.0,
            }
        return {
            "value": float(row[0]),
            "n_eff": float(row[1]),
            "wins_eff": float(row[2]),
            "pnl_r_sum_eff": float(row[3]),
            "pending_delta": float(row[4]),
        }

    def _upsert_state(
        self,
        key: str,
        stats: dict[str, float],
        *,
        ts: int,
    ) -> None:
        self.conn.execute(
            "INSERT INTO learner_state "
            "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
            " pending_delta, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(learner_id, key) DO UPDATE SET "
            " value = excluded.value, "
            " n_eff = excluded.n_eff, "
            " wins_eff = excluded.wins_eff, "
            " pnl_r_sum_eff = excluded.pnl_r_sum_eff, "
            " pending_delta = excluded.pending_delta, "
            " updated_at = excluded.updated_at",
            (
                self.learner_id,
                key,
                clip_individual_mult(stats.get("value", NEUTRAL_MULT)),
                stats.get("n_eff", 0.0),
                stats.get("wins_eff", 0.0),
                stats.get("pnl_r_sum_eff", 0.0),
                stats.get("pending_delta", 0.0),
                ts,
            ),
        )

    def _key_for_lookup(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str,
    ) -> str:
        """Default lookup key composer; subclasses override as needed."""
        return f"{strategy_id}:{regime}"

    def _write_snapshot(self, *, now_ts: int) -> int:
        """Write the in-DB row snapshot (atomic, called inside transaction).

        The on-disk SQLite hot-backup + JSON manifest pair (spec §Q3) is
        written by ``_write_disk_snapshot`` AFTER the transaction commits —
        ``sqlite3.Connection.backup`` would deadlock against the writer
        lock held by ``BEGIN IMMEDIATE`` if we tried to backup here.
        """
        rows = self.conn.execute(
            "SELECT key, value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
            "FROM learner_state WHERE learner_id = ?",
            (self.learner_id,),
        ).fetchall()
        payload = {
            "rows": [
                {
                    "key": r[0],
                    "value": float(r[1]),
                    "n_eff": float(r[2]),
                    "wins_eff": float(r[3]),
                    "pnl_r_sum_eff": float(r[4]),
                    "pending_delta": float(r[5]),
                }
                for r in rows
            ],
        }
        self.conn.execute(
            "INSERT INTO learner_snapshot (snapshot_ts, learner_id, payload_json) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(snapshot_ts, learner_id) DO UPDATE SET "
            " payload_json = excluded.payload_json",
            (now_ts, self.learner_id, json.dumps(payload, separators=(",", ":"))),
        )
        # Stash the payload so the post-commit disk emitter has the exact
        # rows that just landed, without a re-query.
        self._pending_disk_snapshot = (now_ts, payload)
        return now_ts

    def _flush_disk_snapshot(self) -> None:
        """Emit the queued on-disk snapshot, then clear the pending slot.

        DISABLED by default (gate covers ALL callers — commit_hourly AND the
        per-learner direct calls e.g. max_hold). A full ``conn.backup()`` of the
        ~218 MB live DB on the SHARED loop conn blocks the asyncio event loop for
        MINUTES, starving the real-time tick engine + WS (live: 3-min tune freeze,
        tick engine idle). The in-DB row snapshot (``_write_snapshot``) is the
        rollback SSOT — ``rollback`` reads that ROW, nothing reads the disk .db
        files — so the disk pair is purely operator-facing. ``LEARNER_DISK_SNAPSHOT=1``
        re-enables it (accepting the multi-minute loop block).
        """
        if os.environ.get("LEARNER_DISK_SNAPSHOT") != "1":
            self._pending_disk_snapshot = None
            return
        if self._pending_disk_snapshot is None:
            return
        now_ts, payload = self._pending_disk_snapshot
        self._pending_disk_snapshot = None
        self._write_disk_snapshot(now_ts=now_ts, payload=payload)

    def _write_disk_snapshot(
        self, *, now_ts: int, payload: Mapping[str, Any]
    ) -> None:
        """Best-effort SQLite hot backup + manifest JSON.

        Spec §Q3 (vault/30_components/layer-5-learner-network.md):
        ``data/learner_snapshots/<ts>.db`` (full DB hot backup) plus
        ``data/learner_snapshots/<ts>.json`` (manifest with this learner's
        rows). The DB file is shared across learners in the same hour
        (idempotent re-open), while each learner emits its own manifest.

        Skipped automatically when the connection is a ``:memory:`` DB
        (tests, smoke without on-disk path) — the in-DB ``learner_snapshot``
        row already captures the state and is the SSOT for rollback.
        Failures are non-fatal so an ephemeral CI sandbox cannot block
        a learner commit.
        """
        try:
            # ``PRAGMA database_list`` returns the path of the main schema;
            # an empty/`:memory:` value means we have no source file to back
            # up. Skip silently in that case.
            row = self.conn.execute(
                "PRAGMA database_list"
            ).fetchone()
            db_file = "" if row is None else (row[2] or "")
            if not db_file or db_file == ":memory:":
                return
            base = LEARNER_SNAPSHOT_DIR
            base.mkdir(parents=True, exist_ok=True)
            db_path = base / f"{now_ts}.db"
            manifest_path = base / f"{now_ts}.{self.learner_id}.json"
            if not db_path.exists():
                with sqlite3.connect(str(db_path)) as dest:
                    self.conn.backup(dest)
            manifest_payload = {
                "snapshot_ts": now_ts,
                "learner_id": self.learner_id,
                "rows": payload.get("rows", []),
            }
            manifest_path.write_text(
                json.dumps(manifest_payload, separators=(",", ":"))
            )
            _prune_disk_snapshots(base, keep=LEARNER_SNAPSHOT_KEEP)
        except (sqlite3.Error, OSError):
            return

    def _maybe_emit_triple_block(self, trade: ClosedTrade, ts: int) -> None:
        """ADR-007 §triple specific block — delegates to the free function.

        Triggered from regime_mult only (single source-of-truth for blocks);
        the free function is a no-op for any other ``learner_id``.
        """
        maybe_emit_triple_block(
            self.conn, learner_id=self.learner_id, trade=trade, ts=ts
        )


def _prune_disk_snapshots(snapshot_dir: Path, *, keep: int) -> None:
    """Delete all but the newest ``keep`` on-disk snapshot generations.

    A generation = one ``<ts>.db`` hot backup + its sibling ``<ts>.*`` manifests.
    These files are operator disaster-recovery only (``rollback`` reads the
    in-DB ``learner_snapshot`` row, never the disk ``.db``), so pruning old
    generations is safe. Ordered by ``(mtime, ts)`` so the just-written backup is
    always in the keep set and equal-mtime ties break deterministically by
    timestamp (filename stem). ``missing_ok`` keeps it race-safe against a
    sibling learner's prune in the same flush cycle.
    """
    backups = sorted(
        snapshot_dir.glob("*.db"),
        key=lambda p: (p.stat().st_mtime, p.stem),
        reverse=True,
    )
    for stale in backups[keep:]:
        for sibling in snapshot_dir.glob(f"{stale.stem}.*"):
            sibling.unlink(missing_ok=True)


__all__ = [
    "BaseLearner",
    "ClosedTrade",
    "DEFAULT_MAX_HOLD_FALLBACK_BARS",
    "HourlyCommitReport",
    "LEARNER_DELTA_HOURLY_CAP",
    "LEARNER_INDIVIDUAL_MULT_CLIP",
    "LEARNER_MIN_NEFF_FOR_DELTA",
    "LEARNER_PRODUCT_CLIP",
    "LEARNER_SNAPSHOT_DIR",
    "LEARNER_SNAPSHOT_KEEP",
    "LearnerMultResult",
    "NEUTRAL_MULT",
    "TRIPLE_BLOCK_DURATION_SEC",
    "TRIPLE_BLOCK_SIZE_MULT",
    "TripleBlockEntry",
    "WR_DEMOTE_THRESHOLD",
    "WR_PROMOTE_THRESHOLD",
    "clip_hourly_delta",
    "clip_individual_mult",
    "clip_product_mult",
    "evaluate_triple_block",
    "evict_expired_triple_blocks",
    "resolve_final_size_mult",
    "restore_snapshot_from_disk",
]
