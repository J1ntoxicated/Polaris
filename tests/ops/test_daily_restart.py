"""daily_restart: sentinel respect, stop-timeout abort, rotation, wal line, retention."""

from __future__ import annotations

import re
from datetime import UTC, datetime

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


def test_spawn_log_rotated_and_pruned_like_runtime(
    cfg: OpsConfig, stop_start: dict[str, list[str]]
) -> None:
    """bot_spawn.log is rotated + pruned to LOG_KEEP in the same down window.

    The 1.5 G live leak was bot_spawn.log: ``botctl._spawn`` appends subprocess
    stdout/stderr forever and ``_rotate_logs`` only rotated ``bot_log``. The
    spawn log carries unique pre-logging-init crash tracebacks, so it must be
    DATE-ROTATED (never silently truncated) on the same LOG_KEEP cap as runtime.
    """
    cfg.ops_dir.mkdir(parents=True, exist_ok=True)
    cfg.spawn_log.write_text("live spawn traceback\n", encoding="utf-8")
    for i in range(16):
        old = cfg.spawn_log.with_name(f"{cfg.spawn_log.name}.202605{i + 10:02d}")
        old.write_text("x\n", encoding="utf-8")
    assert daily_restart.run(cfg, now=NOW) == "ok"
    rotated = sorted(cfg.spawn_log.parent.glob(cfg.spawn_log.name + ".*"))
    assert len(rotated) == 14  # pruned to LOG_KEEP
    # today's rotation present (the 1.5 G live file got rolled, not deleted)
    assert cfg.spawn_log.with_name(cfg.spawn_log.name + ".20260611") in rotated
    # oldest pruned
    assert cfg.spawn_log.with_name(cfg.spawn_log.name + ".20260510") not in rotated
    # the live spawn_log was rolled away (its content preserved in the dated file)
    assert not cfg.spawn_log.exists()


def test_spawn_log_rotation_absent_file_is_noop(
    cfg: OpsConfig, stop_start: dict[str, list[str]]
) -> None:
    """A missing bot_spawn.log must not break the restart (first-run / fresh box)."""
    cfg.bot_log.write_text("live\n", encoding="utf-8")
    # no spawn_log written
    assert daily_restart.run(cfg, now=NOW) == "ok"
    assert not cfg.spawn_log.exists()


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


def test_never_opens_db_on_stop_timeout_abort(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch, alerts: list[tuple[str, str, float]]
) -> None:
    """The DB is NEVER opened unless the bot is confirmed down.

    The original force-kill-safety invariant: on the stop-timeout abort path
    (bot may still be live) retention must not run and the DB must not be
    touched. Retention only ever opens the DB inside the confirmed-down window
    (after ``botctl.stop`` returns True).
    """
    from polaris.storage import retention

    monkeypatch.setattr(botctl, "stop", lambda c, **kw: False)  # stop times out
    monkeypatch.setattr(
        retention,
        "run_retention_job",
        lambda *a, **kw: pytest.fail("retention ran on the abort path"),
    )
    cfg.bot_log.write_text("live\n", encoding="utf-8")
    assert daily_restart.run(cfg, now=NOW) == "stop_timeout"


def test_retention_runs_in_confirmed_down_window(
    cfg: OpsConfig, stop_start: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention runs exactly once, in the down window, before start (no
    marketdata sibling in this fixture's tmp dir -> single trading-only pass)."""
    from polaris.storage import retention

    order: list[str] = []
    monkeypatch.setattr(
        botctl, "stop", lambda c, **kw: (order.append("stop"), True)[1]
    )
    monkeypatch.setattr(
        botctl, "start", lambda c, **kw: (order.append("start"),
                                          botctl.write_pidfile(c, 4242), True)[2]
    )
    monkeypatch.setattr(
        retention, "run_retention_job",
        lambda *a, **kw: (order.append("retention"), {"bars": 0})[1],
    )
    cfg.wal_path.write_bytes(b"x" * 1024)
    assert daily_restart.run(cfg, now=NOW) == "ok"
    assert order == ["stop", "retention", "start"]


def test_retention_runs_against_both_trading_and_marketdata_dbs(
    cfg: OpsConfig, stop_start: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """storage-split (round 4 fix): the marketdata sibling accumulates the
    actual firehose rows post-split and must get its OWN retention + WAL-
    checkpoint pass — not just the (post-split, largely empty) trading
    db_path scoped to a combined spec."""
    from polaris.storage import retention
    from polaris.storage.schema_marketdata import marketdata_db_path_for

    md_path = marketdata_db_path_for(cfg.db_path)
    md_path.write_bytes(b"")  # marketdata sibling exists (bot booted post-split)

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        retention, "run_retention_job",
        lambda db_path, **kw: (calls.append((str(db_path), kw.get("spec"))), {})[1],
    )
    assert daily_restart.run(cfg, now=NOW) == "ok"
    assert [c[0] for c in calls] == [str(cfg.db_path), str(md_path)]
    assert calls[0][1] is retention.RETENTION_SPEC_TRADING
    assert calls[1][1] is retention.RETENTION_SPEC_MARKETDATA


def test_retention_skips_marketdata_pass_when_sibling_absent(
    cfg: OpsConfig, stop_start: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No marketdata sibling booted yet -> a single trading-only pass, never
    a crash on the missing file."""
    from polaris.storage import retention

    calls: list[str] = []
    monkeypatch.setattr(
        retention, "run_retention_job",
        lambda db_path, **kw: (calls.append(str(db_path)), {})[1],
    )
    assert daily_restart.run(cfg, now=NOW) == "ok"
    assert calls == [str(cfg.db_path)]
