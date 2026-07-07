"""VIRTUAL ACCOUNT ops wiring — DEMO/PAPER only.

Jin 2026-07-07 mandate: start_bot.sh must launch fully virtual by default
(POLARIS_VIRTUAL_ACCOUNT=1) with --real-roundtrip dropped from the SSOT
start command (real_roundtrip is now controlled purely by the env switch,
resolved inside ignite()/run_production_paper_loop()).
"""

from __future__ import annotations

import pytest

from tools.ops import botctl
from tools.ops.ops_config import OpsConfig


def test_start_cmd_drops_real_roundtrip_flag(cfg: OpsConfig) -> None:
    assert "--real-roundtrip" not in cfg.start_cmd


def test_start_cmd_still_has_full_pipeline_and_paper(cfg: OpsConfig) -> None:
    assert "--full-pipeline" in cfg.start_cmd
    assert "--paper" in cfg.start_cmd


def test_spawn_env_defaults_virtual_account_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_VIRTUAL_ACCOUNT", raising=False)
    assert botctl._spawn_env()["POLARIS_VIRTUAL_ACCOUNT"] == "1"


def test_spawn_env_honours_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "0")
    assert botctl._spawn_env()["POLARIS_VIRTUAL_ACCOUNT"] == "0"
