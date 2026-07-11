"""W2(c) — brain ``_frontgate_line`` context injection + mini->5.5 C-predicate.

DEMO/PAPER paper bot. Canon: vault/50_research/backgate-plan/design-brain-ai.md
("컨텍스트 주입" + "mini→5.5 2단 에스컬레이션") + master-sequence.md (W2 (c)).

Zero new GPT calls: ``_frontgate_line`` is a pure string formatter folded into
the EXISTING ``_evidence_block`` (already sent on every judge_entry/judge_exit
call) — no new prompt, no new client. ``judge_escalation_candidate`` is a
SHADOW-COUNT-ONLY predicate (W5 wires the actual mini->5.5 call; here it only
tags ``gate_shadow_events.technical_flags`` so W5 has data to measure against).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.pipeline.agents.ai_judge import (
    JudgeOutcome,
    _evidence_block,
    _frontgate_line,
    judge_escalation_candidate,
    log_judge_event,
)
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.config import AI_JUDGE_MODE_SHADOW
from polaris.core.pipeline.gate_state import GATE_SIGNAL_VALIDATOR, GateContext, GateDecision

# ===========================================================================
# 1. _frontgate_line — n/a pattern, never a block, always renders.
# ===========================================================================


def test_frontgate_line_all_na_when_absent() -> None:
    line = _frontgate_line({})
    assert line == "- frontgate: rank=n/a news=n/a calib=n/a"


def test_frontgate_line_renders_present_values() -> None:
    payload = {
        "frontgate_rank": 3,
        "frontgate_news_conviction": 0.72,
        "frontgate_calibrated_p": 0.58,
    }
    line = _frontgate_line(payload)
    assert "rank=3" in line
    assert "news=0.72" in line
    assert "calib=0.58" in line
    assert "n/a" not in line


def test_frontgate_line_partial_present_partial_na() -> None:
    """A single producer landing (e.g. #3 rank only) never blocks the other
    two slots from rendering — each key is independent."""
    line = _frontgate_line({"frontgate_rank": 7})
    assert "rank=7" in line
    assert "news=n/a" in line
    assert "calib=n/a" in line


# ===========================================================================
# 2. _evidence_block — the line always threads into BOTH judge prompt paths.
# ===========================================================================


def test_evidence_block_always_includes_frontgate_line() -> None:
    """Minimal payload (no scout data wired yet) still carries the line, as
    n/a — distinct from the ``technicals``/``cell`` blocks which OMIT when
    absent. This is the deliberate design-brain-ai.md choice."""
    rendered = _evidence_block({"regime": "chop"})
    assert "- frontgate: rank=n/a news=n/a calib=n/a" in rendered


def test_evidence_block_frontgate_line_with_real_values() -> None:
    rendered = _evidence_block(
        {
            "regime": "bull_trend",
            "frontgate_rank": 1,
            "frontgate_news_conviction": 0.9,
        }
    )
    assert "rank=1" in rendered
    assert "news=0.9" in rendered
    assert "calib=n/a" in rendered


# ===========================================================================
# 3. judge_escalation_candidate — C-predicate, shadow-count only.
# ===========================================================================


@pytest.mark.parametrize(
    "reason", ["gpt_parse_fallback", "gpt_timeout", "gpt_ok_salvaged"]
)
def test_escalation_candidate_true_on_degraded_reasons(reason: str) -> None:
    outcome = JudgeOutcome(
        verdict="PROCEED", deterministic=GateDecision.PASS, escalation_reason=reason
    )
    assert judge_escalation_candidate(outcome) is True


@pytest.mark.parametrize("reason", ["gpt_ok", "no_client", "gpt_error"])
def test_escalation_candidate_false_on_clean_or_no_call(reason: str) -> None:
    outcome = JudgeOutcome(
        verdict="PROCEED", deterministic=GateDecision.PASS, escalation_reason=reason
    )
    assert judge_escalation_candidate(outcome) is False


# ===========================================================================
# 4. Shadow-count wiring — gate_shadow_events carries c_predicate, never acts.
# ===========================================================================


def _ctx() -> GateContext:
    return GateContext(
        run_id="run-frontgate",
        signal_id="sig-fg",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={"regime": "chop"},
        started_ts=int(time.time()),
    )


def test_log_judge_event_c_predicate_flag_on_degraded(memdb: sqlite3.Connection) -> None:
    outcome = JudgeOutcome(
        verdict="PROCEED",
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_timeout",
    )
    log_judge_event(
        memdb, ctx=_ctx(), gate_id=GATE_SIGNAL_VALIDATOR, outcome=outcome,
        mode=AI_JUDGE_MODE_SHADOW,
    )
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    assert "c_predicate:1" in rows[0]["technical_flags"]
    # The deterministic decision is untouched — count-only, never acted on.
    assert rows[0]["technical_decision"] == "PASS"


def test_log_judge_event_c_predicate_flag_on_clean(memdb: sqlite3.Connection) -> None:
    outcome = JudgeOutcome(
        verdict="PROCEED", deterministic=GateDecision.PASS, escalation_reason="gpt_ok",
    )
    log_judge_event(
        memdb, ctx=_ctx(), gate_id=GATE_SIGNAL_VALIDATOR, outcome=outcome,
        mode=AI_JUDGE_MODE_SHADOW,
    )
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    assert "c_predicate:0" in rows[0]["technical_flags"]
