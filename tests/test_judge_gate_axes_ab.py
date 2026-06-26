"""#32 axes A+B — judge CALL gate (anti-flooding + evidence-robustness).

DEMO/PAPER paper bot. These tests PROVE the two pure predicates that decide
whether a deterministic gate decision ESCALATES to the GPT judge:

- A (selective call): clean decisive signals flow deterministically (NO GPT);
  only boundary / decision-moment cases escalate.
- B (evidence robustness): even when A fires, a sparse-evidence case SKIPS the
  call (weak evidence = AI 개입 보류, never a block).

flow_not_block: ``escalate=False`` NEVER blocks — the deterministic decision
flows unchanged; the predicate only decides whether to ASK the AI.
"""

from __future__ import annotations

import time

import pytest

from polaris.core.pipeline.agents.judge_gate import (
    ROBUST_MIN_DEFAULT,
    evidence_robustness,
    robust_min,
    should_escalate_entry,
    should_escalate_exit,
)

# ===========================================================================
# A — ENTRY selective call (G3 / G4)
# ===========================================================================


def test_g3_clean_decisive_warm_does_not_escalate() -> None:
    esc, reason = should_escalate_entry(
        gate="G3", decision="PASS", scalar=1.45, n_eff=20.0,
    )
    assert esc is False
    assert reason == "g3_clean_decisive"


def test_g3_modify_escalates() -> None:
    esc, reason = should_escalate_entry(
        gate="G3", decision="MODIFY", scalar=1.45, n_eff=20.0,
    )
    assert esc is True
    assert reason == "g3_modify_ambiguous"


def test_g3_cold_cell_escalates() -> None:
    esc, reason = should_escalate_entry(
        gate="G3", decision="PASS", scalar=1.45, n_eff=2.0,
    )
    assert esc is True
    assert reason == "g3_cold_cell"


def test_g3_boundary_scalar_escalates() -> None:
    # Scalar inside [0.85, 1.15] default band → ambiguous.
    esc, reason = should_escalate_entry(
        gate="G3", decision="PASS", scalar=1.0, n_eff=20.0,
    )
    assert esc is True
    assert reason == "g3_boundary_scalar"


def test_g3_recent_reject_storm_skips() -> None:
    esc, reason = should_escalate_entry(
        gate="G3", decision="MODIFY", scalar=1.0, n_eff=1.0,
        recent_reject_in_6h=True,
    )
    assert esc is False
    assert reason == "recent_reject_skip"


def test_g4_clean_proceed_skips() -> None:
    esc, reason = should_escalate_entry(
        gate="G4", decision="PROCEED", scalar=1.0, n_eff=20.0, watch_flags=(),
    )
    assert esc is False
    assert reason == "g4_clean_proceed"


def test_g4_flagged_proceed_escalates() -> None:
    esc, reason = should_escalate_entry(
        gate="G4", decision="PROCEED", scalar=1.0, n_eff=20.0,
        watch_flags=("spread_wide",),
    )
    assert esc is True
    assert reason == "g4_watch_flags"


# ===========================================================================
# A — G7 EXIT selective call (decision-moment-only)
# ===========================================================================


def _exit_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        mode="HOLD",
        exit_state_crossed=False,
        last_price=100.0,
        current_stop=80.0,
        atr_one=1.0,
        momentum_drift=0.0,
        atr_slope=-0.01,
        volume_z=0.0,
    )
    base.update(overrides)
    return base


def test_g7_midflight_hold_skips() -> None:
    esc, reason = should_escalate_exit(**_exit_kwargs())  # type: ignore[arg-type]
    assert esc is False
    assert reason == "g7_midflight_skip"


def test_g7_let_run_skips() -> None:
    esc, reason = should_escalate_exit(**_exit_kwargs(mode="LET_RUN"))  # type: ignore[arg-type]
    assert esc is False


def test_g7_rung_crossed_escalates() -> None:
    esc, reason = should_escalate_exit(  # type: ignore[arg-type]
        **_exit_kwargs(exit_state_crossed=True)
    )
    assert esc is True
    assert reason == "g7_rung_crossed"


@pytest.mark.parametrize("mode", ["REMODE", "HARVEST", "CUT"])
def test_g7_decisive_mode_escalates(mode: str) -> None:
    esc, reason = should_escalate_exit(**_exit_kwargs(mode=mode))  # type: ignore[arg-type]
    assert esc is True
    assert reason == f"g7_mode_{mode.lower()}"


def test_g7_stop_near_escalates() -> None:
    esc, reason = should_escalate_exit(  # type: ignore[arg-type]
        **_exit_kwargs(last_price=100.0, current_stop=99.6, atr_one=1.0)
    )
    assert esc is True
    assert reason == "g7_stop_near"


def test_g7_big_move_drift_escalates() -> None:
    esc, reason = should_escalate_exit(  # type: ignore[arg-type]
        **_exit_kwargs(momentum_drift=0.5)
    )
    assert esc is True
    assert reason == "g7_big_move"


def test_g7_big_move_volume_z_escalates() -> None:
    esc, reason = should_escalate_exit(**_exit_kwargs(volume_z=3.0))  # type: ignore[arg-type]
    assert esc is True
    assert reason == "g7_big_move"


# ===========================================================================
# B — evidence robustness
# ===========================================================================


def _rich_payload(now_ts: int) -> dict[str, object]:
    return {
        "evidence": {
            "label": "bull_trend",
            "news_sentiment": 0.4,
            "news_n": 5,
            "crypto_fg": 78,
        },
        "baseline": {"atr": {"p50": 1.2}},
        "cell_routing": {"n_eff": 20.0},
        "ticker_ground": {"has_sentiment": True, "updated_ts": now_ts - 60},
    }


def test_robust_payload_clears_threshold() -> None:
    now = int(time.time())
    rob = evidence_robustness(_rich_payload(now), now_ts=now)
    assert rob.score >= robust_min()
    assert rob.completeness == 1.0
    assert rob.warmth == 1.0
    assert rob.freshness == 1.0


def test_sparse_payload_below_threshold() -> None:
    now = int(time.time())
    # label-only, cold cell, stale ground → withheld.
    payload = {
        "evidence": {"label": "bull_trend"},
        "cell_routing": {"n_eff": 1.0},
        "ticker_ground": {"has_sentiment": False, "updated_ts": now - 50_000},
    }
    rob = evidence_robustness(payload, now_ts=now)
    assert rob.score < ROBUST_MIN_DEFAULT


def test_missing_ground_freshness_is_zero() -> None:
    now = int(time.time())
    payload = {"evidence": {"label": "bull_trend"}, "cell_routing": {"n_eff": 20.0}}
    rob = evidence_robustness(payload, now_ts=now)
    assert rob.freshness == 0.0  # fail-safe: no updated_ts = not fresh


def test_cold_cell_warmth_capped_low() -> None:
    now = int(time.time())
    payload = {"cell_routing": {"n_eff": 4.0}}
    rob = evidence_robustness(payload, now_ts=now)
    assert rob.warmth <= 0.3


def test_empty_payload_score_zero() -> None:
    now = int(time.time())
    rob = evidence_robustness({}, now_ts=now)
    assert rob.score == 0.0


def test_robust_min_default() -> None:
    assert robust_min() == ROBUST_MIN_DEFAULT
