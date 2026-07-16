"""B1 strategy prune (2026-07-06) — KILL the 4 live-ledger book-loss strategies.

DEMO/PAPER paper-trading (virtual funds only). This is CAPITAL CONCENTRATION on
the validated slow-trend edge — NOT a defensive throttle. /debate verdict = GO
(vault/50_research/debates/strategy_prune_b1_b2_2026-07-06.md).

KILLed (dispatch-severed), real-fee-bleed, live-ledger forensic, 100.9% of the
-$2,024 book loss:
  - ``session_breakout``                  -$933.65 NET / 88 trades / fees 9.7x
    gross (pure fee-bleed churn)
  - ``donchian_turtle_breakout``           -$540.58 (real directional loss)
  - ``equity_52wk_high_breakout``          -$137.23
  - ``equity_vol_expansion_pocket_pivot``  -$431.05 (0% win)

Modules preserved read-only (same dispatch-level KILL pattern as the prior
fx_range_fade / volume_burst / spot_donchian prunes); only signal-emit dispatch
is severed under REAL — registry membership itself is unaffected.

P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md): the
first 3 KILLed ids formalized their existing VIRTUAL-only re-admit off ad hoc
env-conditional STRATEGY_REGISTRY membership onto
``dispatch_eligible=virtual_loosen(True, False)`` (the documented dispatch
SSOT) — they are now UNCONDITIONALLY registered, with the REAL-mode KILL
(the whole point of this module) carried by the flag instead of by absence
from the dict. ``equity_vol_expansion_pocket_pivot`` is out of this
promotion's scope and stays fully un-registered under REAL (unchanged).
"""

from __future__ import annotations

import pytest

from polaris.strategies import STRATEGY_REGISTRY, all_strategies

# Formalized under P3 promotion (2026-07-16): registered unconditionally now,
# REAL-mode KILL carried by dispatch_eligible=virtual_loosen(True, False)
# (False under REAL/default env — no ``POLARIS_VIRTUAL_ACCOUNT`` set here).
_KILLED_BY_DISPATCH_FLAG_IDS = (
    "session_breakout",
    "donchian_turtle_breakout",
    "equity_52wk_high_breakout",
)

# Out of the P3 promotion's scope — stays on the ad hoc VIRTUAL-only registry
# path, fully absent from REAL's STRATEGY_REGISTRY (unchanged).
_KILLED_IDS = ("equity_vol_expansion_pocket_pivot",)

_KEEP_IDS = (
    "xau_indices_trend",
    "macd_ema_trend_pullback",
    "bar_breakout_run",
    "okx_donchian_55_breakout",
)


@pytest.mark.parametrize("strategy_id", _KILLED_BY_DISPATCH_FLAG_IDS)
def test_killed_strategy_registered_but_dispatch_ineligible(strategy_id: str) -> None:
    assert strategy_id in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY[strategy_id].metadata.dispatch_eligible is False


@pytest.mark.parametrize("strategy_id", _KILLED_IDS)
def test_killed_strategy_not_registered(strategy_id: str) -> None:
    assert strategy_id not in STRATEGY_REGISTRY


@pytest.mark.parametrize("strategy_id", _KEEP_IDS)
def test_keep_strategy_still_registered(strategy_id: str) -> None:
    assert strategy_id in STRATEGY_REGISTRY


def test_all_strategies_instantiate_without_error() -> None:
    instances = all_strategies()
    assert len(instances) == len(STRATEGY_REGISTRY)
    for inst in instances:
        assert inst.metadata.strategy_id in STRATEGY_REGISTRY
