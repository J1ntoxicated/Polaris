"""botctl.start: idempotence, dedup, adopt, sentinel, preflight, flap backoff."""

from __future__ import annotations

import os
import time

import pytest

from tests.ops.conftest import BOT_CMD
from tools.ops import botctl
from tools.ops.ops_config import OpsConfig, load_state

NOW = 1_781_200_000.0


def _stub_good_spawn(
    monkeypatch: pytest.MonkeyPatch, cfg: OpsConfig, pid: int = 4242
) -> None:
    def fake_spawn(c: OpsConfig) -> int:
        c.bot_log.write_text("[boot] paper loop up\n", encoding="utf-8")
        return pid

    monkeypatch.setattr(botctl, "_spawn", fake_spawn)
    monkeypatch.setattr(botctl, "pid_alive", lambda p: p == pid)


# --- is_bot_command strict predicate (review blocker: no false adopt/stop chain)

def test_is_bot_command_matches_real_macos_argv() -> None:
    assert botctl.is_bot_command(BOT_CMD) is True
    assert botctl.is_bot_command(
        "/Users/jinyoon/Projects/Polaris/.venv/bin/python -m polaris.scripts.ignite_p1"
        " --paper --duration 86400 --tick 5 --db data/polaris_live.sqlite"
    ) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "ruff check polaris/scripts/ignite_p1.py",
        "/usr/bin/grep -rn polaris.scripts.ignite_p1 .",
        "rg polaris.scripts.ignite_p1 --paper",  # not a python executable
        "/opt/homebrew/bin/python3 -m pytest tests/ops",  # python but wrong module
        "/x/.venv/bin/python -m polaris.scripts.ignite_p1",  # missing --paper
        "vim polaris/scripts/ignite_p1.py",
        "",
    ],
)
def test_is_bot_command_rejects_innocent_processes(cmd: str) -> None:
    assert botctl.is_bot_command(cmd) is False


# --- start paths

def test_start_skips_when_lock_held_by_live_owner(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "owner.pid").write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)  # owner alive
    assert botctl.start(cfg, now=NOW) is False  # _spawn stub would raise if reached
    assert not cfg.pidfile.exists()
    assert cfg.lock_dir.exists()  # not stolen


def test_start_clears_stale_lock_only_when_owner_dead(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "owner.pid").write_text("99999999", encoding="utf-8")
    old = time.time() - 700.0
    os.utime(cfg.lock_dir, (old, old))
    _stub_good_spawn(monkeypatch, cfg)  # owner 99999999 ≠ 4242 → dead
    assert botctl.start(cfg) is True
    assert any(k == "stale_lock_cleared" for k, _, _ in alerts)
    assert cfg.pidfile.read_text(encoding="utf-8").strip() == "4242"


def test_start_keeps_old_lock_when_owner_alive(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "owner.pid").write_text("12345", encoding="utf-8")
    old = time.time() - 700.0
    os.utime(cfg.lock_dir, (old, old))
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    assert botctl.start(cfg) is False
    assert cfg.lock_dir.exists()


def test_start_adopts_orphan_without_spawning_or_signalling(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
    sigterms: list[int],
) -> None:
    monkeypatch.setattr(botctl, "list_processes", lambda: [(7777, BOT_CMD)])
    assert botctl.start(cfg) is True  # autouse _spawn raises if spawn attempted
    assert cfg.pidfile.read_text(encoding="utf-8").strip() == "7777"
    assert sigterms == []
    adopted = [m for k, m, _ in alerts if k == "bot_adopted"]
    assert adopted and "ignite_p1" in adopted[0]  # full argv surfaced


def test_auto_start_aborts_on_sentinel_manual_clears_it(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review blocker: a manual stop can never be silently overridden."""
    cfg.sentinel.write_text("2026-06-11T00:00:00Z manual stop\n", encoding="utf-8")
    assert botctl.start(cfg, manual=False) is False
    assert cfg.sentinel.exists()
    assert not cfg.pidfile.exists()

    _stub_good_spawn(monkeypatch, cfg)
    assert botctl.start(cfg, manual=True) is True
    assert not cfg.sentinel.exists()  # cleared only by the manual path, post-success


def test_write_pidfile_is_atomic_no_tmp_residue(cfg: OpsConfig) -> None:
    botctl.write_pidfile(cfg, 555)
    assert cfg.pidfile.read_text(encoding="utf-8").strip() == "555"
    leftovers = [p.name for p in cfg.pidfile.parent.iterdir() if "tmp" in p.name]
    assert leftovers == []


def test_start_failure_alert_throttles_after_three_consecutive(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
) -> None:
    monkeypatch.setattr(botctl, "_spawn", lambda c: 5050)  # spawns then dies
    for _ in range(3):
        assert botctl.start(cfg) is False
    fails = [(k, t) for k, _, t in alerts if k == "start_failed"]
    assert [t for _, t in fails] == [0.0, 0.0, 1800.0]  # throttle kicks in at 3rd
    assert not cfg.pidfile.exists()


def test_env_preflight_blocks_start(
    cfg: OpsConfig, alerts: list[tuple[str, str, float]]
) -> None:
    """Review blocker: missing .env must abort (cwd-silent half-boot prevention)."""
    (cfg.project_dir / ".env").unlink()
    assert botctl.start(cfg) is False
    assert any(k == "start_env_missing" for k, _, _ in alerts)


def test_duplicate_instance_alert_only_never_kill(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[tuple[str, str, float]],
    sigterms: list[int],
) -> None:
    procs: list[tuple[int, str]] = []

    def fake_spawn(c: OpsConfig) -> int:
        c.bot_log.write_text("up\n", encoding="utf-8")
        procs.extend([(4242, BOT_CMD), (4300, BOT_CMD)])  # raw manual start raced us
        return 4242

    monkeypatch.setattr(botctl, "_spawn", fake_spawn)
    monkeypatch.setattr(botctl, "pid_alive", lambda p: p in (4242, 4300))
    monkeypatch.setattr(botctl, "list_processes", lambda: list(procs))
    assert botctl.start(cfg) is True
    assert any(k == "duplicate_instance" for k, _, _ in alerts)
    assert sigterms == []


def test_flap_detection_backoff(
    cfg: OpsConfig, alerts: list[tuple[str, str, float]]
) -> None:
    """3 short-uptime (<300s) deaths within 1h → bot_flapping + 30min backoff."""
    state_path = cfg.state_path
    t = NOW
    for _ in range(3):
        st = load_state(state_path)
        st["last_start_ts"] = t - 100.0  # died 100s after start
        from tools.ops.ops_config import save_state

        save_state(state_path, st)
        botctl.note_unexpected_death(cfg, now=t)
        t += 60.0
    assert any(k == "bot_flapping" for k, _, _ in alerts)
    assert botctl.in_backoff(cfg, now=t) is True
    assert botctl.in_backoff(cfg, now=t + 1801.0) is False
    # same death is not double-counted on the next watchdog cycle
    st = load_state(state_path)
    assert st.get("last_start_ts") is None


def test_long_uptime_death_is_not_a_flap(cfg: OpsConfig) -> None:
    from tools.ops.ops_config import save_state

    for i in range(5):
        st = load_state(cfg.state_path)
        st["last_start_ts"] = NOW - 90_000.0  # ~25h uptime (duration self-exit)
        save_state(cfg.state_path, st)
        botctl.note_unexpected_death(cfg, now=NOW + i)
    assert botctl.in_backoff(cfg, now=NOW + 10) is False
