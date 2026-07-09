"""OKX liquidity-floor step-down ops wiring — DEMO/PAPER only.

Jin 2026-07-09 mandate (very-active/focus-expand): widen the OKX focus
universe by stepping the volume floor DOWN one notch via ``_spawn_env()``,
following the exact same override pattern as ``POLARIS_VIRTUAL_ACCOUNT``
(``tests/ops/test_virtual_account_wiring.py``). ``POLARIS_WATCH_MAX`` is a
hard resource-guard ceiling and MUST stay untouched by this override —
only the quality floor moves, never the cap.
"""

from __future__ import annotations

import pytest

from tools.ops import botctl

LIQFLOOR_ENV = "POLARIS_LIQFLOOR_OKX_MIN_VOL_24H_USD"
STEPPED_DOWN_FLOOR = "15000000"  # step 1: $20M schema default -> $15M


def test_spawn_env_steps_down_okx_liqfloor_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIQFLOOR_ENV, raising=False)
    assert botctl._spawn_env()[LIQFLOOR_ENV] == STEPPED_DOWN_FLOOR


def test_spawn_env_honours_explicit_liqfloor_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIQFLOOR_ENV, "5000000")
    assert botctl._spawn_env()[LIQFLOOR_ENV] == "5000000"


def test_spawn_env_never_sets_watch_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """WATCH_MAX is a hard ceiling guard — this step-down must not touch it."""
    monkeypatch.delenv(LIQFLOOR_ENV, raising=False)
    monkeypatch.delenv("POLARIS_WATCH_MAX", raising=False)
    assert "POLARIS_WATCH_MAX" not in botctl._spawn_env()
