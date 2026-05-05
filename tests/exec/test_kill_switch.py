"""Tests for src/exec/kill_switch.py."""
from __future__ import annotations

import pytest

from src.exec.kill_switch import (
    is_kill_switch_active,
    reset_kill_switch,
    set_kill_switch,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Each test uses a unique kill switch path + clean env."""
    monkeypatch.setenv("POLARIS_KILL_SWITCH_PATH", str(tmp_path / "kill"))
    monkeypatch.delenv("POLARIS_KILL_SWITCH", raising=False)
    reset_kill_switch()
    yield
    reset_kill_switch()


class TestKillSwitch:
    def test_default_inactive(self):
        assert is_kill_switch_active() is False

    def test_in_memory_toggle(self):
        set_kill_switch(True)
        assert is_kill_switch_active() is True
        set_kill_switch(False)
        assert is_kill_switch_active() is False

    def test_env_var_activates(self, monkeypatch):
        monkeypatch.setenv("POLARIS_KILL_SWITCH", "1")
        assert is_kill_switch_active() is True

    def test_file_activates(self, monkeypatch, tmp_path):
        path = tmp_path / "kill"
        monkeypatch.setenv("POLARIS_KILL_SWITCH_PATH", str(path))
        path.touch()
        assert is_kill_switch_active() is True

    def test_persist_writes_file(self, monkeypatch, tmp_path):
        path = tmp_path / "kill"
        monkeypatch.setenv("POLARIS_KILL_SWITCH_PATH", str(path))
        set_kill_switch(True, persist=True)
        assert path.exists()
        set_kill_switch(False, persist=True)
        assert not path.exists()

    def test_reset_clears_all(self, monkeypatch, tmp_path):
        path = tmp_path / "kill"
        monkeypatch.setenv("POLARIS_KILL_SWITCH_PATH", str(path))
        set_kill_switch(True, persist=True)
        reset_kill_switch()
        assert is_kill_switch_active() is False
        assert not path.exists()
