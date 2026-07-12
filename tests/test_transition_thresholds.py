"""Tests for TransitionThresholds (fee-split v1 FLIP item 2) — the
caller-injectable threshold override that lets ``evaluate_transition`` stay
pure/DB-free while a live caller (``_production_close_classes.py``, via
``remap_table.resolve_thresholds``) remaps Schmitt/stagnation/ladder
thresholds onto the flipped gross-bps axis.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Every
existing test in ``test_transition.py`` constructs ``TransitionInput``
without ``thresholds=`` and must keep passing byte-identically — this file
only covers the NEW override behavior + the invariant that KILL/tripwire
(a separate R-multiple scale) are unaffected by any threshold remap.
"""
from __future__ import annotations

from polaris.core.classes.transition import (
    TransitionInput,
    TransitionThresholds,
    evaluate_transition,
)

BASE_TS = 1_700_000_000


def _inp(**kw: object) -> TransitionInput:
    defaults: dict[str, object] = dict(
        strategy_class="EARN",
        kill_state="ACTIVE",
        window_w=20,
        dwell=0,
        ladder_step=0,
        epoch_id=1,
        last_transition_ts=BASE_TS,
        last_earn_demotion_ts=None,
        last_promotion_ts=None,
        timeframe_bucket="intraday",
        shadow_scores=[],
        intent_scores=[],
        recent_r_multiples=[],
        n_fills_total=100,
        n_signals_recent=100,
        n_fills_recent=100,
        last_50_fill_rate=1.0,
        now_ts=BASE_TS + 3600,
    )
    defaults.update(kw)
    return TransitionInput(**defaults)  # type: ignore[arg-type]


def test_default_thresholds_match_legacy_constants() -> None:
    """Zero-arg TransitionThresholds() reproduces the pre-flip literal
    constants (the whole backward-compat contract)."""
    t = TransitionThresholds()
    assert t.schmitt_bench_partial == -4.0
    assert t.schmitt_bench_full == -3.0
    assert t.schmitt_prove == -1.0
    assert t.prove_stagnation == 2.0
    assert t.ladder_step0 == 3.0
    assert t.ladder_step1 == 6.0
    assert t.ladder_step2 == 9.0
    assert t.ladder_decay == 3.0


def test_no_thresholds_kwarg_reproduces_legacy_schmitt_behavior() -> None:
    """A caller that never supplies thresholds= (every pre-flip call site)
    gets the byte-identical legacy Schmitt behavior."""
    scores = [1.0] * 12 + [-2.0] * 8  # partial-8 mean < -1 (legacy PROVE threshold)
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores)
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"


def test_remapped_thresholds_change_schmitt_outcome() -> None:
    """A gross-bps-scale remap (e.g. schmitt_prove moved to -50.0) makes the
    SAME raw scores that used to trigger PROVE now stay EARN — proving the
    override actually reaches the check."""
    scores = [1.0] * 12 + [-2.0] * 8
    remapped = TransitionThresholds(schmitt_prove=-50.0, schmitt_bench_full=-80.0, schmitt_bench_partial=-90.0)
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores, thresholds=remapped)
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "NO_TRANSITION"


def test_remapped_prove_stagnation_threshold_used() -> None:
    full_mean_scores = [1.5] * 20  # legacy stagnation (<=2.0) WOULD trigger BENCH
    remapped = TransitionThresholds(prove_stagnation=-10.0)
    inp = _inp(strategy_class="PROVE", window_w=20, intent_scores=full_mean_scores, thresholds=remapped)
    result = evaluate_transition(inp)
    # 1.5 > -10.0 remapped floor -> no stagnation demotion.
    assert result.strategy_class != "BENCH"


def test_remapped_ladder_thresholds_used_for_step_up() -> None:
    remapped = TransitionThresholds(ladder_step0=500.0)  # unreachable -> no step-up
    inp = _inp(
        strategy_class="BENCH", ladder_step=0, window_w=20,
        shadow_scores=[10.0] * 20, thresholds=remapped,
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 0
    assert result.reason == "NO_TRANSITION"


def test_remapped_ladder_decay_threshold_used() -> None:
    remapped = TransitionThresholds(ladder_decay=500.0)  # any real mean now decays
    inp = _inp(
        strategy_class="BENCH", ladder_step=1, window_w=20,
        shadow_scores=[10.0] * 20, thresholds=remapped,
    )
    result = evaluate_transition(inp)
    assert result.reason == "LADDER_DECAY"
    assert result.ladder_step == 0


# ---------------------------------------------------------------------------
# Invariant: KILL + tripwire (R-multiple scale) are UNAFFECTED by a
# threshold remap — item 6 confirmation (these are not on the score axis).
# ---------------------------------------------------------------------------


def test_kill_state_unaffected_by_extreme_thresholds_override() -> None:
    extreme = TransitionThresholds(
        schmitt_bench_partial=1e9, schmitt_bench_full=1e9, schmitt_prove=1e9,
        prove_stagnation=1e9, ladder_step0=-1e9, ladder_step1=-1e9,
        ladder_step2=-1e9, ladder_decay=-1e9,
    )
    inp = _inp(
        strategy_class="BENCH", kill_state="KILLED",
        shadow_scores=[10.0] * 60, thresholds=extreme,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.kill_state == "KILLED"
    assert result.reason == "KILL_NO_AUTO_REVIVE"


def test_tripwire_unaffected_by_thresholds_override() -> None:
    """Tripwire reads recent_r_multiples (a different scale entirely) — an
    extreme thresholds override must not change whether it fires."""
    extreme = TransitionThresholds(schmitt_bench_partial=-1e9, schmitt_bench_full=-1e9)
    inp = _inp(
        strategy_class="EARN", recent_r_multiples=[-1.0] * 8, thresholds=extreme,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "TRIPWIRE"
