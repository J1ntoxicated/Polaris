"""Seal test: no broad/forceful kill pattern may EVER enter ops automation.

Absolute rule (past incident: tty cleanup killed the Claude session): the
only allowed signal is SIGTERM to the exact pidfile-verified PID. This test
makes the rule regression-proof across every file this wave owns.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = re.compile(
    r"pkill|killall|kill\s+-9|kill\s+-KILL|SIGKILL|killpg|os\.killpg"
)

OWNED_FILES = (
    list((PROJECT_ROOT / "tools" / "ops").glob("*.py"))
    + [
        PROJECT_ROOT / "scripts" / "start_bot.sh",
        PROJECT_ROOT / "scripts" / "stop_bot.sh",
        PROJECT_ROOT / "scripts" / "install_ops_automation.sh",
        PROJECT_ROOT / "scripts" / "uninstall_ops_automation.sh",
    ]
    + list((PROJECT_ROOT / "scripts" / "launchd").glob("*.plist"))
)


def test_owned_files_exist() -> None:
    missing = [str(p) for p in OWNED_FILES if not p.exists()]
    assert not missing, f"expected ops files missing: {missing}"
    assert len([p for p in OWNED_FILES if p.suffix == ".py"]) >= 7


def test_no_kill_patterns_in_owned_sources() -> None:
    offenders: list[str] = []
    for path in OWNED_FILES:
        text = path.read_text(encoding="utf-8")
        for match in FORBIDDEN.finditer(text):
            offenders.append(f"{path.name}: {match.group(0)!r}")
    assert not offenders, f"forbidden kill patterns found: {offenders}"
