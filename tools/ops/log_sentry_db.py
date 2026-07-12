"""DB axis of the dual-axis log+DB sentry — read-only positions/signals scan.

Split from ``tools/ops/log_sentry.py`` (2026-07-12 review) to keep both files
under the project's 500-LOC cap. Contract unchanged: DB opened ``mode=ro``,
deterministic (same DB state -> same output), no write surface.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from polaris.core.sessions.equity_session_gate import us_equity_session_state
from tools.ops._us_market_holidays import us_market_holidays

# --- thresholds (vault backlink: vault/log.md 2026-07-12 WAL-choke incident) ---
RAIL_PNL_R_ANOMALY = -1.2  # exit-rail breach threshold (task spec, matches gate rails)
BATCH_FLUSH_WARN_COUNT = 5  # that day's catch-up flush landed 12 closes in one second
# crypto is the validated LOW-FREQUENCY conditional edge (MEMORY
# project_validated_edge_is_slow_trend_not_scalp) — a live 90min zero-signal
# gap is routine, so only judge crypto silence over a window wide enough to
# clear that with margin. Uses its own dedicated lookback, independent of the
# caller's --window-min (finding 4, 2026-07-12 review): the deployed
# monitor_tick.sh §⑩ --window-min 60 (3600s) made this permanently
# unreachable when coupled to the caller's cutoff.
CRYPTO_SILENT_WINDOW_MIN_S = 14_400
_NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DbMetrics:
    db_reachable: bool
    rail_breach_count: int
    rail_breach_detail: str
    batch_flush_count: int
    batch_flush_detail: str
    crypto_active: bool
    crypto_signals_window: int
    equity_active: bool
    equity_signals_window: int


def open_db_readonly(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error:
        return None


def scan_db_axis(conn: sqlite3.Connection, now_epoch: int, cutoff_epoch: int) -> DbMetrics:
    cur = conn.cursor()

    cur.execute(
        "SELECT symbol, pnl_r FROM positions WHERE closed_ts > ? AND pnl_r <= ? "
        "ORDER BY closed_ts DESC",
        (cutoff_epoch, RAIL_PNL_R_ANOMALY),
    )
    rail_rows = cur.fetchall()

    cur.execute(
        "SELECT closed_ts, COUNT(*) FROM positions WHERE closed_ts > ? "
        "GROUP BY closed_ts HAVING COUNT(*) >= ? ORDER BY closed_ts DESC",
        (cutoff_epoch, BATCH_FLUSH_WARN_COUNT),
    )
    flush_rows = cur.fetchall()

    crypto_cutoff_epoch = now_epoch - CRYPTO_SILENT_WINDOW_MIN_S
    cur.execute(
        "SELECT COUNT(*) FROM signals WHERE ts > ? AND instrument_id LIKE 'okx:%'",
        (crypto_cutoff_epoch,),
    )
    crypto_count = int(cur.fetchone()[0])
    crypto_active = True

    now_ny_date = datetime.fromtimestamp(now_epoch, tz=_NY_TZ).date()
    equity_active = us_equity_session_state(now_epoch) == "rth" and (
        now_ny_date not in us_market_holidays(now_ny_date.year)
    )
    cur.execute(
        "SELECT COUNT(*) FROM signals WHERE ts > ? AND instrument_id LIKE 'alpaca:%'",
        (cutoff_epoch,),
    )
    equity_count = int(cur.fetchone()[0])

    return DbMetrics(
        db_reachable=True,
        rail_breach_count=len(rail_rows),
        rail_breach_detail=" ".join(f"{sym}:{round(float(pnl), 3)}" for sym, pnl in rail_rows),
        batch_flush_count=len(flush_rows),
        batch_flush_detail=" ".join(f"{ts}:{c}" for ts, c in flush_rows),
        crypto_active=crypto_active,
        crypto_signals_window=crypto_count,
        equity_active=equity_active,
        equity_signals_window=equity_count,
    )
