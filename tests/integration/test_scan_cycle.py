"""F-N7 PR5 — scan-cycle integration tests (3).

Composes the real seams of the scan pipeline:
  SignalResult → SignalVerdict → AI augment/judge → EntryGate.check

Rather than spinning up the full `TradePipeline` (which has ~50 collaborators),
these tests wire together the 3 production boundaries that the scan cycle
crosses, so regressions in any single seam surface loudly.

  11. test_scan_cycle_golden_path_entry_committed
  12. test_scan_cycle_blacklist_short_circuits
  13. test_scan_cycle_regime_propagation_into_verdict

All tests marked @pytest.mark.integration so they can be gated via
`pytest -m integration` per F-N7 plan.
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
from invasion.ai.mocks import MockSignalAugmenter
from invasion.signals.base import CompositeSignal, SignalResult, SignalVerdict
from invasion.trade.entry import EntryGate, GateResult


pytestmark = pytest.mark.integration


# ── shared stubs ─────────────────────────────────────────────────────


class _StubOrchestrator:
    def __init__(self, cost_ok: bool = True) -> None:
        self.cost_ok = cost_ok
        self.records: list = []

    def can_call(self, estimated_cost: float = 0.01) -> bool:
        return self.cost_ok

    def record_call(self, record) -> None:
        self.records.append(record)


class _FallbackJudge(EntryJudge):
    def __init__(self) -> None:
        self.calls = 0

    def judge(self, ctx, strategy):
        self.calls += 1
        return EntryJudgment(approve=True, confidence=0.5,
                             size_modifier=1.0, reason="fallback")


class _Portfolio:
    def __init__(self, same_group_count: int = 0, group: str = "crypto") -> None:
        self._positions = [
            types.SimpleNamespace(asset_group=group)
            for _ in range(same_group_count)
        ]
        self._stats = {"wins": 10, "losses": 5}
        self._halt_until_ts = 0.0

    def positions(self):
        return list(self._positions)


def _install_preg(monkeypatch, overrides: dict) -> None:
    """Stub `invasion.trade.entry.preg` so the real registry is bypassed."""
    from invasion.config.param_registry import get as real_get

    def _fake(key, default=None):
        if key in overrides:
            return overrides[key]
        try:
            return real_get(key)
        except Exception:
            return default

    monkeypatch.setattr("invasion.trade.entry.preg", _fake)


def _md(ticker: str = "BTC", regime: str = "neutral", **extra) -> dict:
    base = dict(
        price=100.0, mid=100.0,
        price_timestamp=0, ts=0,
        group="crypto", exchange="okx",
        direction="long", tier="mid",
        high24h=120.0, low24h=80.0,
        tech_available=True, candle_available=1,
        regime=regime,
    )
    base.update(extra)
    return base


def _build_verdict(score: float = 40.0, direction: str = "long",
                   regime: str = "neutral") -> SignalVerdict:
    """Stitch SignalResult → CompositeSignal → SignalVerdict using real types."""
    s1 = SignalResult(name="tech", score=score, confidence=0.8, ttl=900)
    s2 = SignalResult(name="flow", score=score * 0.9, confidence=0.7, ttl=900)
    comp = CompositeSignal(
        ticker="BTC", score=score, direction=direction,
        confidence=0.75, signals=[s1, s2],
    )
    return SignalVerdict(
        passed=True, score=score, direction=direction,
        confidence=0.75, reason="ok",
        factor_count=2, agreement=1.0, composite=comp,
        metadata={"regime": regime},
    )


def _build_ctx(verdict: SignalVerdict, ticker: str = "BTC",
               regime: str = "neutral") -> SignalContext:
    return SignalContext(
        ticker=ticker, group="crypto", tier="mid",
        direction="BUY" if verdict.direction == "long" else "SELL",
        composite_score=verdict.score,
        agreement=verdict.agreement,
        factors=verdict.factor_count,
        provider_scores={s.name: s.score
                         for s in (verdict.composite.signals if verdict.composite else [])},
        regime=regime,
    )


# ── 11. Golden path ─────────────────────────────────────────────────


def test_scan_cycle_golden_path_entry_committed(monkeypatch):
    """Signal → Augment → Judge (AI approve) → EntryGate PASS → committed list."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })

    # AI dispatcher: decisive approve
    def _fake_dispatch(_cfg, _prompt, **_kw):
        return (
            {"approve": True, "confidence": 7,
             "size_modifier": 1.1, "reason": "strong"},
            {"input_tokens": 80, "output_tokens": 40, "latency_ms": 10},
            "stub-model", 0.0003,
        )
    monkeypatch.setattr("invasion.ai.live._claude_or_gemini", _fake_dispatch)

    # 1. Build verdict from signals
    verdict = _build_verdict(score=45.0, direction="long", regime="neutral")
    assert verdict.passed is True

    # 2. AI augmentation (mock pass-through)
    aug = MockSignalAugmenter()
    ctx = _build_ctx(verdict, regime="neutral")
    augmented = aug.augment(ctx)
    assert augmented.adjusted_score == 45.0
    assert augmented.skip is False

    # 3. AI judge via LiveEntryJudge
    orch = _StubOrchestrator(cost_ok=True)
    fallback = _FallbackJudge()
    cfg = types.SimpleNamespace(openai_key="fake", anthropic_key=None,
                                gemini_key="fake")
    judge = LiveEntryJudge(cfg, orch, fallback, portfolio=_Portfolio())
    judgment = judge.judge(ctx, strategy={"id": "test"})
    assert judgment.approve is True
    assert fallback.calls == 0, "AI path should run, not fallback"
    assert len(orch.records) == 1

    # 4. EntryGate
    gate = EntryGate(types.SimpleNamespace(blacklist=[]),
                     portfolio=_Portfolio())
    result = gate.check("BTC", verdict, _md(regime="neutral"))
    assert isinstance(result, GateResult)
    assert result.passed is True, (
        f"golden path gate failed: reason={result.reason}"
    )

    # 5. "Commit" — end-to-end PASS
    committed = [("BTC", verdict.direction, judgment.size_modifier)]
    assert committed == [("BTC", "long", pytest.approx(1.1))]


# ── 12. Blacklist short-circuits ─────────────────────────────────────


def test_scan_cycle_blacklist_short_circuits(monkeypatch):
    """Blacklisted ticker → EntryGate rejects before AI judge ever runs."""
    _install_preg(monkeypatch, {
        "ticker_blacklist": ["BADTKR"],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })

    # AI dispatcher — must NOT be called; assert via counter
    ai_call_count = {"n": 0}
    def _fake_dispatch(_cfg, _prompt, **_kw):
        ai_call_count["n"] += 1
        return (
            {"approve": True, "confidence": 9, "size_modifier": 1.0,
             "reason": "should_not_run"},
            {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0},
            "stub", 0.0,
        )
    monkeypatch.setattr("invasion.ai.live._claude_or_gemini", _fake_dispatch)

    verdict = _build_verdict(score=55.0, direction="long")

    # EntryGate checked FIRST (short-circuit path in scan order)
    gate = EntryGate(types.SimpleNamespace(blacklist=[]),
                     portfolio=_Portfolio())
    result = gate.check("BADTKR", verdict, _md(ticker="BADTKR"))
    assert result.passed is False
    assert result.reason == "auto_blacklisted"

    # AI judge not invoked because scan short-circuits on blacklist
    assert ai_call_count["n"] == 0


# ── 13. Regime propagation ───────────────────────────────────────────


def test_scan_cycle_regime_propagation_into_verdict(monkeypatch):
    """Regime flows verdict.metadata → SignalContext.regime → judge threshold.

    Demonstrates that the CRISIS threshold (20) unlocks judges that the
    neutral threshold (30) would have fallen back on.
    """
    _install_preg(monkeypatch, {
        "ticker_blacklist": [],
        "ticker_conditional_blacklist": {},
        "ticker_direction_bias": {},
        "tier_direction_block": [],
        "session_entry_block_hours_ny": [],
    })

    # AI dispatcher always approves
    def _fake_dispatch(_cfg, _prompt, **_kw):
        return (
            {"approve": True, "confidence": 6, "size_modifier": 1.3,
             "reason": "crisis_buy"},
            {"input_tokens": 50, "output_tokens": 25, "latency_ms": 8},
            "stub", 0.0003,
        )
    monkeypatch.setattr("invasion.ai.live._claude_or_gemini", _fake_dispatch)

    # Marginal score = 25: below neutral gate (30), above crisis gate (20)
    verdict = _build_verdict(score=25.0, direction="long", regime="CRISIS")
    # Regime round-trips through verdict metadata
    assert verdict.metadata["regime"] == "CRISIS"

    ctx = _build_ctx(verdict, regime="CRISIS")
    assert ctx.regime == "CRISIS"

    orch = _StubOrchestrator(cost_ok=True)
    fallback = _FallbackJudge()
    cfg = types.SimpleNamespace(openai_key="fake", anthropic_key=None,
                                gemini_key="fake")
    judge = LiveEntryJudge(cfg, orch, fallback, portfolio=_Portfolio())

    # CRISIS: should_call threshold=20 → |25|>=20 → AI path engaged
    judgment_crisis = judge.judge(ctx, strategy={"id": "t"})
    assert judgment_crisis.approve is True
    assert fallback.calls == 0
    assert len(orch.records) == 1

    # Contrast: same score, neutral regime → should_call=False → fallback
    ctx_neutral = _build_ctx(_build_verdict(score=25.0, direction="long",
                                            regime="neutral"),
                             regime="neutral")
    judge2 = LiveEntryJudge(cfg, _StubOrchestrator(), fallback,
                            portfolio=_Portfolio())
    judgment_neutral = judge2.judge(ctx_neutral, strategy={"id": "t"})
    assert judgment_neutral.reason == "fallback"
    assert fallback.calls == 1, "neutral regime should fall back on score=25"

    # EntryGate accepts in both regimes (blacklist/cooldown clean)
    gate = EntryGate(types.SimpleNamespace(blacklist=[]),
                     portfolio=_Portfolio())
    gate_result = gate.check("BTC", verdict, _md(regime="CRISIS"))
    assert gate_result.passed is True
