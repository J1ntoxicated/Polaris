"""VIRTUAL-mode timeframe downgrade — import-time value pins (Jin 2026-07-09).

DEMO/PAPER only — virtual funds, aggressive/flow_not_block: this is a bar-
cadence LOOSENING (~4x more bar closes to evaluate on), never a throttle.
Applies the existing ``polaris.strategies._virtual_loosen.virtual_loosen``
precedent (already used for entry thresholds / dispatch_eligible on these same
8 files) to ``StrategyMetadata.timeframe`` — VIRTUAL evaluates strategies on a
faster bar, REAL stays byte-identical (env unset -> the original TF wins).

``rsi_bb_pullback`` (OKX) and any 5m timeframe are explicitly OUT of scope
(5m bar supply is 0 on the relevant feeds -> silent INERT) and are untouched
here.

Each module's constants are read ONCE at import time, so both branches of the
env-gated ``virtual_loosen`` call are only observable via ``importlib.reload``
after setting/clearing the env var — the same mechanism
``test_virtual_loosen_okx_donchian55.py`` already established.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from types import ModuleType

import pytest

_ENV = "POLARIS_VIRTUAL_ACCOUNT"

# module dotted path -> (virtual timeframe, real timeframe, class name)
_TARGETS: dict[str, tuple[str, str, str]] = {
    "polaris.strategies.equity_rsi_bb_pullback": ("1H", "1D", "EquityRSIBBPullbackStrategy"),
    # connors_rsi2 REVERTED to flat 1D (2026-07-10, Jin): 1H virtual bled
    # -$588/day vs validated 1D design — removed from the downgrade map.
    "polaris.strategies.supertrend": ("15m", "1H", "SupertrendStrategy"),
    "polaris.strategies.ema_crossover": ("15m", "1H", "EMACrossoverStrategy"),
    "polaris.strategies.macd_ema_trend_pullback": ("1H", "1D", "MACDEMATrendPullbackStrategy"),
    "polaris.strategies.cci_reversion": ("15m", "1H", "CCIReversionStrategy"),
    "polaris.strategies.fx_range_fade": ("15m", "1H", "FXRangeFadeStrategy"),
    "polaris.strategies.fx_breakout_basket": ("15m", "1H", "FXBreakoutBasketStrategy"),
}


@pytest.fixture(autouse=True)
def _restore_env_and_modules() -> Iterator[None]:
    """Isolate the env var + leave every reloaded module back on REAL (no leak
    into other test files that import these modules under the default env)."""
    prior = os.environ.get(_ENV)
    yield
    if prior is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = prior
    for mod_path in _TARGETS:
        mod = importlib.import_module(mod_path)
        importlib.reload(mod)


def _reload_with_env(mod_path: str, value: str | None) -> ModuleType:
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    mod = importlib.import_module(mod_path)
    return importlib.reload(mod)


@pytest.mark.parametrize("mod_path", sorted(_TARGETS))
def test_virtual_timeframe_is_the_downgraded_tf(mod_path: str) -> None:
    virtual_tf, _real_tf, cls_name = _TARGETS[mod_path]
    mod = _reload_with_env(mod_path, "1")
    cls = getattr(mod, cls_name)
    assert cls.metadata.timeframe == virtual_tf


@pytest.mark.parametrize("mod_path", sorted(_TARGETS))
def test_real_timeframe_is_byte_identical_when_env_unset(mod_path: str) -> None:
    _virtual_tf, real_tf, cls_name = _TARGETS[mod_path]
    mod = _reload_with_env(mod_path, None)
    cls = getattr(mod, cls_name)
    assert cls.metadata.timeframe == real_tf


def test_downgrade_is_non_degenerate_and_never_5m() -> None:
    """Every (virtual, real) pair actually differs (a real loosening, not a
    no-op) and never lands on 5m (excluded — 0 bar supply -> INERT)."""
    for mod_path, (virtual_tf, real_tf, _cls_name) in _TARGETS.items():
        assert virtual_tf != real_tf, f"{mod_path}: no-op downgrade"
        assert virtual_tf != "5m", f"{mod_path}: 5m excluded (0 supply -> INERT)"
        assert real_tf != "5m", f"{mod_path}: 5m excluded (0 supply -> INERT)"


def test_rsi_bb_pullback_untouched() -> None:
    """rsi_bb_pullback (OKX) is explicitly OUT of scope for this downgrade wave
    (distinct from equity_rsi_bb_pullback / connors_rsi2, both in scope): its
    timeframe stays the pre-existing frozen "15m" literal, env-independent
    (no ``virtual_loosen`` wrapping added)."""
    from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy

    assert RSIBBPullbackStrategy.metadata.timeframe == "15m"
