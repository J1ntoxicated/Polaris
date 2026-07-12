"""fee-split v1 FLIP (item 4 — re-derivation cadence) — score_f_remap_table
refresh sweeper.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
vault/50_research/debates/fee_split_flip_r2_2026-07-12.md item 4: "250청산
또는 주1회 재도출 + PSI>0.20/KS>0.15 stale 플래그(auto-flag, 차단 아님)."

This script performs ONE full refresh pass (every eligible track + every
venue pool) each time it runs — it does not self-schedule. The "250 closes
or weekly" cadence is an EXTERNAL concern (cron/launchd/ops-automation,
mirroring ``probe_reranker.py``'s own externally-scheduled shape); this
module's job is just to make one refresh pass cheap, idempotent, and safe to
run as often as the scheduler likes (UPSERT — a re-run before the cadence
elapses just overwrites with the same/updated numbers, no double-counting).

Also runs item 5's tripwire report (``polaris.core.classes.tripwires``) and
logs any flagged signal — auto-FLAG only, this script never blocks/kills a
strategy or a transition.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from polaris.core.classes.remap_table import (
    VENUE_POOLS,
    compute_track_remap,
    compute_venue_pool_remap,
    upsert_remap_entry,
)
from polaris.core.classes.tripwires import TripwireReport, compute_tripwire_report

logger = logging.getLogger(__name__)

__all__ = ["refresh_all", "run_refresh"]


def refresh_all(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Refresh every eligible track's remap entry (n>=TRACK_MIN_N) plus all
    3 venue pools. Returns the count of entries written."""
    written = 0
    tracks = conn.execute(
        "SELECT DISTINCT venue, strategy_id FROM positions WHERE status = 'closed'"
    ).fetchall()
    for venue, strategy_id in tracks:
        entry = compute_track_remap(
            conn, venue=str(venue), strategy_id=str(strategy_id), now_ts=now_ts,
        )
        if entry is not None:
            upsert_remap_entry(conn, entry)
            written += 1

    for venue in VENUE_POOLS:
        pool_entry = compute_venue_pool_remap(conn, venue=venue, now_ts=now_ts)
        if pool_entry is not None:
            upsert_remap_entry(conn, pool_entry)
            written += 1

    conn.commit()
    return written


def _log_tripwires(report: TripwireReport) -> None:
    flags = {
        "shadow_divergence_200": report.shadow_divergence_flag,
        "shadow_divergence_7d": report.shadow_divergence_7d_flag,
        "earn_gross_negative": report.earn_flag,
        "kill_fire_rate": report.kill_fire_rate_flag,
        "remap_drift": report.remap_drift_flag,
        "label_churn": report.label_churn_flag,
    }
    hit = {k: v for k, v in flags.items() if v}
    if hit:
        logger.warning("[scoref-remap-refresh] TRIPWIRE FLAG(S): %s", sorted(hit))
    else:
        logger.info("[scoref-remap-refresh] tripwires clean: %s", sorted(flags))


def run_refresh(conn: sqlite3.Connection, *, now_ts: int) -> tuple[int, TripwireReport]:
    written = refresh_all(conn, now_ts=now_ts)
    report = compute_tripwire_report(conn, now_ts=now_ts)
    _log_tripwires(report)
    return written, report


def main() -> int:
    from polaris.logging_config import setup_polaris_logging
    from polaris.storage.schema import init_db
    from tools.ops.ops_config import OpsConfig

    cfg = OpsConfig.default()
    setup_polaris_logging(level="INFO", log_file=str(cfg.bot_log))
    conn = init_db(cfg.db_path)
    try:
        written, report = run_refresh(conn, now_ts=int(time.time()))
        print(
            f"[scoref-remap-refresh] written={written} "
            f"divergence_200={report.shadow_divergence.behavior_divergence_pct:.3f} "
            f"earn_gross_sum={report.earn_gross_sum:.2f} "
            f"stale_scopes={len(report.remap_stale_scopes)}"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
