"""Measurement-reset baseline (Jin 2026-06-23) — stamp + read helpers + tiny CLI.

Jin: '실측 위해서 메인로직 바뀌면 pnl 리셋해서 측정해줘.' = a FORWARD measurement
window that restarts clean at each main-logic change. Old positions / fills are
NEVER deleted — they stay archived / referenceable; only the measurement WINDOW
moves so the dashboard reads the NEW edge, not the diluted all-time blend.

This module owns the WRITE + READ of the ``measurement_resets`` ledger:
  - ``stamp_measurement_reset`` inserts one row at the current ts carrying the
    current real-fee-net equity as the baseline (call it when applying a
    main-logic batch). A tiny CLI wraps it for ops use.
  - ``latest_reset`` returns the newest stamped reset (or None).

OBSERVABILITY-ONLY: this is a measurement window key. It is NEVER read by
sizing / gating / exit / strategy / loop — it changes only what the dashboard
measures over (flow_not_block: nothing here blocks or dampens the pipeline).
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import NamedTuple


class MeasurementReset(NamedTuple):
    """One stamped measurement-reset row (the forward-window anchor)."""

    reset_id: int
    reset_ts: int
    equity_baseline_usd: float
    label: str
    git_sha: str


def stamp_measurement_reset(
    conn: sqlite3.Connection,
    *,
    label: str,
    git_sha: str,
    reset_ts: int | None = None,
    equity_baseline_usd: float,
) -> int:
    """Insert one measurement-reset row → return its ``reset_id``.

    ``reset_ts`` defaults to the current wall-clock SECOND (matches
    ``positions.opened_ts``, the since-reset window key). Old rows are NEVER
    touched — each stamp appends; ``latest_reset`` then picks the newest. This is
    the ONLY write to the ledger; it deletes nothing (pre-reset data preserved).
    """
    ts = int(time.time()) if reset_ts is None else int(reset_ts)
    cur = conn.execute(
        "INSERT INTO measurement_resets "
        "(reset_ts, equity_baseline_usd, label, git_sha) VALUES (?, ?, ?, ?)",
        (ts, float(equity_baseline_usd), str(label), str(git_sha)),
    )
    rid = cur.lastrowid
    return int(rid) if rid is not None else 0


def latest_reset(conn: sqlite3.Connection) -> MeasurementReset | None:
    """Newest stamped reset (by reset_ts, tie-break reset_id), or None when none.

    Graceful: a missing table (legacy DB pre-migration) yields None so the
    snapshot falls back to all-time cleanly.
    """
    try:
        row = conn.execute(
            "SELECT reset_id, reset_ts, equity_baseline_usd, label, git_sha "
            "FROM measurement_resets ORDER BY reset_ts DESC, reset_id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return MeasurementReset(
        reset_id=int(row[0]),
        reset_ts=int(row[1] or 0),
        equity_baseline_usd=float(row[2] or 0.0),
        label=str(row[3] or ""),
        git_sha=str(row[4] or ""),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_git_sha() -> str:
    """Current short git sha (best-effort; empty string when git is unavailable)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _current_real_fee_net_equity(db_path: Path) -> float:
    """Real-fee-net equity 'now' from the dashboard snapshot — the baseline.

    Lazy import so the CLI does not drag the snapshot stack into the storage
    package at module load. Falls back to the demo starting equity total when the
    DB has no fills yet (snapshot returns the starting equity in that case).
    """
    from polaris.scripts.dashboard.snapshot import collect_snapshot

    snap = collect_snapshot(db_path)
    return float(snap.equity_now_real_fee_net)


def main(argv: list[str] | None = None) -> int:
    """``stamp_measurement_reset`` CLI — stamp a reset at the current ts.

    Inserts a measurement-reset row carrying the current real-fee-net equity as
    the baseline so the dashboard's since-reset window starts here. Pre-reset data
    is untouched. DEMO/PAPER; observability only — no trading behavior changes.
    """
    parser = argparse.ArgumentParser(
        prog="stamp_measurement_reset",
        description=(
            "Stamp a measurement-reset baseline (forward edge window). "
            "Call when applying a main-logic batch. DEMO; observability only."
        ),
    )
    parser.add_argument("--label", required=True, help="human label for the reset")
    parser.add_argument(
        "--git-sha", default=None,
        help="git sha of the batch (default: current short HEAD)",
    )
    parser.add_argument(
        "--db", default="data/polaris_live.sqlite", help="SQLite DB path",
    )
    parser.add_argument(
        "--equity-baseline", type=float, default=None,
        help="override baseline equity (default: current real-fee-net equity)",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    git_sha = args.git_sha if args.git_sha is not None else _resolve_git_sha()
    baseline = (
        args.equity_baseline
        if args.equity_baseline is not None
        else _current_real_fee_net_equity(db_path)
    )

    # init_db is idempotent (CREATE IF NOT EXISTS) so the table exists even on a
    # legacy DB that predates this migration.
    from polaris.storage.schema import init_db

    conn = init_db(db_path)
    try:
        ts = int(time.time())
        rid = stamp_measurement_reset(
            conn, label=args.label, git_sha=git_sha,
            reset_ts=ts, equity_baseline_usd=baseline,
        )
    finally:
        conn.close()

    print(
        f"stamped measurement reset #{rid}: ts={ts} label={args.label!r} "
        f"git_sha={git_sha!r} equity_baseline=${baseline:,.2f}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
