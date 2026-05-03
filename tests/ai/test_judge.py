"""F-N7 PR5 — EntryJudge / LiveEntryJudge unit tests (4).

Covers the Plan F-N7 PR5 scope for `invasion/ai/base.py::EntryJudge`
and `invasion/ai/live.py::LiveEntryJudge`:

  1. test_judge_should_call_crisis_threshold_20
  2. test_judge_should_call_neutral_threshold_30
  3. test_live_judge_falls_back_when_should_call_false
  4. test_live_judge_rejects_on_same_group_limit

`should_call` thresholds are regime-adaptive (see `EntryJudge.should_call`
docstring): CRISIS/RISK_OFF=20, TRANSITION=25, else=30.
"""
from __future__ import annotations

import types

import pytest

from invasion.ai.base import (
    EntryJudge,
    EntryJudgment,
    SignalContext,
)
from invasion.ai.live import LiveEntryJudge


# ── helpers ──────────────────────────────────────────────────────────


def _ctx(score: float = 40.0, regime: str = "neutral",
         ticker: str = "BTC", group: str = "crypto",
         direction: str = "BUY") -> SignalContext:
    return SignalContext(
        ticker=ticker, group=group, tier="mid",
        direction=direction, composite_score=score,
        agreement=0.75, factors=3,
        provider_scores={"tech": score},
        regime=regime,
    )


class _RecordingFallbackJudge(EntryJudge):
    """Fallback judge that records every judge() call + returns approve=True."""

    def __init__(self) -> None:
        self.calls: list[tuple[SignalContext, dict]] = []

    def judge(self, ctx: SignalContext, strategy: dict) -> EntryJudgment:
        self.calls.append((ctx, strategy))
        return EntryJudgment(
            approve=True, confidence=0.7,
            size_modifier=1.0, reason="fallback_used",
        )


class _StubOrchestrator:
    """Minimal AIOrchestrator stub — cost_ok=True by default."""

    def __init__(self, cost_ok: bool = True) -> None:
        self.cost_ok = cost_ok
        self.records: list = []

    def can_call(self, estimated_cost: float = 0.01) -> bool:
        return self.cost_ok

    def record_call(self, record) -> None:
        self.records.append(record)


class _StubPortfolio:
    """Minimal portfolio — positions() returns list of objects with asset_group."""

    def __init__(self, same_group_count: int = 0, group: str = "crypto") -> None:
        self._positions = [
            types.SimpleNamespace(asset_group=group, ticker=f"TK{i}")
            for i in range(same_group_count)
        ]
        self._stats = {"wins": 5, "losses": 5}

    def positions(self):
        return list(self._positions)


# ── 1. should_call thresholds ────────────────────────────────────────


def test_judge_should_call_crisis_threshold_20():
    """CRISIS regime: threshold=20 → |score|=25 passes, |score|=15 rejects."""

    class _Dummy(EntryJudge):
        def judge(self, ctx, strategy):
            return EntryJudgment(approve=True, confidence=0.5)

    j = _Dummy()
    assert j.should_call(_ctx(score=25.0, regime="CRISIS")) is True
    assert j.should_call(_ctx(score=-25.0, regime="CRISIS")) is True  # abs()
    assert j.should_call(_ctx(score=15.0, regime="CRISIS")) is False
    # RISK_OFF shares the 20 threshold
    assert j.should_call(_ctx(score=20.0, regime="RISK_OFF")) is True
    assert j.should_call(_ctx(score=19.9, regime="RISK_OFF")) is False


def test_judge_should_call_neutral_threshold_30():
    """NEUTRAL/unknown regime: threshold=30; TRANSITION=25."""

    class _Dummy(EntryJudge):
        def judge(self, ctx, strategy):
            return EntryJudgment(approve=True, confidence=0.5)

    j = _Dummy()
    # neutral: 30 gate
    assert j.should_call(_ctx(score=30.0, regime="NEUTRAL")) is True
    assert j.should_call(_ctx(score=29.9, regime="NEUTRAL")) is False
    # unknown defaults to 30
    assert j.should_call(_ctx(score=30.0, regime="unknown")) is True
    assert j.should_call(_ctx(score=29.9, regime="unknown")) is False
    # TRANSITION = 25
    assert j.should_call(_ctx(score=25.0, regime="TRANSITION")) is True
    assert j.should_call(_ctx(score=24.9, regime="TRANSITION")) is False


# ── 2. LiveEntryJudge fallback ───────────────────────────────────────


def test_live_judge_falls_back_when_should_call_false():
    """should_call=False (score below threshold) → fallback invoked, no API call."""
    fallback = _RecordingFallbackJudge()
    orch = _StubOrchestrator(cost_ok=True)
    cfg = types.SimpleNamespace(openai_key=None, anthropic_key=None,
                                gemini_key=None)
    live = LiveEntryJudge(cfg, orch, fallback, portfolio=None)

    # score=10 in neutral regime → threshold=30 → should_call=False
    ctx = _ctx(score=10.0, regime="neutral")
    judgment = live.judge(ctx, strategy={"id": "test"})

    assert isinstance(judgment, EntryJudgment)
    assert judgment.reason == "fallback_used"
    assert len(fallback.calls) == 1
    # Orchestrator never recorded (no API call made)
    assert orch.records == []


# ── 3. LiveEntryJudge same-group limit ───────────────────────────────


def test_live_judge_rejects_on_same_group_limit(monkeypatch):
    """same_group_count >= 3 → hard gate rejects even if AI approves.

    We stub `_claude_or_gemini` to return a permissive AI response so the
    only remaining gate is the same-group overload check.
    """
    fallback = _RecordingFallbackJudge()
    orch = _StubOrchestrator(cost_ok=True)
    cfg = types.SimpleNamespace(openai_key="fake", anthropic_key=None,
                                gemini_key="fake")
    portfolio = _StubPortfolio(same_group_count=3, group="crypto")

    # Stub the LLM dispatcher: return a decisive YES with confidence=8
    def _fake_dispatch(_cfg, _prompt, **_kw):
        parsed = {
            "approve": True, "confidence": 8,
            "size_modifier": 1.0, "reason": "looks good",
        }
        usage = {"input_tokens": 100, "output_tokens": 50, "latency_ms": 12}
        return parsed, usage, "stub-model", 0.0003

    monkeypatch.setattr("invasion.ai.live._claude_or_gemini", _fake_dispatch)

    live = LiveEntryJudge(cfg, orch, fallback, portfolio=portfolio)
    # score=40 in neutral → should_call passes → LLM path runs
    ctx = _ctx(score=40.0, regime="neutral", group="crypto")
    judgment = live.judge(ctx, strategy={"id": "test"})

    assert isinstance(judgment, EntryJudgment)
    assert judgment.approve is False, (
        f"expected reject on same_group_count=3, got approve={judgment.approve} "
        f"reason={judgment.reason}"
    )
    assert "Same group overload" in judgment.reason
    # AI was called (orchestrator recorded); fallback was NOT invoked
    assert len(orch.records) == 1
    assert fallback.calls == []
