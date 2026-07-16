"""G6 probe TIGHTEN/HARVEST consumer default-ON ops wiring — DEMO/PAPER only.

P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md): defaults
``POLARIS_G6_PROBE_TIGHTEN`` ON via ``_spawn_env()``, following the exact same
override pattern as ``POLARIS_VIRTUAL_ACCOUNT`` / the OKX liquidity-floor step-down
(``tests/ops/test_virtual_account_wiring.py`` / ``test_liqfloor_stepdown_wiring.py``).
The consumer itself only escalates an adverse HOLD to a tighter exit trail (G7); it
never touches the -1.0R rail, sizing, or entry — see
``tests/test_g6_tighten_consumer.py`` / ``tests/ops/test_probe_tighten_rail_safety.py``
for the decision-level guarantees.
"""

from __future__ import annotations

import pytest

from tools.ops import botctl

TIGHTEN_ENV = "POLARIS_G6_PROBE_TIGHTEN"


def test_spawn_env_defaults_probe_tighten_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TIGHTEN_ENV, raising=False)
    assert botctl._spawn_env()[TIGHTEN_ENV] == "1"


def test_spawn_env_honours_explicit_probe_tighten_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TIGHTEN_ENV, "0")
    assert botctl._spawn_env()[TIGHTEN_ENV] == "0"
