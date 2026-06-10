"""Correct fake-PENDING Capital faults — relabel + halt invalidation (dry-run default).

DEMO/PAPER. The D-1 adapter bug labelled Capital HTTP errors "PENDING", which
``_production_reject`` then misclassified as FAULT_REJECT (strategy fault) and
chained into SOFT_HALTs (live: 159 faults / 55 halts across 4 strategies).
This script marks those rows invalid WITHOUT deleting (A3 raw-data audit
trail): ``fault_type`` ``reject`` → ``reject_invalidated`` + a ``detail_json``
marker; chained halts get the marker (+ a reset stamp for a still-open row)
behind a per-halt recompute guard.

Both venue-side label paths (the adapter HTTP-error mislabel AND the
confirm-poll PENDING stall) wrote identical detail keys — indistinguishable in
the DB and equally invalid as strategy faults, so one match corrects both.

Usage (bot STOPPED; back up the DB first):
    python3 -m polaris.scripts.correct_fake_pending_faults --db data/polaris_live.sqlite
    python3 -m polaris.scripts.correct_fake_pending_faults --db ... --apply

Dry-run (default) opens the DB read-only (``mode=ro`` URI) — writes are
impossible by connection mode. ``--apply`` runs one BEGIN IMMEDIATE txn.
"""

from __future__ import annotations

import argparse
import sqlite3
from time import time as _now
from typing import Any

from polaris.core.isolation.circuit_breaker import CB_REJECT_THRESHOLD

__all__ = ["main", "run"]

INVALIDATION_REASON = "fake_pending_http_error_mislabel"
RESET_BY = "fake_pending_correction"

# The fake-PENDING fault signature (written by _handle_open_reject). COALESCE
# is not needed here: a missing key → NULL → no match, which is correct.
_FAKE_FAULT_WHERE = (
    "fault_type = 'reject' "
    "AND json_extract(detail_json, '$.reject_code') = 'PENDING' "
    "AND json_extract(detail_json, '$.venue') = 'capital' "
    "AND json_extract(detail_json, '$.phase') = 'real_open_fill'"
)
# Same signature for use inside a NOT(...) — COALESCE so a reject fault with
# different detail keys (e.g. idempotent_register) is never NULLed out of the
# remaining-reject recompute count.
_NOT_FAKE_FAULT = (
    "NOT (COALESCE(json_extract(detail_json, '$.reject_code'), '') = 'PENDING' "
    "AND COALESCE(json_extract(detail_json, '$.venue'), '') = 'capital' "
    "AND COALESCE(json_extract(detail_json, '$.phase'), '') = 'real_open_fill')"
)


def _connect(db_path: str, *, apply: bool) -> sqlite3.Connection:
    if apply:
        return sqlite3.connect(db_path, isolation_level=None)
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)


def _select_fake_faults(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """[(event_id, strategy_id)] for every still-unlabeled fake-PENDING fault."""
    rows = conn.execute(
        "SELECT event_id, strategy_id FROM strategy_fault_events "
        f"WHERE {_FAKE_FAULT_WHERE} ORDER BY event_ts"
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def _select_target_halts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Halts whose TRIGGER fault carried the fake PENDING detail (the halt
    detail_json is a copy of the trigger fault's detail). Idempotent: rows
    already carrying the invalidation marker are excluded."""
    rows = conn.execute(
        "SELECT halt_id, strategy_id, opened_ts, reset_ts FROM strategy_halts "
        "WHERE reason_code = 'reject' "
        "AND json_extract(detail_json, '$.reject_code') = 'PENDING' "
        "AND json_extract(detail_json, '$.venue') = 'capital' "
        "AND json_extract(detail_json, '$.invalidated') IS NULL "
        "ORDER BY opened_ts"
    ).fetchall()
    return [
        {
            "halt_id": str(r[0]),
            "strategy_id": str(r[1]),
            "opened_ts": int(r[2]),
            "open": r[3] is None,
        }
        for r in rows
    ]


def _remaining_rejects_in_window(
    conn: sqlite3.Connection, *, strategy_id: str, opened_ts: int
) -> int:
    """Reject faults that would REMAIN in the halt's trigger window after the
    fake-PENDING relabel (post-apply the relabeled rows are already excluded
    by fault_type; the NOT-fake clause makes dry-run and apply agree)."""
    _, window_sec = CB_REJECT_THRESHOLD
    row = conn.execute(
        "SELECT COUNT(*) FROM strategy_fault_events "
        "WHERE strategy_id = ? AND fault_type = 'reject' "
        "AND event_ts >= ? AND event_ts <= ? "
        f"AND {_NOT_FAKE_FAULT}",
        (strategy_id, opened_ts - window_sec, opened_ts),
    ).fetchone()
    return int(row[0])


def _reverse_mixed_windows(conn: sqlite3.Connection) -> int:
    """Review nit: halts TRIGGERED by a non-PENDING reject whose window also
    held fake PENDING faults. Out of correction scope (the trigger fault was
    real) — reported so a non-zero count is visible. Live expectation: 0."""
    _, window_sec = CB_REJECT_THRESHOLD
    rows = conn.execute(
        "SELECT strategy_id, opened_ts FROM strategy_halts "
        "WHERE reason_code = 'reject' "
        "AND NOT (COALESCE(json_extract(detail_json, '$.reject_code'), '') = 'PENDING' "
        "AND COALESCE(json_extract(detail_json, '$.venue'), '') = 'capital')"
    ).fetchall()
    mixed = 0
    for strategy_id, opened_ts in rows:
        fake = conn.execute(
            "SELECT COUNT(*) FROM strategy_fault_events "
            f"WHERE strategy_id = ? AND {_FAKE_FAULT_WHERE} "
            "AND event_ts >= ? AND event_ts <= ?",
            (str(strategy_id), int(opened_ts) - window_sec, int(opened_ts)),
        ).fetchone()
        if int(fake[0]) > 0:
            mixed += 1
    return mixed


def _marker_json(now_ts: int, *, with_orig: bool) -> str:
    """SQL fragment: json_object(...) for the '$.invalidated' marker."""
    if with_orig:
        return (
            "json_object('reason', ?, 'orig_fault_type', 'reject', "
            f"'corrected_ts', {now_ts})"
        )
    return f"json_object('reason', ?, 'corrected_ts', {now_ts})"


def run(db_path: str, *, apply: bool, now_ts: int | None = None) -> dict[str, Any]:
    """Execute the correction (or the dry-run report). Returns the summary."""
    now = int(now_ts if now_ts is not None else _now())
    threshold_count, _ = CB_REJECT_THRESHOLD
    conn = _connect(db_path, apply=apply)
    try:
        faults = _select_fake_faults(conn)
        by_strategy: dict[str, int] = {}
        for _eid, strategy in faults:
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        halts = _select_target_halts(conn)
        for h in halts:
            remaining = _remaining_rejects_in_window(
                conn, strategy_id=h["strategy_id"], opened_ts=h["opened_ts"]
            )
            h["remaining_rejects"] = remaining
            h["verdict"] = (
                "STILL_VALID" if remaining >= threshold_count else "INVALIDATED"
            )
        reverse_mixed = _reverse_mixed_windows(conn)

        fault_relabeled = 0
        halt_invalidated = 0
        halt_reset = 0
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE strategy_fault_events "
                "SET fault_type = 'reject_invalidated', "
                "detail_json = json_set(detail_json, '$.invalidated', "
                f"{_marker_json(now, with_orig=True)}) "
                f"WHERE {_FAKE_FAULT_WHERE}",
                (INVALIDATION_REASON,),
            )
            fault_relabeled = cur.rowcount or 0
            for h in halts:
                if h["verdict"] != "INVALIDATED":
                    continue
                conn.execute(
                    "UPDATE strategy_halts "
                    "SET detail_json = json_set(detail_json, '$.invalidated', "
                    f"{_marker_json(now, with_orig=False)}) "
                    "WHERE halt_id = ?",
                    (INVALIDATION_REASON, h["halt_id"]),
                )
                halt_invalidated += 1
                if h["open"]:
                    # Still-open chained halt: stamp the reset (record honesty —
                    # the SOFT_HALT would auto-expire on the next mode read).
                    conn.execute(
                        "UPDATE strategy_halts SET reset_ts = ?, reset_by = ? "
                        "WHERE halt_id = ? AND reset_ts IS NULL",
                        (now, RESET_BY, h["halt_id"]),
                    )
                    halt_reset += 1
            conn.execute("COMMIT")
        summary: dict[str, Any] = {
            "applied": apply,
            "fault_matches": len(faults),
            "fault_by_strategy": by_strategy,
            "fault_relabeled": fault_relabeled,
            "halt_matches": len(halts),
            "halts": halts,
            "halt_invalidated": halt_invalidated,
            "halt_still_valid": sum(
                1 for h in halts if h["verdict"] == "STILL_VALID"
            ),
            "halt_reset": halt_reset,
            "reverse_mixed_windows": reverse_mixed,
        }
        return summary
    finally:
        conn.close()


def _print_report(summary: dict[str, Any]) -> None:
    mode = "APPLY" if summary["applied"] else "DRY-RUN (read-only)"
    print(f"[fake-pending correction] mode={mode}")
    print(f"fake PENDING faults matched: {summary['fault_matches']}")
    for strategy, n in sorted(summary["fault_by_strategy"].items()):
        print(f"  {strategy}: {n}")
    print(f"chained halts matched: {summary['halt_matches']}")
    for h in summary["halts"]:
        print(
            f"  {h['halt_id']} strategy={h['strategy_id']} "
            f"opened_ts={h['opened_ts']} open={h['open']} "
            f"remaining_rejects={h['remaining_rejects']} verdict={h['verdict']}"
        )
    print(
        "reverse mixed windows (non-PENDING-triggered halts with fakes in "
        f"window, NOT corrected): {summary['reverse_mixed_windows']}"
    )
    if summary["applied"]:
        print(
            f"applied: faults relabeled={summary['fault_relabeled']} "
            f"halts invalidated={summary['halt_invalidated']} "
            f"(still_valid preserved={summary['halt_still_valid']}) "
            f"open halts reset={summary['halt_reset']}"
        )
    else:
        print("no changes written — rerun with --apply (bot stopped + DB backed up)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Invalidate fake-PENDING Capital faults + chained halts "
        "(DEMO/PAPER; dry-run by default)."
    )
    parser.add_argument("--db", required=True, help="path to the SQLite DB")
    parser.add_argument(
        "--apply", action="store_true",
        help="write the correction (default: read-only dry-run report)",
    )
    args = parser.parse_args(argv)
    summary = run(args.db, apply=bool(args.apply))
    _print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
