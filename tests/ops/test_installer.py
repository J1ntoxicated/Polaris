"""Installer/uninstaller + launchd plist contract (no KeepAlive, schedules, cwd)."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHD = PROJECT_ROOT / "scripts" / "launchd"
PROJECT_DIR = "/Users/jinyoon/Projects/Polaris"

PLISTS = {
    "watchdog": LAUNCHD / "com.polaris.watchdog.plist",
    "restart": LAUNCHD / "com.polaris.daily.restart.plist",
    "digest": LAUNCHD / "com.polaris.daily.digest.plist",
}
SCRIPTS = [
    PROJECT_ROOT / "scripts" / "install_ops_automation.sh",
    PROJECT_ROOT / "scripts" / "uninstall_ops_automation.sh",
    PROJECT_ROOT / "scripts" / "start_bot.sh",
    PROJECT_ROOT / "scripts" / "stop_bot.sh",
]


def _load(name: str) -> dict[str, Any]:
    with open(PLISTS[name], "rb") as fh:
        data: dict[str, Any] = plistlib.load(fh)
    return data


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shell_syntax_ok(script: Path) -> None:
    assert script.exists(), script
    proc = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("name", sorted(PLISTS), ids=lambda n: str(n))
def test_plutil_lint(name: str) -> None:
    if shutil.which("plutil") is None:
        pytest.skip("plutil unavailable (non-macOS)")
    proc = subprocess.run(
        ["plutil", "-lint", str(PLISTS[name])], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("name", sorted(PLISTS), ids=lambda n: str(n))
def test_common_plist_contract(name: str) -> None:
    data = _load(name)
    assert "KeepAlive" not in data  # polling model only — launchd never owns the bot
    assert data["WorkingDirectory"] == PROJECT_DIR  # .env / relative-path safety
    args = data["ProgramArguments"]
    assert args[0] == f"{PROJECT_DIR}/.venv/bin/python"
    assert args[1] == "-m" and args[2].startswith("tools.ops.")
    assert data["StandardOutPath"].startswith(f"{PROJECT_DIR}/data/paper/ops/")
    assert data["StandardErrorPath"].startswith(f"{PROJECT_DIR}/data/paper/ops/")


def test_watchdog_schedule() -> None:
    data = _load("watchdog")
    assert data["StartInterval"] == 300
    assert data["RunAtLoad"] is True
    assert "StartCalendarInterval" not in data


def test_restart_schedule_0730_local() -> None:
    data = _load("restart")
    assert data["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}


def test_digest_schedule_1010_local() -> None:
    data = _load("digest")
    assert data["StartCalendarInterval"] == {"Hour": 10, "Minute": 10}


def test_installer_never_touches_dashboard() -> None:
    for script in SCRIPTS:
        assert "com.polaris.dashboard" not in script.read_text(encoding="utf-8")


def test_installer_handles_both_ghosts_with_backup() -> None:
    text = (PROJECT_ROOT / "scripts" / "install_ops_automation.sh").read_text(
        encoding="utf-8"
    )
    assert "com.polaris.paper.realtime" in text
    assert "com.polaris.paper.daily" in text
    assert "_polaris_ghost_backup_" in text
    assert "bootout" in text and "bootstrap" in text


def test_wrappers_route_through_botctl_manual() -> None:
    start = (PROJECT_ROOT / "scripts" / "start_bot.sh").read_text(encoding="utf-8")
    stop = (PROJECT_ROOT / "scripts" / "stop_bot.sh").read_text(encoding="utf-8")
    assert "tools.ops.botctl start --manual" in start
    assert "tools.ops.botctl stop --manual" in stop
