"""botctl.stop safety: exact-PID SIGTERM only, sentinel ordering, no escalation."""

from __future__ import annotations

import pytest

from tests.ops.conftest import BOT_CMD
from tools.ops import botctl
from tools.ops.ops_config import OpsConfig


def test_command_mismatch_sends_no_signal(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    sigterms: list[int],
    alerts: list[tuple[str, str, float]],
) -> None:
    """PID-reuse protection: alive PID whose command is not the bot → 0 signals."""
    cfg.pidfile.write_text("9999\n", encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    monkeypatch.setattr(
        botctl, "ps_command", lambda pid: "ruff check polaris/scripts/ignite_p1.py"
    )
    assert botctl.stop(cfg) is True  # nothing to stop
    assert sigterms == []
    assert not cfg.pidfile.exists()  # stale pidfile cleaned (file only)


def test_match_sends_exactly_one_sigterm_to_exact_pid(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch, sigterms: list[int]
) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")
    killed: set[int] = set()
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: pid not in killed)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: BOT_CMD)
    monkeypatch.setattr(
        botctl, "_send_sigterm", lambda pid: (sigterms.append(pid), killed.add(pid))[0]
    )
    assert botctl.stop(cfg) is True
    assert sigterms == [9999]
    assert not cfg.pidfile.exists()


def test_timeout_never_escalates(
    cfg: OpsConfig,
    monkeypatch: pytest.MonkeyPatch,
    sigterms: list[int],
    alerts: list[tuple[str, str, float]],
) -> None:
    """No exit within 60s → alert + give up. SIGKILL does not exist in this codebase."""
    cfg.pidfile.write_text("9999", encoding="utf-8")
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: True)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: BOT_CMD)
    assert botctl.stop(cfg) is False
    assert sigterms == [9999]  # exactly one TERM, nothing more
    assert any(k == "stop_timeout" for k, _, _ in alerts)
    assert cfg.pidfile.exists()  # process still owns it


def test_manual_creates_sentinel_before_sigterm(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")
    killed: set[int] = set()
    sentinel_at_signal: list[bool] = []

    def fake_sigterm(pid: int) -> None:
        sentinel_at_signal.append(cfg.sentinel.exists())
        killed.add(pid)

    monkeypatch.setattr(botctl, "pid_alive", lambda pid: pid not in killed)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: BOT_CMD)
    monkeypatch.setattr(botctl, "_send_sigterm", fake_sigterm)
    assert botctl.stop(cfg, manual=True) is True
    assert sentinel_at_signal == [True]  # sentinel existed BEFORE the signal
    assert cfg.sentinel.exists()
    assert "T" in cfg.sentinel.read_text(encoding="utf-8")  # ISO ts recorded


def test_manual_with_stale_pidfile_still_creates_sentinel(
    cfg: OpsConfig, sigterms: list[int]
) -> None:
    """Review blocker: intent must be recorded on EVERY failure path."""
    cfg.pidfile.write_text("9999", encoding="utf-8")  # dead (autouse pid_alive=False)
    assert botctl.stop(cfg, manual=True) is True
    assert cfg.sentinel.exists()
    assert sigterms == []


def test_stale_pidfile_falls_back_to_strict_match_orphan(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch, sigterms: list[int]
) -> None:
    cfg.pidfile.write_text("9999", encoding="utf-8")
    killed: set[int] = set()
    monkeypatch.setattr(
        botctl, "pid_alive", lambda pid: pid == 8888 and pid not in killed
    )
    monkeypatch.setattr(botctl, "list_processes", lambda: [(8888, BOT_CMD)])
    monkeypatch.setattr(
        botctl, "_send_sigterm", lambda pid: (sigterms.append(pid), killed.add(pid))[0]
    )
    assert botctl.stop(cfg, manual=True) is True
    assert sigterms == [8888]
    assert cfg.sentinel.exists()
