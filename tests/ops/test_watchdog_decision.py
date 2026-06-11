"""Watchdog pure decision table (exhaustive 2^4) + run_once wiring."""

from __future__ import annotations

import itertools
import os
import time

import pytest

from tests.ops.conftest import BOT_CMD
from tools.ops import botctl, watchdog
from tools.ops.ops_config import OpsConfig, load_state, save_state

NOW = 1_781_200_000.0


def _expected(sentinel: bool, pid_alive: bool, cmd_match: bool, orphan: bool) -> str:
    if sentinel:
        return "none"
    if pid_alive and cmd_match:
        return "health"
    return "adopt" if orphan else "start"


@pytest.mark.parametrize(
    ("sentinel", "pid_alive", "cmd_match", "orphan"),
    list(itertools.product([False, True], repeat=4)),
)
def test_decide_full_table(
    sentinel: bool, pid_alive: bool, cmd_match: bool, orphan: bool
) -> None:
    got = watchdog.decide(
        sentinel=sentinel, pid_alive=pid_alive, cmd_match=cmd_match, orphan=orphan
    )
    assert got == _expected(sentinel, pid_alive, cmd_match, orphan)


def test_sentinel_never_starts() -> None:
    for pid_alive, cmd_match, orphan in itertools.product([False, True], repeat=3):
        assert (
            watchdog.decide(
                sentinel=True, pid_alive=pid_alive, cmd_match=cmd_match, orphan=orphan
            )
            == "none"
        )


def test_run_once_sentinel_reminder_24h_throttle(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    cfg.sentinel.write_text("2026-06-11T00:00:00Z manual stop\n", encoding="utf-8")
    started: list[bool] = []
    monkeypatch.setattr(botctl, "start", lambda c, **kw: started.append(True) or True)
    assert watchdog.run_once(cfg, now=NOW) == "none"
    assert started == []
    reminders = [(k, t) for k, _, t in alerts if k == "manual_stop_reminder"]
    assert reminders == [("manual_stop_reminder", 86400.0)]


def test_run_once_dead_bot_restarts_and_alerts(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")  # dead (autouse)
    calls: list[dict[str, object]] = []

    def fake_start(c: OpsConfig, **kw: object) -> bool:
        calls.append(kw)
        return True

    monkeypatch.setattr(botctl, "start", fake_start)
    assert watchdog.run_once(cfg, now=NOW) == "start"
    assert len(calls) == 1
    assert calls[0].get("manual") is False
    restarted = [(k, t) for k, _, t in alerts if k == "bot_dead_restarted"]
    assert restarted == [("bot_dead_restarted", 300.0)]


def test_run_once_respects_flap_backoff(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = load_state(cfg.state_path)
    st["backoff_until"] = NOW + 900.0
    save_state(cfg.state_path, st)
    monkeypatch.setattr(
        botctl, "start", lambda c, **kw: pytest.fail("must not start in backoff")
    )
    assert watchdog.run_once(cfg, now=NOW) == "backoff"


def test_run_once_adopts_orphan(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    monkeypatch.setattr(botctl, "list_processes", lambda: [(7777, BOT_CMD)])
    monkeypatch.setattr(
        botctl, "start", lambda c, **kw: pytest.fail("adopt must not spawn")
    )
    assert watchdog.run_once(cfg, now=NOW) == "adopt"
    assert cfg.pidfile.read_text(encoding="utf-8").strip() == "7777"


def test_run_once_healthy_bot_runs_health_checks(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: BOT_CMD)
    cfg.bot_log.write_text("[boot] ok\n", encoding="utf-8")
    assert watchdog.run_once(cfg, now=time.time()) == "health"
    assert alerts == []  # fresh log, no markers → silence


# --- health checks

def _healthy_setup(cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: BOT_CMD)


def test_health_wedge_alert_only_never_kill(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
    sigterms: list[int],
) -> None:
    _healthy_setup(cfg, monkeypatch)
    cfg.bot_log.write_text("old\n", encoding="utf-8")
    stale = time.time() - 900.0
    os.utime(cfg.bot_log, (stale, stale))
    watchdog.run_once(cfg, now=time.time())
    assert any(k == "bot_wedged" for k, _, _ in alerts)
    assert sigterms == []  # observation only — a live process is human territory


def test_health_marker_thresholds(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    _healthy_setup(cfg, monkeypatch)
    lines = (
        ["[tick-engine] STALL detected — iteration gap=12.00s (cadence=5.00s)"] * 3
        + ["sqlite3.OperationalError: database is locked"] * 3
        + ["[okx ws] connection error (attempt 1/8): TimeoutError()"] * 5
        + ["[okx ws] 8 consecutive failures — giving up, REST-only mode"]
    )
    cfg.bot_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    watchdog.run_once(cfg, now=time.time())
    keys = {k for k, _, _ in alerts}
    assert {"stall_burst", "db_lock_burst", "ws_flapping", "ws_gave_up"} <= keys


def test_health_wal_oversize(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    _healthy_setup(cfg, monkeypatch)
    cfg.bot_log.write_text("ok\n", encoding="utf-8")
    cfg.wal_path.write_bytes(b"x" * 2048)
    monkeypatch.setattr(watchdog, "WAL_ALERT_BYTES", 1024)
    watchdog.run_once(cfg, now=time.time())
    assert any(k == "wal_oversize" for k, _, _ in alerts)
