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
_UTC = ZoneInfo("UTC")

# --- Capital (CFD) session calendar (task spec, 2026-07-12 sentry reopen) ---
# Approximate weekday calendar: closed Friday >= 21:00 UTC through Sunday <
# 22:00 UTC. The reopen-side SSOT is the vendor's marketStatus
# (venue-authoritative — polaris/venues/capital/session_calendar.py); this
# sentry uses a plain calendar approximation, which is sufficient here since
# it only judges silence, it never gates an entry (flow_not_block). NOTE this
# intentionally differs from the pipeline's cfd_market_open_at (Friday 22:00
# close) — the task spec pins the sentry's own close hour to 21:00 UTC.
_CAPITAL_CLOSE_HOUR_UTC = 21  # Friday close
_CAPITAL_REOPEN_HOUR_UTC = 22  # Sunday reopen
# Post-reopen grace: SILENT_CAPITAL must not false-positive on the routine
# quiet immediately after the weekly reopen (thin liquidity / gates still
# warming up) before real flow has had a chance to appear.
CAPITAL_REOPEN_GRACE_S = 90 * 60


def capital_session_active(now_epoch: int) -> bool:
    """Approximate Capital CFD weekday-session active check (sentry-only).

    ``True`` Monday-Thursday, Friday before 21:00 UTC, and Sunday at/after
    22:00 UTC + ``CAPITAL_REOPEN_GRACE_S``. ``False`` all day Saturday, Sunday
    before the grace-adjusted reopen, and Friday at/after 21:00 UTC.
    """
    d = datetime.fromtimestamp(now_epoch, tz=_UTC)
    weekday = d.weekday()  # Mon=0 .. Sun=6
    if weekday == 5:  # Saturday — fully closed
        return False
    if weekday == 6:  # Sunday — reopens 22:00 UTC + post-reopen grace
        reopen_ts = int(
            datetime(d.year, d.month, d.day, _CAPITAL_REOPEN_HOUR_UTC, tzinfo=_UTC).timestamp()
        )
        return now_epoch >= reopen_ts + CAPITAL_REOPEN_GRACE_S
    if weekday == 4:  # Friday — closes 21:00 UTC
        return d.hour < _CAPITAL_CLOSE_HOUR_UTC
    return True  # Mon-Thu — open


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
    capital_active: bool
    capital_signals_window: int
    capital_fills_window: int
    capital_gate_events_window: int


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

    capital_active = capital_session_active(now_epoch)
    cur.execute(
        "SELECT COUNT(*) FROM signals WHERE ts > ? AND instrument_id LIKE 'capital:%'",
        (cutoff_epoch,),
    )
    capital_signal_count = int(cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM fills WHERE ts_ms > ? AND instrument_id LIKE 'capital:%'",
        (cutoff_epoch * 1000,),
    )
    capital_fill_count = int(cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM gate_events ge WHERE ge.created_ts > ? AND EXISTS ("
        "SELECT 1 FROM signals s WHERE s.signal_id = ge.signal_id "
        "AND s.instrument_id LIKE 'capital:%')",
        (cutoff_epoch,),
    )
    capital_gate_event_count = int(cur.fetchone()[0])

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
        capital_active=capital_active,
        capital_signals_window=capital_signal_count,
        capital_fills_window=capital_fill_count,
        capital_gate_events_window=capital_gate_event_count,
    )
