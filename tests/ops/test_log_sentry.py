"""log_sentry: synthetic log/DB fixtures -> deterministic judgement assertions.

DEMO/PAPER paper-trading bot, read-only sentry (no write surface). Fixtures
mirror real ``data/paper/polaris_runtime.log`` line shapes (see
vault/log.md 2026-07-12 incident this sentry exists to catch).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pytest

from polaris.storage.schema import ALL_DDL
from tools.ops import log_sentry
from tools.ops.log_sentry_db import CRYPTO_SILENT_WINDOW_MIN_S, capital_session_active

NOW = datetime(2026, 7, 12, 4, 0, 0, tzinfo=UTC)
RTH_EPOCH = 1768402800  # Wed 2026-01-14 15:00 UTC -> us_equity_session_state == "rth"
CLOSED_EPOCH = 1768359600  # Wed 2026-01-14 03:00 UTC -> "closed" (off-hours, weekday)
WEEKEND_EPOCH = 1768662000  # Sat 2026-01-17 15:00 UTC -> "closed" (weekend)
CHRISTMAS_RTH_EPOCH = 1798210800  # Fri 2026-12-25 15:00 UTC -> "rth" by clock-of-day, holiday


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000") + "Z"


def _tick_line(dt: datetime, n: int) -> str:
    return f"{_ts(dt)} [INFO] polaris.scripts._production_tick:1423 [tick {n}] focus=102 open=1"


def _batch_line(dt: datetime, ms: float) -> str:
    return f"{_ts(dt)} [DEBUG] polaris.storage.db_writer:304 [db_writer] batch 1 jobs {ms}ms"


def _ignite_line(dt: datetime) -> str:
    return f"{_ts(dt)} [INFO] __main__:393 [ignite] CLI invoked level=DEBUG argv=['--paper']"


def _refresh_line(dt: datetime, source: str, ttl: int) -> str:
    return (
        f"{_ts(dt)} [INFO] polaris.scripts.production_paper_loop:513 "
        f"[altdata] {source} refreshed asset=crypto ttl={ttl}s"
    )


def _error_locked_line(dt: datetime) -> str:
    return (
        f"{_ts(dt)} [ERROR] polaris.scripts._production_tick:537 "
        "[tick 1] stage=regime_compute[okx:BTC-USDT] failed: OperationalError: "
        "database is locked"
    )


def _error_locked_lines(dt: datetime, count: int) -> list[str]:
    return [_error_locked_line(dt) for _ in range(count)]


def _error_other_line(dt: datetime, n: int) -> str:
    return (
        f"{_ts(dt)} [ERROR] polaris.scripts._production_tick:537 "
        f"[tick {n}] stage=sizing[okx:BTC-USDT] failed: ValueError: boom {n}"
    )


class MakeDb(Protocol):
    def __call__(self, path: Path | None = None) -> Path: ...


@pytest.fixture
def make_db(tmp_path: Path) -> MakeDb:
    def _make(path: Path | None = None) -> Path:
        db_path = path or (tmp_path / "polaris_live.sqlite")
        conn = sqlite3.connect(db_path)
        for stmt in ALL_DDL:
            conn.execute(stmt)
        conn.commit()
        conn.close()
        return db_path

    return _make


def _insert_position(
    db_path: Path, position_id: str, symbol: str, pnl_r: float, closed_ts: int
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, closed_ts, pnl_r) "
        "VALUES (?, 'okx', ?, 'strat', 'strat', 'strat', 'long', 1.0, 'closed', ?, ?)",
        (position_id, symbol, closed_ts, pnl_r),
    )
    conn.commit()
    conn.close()


def _insert_signal(db_path: Path, signal_id: str, instrument_id: str, ts: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
        "score, thesis, ts) VALUES ('strat', ?, ?, 'long', 1.0, 't', ?)",
        (signal_id, instrument_id, ts),
    )
    conn.commit()
    conn.close()


def _insert_fill(db_path: Path, fill_id: str, instrument_id: str, ts_ms: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id) VALUES "
        "(?, 'capital', ?, 'strat', 'long', 100.0, 1.0, ?, 'o1')",
        (fill_id, instrument_id, ts_ms),
    )
    conn.commit()
    conn.close()


def _insert_gate_event(
    db_path: Path, event_id: str, signal_id: str, created_ts: int
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, signal_id, gate_id, phase, "
        "created_ts) VALUES (?, 'run1', ?, 3, 'success', ?)",
        (event_id, signal_id, created_ts),
    )
    conn.commit()
    conn.close()


# --- log axis ----------------------------------------------------------------


def test_normal_log_is_ok() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=70), 10),
        _tick_line(NOW - timedelta(seconds=10), 11),
        _refresh_line(NOW - timedelta(seconds=30), "binance_deriv", 300),
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.last_tick_n == 11
    assert m.last_tick_age_s == 10
    assert m.max_tick_gap_s == 60
    assert m.altdata_stale_channels == ""
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "OK"
    assert reasons == []


def test_tick_gap_anomaly_isolated() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=1600), 1),  # gap victim
        _tick_line(NOW - timedelta(seconds=40), 2),  # recovered, recent
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=40))
    assert m.max_tick_gap_s == 1560  # today's 26min freeze class
    assert m.max_tick_gap_spans_restart is False  # no [ignite] in the gap -> real stall
    assert m.last_tick_age_s == 40  # NOT stale — isolates the gap signal
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "TICK_GAP" in reasons
    assert "TICK_STALE" not in reasons


def test_tick_gap_across_restart_downgraded_to_warn() -> None:
    """A tick-gap whose interval brackets an [ignite] restart is downtime
    explained by the restart, not a live stall — RESTART_DETECTED already
    covers it at WARN, so TICK_GAP must not additionally escalate to ANOMALY
    on an already-recovered bot (2026-07-12 log_sentry review)."""
    lines = [
        _tick_line(NOW - timedelta(seconds=1600), 1),  # gap victim
        _ignite_line(NOW - timedelta(seconds=800)),  # restart inside the gap
        _tick_line(NOW - timedelta(seconds=40), 2),  # recovered, recent
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=40))
    assert m.max_tick_gap_s == 1560
    assert m.max_tick_gap_spans_restart is True
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "TICK_GAP" in reasons
    assert "RESTART_DETECTED" in reasons


def test_tick_gap_non_restart_anomaly_not_masked_by_larger_restart_gap() -> None:
    """A genuine post-restart stall must ANOMALY even when a larger restart-
    bracketed boot gap is the window's global max (finding 2/3, 2026-07-12
    review — max_tick_gap collapsing to a single gap masked exactly this
    incident pattern: restart THEN freeze)."""
    lines = [
        _tick_line(NOW - timedelta(seconds=3600), 1),  # pre-restart tick
        _ignite_line(NOW - timedelta(seconds=3000)),  # restart
        _tick_line(NOW - timedelta(seconds=1680), 2),  # boot gap 1920s (spans restart)
        _tick_line(NOW - timedelta(seconds=720), 3),  # real stall gap 960s (no restart)
        _tick_line(NOW - timedelta(seconds=30), 4),
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=70))
    assert m.max_tick_gap_s == 1920
    assert m.max_tick_gap_spans_restart is True
    assert m.max_tick_gap_non_restart_s == 960  # the real, unmasked stall
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "TICK_GAP" in reasons


def test_tick_stale_age_anomaly_isolated() -> None:
    lines = [_tick_line(NOW - timedelta(seconds=1000), 1)]  # only one line, no gap possible
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.max_tick_gap_s is None
    assert m.last_tick_age_s == 1000
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "TICK_STALE" in reasons
    assert "TICK_GAP" not in reasons


def test_tick_missing_is_anomaly() -> None:
    lines = [_batch_line(NOW - timedelta(seconds=10), 5.0)]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.last_tick_n is None
    assert m.last_ignite_age_s is None
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "TICK_MISSING" in reasons


def test_tick_missing_within_boot_grace_is_warn_booting() -> None:
    """A freshly-rotated log holds [ignite] but no [tick] yet during the
    routine ignite->first-tick warmup (measured 16m53s / 3m45s in real logs
    via module replay, 2026-07-12 log_sentry review finding 1) — must
    downgrade to WARN, not ANOMALY every daily restart (and every watchdog
    revival), or the sentry gets muted and recreates the original miss."""
    lines = [_ignite_line(NOW - timedelta(seconds=600))]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.last_tick_n is None
    assert m.last_ignite_age_s == 600
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "BOOTING" in reasons
    assert "TICK_MISSING" not in reasons


def test_tick_missing_beyond_boot_grace_is_still_anomaly() -> None:
    """Grace is bounded — a boot stuck well past the observed warmup class
    must still ANOMALY, not silently downgrade forever."""
    lines = [_ignite_line(NOW - timedelta(seconds=3600))]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.last_tick_n is None
    assert m.last_ignite_age_s == 3600
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "TICK_MISSING" in reasons
    assert "BOOTING" not in reasons


def test_writer_batch_slow_anomaly() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        _batch_line(NOW - timedelta(seconds=20), 45_000.0),
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.batch_max_ms == 45_000.0
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "WRITER_BATCH_SLOW" in reasons


def test_error_other_high_anomaly_locked_counted_separately() -> None:
    lines = [_tick_line(NOW - timedelta(seconds=10), 1)]
    lines += [_error_other_line(NOW - timedelta(seconds=5), i) for i in range(11)]
    lines += [_error_locked_line(NOW - timedelta(seconds=5)) for _ in range(3)]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.errors_other_window == 11
    assert m.errors_db_locked_window == 3
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "ANOMALY"
    assert "ERROR_OTHER_HIGH" in reasons


def test_db_locked_storm_is_warn_not_print_only() -> None:
    """errors_db_locked_window must actually gate the verdict — previously it
    was tracked but never consulted by evaluate_status (2026-07-12 log_sentry
    review; feedback_db_lock_is_architecture_signal: lock contention is an
    early-warning signal, must not silently read STATUS=OK while building)."""
    lines = [_tick_line(NOW - timedelta(seconds=10), 1)]
    lines += _error_locked_lines(NOW - timedelta(seconds=5), 15)
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.errors_db_locked_window == 15
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "DB_LOCK_CONTENTION" in reasons


def test_db_locked_below_threshold_not_flagged() -> None:
    lines = [_tick_line(NOW - timedelta(seconds=10), 1)]
    lines += _error_locked_lines(NOW - timedelta(seconds=5), 5)
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.errors_db_locked_window == 5
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert "DB_LOCK_CONTENTION" not in reasons


def test_restart_detected_is_warn_not_anomaly() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        _ignite_line(NOW - timedelta(seconds=100)),
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.restart_count_window == 1
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "RESTART_DETECTED" in reasons


def test_altdata_stale_channel_flagged() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        _refresh_line(NOW - timedelta(seconds=100), "binance_deriv", 300),  # fresh
        _refresh_line(NOW - timedelta(seconds=700), "crypto_fg", 300),  # stale: >2*300s
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.altdata_stale_channels == "crypto_fg"
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "ALTDATA_STALE" in reasons


def test_missing_log_file_is_warn(tmp_path: Path) -> None:
    lines = log_sentry.read_tail_lines(tmp_path / "nope.log")
    assert lines == []
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.log_found is False
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "LOG_UNREADABLE" in reasons


def test_read_tail_lines_bounds_to_max_bytes(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text(("x" * 50 + "\n") * 100, encoding="utf-8")
    lines = log_sentry.read_tail_lines(path, max_bytes=200)
    assert len(lines) < 100  # truncated, not the whole file
    assert all(line == "x" * 50 for line in lines)  # partial leading line dropped cleanly


# --- db axis -------------------------------------------------------------


def _ok_db() -> log_sentry.DbMetrics:
    return log_sentry.DbMetrics(
        db_reachable=True,
        rail_breach_count=0,
        rail_breach_detail="",
        batch_flush_count=0,
        batch_flush_detail="",
        crypto_active=True,
        crypto_signals_window=1,
        equity_active=False,
        equity_signals_window=0,
        capital_active=False,
        capital_signals_window=0,
        capital_fills_window=0,
        capital_gate_events_window=0,
    )


def test_rail_breach_warn(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = int(NOW.timestamp())
    cutoff_epoch = now_epoch - 1800
    _insert_position(db_path, "p1", "BTC-USDT", -1.5, now_epoch - 60)
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.rail_breach_count == 1
    assert "BTC-USDT:-1.5" in m.rail_breach_detail
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "WARN"
    assert "RAIL_BREACH" in reasons


def test_rail_breach_boundary_not_triggered_above_threshold(make_db: MakeDb) -> None:
    """pnl_r=-1.19 is inside the rail (spec: <= -1.2 breaches), must NOT flag."""
    db_path = make_db()
    now_epoch = int(NOW.timestamp())
    cutoff_epoch = now_epoch - 1800
    _insert_position(db_path, "p1", "BTC-USDT", -1.19, now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.rail_breach_count == 0


def test_batch_flush_warn(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = int(NOW.timestamp())
    cutoff_epoch = now_epoch - 1800
    closed_ts = now_epoch - 60
    for i in range(5):
        _insert_position(db_path, f"p{i}", f"SYM{i}", 0.5, closed_ts)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.batch_flush_count == 1
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "WARN"
    assert "BATCH_FLUSH" in reasons


def test_session_silent_crypto_warn_wide_window(make_db: MakeDb) -> None:
    """A genuinely wide silent window (>= CRYPTO_SILENT_WINDOW_MIN_S) still WARNs
    — the fix (finding 2) only raises the bar past the routine short-window
    quiet gap, it does not remove crypto-silence coverage outright."""
    db_path = make_db()
    now_epoch = WEEKEND_EPOCH  # equity closed -> isolates the crypto-silence check
    cutoff_epoch = now_epoch - CRYPTO_SILENT_WINDOW_MIN_S
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.crypto_signals_window == 0
    assert m.equity_active is False
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "WARN"
    assert "SESSION_SILENT_CRYPTO" in reasons
    assert "SESSION_SILENT_EQUITY" not in reasons


def test_session_silent_crypto_reachable_at_deployed_narrow_window(make_db: MakeDb) -> None:
    """SESSION_SILENT_CRYPTO must fire even when the caller passes the
    deployed monitor_tick.sh §⑩ cadence (--window-min 60 = 3600s), well under
    CRYPTO_SILENT_WINDOW_MIN_S (14400s) — finding 4, 2026-07-12 review:
    crypto_active was previously gated by the caller's window and was
    permanently unreachable at that cadence (confirmed crypto_active=0 in
    real output). The check now owns its own dedicated lookback."""
    db_path = make_db()
    now_epoch = WEEKEND_EPOCH
    cutoff_epoch = now_epoch - 3600  # deployed cadence, << CRYPTO_SILENT_WINDOW_MIN_S
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.crypto_active is True
    assert m.crypto_signals_window == 0
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "WARN"
    assert "SESSION_SILENT_CRYPTO" in reasons


def test_session_silent_crypto_sees_signal_outside_narrow_caller_window(make_db: MakeDb) -> None:
    """A crypto signal 50min ago falls outside a --window-min 60 caller's own
    cutoff (used for the other checks) but must still be seen by the
    dedicated crypto lookback — proves the decoupling (finding 4) didn't
    just widen the bar, it correctly finds real recent activity too."""
    db_path = make_db()
    now_epoch = WEEKEND_EPOCH
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 3000)  # 50min ago
    cutoff_epoch = now_epoch - 1800  # 30min — signal falls outside this narrow cutoff
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.crypto_signals_window == 1
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SESSION_SILENT_CRYPTO" not in reasons


def test_session_silent_equity_only_during_rth(make_db: MakeDb) -> None:
    db_path = make_db()
    cutoff_epoch = RTH_EPOCH - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", RTH_EPOCH - 60)  # crypto not silent
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, RTH_EPOCH, cutoff_epoch)
    conn.close()
    assert m.equity_active is True
    assert m.equity_signals_window == 0
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SESSION_SILENT_EQUITY" in reasons


def test_session_equity_silence_not_flagged_off_hours_weekday(make_db: MakeDb) -> None:
    """Off-RTH weekday with zero equity signals must NOT warn (session-aware,
    same class of bug as the pre-existing weekend-STALL fix in monitor_tick.sh
    §⑦ — a naive always-on check would misreport routine market-closed quiet)."""
    db_path = make_db()
    cutoff_epoch = CLOSED_EPOCH - 1800
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, CLOSED_EPOCH, cutoff_epoch)
    conn.close()
    assert m.equity_active is False
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SESSION_SILENT_EQUITY" not in reasons


def test_session_equity_silence_not_flagged_on_holiday(make_db: MakeDb) -> None:
    """Christmas 2026 (Fri, weekday) during would-be RTH hours must NOT flag
    SESSION_SILENT_EQUITY — us_equity_session_state is weekend-aware but NOT
    holiday-aware by design; scan_db_axis layers a static NYSE holiday
    calendar on top (finding 1, 2026-07-12 log_sentry review — same bug class
    as the 2026-06-28 weekend-STALL)."""
    db_path = make_db()
    now_epoch = CHRISTMAS_RTH_EPOCH
    cutoff_epoch = now_epoch - 1800
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.equity_active is False  # holiday overrides the clock-of-day "rth" read
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SESSION_SILENT_EQUITY" not in reasons


def _ok_log() -> log_sentry.LogMetrics:
    return log_sentry.LogMetrics(
        log_found=True,
        last_tick_n=1,
        last_tick_utc="2026-07-12T04:00:00Z",
        last_tick_age_s=10,
        max_tick_gap_s=60,
        max_tick_gap_spans_restart=False,
        max_tick_gap_non_restart_s=None,
        batch_count_window=0,
        batch_max_ms=None,
        batch_p95_ms=None,
        errors_db_locked_window=0,
        errors_other_window=0,
        restart_count_window=0,
        last_ignite_age_s=None,
        altdata_last_refreshed_utc=None,
        altdata_last_refreshed_age_s=None,
        altdata_stale_channels="",
    )


def test_db_unreachable_is_warn(tmp_path: Path) -> None:
    assert log_sentry.open_db_readonly(tmp_path / "nope.sqlite") is None


# --- end-to-end / determinism ----------------------------------------------


def test_run_sentry_is_deterministic(tmp_path: Path, make_db: MakeDb) -> None:
    log_path = tmp_path / "run.log"
    log_path.write_text(_tick_line(NOW - timedelta(seconds=10), 1) + "\n", encoding="utf-8")
    db_path = make_db(tmp_path / "run.sqlite")
    out1 = log_sentry.run_sentry(log_path, db_path, 30, NOW)
    out2 = log_sentry.run_sentry(log_path, db_path, 30, NOW)
    assert out1 == out2
    assert out1.splitlines()[-2].startswith("SENTRY_STATUS=")
    assert out1.splitlines()[-1].startswith("SENTRY_REASONS=")


def test_run_sentry_db_missing_reports_unreachable(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    log_path.write_text(_tick_line(NOW - timedelta(seconds=10), 1) + "\n", encoding="utf-8")
    out = log_sentry.run_sentry(log_path, tmp_path / "nope.sqlite", 30, NOW)
    assert "db_reachable=0" in out
    assert "DB_UNREACHABLE" in out


def test_main_smoke(tmp_path: Path, make_db: MakeDb, capsys: pytest.CaptureFixture[str]) -> None:
    log_path = tmp_path / "run.log"
    log_path.write_text(_tick_line(datetime.now(UTC), 1) + "\n", encoding="utf-8")
    db_path = make_db(tmp_path / "run.sqlite")
    rc = log_sentry.main(["--window-min", "30", "--log", str(log_path), "--db", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SENTRY_STATUS=" in out
    assert "SENTRY_REASONS=" in out


def test_rail_breach_clustered_is_anomaly(make_db: MakeDb) -> None:
    """3+ rail breaches = systemic -> ANOMALY (2026-07-12 review MED-2:
    a single large loss is normal aggressive variance -> WARN only)."""
    db_path = make_db()
    now_epoch = int(NOW.timestamp())
    cutoff_epoch = now_epoch - 1800
    for i, r in enumerate((-1.3, -1.5, -2.0)):
        _insert_position(db_path, f"p{i}", f"S{i}-USDT", r, now_epoch - 60)
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.rail_breach_count == 3
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "ANOMALY"
    assert "RAIL_BREACH" in reasons


def test_writer_queue_full_is_anomaly(make_db: MakeDb, tmp_path: Path) -> None:
    """Terminal-phase writer wedge: queue-full degrades with NO completed
    batches must still fire the writer axis (2026-07-12 review MED-1)."""
    log = tmp_path / "rt.log"
    base = NOW.strftime("%Y-%m-%dT%H:%M")
    lines = [
        f"{base}:01.000Z [INFO] polaris.scripts._production_tick:1423 [tick 5] focus=52",
        f"{base}:05.000Z [WARNING] polaris.storage.db_writer:210 DBWriter queue full (max=4096) — job dropped",
        f"{base}:06.000Z [WARNING] polaris.storage.db_writer:210 DBWriter queue full (max=4096) — job dropped",
    ]
    log.write_text("\n".join(lines) + "\n")
    from datetime import timedelta as _td
    m = log_sentry.scan_log_axis(lines, now=NOW, cutoff=NOW - _td(minutes=60))
    assert m.writer_queue_full_window == 2
    db_path = make_db()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dbm = log_sentry.scan_db_axis(conn, int(NOW.timestamp()), int(NOW.timestamp()) - 1800)
    conn.close()
    status, reasons = log_sentry.evaluate_status(m, dbm)
    assert "WRITER_QUEUE_FULL" in reasons
    assert status == "ANOMALY"


# --- db_writer qdepth (WRITER_QUEUE_PRESSURE) -------------------------------


def _batch_line_qdepth(dt: datetime, ms: float, qdepth: int, qcap: int) -> str:
    return (
        f"{_ts(dt)} [DEBUG] polaris.storage.db_writer:304 "
        f"[db_writer] batch 1 jobs {ms}ms qdepth={qdepth}/{qcap}"
    )


def test_writer_qdepth_parsed_and_pressure_warn_at_50pct() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        _batch_line_qdepth(NOW - timedelta(seconds=5), 5.0, 2048, 4096),  # exactly 50%
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.writer_qdepth_pct_max == 0.5
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert status == "WARN"
    assert "WRITER_QUEUE_PRESSURE" in reasons


def test_writer_qdepth_below_threshold_not_flagged() -> None:
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        _batch_line_qdepth(NOW - timedelta(seconds=5), 5.0, 2047, 4096),  # just under 50%
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.writer_qdepth_pct_max is not None
    assert m.writer_qdepth_pct_max < 0.5
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert "WRITER_QUEUE_PRESSURE" not in reasons


def test_writer_qdepth_absent_on_legacy_line_batch_ms_still_parses() -> None:
    """Pre-qdepth log lines (no trailing qdepth=D/CAP) must still parse
    batch_max_ms — the qdepth group is optional (backward compat)."""
    lines = [_batch_line(NOW - timedelta(seconds=5), 12.3)]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    assert m.batch_max_ms == 12.3
    assert m.writer_qdepth_pct_max is None


def test_writer_queue_full_takes_precedence_over_pressure() -> None:
    """A terminal FULL degrade this window must not also emit the earlier-
    stage PRESSURE warning for the same underlying saturation."""
    lines = [
        _tick_line(NOW - timedelta(seconds=10), 1),
        f"{_ts(NOW - timedelta(seconds=8))} [WARNING] polaris.storage.db_writer:210 "
        "DBWriter queue full (max=4096) — job dropped",
        _batch_line_qdepth(NOW - timedelta(seconds=5), 5.0, 4096, 4096),
    ]
    m = log_sentry.scan_log_axis(lines, NOW, NOW - timedelta(minutes=30))
    status, reasons = log_sentry.evaluate_status(m, _ok_db())
    assert "WRITER_QUEUE_FULL" in reasons
    assert "WRITER_QUEUE_PRESSURE" not in reasons
    assert status == "ANOMALY"


# --- capital session (capital_session_active) -------------------------------

CAPITAL_MON_EPOCH = 1783944000  # Mon 2026-07-13 12:00 UTC
CAPITAL_SAT_EPOCH = 1783771200  # Sat 2026-07-11 12:00 UTC
CAPITAL_FRI_BEFORE_CLOSE_EPOCH = 1783717140  # Fri 2026-07-10 20:59 UTC
CAPITAL_FRI_AT_CLOSE_EPOCH = 1783717200  # Fri 2026-07-10 21:00 UTC
CAPITAL_SUN_BEFORE_REOPEN_EPOCH = 1783893540  # Sun 2026-07-12 21:59 UTC
CAPITAL_SUN_AT_REOPEN_EPOCH = 1783893600  # Sun 2026-07-12 22:00 UTC (grace starts)
CAPITAL_SUN_IN_GRACE_EPOCH = 1783898940  # Sun 2026-07-12 23:29 UTC (89min post-reopen)
CAPITAL_SUN_GRACE_ELAPSED_EPOCH = 1783899000  # Sun 2026-07-12 23:30 UTC (90min post-reopen)


def test_capital_session_active_weekday() -> None:
    assert capital_session_active(CAPITAL_MON_EPOCH) is True


def test_capital_session_inactive_saturday() -> None:
    assert capital_session_active(CAPITAL_SAT_EPOCH) is False


def test_capital_session_active_friday_before_close() -> None:
    assert capital_session_active(CAPITAL_FRI_BEFORE_CLOSE_EPOCH) is True


def test_capital_session_inactive_friday_at_close() -> None:
    assert capital_session_active(CAPITAL_FRI_AT_CLOSE_EPOCH) is False


def test_capital_session_inactive_sunday_before_reopen() -> None:
    assert capital_session_active(CAPITAL_SUN_BEFORE_REOPEN_EPOCH) is False


def test_capital_session_inactive_during_reopen_grace() -> None:
    """The reopen instant and the 89min mark must both stay inactive — the
    false-positive-avoidance grace window (task spec: 90min post-reopen)."""
    assert capital_session_active(CAPITAL_SUN_AT_REOPEN_EPOCH) is False
    assert capital_session_active(CAPITAL_SUN_IN_GRACE_EPOCH) is False


def test_capital_session_active_after_grace_elapses() -> None:
    assert capital_session_active(CAPITAL_SUN_GRACE_ELAPSED_EPOCH) is True


# --- SILENT_CAPITAL (scan_db_axis + evaluate_status) ------------------------


def test_silent_capital_anomaly_when_active_and_crypto_flowing(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = CAPITAL_MON_EPOCH
    cutoff_epoch = now_epoch - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)  # crypto flowing
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.capital_active is True
    assert m.capital_signals_window == 0
    assert m.capital_fills_window == 0
    assert m.capital_gate_events_window == 0
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert status == "ANOMALY"
    assert "SILENT_CAPITAL" in reasons


def test_silent_capital_not_flagged_when_session_inactive(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = CAPITAL_SAT_EPOCH  # weekend -> capital session inactive
    cutoff_epoch = now_epoch - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.capital_active is False
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SILENT_CAPITAL" not in reasons


def test_silent_capital_not_flagged_when_capital_signal_present(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = CAPITAL_MON_EPOCH
    cutoff_epoch = now_epoch - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    _insert_signal(db_path, "s2", "capital:EURUSD", now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.capital_signals_window == 1
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SILENT_CAPITAL" not in reasons


def test_silent_capital_not_flagged_when_capital_fill_present(make_db: MakeDb) -> None:
    db_path = make_db()
    now_epoch = CAPITAL_MON_EPOCH
    cutoff_epoch = now_epoch - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    _insert_fill(db_path, "f1", "capital:EURUSD", (now_epoch - 60) * 1000)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.capital_fills_window == 1
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SILENT_CAPITAL" not in reasons


def test_silent_capital_not_flagged_when_capital_gate_event_present(make_db: MakeDb) -> None:
    """A capital gate_event counts even when its originating signal itself
    falls outside the cutoff window (join is on signal_id, not signals.ts)."""
    db_path = make_db()
    now_epoch = CAPITAL_MON_EPOCH
    cutoff_epoch = now_epoch - 1800
    _insert_signal(db_path, "s1", "okx:BTC-USDT", now_epoch - 60)
    _insert_signal(db_path, "s2", "capital:EURUSD", now_epoch - 3600)
    _insert_gate_event(db_path, "g1", "s2", now_epoch - 60)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.capital_signals_window == 0
    assert m.capital_gate_events_window == 1
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SILENT_CAPITAL" not in reasons


def test_silent_capital_not_flagged_when_crypto_also_silent(make_db: MakeDb) -> None:
    """Crypto also silent = global-outage signature, not capital-specific —
    SILENT_CAPITAL must not fire on top of SESSION_SILENT_CRYPTO."""
    db_path = make_db()
    now_epoch = CAPITAL_MON_EPOCH
    cutoff_epoch = now_epoch - CRYPTO_SILENT_WINDOW_MIN_S
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    m = log_sentry.scan_db_axis(conn, now_epoch, cutoff_epoch)
    conn.close()
    assert m.crypto_signals_window == 0
    status, reasons = log_sentry.evaluate_status(_ok_log(), m)
    assert "SILENT_CAPITAL" not in reasons
