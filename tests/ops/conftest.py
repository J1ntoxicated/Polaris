"""Fixtures for ops automation tests (DEMO/PAPER bot ops — fully mocked).

Safety: NOTHING in here may touch a real process, the live DB, the real
vault, or macOS notifications. The autouse fixture stubs every side-effect
seam in tools.ops so a forgotten override can never signal a live PID or
pop a banner.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.ops import alerting, botctl
from tools.ops.ops_config import OpsConfig

# Real-world command line observed via `ps` on this machine (macOS venv
# python execs the framework binary — argv[0] is NOT .venv/bin/python).
BOT_CMD = (
    "/opt/homebrew/Cellar/python@3.13/3.13.5/Frameworks/Python.framework/"
    "Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
    " -m polaris.scripts.ignite_p1 --paper --duration 172800 --tick 5"
    " --full-pipeline --real-roundtrip --db /tmp/x/data/polaris_live.sqlite"
    " -vv --log-file /tmp/x/data/paper/polaris_runtime.log"
)


@pytest.fixture
def cfg(tmp_path: Path) -> OpsConfig:
    proj = tmp_path / "proj"
    (proj / "data" / "paper").mkdir(parents=True)
    (proj / ".env").write_text("OKX_API_KEY=demo\n", encoding="utf-8")
    vault = tmp_path / "ops_vault"
    vault.mkdir()
    return OpsConfig(
        project_dir=proj,
        db_path=proj / "data" / "polaris_live.sqlite",
        bot_log=proj / "data" / "paper" / "polaris_runtime.log",
        pidfile=proj / "data" / "paper" / "production.pid",
        sentinel=proj / "data" / "paper" / "MANUAL_STOP",
        ops_dir=proj / "data" / "paper" / "ops",
        vault_dir=vault,
        python_bin=proj / ".venv" / "bin" / "python",
    )


def _no_spawn(cfg: OpsConfig) -> int:
    raise AssertionError("_spawn must be stubbed explicitly in tests")


@pytest.fixture(autouse=True)
def _ops_isolation(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stub every real-world seam: no banner, no sleep, no ps, no spawn."""
    monkeypatch.setattr(alerting, "_send_banner", lambda text: None)
    monkeypatch.setattr(botctl, "_sleep", lambda sec: None)
    monkeypatch.setattr(botctl, "list_processes", lambda: [])
    monkeypatch.setattr(botctl, "ancestor_pids", lambda: {os.getpid()})
    monkeypatch.setattr(botctl, "pid_alive", lambda pid: False)
    monkeypatch.setattr(botctl, "ps_command", lambda pid: None)
    monkeypatch.setattr(botctl, "_spawn", _no_spawn)
    yield


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, float]]:
    """Record alerting.notify calls as (key, msg, throttle_sec)."""
    calls: list[tuple[str, str, float]] = []

    def fake(
        cfg: OpsConfig,
        key: str,
        msg: str,
        *,
        throttle_sec: float = 1800.0,
        now: float | None = None,
    ) -> bool:
        calls.append((key, msg, throttle_sec))
        return True

    monkeypatch.setattr(alerting, "notify", fake)
    return calls


@pytest.fixture
def sigterms(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record botctl._send_sigterm target PIDs (no real signal ever)."""
    sent: list[int] = []
    monkeypatch.setattr(botctl, "_send_sigterm", sent.append)
    return sent
