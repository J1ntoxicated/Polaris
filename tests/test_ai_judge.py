"""#32 — AI per-ticker JUDGE over the bot's own information (structurally non-blocking).

DEMO/PAPER paper bot. The judge consumes technicals + alt-data evidence + regime +
ground and emits a per-ticker verdict that HELPS THE FLOW, never blocks it. These
tests PROVE the structural no-KILL guarantee + the shadow/active mode mechanism +
deterministic fallback + full-info consumption + 9-stack non-regression + the -1.0R /
pass-through preservation invariants.

flow_not_block: the judge's verdict vocabulary has NO block member; no GPT output (incl.
the literal strings "KILL"/"BLOCK"/"EXIT_NOW") can manufacture a block; and verdict
application is flow-additive only.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.core.pipeline.agents.ai_judge import (
    EntryJudgeVerdict,
    ExitJudgeVerdict,
    JudgeOutcome,
    _evidence_block,
    apply_entry_verdict,
    apply_exit_verdict,
    judge_entry,
    judge_exit,
    log_judge_event,
    parse_entry_verdict,
    parse_exit_verdict,
)
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.config import (
    AI_JUDGE_MODE_ACTIVE,
    AI_JUDGE_MODE_SHADOW,
    ai_judge_mode,
)
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)

# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class _FakeGPT:
    """Anthropic-shaped client returning a fixed response (counts calls)."""

    def __init__(self, response_text: str) -> None:
        self.call_count = 0
        outer = self

        class _Block:
            text = response_text

        class _Resp:
            content = [_Block()]
            usage = None

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                outer.call_count += 1
                return _Resp()

        self.messages = _Messages()


class _BoomGPT:
    """A client whose create() raises — exercises the deterministic fallback."""

    def __init__(self) -> None:
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                raise RuntimeError("gpt down")  # noqa: TRY003

        self.messages = _Messages()
        _ = outer


def _ctx() -> GateContext:
    return GateContext(
        run_id="run-judge",
        signal_id="sig-1",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "regime": "trend_up",
            # The keys ``altdata.fuser.fuse_evidence`` ACTUALLY emits (no
            # ``news_headline`` — that key never existed in the fuser output).
            "evidence": {
                "label": "bull_trend",
                "scores": {"bull_trend": 2.1, "bear_trend": 0.2},
                "news_sentiment": 0.42,
                "news_magnitude": 0.8,
                "news_n": 5,
                "crypto_fg": 78,
                "avg_funding": 0.004,
            },
            "baseline": {"atr": {"p50": 1.2}, "volume": {"p50": 1000.0}},
            "cell_routing": {"quartile": "top", "avg_pnl_r": 0.4, "n_eff": 12.0},
            "ticker_ground": {"has_sentiment": True, "has_event": True},
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )


def _det_pass() -> GateResult:
    return GateResult(
        decision=GateDecision.PASS,
        next_gate=GATE_PRE_ENTRY_WATCHER,
        payload={"validated_signal": {"symbol": "BTC-USDT", "strength_scalar": 1.0}},
        model_used="python",
    )


def _det_hold() -> GateResult:
    return GateResult(
        decision=GateDecision.HOLD,
        next_gate=None,
        payload={"stop_price": 95.0},
        model_used="python",
    )


# ===========================================================================
# 1. STRUCTURAL no-KILL guarantee — the verdict vocabulary has no block member.
# ===========================================================================


def test_entry_verdict_enum_has_no_block_member() -> None:
    members = {v.value for v in EntryJudgeVerdict}
    for forbidden in ("KILL", "BLOCK", "INVALID", "VETO", "REJECT", "EXIT_NOW", "SKIP"):
        assert forbidden not in members


def test_exit_verdict_enum_has_no_block_member() -> None:
    members = {v.value for v in ExitJudgeVerdict}
    for forbidden in ("KILL", "BLOCK", "INVALID", "VETO", "EXIT_NOW", "SKIP"):
        assert forbidden not in members


@pytest.mark.parametrize(
    "hostile", ["KILL", "BLOCK", "INVALID", "VETO", "broken premise", "", None, "garbage"]
)
def test_entry_parse_hostile_collapses_to_proceed(hostile: Any) -> None:
    # No hostile / unrecognized token can manufacture a block — it flows.
    assert parse_entry_verdict(hostile) is EntryJudgeVerdict.PROCEED


@pytest.mark.parametrize(
    "hostile", ["EXIT_NOW", "KILL", "CUT", "", None, "broken premise"]
)
def test_exit_parse_hostile_collapses_to_protect(hostile: Any) -> None:
    # No forced-cut token is honored — exit stays PROTECT (hold the floor).
    assert parse_exit_verdict(hostile) is ExitJudgeVerdict.PROTECT


def test_apply_entry_never_kills_for_any_verdict_or_hostile_input() -> None:
    """apply_entry_verdict is TOTAL: across every enum member AND hostile tokens,
    in active mode, the result is NEVER KILL and next_gate is NEVER stripped."""
    det = _det_pass()
    candidates = [v.value for v in EntryJudgeVerdict] + ["KILL", "BLOCK", "garbage"]
    for token in candidates:
        outcome = JudgeOutcome(
            verdict=token, deterministic=GateDecision.PASS, escalation_reason="t"
        )
        res = apply_entry_verdict(
            outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE
        )
        assert res.decision == GateDecision.PASS
        assert res.next_gate == GATE_PRE_ENTRY_WATCHER
        assert res.decision != GateDecision.KILL


def test_apply_exit_never_exit_now_for_any_verdict() -> None:
    det = _det_hold()
    candidates = [v.value for v in ExitJudgeVerdict] + ["EXIT_NOW", "KILL"]
    for token in candidates:
        outcome = JudgeOutcome(
            verdict=token, deterministic=GateDecision.HOLD, escalation_reason="t"
        )
        res = apply_exit_verdict(
            outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE
        )
        assert res.decision == GateDecision.HOLD
        assert res.decision not in {GateDecision.EXIT_NOW, GateDecision.KILL}


# ===========================================================================
# 2. MODE toggle — shadow (default) = deterministic acts; active = annotate.
# ===========================================================================


def test_mode_default_is_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_AI_JUDGE_MODE", raising=False)
    assert ai_judge_mode() == AI_JUDGE_MODE_SHADOW


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, AI_JUDGE_MODE_SHADOW),
        ("", AI_JUDGE_MODE_SHADOW),
        ("shadow", AI_JUDGE_MODE_SHADOW),
        ("garbage", AI_JUDGE_MODE_SHADOW),  # unknown → safe shadow (never silent-activate)
        ("ACTIVE", AI_JUDGE_MODE_ACTIVE),
        ("active", AI_JUDGE_MODE_ACTIVE),
    ],
)
def test_mode_parse(raw: str | None, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_AI_JUDGE_MODE", raising=False)
    assert ai_judge_mode(raw) == expected


def test_shadow_mode_returns_deterministic_verbatim() -> None:
    """Pass-through preservation: in shadow mode the deterministic result is
    returned byte-identical (same object) — the judge never touches the loop."""
    det = _det_pass()
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.SIZE_UP.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    res = apply_entry_verdict(outcome, deterministic_result=det, mode=AI_JUDGE_MODE_SHADOW)
    assert res is det  # identity — no copy, no mutation


def test_active_mode_annotates_but_preserves_decision() -> None:
    det = _det_pass()
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.STRENGTHEN_EVIDENCE.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    res = apply_entry_verdict(outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE)
    assert res.decision == GateDecision.PASS
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
    assert res.payload["ai_judge"]["verdict"] == "STRENGTHEN_EVIDENCE"
    # The original validated_signal is preserved.
    assert res.payload["validated_signal"]["strength_scalar"] == 1.0


# ===========================================================================
# 3. 9-stack non-regression — SIZE_UP is an INTENT flag, NOT a sizing multiplier.
# ===========================================================================


def test_size_up_does_not_touch_strength_scalar() -> None:
    """SIZE_UP must NOT fold a ≤1 (or any) multiplier into the sizing chain — it
    is an intent flag only (9-stack permanently sealed)."""
    det = _det_pass()
    original_scalar = det.payload["validated_signal"]["strength_scalar"]
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.SIZE_UP.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    res = apply_entry_verdict(outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE)
    # strength_scalar is UNCHANGED — the judge added an intent flag, not a mult.
    assert res.payload["validated_signal"]["strength_scalar"] == original_scalar
    assert res.payload["ai_judge_size_up_intent"] is True


def test_refine_timing_is_flow_passthrough() -> None:
    # Inert one-shot payload stamp removed (dead telemetry, zero consumers);
    # REFINE_TIMING remains a pure flow-through verdict.
    det = _det_pass()
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.REFINE_TIMING.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    res = apply_entry_verdict(outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE)
    assert "ai_judge_refine_timing" not in res.payload
    assert res.decision == GateDecision.PASS  # still flows


# ===========================================================================
# 4. Full-info consumption — the prompt carries the bot's own information.
# ===========================================================================


@pytest.mark.asyncio
async def test_entry_prompt_consumes_full_info() -> None:
    captured: dict[str, Any] = {}

    async def _spy(**kwargs: Any) -> Any:
        captured["prompt"] = kwargs["user_prompt"]
        from polaris.core.pipeline.agents._gpt_client import GPTCallResult

        return GPTCallResult(
            text='{"verdict":"PROCEED"}',
            parsed={"verdict": "PROCEED"},
            latency_ms=5,
        )

    import polaris.core.pipeline.agents.ai_judge as mod

    orig = mod.call_gpt
    mod.call_gpt = _spy  # type: ignore[assignment]
    try:
        await judge_entry(
            _ctx(),
            deterministic=GateDecision.PASS,
            subject={"symbol": "BTC-USDT", "side": "long"},
            client=_FakeGPT('{"verdict":"PROCEED"}'),
        )
    finally:
        mod.call_gpt = orig  # type: ignore[assignment]
    prompt = captured["prompt"]
    # technicals + alt-data evidence + regime + ground all reach the judge, all
    # via the keys the fuser ACTUALLY emits (no dead ``news_headline`` read).
    assert "trend_up" in prompt  # regime
    assert "bull_trend" in prompt  # alt-data evidence label + scores
    assert "sentiment=0.42" in prompt  # fused news tone (real key)
    assert "crypto_fg" in prompt  # raw alt-data signal surfaced
    assert "technicals" in prompt  # baseline technicals
    assert "has_sentiment" in prompt  # ground coverage
    # The prompt explicitly forbids blocking.
    assert "cannot block" in prompt.lower()
    # The dead ``news_headline`` key must NOT appear (it was never fuser output).
    assert "news_headline" not in prompt


def test_evidence_block_consumes_real_fuser_output_keys() -> None:
    """``_evidence_block`` renders the keys ``fuse_evidence`` ACTUALLY emits.

    Drives the REAL fuser over a crypto group with fresh fear-greed + funding +
    news sources, then feeds its evidence dict to ``_evidence_block`` — proving
    the renderer consumes the genuine emit keys (label / scores / news_sentiment /
    news_magnitude / news_n / crypto_fg / avg_funding) and NOT the dead
    ``news_headline`` key. This is the info-completeness contract: the judge sees
    the bot's own information, byte-for-byte from the fuser.
    """
    from polaris.core.altdata.fuser import fuse_evidence

    class _Cache:
        def get_for_group(self, gid: str, *, now_ts: Any = None) -> dict[str, Any]:
            return {
                "crypto_fg": {"value": 80},
                "okx_funding": {"BTC-USDT-SWAP": {"fundingRate": 0.004}},
                "news_sentiment": {
                    "BTC": {
                        "sentiment": 0.6, "relevance": 0.9,
                        "magnitude": 0.7, "n": 4,
                    }
                },
            }

    _hint, _conf, evidence = fuse_evidence("crypto:BTC", _Cache())
    # The fuser DID emit the real keys (sanity — guards against a fuser rename).
    assert "news_sentiment" in evidence
    assert "crypto_fg" in evidence
    assert "news_headline" not in evidence  # never a fuser key

    rendered = _evidence_block(
        {"regime": "bull_trend", "evidence": evidence,
         "ticker_ground": {"has_sentiment": True, "has_event": True}}
    )
    assert "label=" in rendered  # fused regime label line
    assert "scores=" in rendered
    assert "sentiment=" in rendered  # fused news tone (real key)
    assert "crypto_fg" in rendered  # raw alt-data signal
    assert "has_sentiment=True" in rendered  # ground coverage
    assert "news_headline" not in rendered  # dead key removed


def test_evidence_block_annotates_source_age() -> None:
    """Currency fix: the judge prompt labels stale evidence with its age so the
    model can self-discount a weekend macro print or a weekly COT report. The age
    is appended INLINE on each raw signal — no source is dropped (flow_not_block).
    """
    evidence = {
        "label": "bull_trend",
        "scores": {"bull_trend": 2.5},
        "vix": 18.5,
        "vix_asof": "2026-06-26",
        "macro_age_days": 3,
        "cot_net_spec_pctile": 0.82,
        "cot_report_date": "2026-06-16",
        "news_sentiment": 0.6,
        "news_magnitude": 0.7,
        "news_n": 4,
        "news_max_age_h": 20.0,
    }
    rendered = _evidence_block({"regime": "bull_trend", "evidence": evidence})
    # Macro age: VIX print labelled with its observed-days-ago.
    assert "3d" in rendered or "asof 2026-06-26" in rendered
    # COT report age labelled.
    assert "2026-06-16" in rendered or "report" in rendered.lower()
    # News headline age labelled (hours).
    assert "20" in rendered and ("h" in rendered.lower() or "age" in rendered.lower())


def test_evidence_block_no_age_keys_is_unchanged() -> None:
    """Pre-fix evidence with no age keys renders without any age suffix (no crash,
    no fabricated 'asof')."""
    evidence = {
        "label": "bull_trend",
        "scores": {"bull_trend": 2.5},
        "vix": 18.5,
        "news_sentiment": 0.6,
        "news_n": 4,
    }
    rendered = _evidence_block({"regime": "bull_trend", "evidence": evidence})
    assert "asof" not in rendered
    assert "vix" in rendered.lower()


def test_evidence_block_graceful_when_no_evidence() -> None:
    """A payload with no fused evidence / ground renders only the regime +
    frontgate lines.

    Mirrors the production hot path BEFORE the ticker-ground producer covers a
    name: absent ``evidence`` / ``ticker_ground`` keys → graceful (no crash, no
    manufactured judgment), only regime + the n/a-padded frontgate scout line
    are surfaced (backgate-plan W2(c) — ``_frontgate_line`` ALWAYS renders).
    """
    rendered = _evidence_block({"regime": "chop"})
    assert rendered == "- regime: chop\n- frontgate: rank=n/a news=n/a calib=n/a"


# ── Allowlist-gap fix: max-expand altdata granular keys reach the judge ───────
def test_evidence_block_renders_binance_positioning() -> None:
    """The altdata expansion adds keyless Binance derivative positioning to the
    fused evidence. The judge prompt MUST surface the granular keys (smart-money
    long/short ratio, taker imbalance, OI delta) — previously dropped by the
    hardcoded 5-key allowlist. flow_not_block: pure read-only context.
    """
    evidence = {
        "label": "bull_trend",
        "scores": {"bull_trend": 3.0},
        "binance_top_long_short": 1.42,
        "binance_taker_buy_sell": 1.18,
        "binance_global_long_short": 2.1,
        "binance_oi_change_24h": 0.057,
        "binance_oi_asof": "2026-06-26T12:00:00Z",
    }
    rendered = _evidence_block({"regime": "bull_trend", "evidence": evidence})
    assert "binance_top_long_short" in rendered
    assert "1.42" in rendered
    assert "binance_taker_buy_sell" in rendered
    # Currency: the OI observation date is age-labelled, never silently 'current'.
    assert "2026-06-26" in rendered


def test_evidence_block_renders_coinglass_and_myfxbook() -> None:
    """Coinglass liquidation cascade + MyFxBook retail crowd positioning reach the
    judge prompt (granular keys), each carrying its asof currency label."""
    evidence = {
        "label": "crisis",
        "scores": {"crisis": 2.0},
        "cg_long_liq_usd": 4.2e7,
        "cg_short_liq_usd": 3.1e6,
        "cg_liquidation_asof": "2026-06-26T10:00:00Z",
        "fxb_long_pct": 82.0,
        "fxb_short_pct": 18.0,
        "fxb_asof": "2026-06-26T11:00:00Z",
    }
    rendered = _evidence_block({"regime": "crisis", "evidence": evidence})
    assert "cg_long_liq_usd" in rendered
    assert "fxb_long_pct" in rendered
    assert "82" in rendered
    # Both positioning sources carry their currency label.
    assert rendered.count("2026-06-26") >= 2


def test_evidence_block_renders_curated_fred_panel() -> None:
    """The FRED 4->32 expansion surfaces a curated macro panel as evidence-only
    (no scoring). The judge prompt MUST render the DECISION-relevant subset (yield
    curve / rates / breakevens / dollar / credit spread / vol term-structure) with
    age labels — NOT a 32-number dump (prompt bloat). Off-curate series (e.g.
    housing_starts) stay out of the prompt even when present in evidence.
    """
    evidence = {
        "label": "bull_trend",
        "scores": {"bull_trend": 2.0},
        "yield_curve": -0.18,
        "yield_curve_asof": "2026-06-26",
        "ust_10y": 4.31,
        "breakeven_10y": 2.34,
        "dollar_index": 121.5,
        "dollar_index_asof": "2026-06-25",
        "ig_spread": 95.0,
        "vix_3m": 20.1,
        # Off-curate panel members present but should NOT bloat the prompt.
        "housing_starts": 1.42,
        "nonfarm_payrolls": 159000.0,
    }
    rendered = _evidence_block({"regime": "bull_trend", "evidence": evidence})
    assert "yield_curve" in rendered
    assert "-0.18" in rendered
    assert "dollar_index" in rendered
    assert "breakeven_10y" in rendered
    # Currency label present on at least one curated panel member.
    assert "2026-06-26" in rendered or "2026-06-25" in rendered
    # Bloat guard: off-curate members are NOT dumped into the prompt.
    assert "housing_starts" not in rendered
    assert "nonfarm_payrolls" not in rendered


def test_evidence_block_no_new_keys_is_unchanged() -> None:
    """Pre-expansion evidence (only the original 5 allowlist keys) renders with no
    binance/coinglass/myfxbook/panel lines — no fabricated content, no crash."""
    evidence = {
        "label": "bull_trend",
        "scores": {"bull_trend": 2.5},
        "crypto_fg": 80,
        "avg_funding": 0.004,
    }
    rendered = _evidence_block({"regime": "bull_trend", "evidence": evidence})
    assert "binance_" not in rendered
    assert "cg_" not in rendered
    assert "fxb_" not in rendered
    assert "yield_curve" not in rendered
    # Original signals still surfaced.
    assert "crypto_fg" in rendered


def test_entry_and_exit_prompts_both_carry_new_evidence() -> None:
    """Both the ENTRY and EXIT prompt paths consume ``_evidence_block`` — the new
    granular positioning + panel evidence reaches the model on BOTH legs (the
    allowlist gap fix is single-source, so neither path can diverge)."""
    from polaris.core.pipeline.agents.ai_judge import (
        _build_entry_prompt,
        _build_exit_prompt,
    )

    payload = {
        "regime": "bull_trend",
        "evidence": {
            "label": "bull_trend",
            "scores": {"bull_trend": 3.0},
            "binance_top_long_short": 1.42,
            "yield_curve": -0.18,
        },
    }
    subject = {"symbol": "BTC-USDT", "side": "buy"}
    entry = _build_entry_prompt(payload, subject=subject)
    exit_ = _build_exit_prompt(payload, subject=subject)
    for rendered in (entry, exit_):
        assert "binance_top_long_short" in rendered
        assert "yield_curve" in rendered


# ===========================================================================
# 5. Deterministic fallback — GPT error / no-client never degrades flow.
# ===========================================================================


@pytest.mark.asyncio
async def test_no_client_falls_back_to_safe_verdict() -> None:
    outcome = await judge_entry(
        _ctx(),
        deterministic=GateDecision.PASS,
        subject={"symbol": "BTC-USDT"},
        client=None,
    )
    assert outcome.verdict == EntryJudgeVerdict.PROCEED.value
    assert outcome.deterministic == GateDecision.PASS
    assert outcome.escalation_reason == "no_client"


@pytest.mark.asyncio
async def test_gpt_error_falls_back_to_safe_verdict() -> None:
    outcome = await judge_exit(
        _ctx(),
        deterministic=GateDecision.HOLD,
        subject={"symbol": "BTC-USDT"},
        client=_BoomGPT(),
    )
    # The error path returns the SAFE non-blocking verdict + deterministic decision.
    assert outcome.verdict == ExitJudgeVerdict.PROTECT.value
    assert outcome.deterministic == GateDecision.HOLD
    assert outcome.escalation_reason == "gpt_error"


class _TricklingGPT:
    """A client whose ``create()`` hangs past the per-op timeout.

    Reproduces the audited defect: ``httpx``'s ``timeout=`` kwarg only bounds
    EACH connect/read/write phase, not the total call — a trickling response
    (partial reads that each individually stay under the per-op timeout) can
    occupy the gate far longer than ``JUDGE_TIMEOUT_SEC`` (audited max 495s).
    This fake just sleeps past any per-op bound to stand in for that failure
    mode without a real slow socket.
    """

    def __init__(self, sleep_sec: float) -> None:
        self._sleep_sec = sleep_sec
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                await __import__("asyncio").sleep(outer._sleep_sec)
                raise AssertionError("should never complete — deadline must cut in first")

        self.messages = _Messages()


@pytest.mark.asyncio
async def test_judge_call_wall_clock_deadline_falls_back_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#32 wall-clock fix: a trickling GPT response must NOT occupy the gate
    past a bounded TOTAL deadline, even though the per-op ``httpx`` timeout
    alone cannot see it (audited max 495s vs JUDGE_TIMEOUT_SEC=8.0). The
    deadline is env-tunable so this test runs fast (no real 8s+ wait) and the
    fallback is the SAME deterministic safe-verdict path as any other error."""
    import time as _time

    monkeypatch.setenv("POLARIS_JUDGE_WALL_CLOCK_DEADLINE_SEC", "0.05")
    started = _time.monotonic()
    outcome = await judge_exit(
        _ctx(),
        deterministic=GateDecision.HOLD,
        subject={"symbol": "BTC-USDT"},
        client=_TricklingGPT(sleep_sec=5.0),
    )
    elapsed = _time.monotonic() - started
    assert elapsed < 1.0  # bounded by the 0.05s deadline, NOT the 5s trickle
    assert outcome.verdict == ExitJudgeVerdict.PROTECT.value
    assert outcome.deterministic == GateDecision.HOLD
    assert outcome.escalation_reason == "gpt_timeout"


@pytest.mark.asyncio
async def test_gpt_kill_output_cannot_block() -> None:
    """Even if the model returns a hostile {"verdict":"KILL"}, the judge parses it
    to the safe non-blocking verdict — a block is structurally impossible."""
    outcome = await judge_entry(
        _ctx(),
        deterministic=GateDecision.PASS,
        subject={"symbol": "BTC-USDT"},
        client=_FakeGPT('{"verdict":"KILL","reason":"broken premise"}'),
    )
    assert outcome.verdict == EntryJudgeVerdict.PROCEED.value
    # And applying it still flows.
    res = apply_entry_verdict(
        outcome, deterministic_result=_det_pass(), mode=AI_JUDGE_MODE_ACTIVE
    )
    assert res.decision == GateDecision.PASS
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER


@pytest.mark.asyncio
async def test_truncated_reason_salvages_verdict_from_before_the_cut() -> None:
    """#32 fix: [[judge-probe-reality]] audited gpt_parse_fallback at 10.6%
    (984/9,327) — root cause is MAX_TOKENS=180 cutting the response mid the
    free-text ``reason`` field, which is emitted AFTER ``verdict`` in the
    prompt's requested JSON shape. Since ``reason`` is NEVER consumed
    downstream (only ``verdict`` drives behaviour), a truncated-but-otherwise-
    well-formed response should salvage the ALREADY-COMPLETE verdict instead
    of discarding the whole call as a parse failure."""
    truncated = (
        '{"verdict": "STRENGTHEN_EVIDENCE", "reason": "The alt-data evidence '
        "shows strong bullish alignment with crypto fear-greed at 78 and "
        "positive funding, while the cell expectancy for this quartile has "
        "historically produced positive avg_pnl_r, and the ground coverage "
        "confirms sentiment pres"  # cut mid-word — no closing quote/brace
    )
    outcome = await judge_entry(
        _ctx(),
        deterministic=GateDecision.PASS,
        subject={"symbol": "BTC-USDT"},
        client=_FakeGPT(truncated),
    )
    assert outcome.verdict == EntryJudgeVerdict.STRENGTHEN_EVIDENCE.value
    assert outcome.escalation_reason == "gpt_ok_salvaged"


@pytest.mark.asyncio
async def test_truncated_with_no_recoverable_verdict_still_falls_back() -> None:
    """A truncation that cuts BEFORE the verdict value is complete has nothing
    to salvage — falls back to the safe verdict exactly as before."""
    truncated = '{"verd'
    outcome = await judge_exit(
        _ctx(),
        deterministic=GateDecision.HOLD,
        subject={"symbol": "BTC-USDT"},
        client=_FakeGPT(truncated),
    )
    assert outcome.verdict == ExitJudgeVerdict.PROTECT.value
    assert outcome.escalation_reason == "gpt_parse_fallback"


@pytest.mark.asyncio
async def test_truncated_hostile_verdict_salvage_cannot_block() -> None:
    """Salvage reuses the SAME hostile-input-safe ``parse`` — a truncated
    response claiming a hostile verdict still collapses to the safe one."""
    truncated = '{"verdict": "KILL", "reason": "trunc'
    outcome = await judge_entry(
        _ctx(),
        deterministic=GateDecision.PASS,
        subject={"symbol": "BTC-USDT"},
        client=_FakeGPT(truncated),
    )
    assert outcome.verdict == EntryJudgeVerdict.PROCEED.value


@pytest.mark.asyncio
async def test_well_formed_verdict_is_honored() -> None:
    outcome = await judge_entry(
        _ctx(),
        deterministic=GateDecision.PASS,
        subject={"symbol": "BTC-USDT"},
        client=_FakeGPT('{"verdict":"STRENGTHEN_EVIDENCE","reason":"news confirms"}'),
    )
    assert outcome.verdict == EntryJudgeVerdict.STRENGTHEN_EVIDENCE.value
    assert outcome.escalation_reason == "gpt_ok"


# ===========================================================================
# 6. Measurement — gate_shadow_events row + pass-through tracking.
# ===========================================================================


def test_log_judge_event_writes_shadow_row(memdb: sqlite3.Connection) -> None:
    ctx = _ctx()
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.SIZE_UP.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    log_judge_event(
        memdb,
        ctx=ctx,
        gate_id=GATE_SIGNAL_VALIDATOR,
        outcome=outcome,
        mode=AI_JUDGE_MODE_SHADOW,
    )
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    assert len(rows) == 1
    row = rows[0]
    # The deterministic decision is what the live loop acts on (pass-through).
    assert row["technical_decision"] == "PASS"
    # The judge verdict + mode + escalation are recorded in the flags column.
    flags = row["technical_flags"]
    assert "judge:SIZE_UP" in flags
    assert "mode:shadow" in flags
    assert "escalation:gpt_ok" in flags
    # No GPT GateDecision to compare → empty (excluded from legacy agreement).
    assert row["gpt_decision"] == ""


def test_log_judge_event_none_conn_is_noop() -> None:
    # Must never crash the hot path when there is no shadow conn.
    outcome = JudgeOutcome(
        verdict=EntryJudgeVerdict.PROCEED.value,
        deterministic=GateDecision.PASS,
        escalation_reason="gpt_ok",
    )
    log_judge_event(
        None, ctx=_ctx(), gate_id=GATE_PRE_ENTRY_WATCHER, outcome=outcome,
        mode=AI_JUDGE_MODE_SHADOW,
    )  # no exception


# ===========================================================================
# 7. -1.0R rail / deterministic ownership — the judge never sees the rail.
# ===========================================================================


def test_judge_module_does_not_reference_gate_decision_kill() -> None:
    """The judge module's verdict types must not be able to emit GateDecision.KILL
    or GateDecision.EXIT_NOW. Confirm no verdict member maps to either."""
    entry_values = {v.value for v in EntryJudgeVerdict}
    exit_values = {v.value for v in ExitJudgeVerdict}
    assert GateDecision.KILL.value not in entry_values | exit_values
    assert GateDecision.EXIT_NOW.value not in entry_values | exit_values


def test_apply_preserves_exit_hold_floor_and_stop_price() -> None:
    """An EXTEND verdict in active mode does NOT mutate the deterministic stop or
    decision here — the widen/tighten timing rails stay G7-owned (-1.0R untouched)."""
    det = _det_hold()
    outcome = JudgeOutcome(
        verdict=ExitJudgeVerdict.EXTEND.value,
        deterministic=GateDecision.HOLD,
        escalation_reason="gpt_ok",
    )
    res = apply_exit_verdict(outcome, deterministic_result=det, mode=AI_JUDGE_MODE_ACTIVE)
    assert res.decision == GateDecision.HOLD
    assert res.payload["stop_price"] == 95.0  # deterministic stop preserved
    assert res.payload["ai_judge"]["verdict"] == "EXTEND"


# ===========================================================================
# 8. Gate wiring — G3/G4/G7 byte-identical when judge_client=None; judged when set.
# ===========================================================================


def _g3_gate_ctx() -> GateContext:
    return GateContext(
        run_id="run-w",
        signal_id="sig-w",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "raw_signal": {"symbol": "BTC-USDT", "side": "long", "strength": 1.2},
            "cell_routing": {"quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5, "score": 0.5},
            # Robust evidence so the #32 A+B call gate ESCALATES (B passes); the
            # PASS scalar (1.0) lands in the G3 boundary band → A fires.
            "evidence": {
                "label": "bull_trend",
                "news_sentiment": 0.4,
                "news_n": 5,
                "crypto_fg": 78,
            },
            "baseline": {"atr": {"p50": 1.2}},
            "ticker_ground": {"has_sentiment": True, "updated_ts": int(time.time()) - 60},
            "recent_trades": [],
            "regime": "trend_up",
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )


@pytest.mark.asyncio
async def test_g3_judge_client_none_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """G3 with judge_client=None is byte-identical to the pre-#32 deterministic path."""
    from polaris.core.pipeline.agents.signal_validator import signal_validator_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    res = await signal_validator_gate(_g3_gate_ctx(), client=None, judge_client=None)
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"
    assert "ai_judge" not in res.payload


@pytest.mark.asyncio
async def test_g3_active_judge_kill_output_still_passes(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """Even with a hostile judge KILL output in ACTIVE mode, G3 still PASSES — the
    deterministic decision is structurally preserved (no AI block path)."""
    from polaris.core.pipeline.agents.signal_validator import signal_validator_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    judge = _FakeGPT('{"verdict":"KILL","reason":"broken premise"}')
    res = await signal_validator_gate(
        _g3_gate_ctx(), client=None, judge_client=judge, shadow_conn=memdb
    )
    assert res.decision == GateDecision.PASS  # NOT KILL — structurally impossible
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
    assert res.payload["ai_judge"]["verdict"] == "PROCEED"  # KILL parsed to safe verdict
    # P2a group B: G3 now ALSO logs its own technical-decision row (gpt_decision=
    # None, measurement continuity) — filter to the judge's own row by its flag.
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    judge_rows = [r for r in rows if "judge:PROCEED" in r["technical_flags"]]
    assert len(judge_rows) == 1


@pytest.mark.asyncio
async def test_g3_shadow_mode_deterministic_unaffected(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """Shadow mode (default): judge logs but the deterministic decision acts and the
    payload is NOT annotated (pass-through preservation)."""
    from polaris.core.pipeline.agents.signal_validator import signal_validator_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.delenv("POLARIS_AI_JUDGE_MODE", raising=False)  # default shadow
    judge = _FakeGPT('{"verdict":"SIZE_UP"}')
    res = await signal_validator_gate(
        _g3_gate_ctx(), client=None, judge_client=judge, shadow_conn=memdb
    )
    assert res.decision == GateDecision.PASS
    assert "ai_judge" not in res.payload  # shadow = no annotation, deterministic acts
    assert res.payload["validated_signal"]["strength_scalar"] == 1.0
    # The judge row IS logged for measurement (alongside G3's own technical-
    # decision row added by P2a group B — filter to the judge's own row).
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    judge_rows = [r for r in rows if "mode:shadow" in r["technical_flags"]]
    assert len(judge_rows) == 1


def _g7_gate_ctx() -> GateContext:
    return GateContext(
        run_id="run-w",
        signal_id="sig-w",
        position_id="pos-1",
        gate_id=7,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "regime": "trend_up",
            # Robust evidence so the #32 A+B call gate ESCALATES (B passes); no
            # ``judge_exit_escalate`` key → A defaults True (orchestrator path).
            "evidence": {
                "label": "bull_trend",
                "news_sentiment": 0.4,
                "news_n": 5,
                "crypto_fg": 78,
            },
            "baseline": {"atr": {"p50": 1.2}},
            "cell_routing": {"n_eff": 12.0},
            "ticker_ground": {"has_sentiment": True, "updated_ts": int(time.time()) - 60},
            "widen_proposal": {
                "side": "long",
                "current_stop_price": 95.0,
                "proposed_stop_price": 93.0,
                "entry_price": 100.0,
                "unrealized_pnl_r": 1.0,
                "max_loss_r": 1.0,
                "overrides_used": 0,
                "seconds_since_last_override": 60,
                "initial_stop_price": 90.0,
            },
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.MONITORED,
    )


@pytest.mark.asyncio
async def test_g7_judge_client_none_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    res = await adaptive_exit_gate(_g7_gate_ctx(), client=None, judge_client=None)
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.model_used == "python"
    assert "ai_judge" not in res.payload


@pytest.mark.asyncio
async def test_g7_active_judge_exit_now_output_cannot_force_cut(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """A hostile {"verdict":"EXIT_NOW"} judge output in ACTIVE mode cannot turn the
    deterministic G7 decision into a forced cut — exit stays on the Q9 rail."""
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    judge = _FakeGPT('{"verdict":"EXIT_NOW"}')
    res = await adaptive_exit_gate(
        _g7_gate_ctx(), client=None, judge_client=judge, shadow_conn=memdb
    )
    # The deterministic Q9 widen rail (ADJUST_EXIT) is preserved; EXIT_NOW parsed to
    # the safe PROTECT verdict, never a cut.
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.decision != GateDecision.EXIT_NOW
    assert res.payload["stop_price"] == 93.0
    assert res.payload["ai_judge"]["verdict"] == "PROTECT"
