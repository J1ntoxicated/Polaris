"""Dual-axis log+DB sentry (read-only, deterministic) — DEMO/PAPER only.

Background: vault/log.md 2026-07-12 incident — a dashboard reader choked WAL
checkpointing (writer 497s stall, tick loop frozen 26-50min, a 12-position
catch-up flush landed after the freeze and blew through the exit rail). The
1h Haiku tick monitor (``monitor_tick.sh``) never caught it — it only reads a
fixed 1h DB window, not the log's own cadence/error signals. This module scans
BOTH axes over a recent window and prints fixed ``key=value`` lines any Haiku
tick agent can consume without free-form querying (design precedent:
``monitor_tick.sh`` §⑦/§⑧/§⑨, vault/50_research/backgate-plan/design-monitoring.md
W1 "판정 주체 고정").

Contract: read-only (DB opened ``mode=ro``; log opened for read only), fully
deterministic (same log+DB state -> same output), no write surface — every
result goes to stdout. ``main()`` always returns 0 (an observability probe,
never a gate — flow_not_block).
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from polaris.core.sessions.equity_session_gate import us_equity_session_state
from tools.ops._us_market_holidays import us_market_holidays
from tools.ops.ops_config import OpsConfig

# --- thresholds (vault backlink: vault/log.md 2026-07-12 WAL-choke incident +
# vault/50_research/backgate-plan/design-monitoring.md W1) ---------------
TICK_GAP_WARN_S = 300
TICK_GAP_ANOMALY_S = 900  # today's 26min(1560s) freeze — the miss this sentry fixes
DB_WRITER_BATCH_ANOMALY_MS = 30_000.0  # writer stalled 497s == 497000ms that day
ERROR_OTHER_ANOMALY_COUNT = 10
# early-warning precursor threshold, well below the 2026-07-12 incident's
# 305-count full-stall storm (feedback_db_lock_is_architecture_signal — lock
# contention is an architecture signal, must not read STATUS=OK while building)
DB_LOCKED_WARN_COUNT = 10
RAIL_PNL_R_ANOMALY = -1.2  # exit-rail breach threshold (task spec, matches gate rails)
BATCH_FLUSH_WARN_COUNT = 5  # that day's catch-up flush landed 12 closes in one second
ALTDATA_STALE_TTL_MULT = 2
# crypto is the validated LOW-FREQUENCY conditional edge (MEMORY
# project_validated_edge_is_slow_trend_not_scalp) — a live 90min zero-signal
# gap is routine (2026-07-12 log_sentry review), so only judge crypto silence
# over a window wide enough to clear that with margin
CRYPTO_SILENT_WINDOW_MIN_S = 14_400
MAX_TAIL_BYTES = 20_000_000  # bounds a multi-day log; daily restart keeps this generous
_NY_TZ = ZoneInfo("America/New_York")

_TICK_RE = re.compile(r"\[tick (\d+)\] focus=")
_BATCH_RE = re.compile(r"\[db_writer\] batch \d+ jobs ([\d.]+)\s*ms")
_IGNITE_RE = re.compile(r"\[ignite\] CLI invoked")
_REFRESH_RE = re.compile(r"\[altdata\] (\S+) refreshed asset=\S+ ttl=(\d+)s")
_ERROR_RE = re.compile(r"\[ERROR\]")
_DB_LOCKED_RE = re.compile(r"database is locked")
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z")


@dataclass(frozen=True)
class LogMetrics:
    log_found: bool
    last_tick_n: int | None
    last_tick_utc: str | None
    last_tick_age_s: int | None
    max_tick_gap_s: int | None
    max_tick_gap_spans_restart: bool
    batch_count_window: int
    batch_max_ms: float | None
    batch_p95_ms: float | None
    errors_db_locked_window: int
    errors_other_window: int
    restart_count_window: int
    altdata_last_refreshed_utc: str | None
    altdata_last_refreshed_age_s: int | None
    altdata_stale_channels: str


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


def _fmt(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
    except ValueError:
        return None


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(pct * len(s)) - 1))
    return s[idx]


def read_tail_lines(path: Path, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    """Read the log's tail (bounded), read-only. [] if the file is absent."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()  # drop the partial first line from the seek
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()


def scan_log_axis(lines: list[str], now: datetime, cutoff: datetime) -> LogMetrics:
    empty = LogMetrics(
        log_found=False,
        last_tick_n=None,
        last_tick_utc=None,
        last_tick_age_s=None,
        max_tick_gap_s=None,
        max_tick_gap_spans_restart=False,
        batch_count_window=0,
        batch_max_ms=None,
        batch_p95_ms=None,
        errors_db_locked_window=0,
        errors_other_window=0,
        restart_count_window=0,
        altdata_last_refreshed_utc=None,
        altdata_last_refreshed_age_s=None,
        altdata_stale_channels="",
    )
    if not lines:
        return empty

    tick_ts: list[datetime] = []
    ignite_ts: list[datetime] = []
    last_tick_n: int | None = None
    last_tick_ts: datetime | None = None
    batch_ms_window: list[float] = []
    db_locked_window = 0
    other_error_window = 0
    restart_window = 0
    refresh_last_ts: dict[str, datetime] = {}
    refresh_ttl: dict[str, int] = {}

    for line in lines:
        ts = _parse_ts(line)
        if ts is None:
            continue

        m_tick = _TICK_RE.search(line)
        if m_tick:
            tick_ts.append(ts)
            last_tick_n = int(m_tick.group(1))
            last_tick_ts = ts
            continue

        m_batch = _BATCH_RE.search(line)
        if m_batch:
            if ts >= cutoff:
                batch_ms_window.append(float(m_batch.group(1)))
            continue

        if _IGNITE_RE.search(line):
            ignite_ts.append(ts)  # unfiltered: used below to bridge a tick-gap pair
            if ts >= cutoff:
                restart_window += 1
            continue

        m_refresh = _REFRESH_RE.search(line)
        if m_refresh:
            source, ttl_s = m_refresh.group(1), int(m_refresh.group(2))
            refresh_ttl[source] = ttl_s
            if source not in refresh_last_ts or ts > refresh_last_ts[source]:
                refresh_last_ts[source] = ts
            continue

        if _ERROR_RE.search(line) and ts >= cutoff:
            if _DB_LOCKED_RE.search(line):
                db_locked_window += 1
            else:
                other_error_window += 1

    tick_ts.sort()
    ignite_ts.sort()
    # (gap_seconds, spans_a_restart) per consecutive tick pair in the window. A
    # gap that brackets an [ignite] line is downtime explained by a restart
    # (crash+watchdog, slow boot, manual) — not a live stall (2026-07-12
    # log_sentry review: an append-mode log tail pairs the last pre-restart
    # tick with the first post-restart tick, so this class was false-ANOMALYing
    # an already-recovered bot).
    gaps_in_window = [
        ((t2 - t1).total_seconds(), any(t1 <= ig <= t2 for ig in ignite_ts))
        for t1, t2 in zip(tick_ts, tick_ts[1:], strict=False)
        if t2 >= cutoff
    ]
    if gaps_in_window:
        max_gap_s, max_gap_spans_restart = max(gaps_in_window, key=lambda g: g[0])
        max_gap: int | None = int(max_gap_s)
    else:
        max_gap = None
        max_gap_spans_restart = False
    last_tick_age = int((now - last_tick_ts).total_seconds()) if last_tick_ts else None

    stale_channels = sorted(
        source
        for source, last in refresh_last_ts.items()
        if (now - last).total_seconds() > ALTDATA_STALE_TTL_MULT * refresh_ttl[source]
    )
    last_refresh_ts = max(refresh_last_ts.values()) if refresh_last_ts else None

    return LogMetrics(
        log_found=True,
        last_tick_n=last_tick_n,
        last_tick_utc=_fmt(last_tick_ts) if last_tick_ts else None,
        last_tick_age_s=last_tick_age,
        max_tick_gap_s=max_gap,
        max_tick_gap_spans_restart=max_gap_spans_restart,
        batch_count_window=len(batch_ms_window),
        batch_max_ms=max(batch_ms_window) if batch_ms_window else None,
        batch_p95_ms=_percentile(batch_ms_window, 0.95) if batch_ms_window else None,
        errors_db_locked_window=db_locked_window,
        errors_other_window=other_error_window,
        restart_count_window=restart_window,
        altdata_last_refreshed_utc=_fmt(last_refresh_ts) if last_refresh_ts else None,
        altdata_last_refreshed_age_s=(
            int((now - last_refresh_ts).total_seconds()) if last_refresh_ts else None
        ),
        altdata_stale_channels=",".join(stale_channels),
    )


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

    cur.execute(
        "SELECT COUNT(*) FROM signals WHERE ts > ? AND instrument_id LIKE 'okx:%'",
        (cutoff_epoch,),
    )
    crypto_count = int(cur.fetchone()[0])
    # crypto silence is only meaningful over a window wide enough to clear the
    # routine low-frequency quiet gap with margin (CRYPTO_SILENT_WINDOW_MIN_S)
    crypto_active = (now_epoch - cutoff_epoch) >= CRYPTO_SILENT_WINDOW_MIN_S

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


def evaluate_status(log_m: LogMetrics, db_m: DbMetrics) -> tuple[str, list[str]]:
    reasons: list[str] = []
    anomaly = False
    warn = False

    if not log_m.log_found:
        reasons.append("LOG_UNREADABLE")
        warn = True
    else:
        if log_m.last_tick_n is None:
            reasons.append("TICK_MISSING")
            anomaly = True
        elif log_m.last_tick_age_s is not None:
            if log_m.last_tick_age_s > TICK_GAP_ANOMALY_S:
                reasons.append("TICK_STALE")
                anomaly = True
            elif log_m.last_tick_age_s > TICK_GAP_WARN_S:
                reasons.append("TICK_STALE")
                warn = True

        if log_m.max_tick_gap_s is not None:
            if log_m.max_tick_gap_s > TICK_GAP_ANOMALY_S:
                reasons.append("TICK_GAP")
                if log_m.max_tick_gap_spans_restart:
                    # downtime explained by a restart, not a live stall
                    warn = True
                else:
                    anomaly = True
            elif log_m.max_tick_gap_s > TICK_GAP_WARN_S:
                reasons.append("TICK_GAP")
                warn = True

        if log_m.batch_max_ms is not None and log_m.batch_max_ms > DB_WRITER_BATCH_ANOMALY_MS:
            reasons.append("WRITER_BATCH_SLOW")
            anomaly = True

        if log_m.errors_other_window > ERROR_OTHER_ANOMALY_COUNT:
            reasons.append("ERROR_OTHER_HIGH")
            anomaly = True

        if log_m.errors_db_locked_window > DB_LOCKED_WARN_COUNT:
            reasons.append("DB_LOCK_CONTENTION")
            warn = True

        if log_m.restart_count_window > 0:
            reasons.append("RESTART_DETECTED")
            warn = True

        if log_m.altdata_stale_channels:
            reasons.append("ALTDATA_STALE")
            warn = True

    if not db_m.db_reachable:
        reasons.append("DB_UNREACHABLE")
        warn = True
    else:
        if db_m.rail_breach_count > 0:
            reasons.append("RAIL_BREACH")
            anomaly = True
        if db_m.batch_flush_count > 0:
            reasons.append("BATCH_FLUSH")
            warn = True
        if db_m.crypto_active and db_m.crypto_signals_window == 0:
            reasons.append("SESSION_SILENT_CRYPTO")
            warn = True
        if db_m.equity_active and db_m.equity_signals_window == 0:
            reasons.append("SESSION_SILENT_EQUITY")
            warn = True

    if anomaly:
        return "ANOMALY", reasons
    if warn:
        return "WARN", reasons
    return "OK", reasons


def _s(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def format_output(
    now: datetime,
    window_min: int,
    cutoff: datetime,
    log_m: LogMetrics,
    db_m: DbMetrics,
    status: str,
    reasons: list[str],
) -> str:
    lines = [
        f"now_utc={_fmt(now)}",
        f"window_min={window_min}",
        f"window_cutoff_utc={_fmt(cutoff)}",
        f"log_found={_s(log_m.log_found)}",
        f"last_tick_n={_s(log_m.last_tick_n)}",
        f"last_tick_utc={_s(log_m.last_tick_utc)}",
        f"last_tick_age_s={_s(log_m.last_tick_age_s)}",
        f"max_tick_gap_s={_s(log_m.max_tick_gap_s)}",
        f"max_tick_gap_spans_restart={_s(log_m.max_tick_gap_spans_restart)}",
        f"db_writer_batch_count_window={log_m.batch_count_window}",
        f"db_writer_batch_max_ms={_s(log_m.batch_max_ms)}",
        f"db_writer_batch_p95_ms={_s(log_m.batch_p95_ms)}",
        f"errors_db_locked_window={log_m.errors_db_locked_window}",
        f"errors_other_window={log_m.errors_other_window}",
        f"restart_count_window={log_m.restart_count_window}",
        f"altdata_last_refreshed_utc={_s(log_m.altdata_last_refreshed_utc)}",
        f"altdata_last_refreshed_age_s={_s(log_m.altdata_last_refreshed_age_s)}",
        f"altdata_stale_channels={log_m.altdata_stale_channels or 'NONE'}",
        f"db_reachable={_s(db_m.db_reachable)}",
        f"rail_breach_count={db_m.rail_breach_count}",
        f"rail_breach_detail={db_m.rail_breach_detail or 'NONE'}",
        f"batch_flush_count={db_m.batch_flush_count}",
        f"batch_flush_detail={db_m.batch_flush_detail or 'NONE'}",
        f"crypto_active={_s(db_m.crypto_active)}",
        f"crypto_signals_window={db_m.crypto_signals_window}",
        f"equity_active={_s(db_m.equity_active)}",
        f"equity_signals_window={db_m.equity_signals_window}",
        f"SENTRY_STATUS={status}",
        f"SENTRY_REASONS={','.join(reasons) if reasons else 'NONE'}",
    ]
    return "\n".join(lines)


def run_sentry(log_path: Path, db_path: Path, window_min: int, now: datetime) -> str:
    cutoff = now - timedelta(minutes=window_min)
    log_m = scan_log_axis(read_tail_lines(log_path), now, cutoff)

    conn = open_db_readonly(db_path)
    if conn is not None:
        try:
            db_m = scan_db_axis(conn, int(now.timestamp()), int(cutoff.timestamp()))
        finally:
            conn.close()
    else:
        db_m = DbMetrics(
            db_reachable=False,
            rail_breach_count=0,
            rail_breach_detail="",
            batch_flush_count=0,
            batch_flush_detail="",
            crypto_active=True,
            crypto_signals_window=0,
            equity_active=False,
            equity_signals_window=0,
        )

    status, reasons = evaluate_status(log_m, db_m)
    return format_output(now, window_min, cutoff, log_m, db_m, status, reasons)


def main(argv: list[str] | None = None) -> int:
    cfg = OpsConfig.default()
    parser = argparse.ArgumentParser(description="Polaris dual-axis log+DB sentry (read-only).")
    parser.add_argument("--window-min", type=int, default=30)
    parser.add_argument("--log", type=Path, default=cfg.bot_log)
    parser.add_argument("--db", type=Path, default=cfg.db_path)
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    print(run_sentry(args.log, args.db, args.window_min, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
