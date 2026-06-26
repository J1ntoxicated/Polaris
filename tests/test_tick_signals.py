"""P5 tick signals — KILLED 2026-06-26 (gross-negative entry expectancy).

The three tick generators (``burst_rider`` / ``flow_pressure`` /
``micro_reversion``) were removed: negative entry expectancy BEFORE fees,
cross-validated over two windows. This file is now the KILL regression guard —
it asserts the generators are gone, the regime gate is empty (no signal active
in any regime → the engine dispatches nothing), and the retained pure helpers
(``normalize_regime`` / ``direction_bias``, consumed by ``regime_fit`` and the
entrance-leans probe) still behave. This is removal of a no-edge generator, NOT
a defensive throttle — there is no edge left here to throttle (flow_not_block).

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md §"신규 모듈".
"""

from __future__ import annotations

import polaris.core.ticks.signals as signals_mod
from polaris.core.ticks.regime_gate import (
    active_signals,
    direction_bias,
    normalize_regime,
)


def test_killed_tick_generators_are_gone() -> None:
    # The three negative-edge generators no longer exist on the signals module.
    assert not hasattr(signals_mod, "burst_rider")
    assert not hasattr(signals_mod, "flow_pressure")
    assert not hasattr(signals_mod, "micro_reversion")
    # Only the carrier type is exported (kept for historical-position exit typing).
    assert signals_mod.__all__ == ["TickIntent"]


def test_regime_gate_active_set_is_empty_in_every_regime() -> None:
    # No tick signal is active in ANY regime → the dispatch loop emits nothing.
    for regime in (
        "bull_trend",
        "bear_trend",
        "chop",
        "crisis",
        "trend",
        "range",
        "wat",
        None,
    ):
        assert active_signals(regime) == frozenset()


def test_regime_gate_normalize_and_bias() -> None:
    # Retained helpers (independent of tick dispatch) keep their behaviour.
    assert normalize_regime("bull_trend") == "trend"
    assert normalize_regime("CHOP") == "range"
    assert normalize_regime(None) == "unknown"
    assert direction_bias("bull_trend") == 1
    assert direction_bias("bear_trend") == -1
    assert direction_bias("chop") == 0
    assert direction_bias("crisis") == 0
