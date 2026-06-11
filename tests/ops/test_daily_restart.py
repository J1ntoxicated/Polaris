"""daily_restart: sentinel respect, stop-timeout abort, rotation, wal line, no-DB."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.ops import botctl, daily_restart
from tools.ops.ops_config import OpsConfig

NOW = datetime(2026, 6, 11, 21, 30, tzinfo=UTC).timestamp()


@pytest.fixture
def stop_start(monkeypatch: pytest.MonkeyPatch, cfg: OpsConfig) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"stop": [], "start": []}

    def fake_stop(c: OpsConfig, **kw: object) -> bool:
        calls["stop"].append("stop")
        return True

    def fake_start(c: OpsConfig, **kw: object) -> bool:
        calls["start"].append("start")
        botctl.write_pidfile(c, 4242)
        c.bot_log.write_text("fresh boot\n", encoding="utf-8")
        return True

    monkeypatch.setattr(botctl, "stop", fake_stop)
    monkeypatch.setattr(botctl, "start", fake_start)
    return calls


def test_sentinel_skips_everything(
    cfg: OpsConfig, stop_start: dict[str, list[str]]
) -> None:
    cfg.sentinel.write_text("2026-06-11T00:00:00Z manual stop\n", encoding="utf-8")
    assert daily_restart.run(cfg, now=NOW) == "sentinel_skip"
    assert stop_start == {"stop": [], "start": []}


def test_stop_timeout_aborts_no_start_no_rotation(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    monkeypatch.setattr(botctl, "stop", lambda c, **kw: False)
    monkeypatch.setattr(
        botctl, "start", lambda c, **kw: pytest.fail("must not start after stop timeout")
    )
    cfg.bot_log.write_text("live\n", encoding="utf-8")
    assert daily_restart.run(cfg, now=NOW) == "stop_timeout"
    assert cfg.bot_log.exists()  # NOT rotated under a live process
    assert any(k == "restart_stop_timeout" for k, _, _ in alerts)
    assert not cfg.lock_dir.exists()  # lock released


def test_rotation_after_stop_and_prune_to_14(
    cfg: OpsConfig, stop_start: dict[str, list[str]]
) -> None:
    cfg.bot_log.write_text("old day\n", encoding="utf-8")
    for i in range(16):
        rotated = cfg.bot_log.with_name(f"{cfg.bot_log.name}.202605{i + 10:02d}")
        rotated.write_text("x\n", encoding="utf-8")
    assert daily_restart.run(cfg, now=NOW) == "ok"
    assert stop_start == {"stop": ["stop"], "start": ["start"]}
    rotated_files = sorted(cfg.bot_log.parent.glob(cfg.bot_log.name + ".*"))
    assert len(rotated_files) == 14
    # today's rotation present, oldest pruned
    assert cfg.bot_log.with_name(cfg.bot_log.name + ".20260611") in rotated_files
    assert cfg.bot_log.with_name(cfg.bot_log.name + ".20260510") not in rotated_files


def test_wal_line_format(cfg: OpsConfig, stop_start: dict[str, list[str]]) -> None:
    cfg.wal_path.write_bytes(b"x" * (3 * 1024 * 1024))
    assert daily_restart.run(cfg, now=NOW) == "ok"
    line = cfg.restart_log.read_text(encoding="utf-8").strip()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z wal_before=\d+\.\dMB"
        r" wal_after=\d+\.\dMB pid=4242",
        line,
    ), line
    assert "wal_before=3.0MB" in line


def test_lock_held_skips_with_alert(
    cfg: OpsConfig,
    stop_start: dict[str, list[str]],
    alerts: list[tuple[str, str, float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "owner.pid").write_text("1", encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    assert daily_restart.run(cfg, now=NOW) == "lock_busy"
    assert stop_start == {"stop": [], "start": []}
    assert any(k == "restart_lock_busy" for k, _, _ in alerts)


def test_never_opens_the_database(
    cfg: OpsConfig, stop_start: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: pytest.fail("daily_restart opened the DB")
    )
    cfg.wal_path.write_bytes(b"x" * 1024)
    assert daily_restart.run(cfg, now=NOW) == "ok"
    # and the module source never references sqlite at all
    src = Path(daily_restart.__file__).read_text(encoding="utf-8")
    assert "sqlite" not in src
