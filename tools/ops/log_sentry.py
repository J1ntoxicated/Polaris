"""Dual-axis log+DB sentry (read-only, deterministic) — DEMO/PAPER only.

Background: vault/log.md 2026-07-12 incident — a dashboard reader choked WAL
checkpointing (writer 497s stall, tick loop frozen 26-50min, a 12-position
catch-up flush landed after the freeze and blew through the exit rail). The
1h Haiku tick monitor (``monitor_tick.sh``) never caught it — it only reads a
fixed 1h DB window, not the log's own cadence/error signals. This module scans
BOTH axes over a recent window and prints fixed ``key=value`` lines any Haiku
tick agent can consume without free-form querying (design precedent:
``monitor_tick.sh`` §⑦/§⑧/§⑨, vault/50_research/backgate-plan/design-monitoring.md
W1 "판정 주체 고정"). DB axis lives in ``tools/ops/log_sentry_db.py`` (split to
keep both files under the project's 500-LOC cap).

Contract: read-only (DB opened ``mode=ro``; log opened for read only), fully
deterministic (same log+DB state -> same output), no write surface — every
result goes to stdout. ``main()`` always returns 0 (an observability probe,
never a gate — flow_not_block).
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.ops.log_sentry_db import (
    DbMetrics,
    capital_session_active,
    open_db_readonly,
    scan_db_axis,
)
from tools.ops.ops_config import OpsConfig

# --- thresholds (vault backlink: vault/log.md 2026-07-12 WAL-choke incident +
# vault/50_research/backgate-plan/design-monitoring.md W1) ---------------
TICK_GAP_WARN_S = 300
TICK_GAP_ANOMALY_S = 900  # today's 26min(1560s) freeze — the miss this sentry fixes
# ignite->first-tick warmup measured 16m53s/3m45s live (finding 1) — daily
# rotation leaves [ignite] w/o [tick] during routine boot; grace covers that.
TICK_MISSING_BOOT_GRACE_S = 1800
DB_WRITER_BATCH_ANOMALY_MS = 30_000.0  # writer stalled 497s == 497000ms that day
ERROR_OTHER_ANOMALY_COUNT = 10
# early-warning precursor threshold, well below the 2026-07-12 incident's
# 305-count full-stall storm (feedback_db_lock_is_architecture_signal — lock
# contention is an architecture signal, must not read STATUS=OK while building)
DB_LOCKED_WARN_COUNT = 10
ALTDATA_STALE_TTL_MULT = 2
MAX_TAIL_BYTES = 20_000_000  # bounds a multi-day log; daily restart keeps this generous
# early-warning precursor to WRITER_QUEUE_FULL, before the queue saturates.
WRITER_QUEUE_PRESSURE_PCT = 0.5

_TICK_RE = re.compile(r"\[tick (\d+)\] focus=")
# qdepth=D/CAP is an optional trailing group so pre-qdepth log lines still parse.
_BATCH_RE = re.compile(r"\[db_writer\] batch \d+ jobs ([\d.]+)\s*ms(?: qdepth=(\d+)/(\d+))?")
_IGNITE_RE = re.compile(r"\[ignite\] CLI invoked")
_REFRESH_RE = re.compile(r"\[altdata\] (\S+) refreshed asset=\S+ ttl=(\d+)s")
_ERROR_RE = re.compile(r"\[ERROR\]")
_DB_LOCKED_RE = re.compile(r"database is locked")
# terminal-phase writer saturation (2026-07-12 review MED-1): once the writer
# is fully wedged it stops COMPLETING batches (batch_max_ms goes silent) but
# emits queue-full degrades — count them so the writer axis stays live.
_QUEUE_FULL_RE = re.compile(r"DBWriter queue full")
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z")


@dataclass(frozen=True)
class LogMetrics:
    log_found: bool
    last_tick_n: int | None
    last_tick_utc: str | None
    last_tick_age_s: int | None
    max_tick_gap_s: int | None
    max_tick_gap_spans_restart: bool
    max_tick_gap_non_restart_s: int | None
    batch_count_window: int
    batch_max_ms: float | None
    batch_p95_ms: float | None
    errors_db_locked_window: int
    errors_other_window: int
    restart_count_window: int
    last_ignite_age_s: int | None
    altdata_last_refreshed_utc: str | None
    altdata_last_refreshed_age_s: int | None
    altdata_stale_channels: str
    writer_queue_full_window: int = 0
    writer_qdepth_pct_max: float | None = None


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
        max_tick_gap_non_restart_s=None,
        batch_count_window=0,
        batch_max_ms=None,
        batch_p95_ms=None,
        errors_db_locked_window=0,
        errors_other_window=0,
        restart_count_window=0,
        writer_queue_full_window=0,
        last_ignite_age_s=None,
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
    qdepth_pct_window: list[float] = []
    db_locked_window = 0
    other_error_window = 0
    restart_window = 0
    queue_full_window = 0
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
                qdepth_str, qcap_str = m_batch.group(2), m_batch.group(3)
                if qdepth_str is not None and qcap_str is not None:
                    qcap = int(qcap_str)
                    if qcap > 0:
                        qdepth_pct_window.append(int(qdepth_str) / qcap)
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

        if ts >= cutoff and _QUEUE_FULL_RE.search(line):
            queue_full_window += 1
            continue

        if ts >= cutoff and _DB_LOCKED_RE.search(line):
            # ERROR 레벨 게이트 없이 독립 매칭 (review LOW — WARNING 레벨
            # 락 라인 ~16%가 조기경보에서 새고 있었음)
            db_locked_window += 1
            continue

        if _ERROR_RE.search(line) and ts >= cutoff:
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
        # non-restart max, split from global (masks a real stall — finding 2/3)
        non_restart_gaps = [g for g, spans in gaps_in_window if not spans]
        max_gap_non_restart: int | None = int(max(non_restart_gaps)) if non_restart_gaps else None
    else:
        max_gap = None
        max_gap_spans_restart = False
        max_gap_non_restart = None
    last_tick_age = int((now - last_tick_ts).total_seconds()) if last_tick_ts else None
    last_ignite_ts = max(ignite_ts) if ignite_ts else None
    last_ignite_age = int((now - last_ignite_ts).total_seconds()) if last_ignite_ts else None

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
        max_tick_gap_non_restart_s=max_gap_non_restart,
        batch_count_window=len(batch_ms_window),
        batch_max_ms=max(batch_ms_window) if batch_ms_window else None,
        batch_p95_ms=_percentile(batch_ms_window, 0.95) if batch_ms_window else None,
        errors_db_locked_window=db_locked_window,
        errors_other_window=other_error_window,
        restart_count_window=restart_window,
        writer_queue_full_window=queue_full_window,
        writer_qdepth_pct_max=max(qdepth_pct_window) if qdepth_pct_window else None,
        last_ignite_age_s=last_ignite_age,
        altdata_last_refreshed_utc=_fmt(last_refresh_ts) if last_refresh_ts else None,
        altdata_last_refreshed_age_s=(
            int((now - last_refresh_ts).total_seconds()) if last_refresh_ts else None
        ),
        altdata_stale_channels=",".join(stale_channels),
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
            age = log_m.last_ignite_age_s
            if age is not None and age <= TICK_MISSING_BOOT_GRACE_S:
                reasons.append("BOOTING")  # routine daily-restart warmup (finding 1)
                warn = True
            else:
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

        gap_nr = log_m.max_tick_gap_non_restart_s
        if gap_nr is not None and gap_nr > TICK_GAP_ANOMALY_S:
            if "TICK_GAP" not in reasons:  # real stall, unmasked (finding 2/3)
                reasons.append("TICK_GAP")
            anomaly = True

        if log_m.batch_max_ms is not None and log_m.batch_max_ms > DB_WRITER_BATCH_ANOMALY_MS:
            reasons.append("WRITER_BATCH_SLOW")
            anomaly = True

        if log_m.errors_other_window > ERROR_OTHER_ANOMALY_COUNT:
            reasons.append("ERROR_OTHER_HIGH")
            anomaly = True

        if log_m.errors_db_locked_window > DB_LOCKED_WARN_COUNT:
            reasons.append("DB_LOCK_CONTENTION")
            warn = True

        if log_m.writer_queue_full_window > 0:
            reasons.append("WRITER_QUEUE_FULL")
            anomaly = True
        elif (
            log_m.writer_qdepth_pct_max is not None
            and log_m.writer_qdepth_pct_max >= WRITER_QUEUE_PRESSURE_PCT
        ):
            reasons.append("WRITER_QUEUE_PRESSURE")
            warn = True

        if log_m.restart_count_window >= 3:
            reasons.append("RESTART_LOOP")  # 워치독 부활 반복 = 크래시 루프
            anomaly = True
        elif log_m.restart_count_window > 0:
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
            # 단독 대형 손실 1-2건 = aggressive 정상 분산 → WARN.
            # 배치 플러시 동반(밀린 엑싯 정산) 또는 3건+ = 시스템성 → ANOMALY
            # (2026-07-12 review MED-2 — 오보 피로 방지, 스로틀 아님·표시 전용).
            if db_m.batch_flush_count > 0 or db_m.rail_breach_count >= 3:
                anomaly = True
            else:
                warn = True
        if db_m.batch_flush_count > 0:
            reasons.append("BATCH_FLUSH")
            warn = True
        if db_m.crypto_active and db_m.crypto_signals_window == 0:
            reasons.append("SESSION_SILENT_CRYPTO")
            warn = True
        if db_m.equity_active and db_m.equity_signals_window == 0:
            reasons.append("SESSION_SILENT_EQUITY")
            warn = True
        if (
            db_m.capital_active
            and db_m.capital_signals_window == 0
            and db_m.capital_fills_window == 0
            and db_m.capital_gate_events_window == 0
            and db_m.crypto_active
            and db_m.crypto_signals_window > 0
        ):
            # capital session active + zero capital activity + crypto flowing
            # normally == the capital track itself is dark (silent-INERT class).
            reasons.append("SILENT_CAPITAL")
            anomaly = True

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
    qdepth_pct_str = (
        "NULL"
        if log_m.writer_qdepth_pct_max is None
        else str(round(log_m.writer_qdepth_pct_max, 3))
    )
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
        f"max_tick_gap_non_restart_s={_s(log_m.max_tick_gap_non_restart_s)}",
        f"db_writer_batch_count_window={log_m.batch_count_window}",
        f"db_writer_batch_max_ms={_s(log_m.batch_max_ms)}",
        f"db_writer_batch_p95_ms={_s(log_m.batch_p95_ms)}",
        f"errors_db_locked_window={log_m.errors_db_locked_window}",
        f"errors_other_window={log_m.errors_other_window}",
        f"restart_count_window={log_m.restart_count_window}",
        f"writer_queue_full_window={log_m.writer_queue_full_window}",
        f"db_writer_qdepth_pct_max={qdepth_pct_str}",
        f"last_ignite_age_s={_s(log_m.last_ignite_age_s)}",
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
        f"capital_active={_s(db_m.capital_active)}",
        f"capital_signals_window={db_m.capital_signals_window}",
        f"capital_fills_window={db_m.capital_fills_window}",
        f"capital_gate_events_window={db_m.capital_gate_events_window}",
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
            capital_active=capital_session_active(int(now.timestamp())),
            capital_signals_window=0,
            capital_fills_window=0,
            capital_gate_events_window=0,
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
