"""VIRTUAL-mode equity re-admit — dispatch/warmup live-fire adversarial check.

DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block. Jin 2026-07-09:
``equity_52wk_high_breakout`` / ``equity_vol_expansion_pocket_pivot`` (B1-pruned
2026-07-06, withheld from the first VIRTUAL-only re-admit pending Alpaca 1D bar
supply) now join ``session_breakout`` / ``donchian_turtle_breakout`` /
``spot_donchian`` / ``volume_burst`` in the VIRTUAL-only re-registration block
(``polaris/strategies/__init__.py``).

Proves, per the repo's own registered != dispatched INERT lesson
(``tests/test_dispatch_ssot.py``):

  (a) REAL byte-identical — env unset, both ids stay OUT of STRATEGY_REGISTRY.
  (b) VIRTUAL re-admit — env=1, both ids ARE in STRATEGY_REGISTRY, mapped to
      the correct classes, ``dispatch_eligible`` True.
  (c) live-fire, not just registered — the live bar-dispatch DERIVED set
      (``_production_tick._all_strategies()``) actually includes both AND
      ``generate_raw_signal`` actually emits a ``RawSignal`` given a
      qualifying bar series (registered-alone would be the exact silent-INERT
      class this repo has hit before).

Module-level env-branch, so both states are only observable via
``importlib.reload`` — mirrors ``tests/test_virtual_loosen_okx_donchian55.py``.
``_production_tick`` binds ``STRATEGY_REGISTRY`` by reference at its own
import time, so reloading only the parent package would silently leave its
dispatch set stale (a second, subtler INERT trap) — both are reloaded, in
that order.
"""

from __future__ import annotations

import importlib
import os

import pytest

from polaris.strategies.base import BarView
from polaris.strategies.equity_52wk_high_breakout import HIGH_LOOKBACK as EQ_HIGH_LOOKBACK
from tests.test_strategy_wave2 import _bars, _mv

_ENV = "POLARIS_VIRTUAL_ACCOUNT"


# NOTE: no return-type annotation — mirrors the accepted
# ``tests/test_virtual_loosen_okx_donchian55.py::_reload_with_env`` pattern.
# ``importlib.reload`` genuinely returns "whatever attributes the module now
# has", which a static tuple type cannot express; typing it concretely would
# just force spurious ``Any``-casts at every call site.
def _reload_with_env(value: str | None):
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.scripts._production_tick as tick_mod
    import polaris.strategies as strategies_mod

    strategies_mod = importlib.reload(strategies_mod)
    tick_mod = importlib.reload(tick_mod)
    return strategies_mod, tick_mod


@pytest.fixture(autouse=True)
def _restore_env_and_modules():
    """Isolate the env var + force a fresh import per test (no cross-test leak)."""
    prior = os.environ.get(_ENV)
    yield
    if prior is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = prior
    _reload_with_env(prior)


# ===========================================================================
# (a) REAL byte-identical — env unset
# ===========================================================================


def test_real_mode_equity_ids_absent_byte_identical() -> None:
    strategies_mod, tick_mod = _reload_with_env(None)
    assert "equity_52wk_high_breakout" not in strategies_mod.STRATEGY_REGISTRY
    assert "equity_vol_expansion_pocket_pivot" not in strategies_mod.STRATEGY_REGISTRY
    dispatched = {s.metadata.strategy_id for s in tick_mod._all_strategies()}
    assert "equity_52wk_high_breakout" not in dispatched
    assert "equity_vol_expansion_pocket_pivot" not in dispatched


# ===========================================================================
# (b) + (c) VIRTUAL re-admit: registered, dispatch_eligible, AND live-fires
# ===========================================================================


def test_virtual_mode_equity_ids_registered_and_dispatch_eligible() -> None:
    strategies_mod, _tick_mod = _reload_with_env("1")
    reg = strategies_mod.STRATEGY_REGISTRY
    assert reg["equity_52wk_high_breakout"] is strategies_mod.Equity52WkHighBreakoutStrategy
    assert (
        reg["equity_vol_expansion_pocket_pivot"]
        is strategies_mod.EquityVolExpansionPocketPivotStrategy
    )
    assert reg["equity_52wk_high_breakout"].metadata.dispatch_eligible is True
    assert reg["equity_vol_expansion_pocket_pivot"].metadata.dispatch_eligible is True


def test_virtual_mode_equity_ids_reachable_on_live_dispatch_path() -> None:
    # registered != dispatched (test_dispatch_ssot.py's own root-cause lesson):
    # confirm both ids are actually present on the LIVE bar-pipeline dispatch
    # set, not just sitting in the registry dict.
    _strategies_mod, tick_mod = _reload_with_env("1")
    dispatched = {s.metadata.strategy_id for s in tick_mod._all_strategies()}
    assert "equity_52wk_high_breakout" in dispatched
    assert "equity_vol_expansion_pocket_pivot" in dispatched


def test_virtual_mode_equity_52wk_actually_emits_a_signal() -> None:
    # Dispatch reachability alone is not proof of live-fire — drive the actual
    # generate_raw_signal path with a qualifying bar series (same fixture the
    # pre-existing test_strategy_wave2.test_equity_52wk_emits_on_high_above_
    # sma200 uses) and confirm a RawSignal comes out.
    strategies_mod, tick_mod = _reload_with_env("1")
    cls = next(
        s
        for s in tick_mod._all_strategies()
        if s.metadata.strategy_id == "equity_52wk_high_breakout"
    )
    assert isinstance(cls, strategies_mod.Equity52WkHighBreakoutStrategy)
    bars = _bars(260, base_close=100.0, drift=0.5)
    prior_high = max(b.high for b in bars[-(EQ_HIGH_LOOKBACK + 1) : -1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 2.0,
        low=last.low, close=prior_high + 1.0, volume=last.volume,
    )
    sig = cls.generate_raw_signal(_mv(bars, symbol="AAPL", venue="alpaca"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_52wk_high_breakout"
