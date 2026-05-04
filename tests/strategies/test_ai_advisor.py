"""tests/strategies/test_ai_advisor.py — AIAdvisor strategy tests.

TDD: runtime verify (lessons #46 — import pass != runtime pass).
Mock Anthropic/OpenAI clients — no actual API calls.

Test coverage:
- test_ai_advisor_long_signal_high_confidence: conf 0.8 → ENTER_LONG
- test_ai_advisor_exit_signal: action=exit → EXIT
- test_ai_advisor_caches_responses: same state hash → 1 API call
- test_ai_advisor_below_confidence_holds: conf 0.50 < 0.65 → HOLD
- test_ai_advisor_hold_action_holds: action=hold → HOLD
- test_ai_advisor_rate_limit_holds: 2nd call within 60s → HOLD (rate_limit)
- test_ai_advisor_api_error_holds: API exception → HOLD safely
- test_parse_response_valid_json: pure parse helper
- test_parse_response_invalid_json: fallback to HOLD
- test_parse_response_unknown_action: fallback to HOLD
- test_build_prompt_contains_ticker: pure prompt builder
- test_evaluate_candle_fallback_holds: fallback evaluate() → HOLD
- test_cost_estimation: _estimate_cost pure math
- test_state_hash_deterministic: same input → same hash
- test_state_hash_different: different state → different hash
- test_provider_auto_detect_anthropic: ANTHROPIC_API_KEY → provider=anthropic
- test_provider_auto_detect_openai: no ANTHROPIC key, OPENAI key → provider=openai
- test_anthropic_credit_fail_fallback_openai: credit error → OpenAI fallback
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.ai_advisor import (
    AIAdvisor,
    _build_prompt,
    _estimate_cost,
    _parse_response,
    _state_hash,
    ANTHROPIC_MODEL,
    OPENAI_MODEL,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_market_state(
    ticker: str = "BTC-USDT",
    last: float = 60_000.0,
    change_24h: float = 2.5,
    taker_buy_ratio: float = 0.72,
    regime: str = "bull",
) -> dict:
    return {
        "ticker": ticker,
        "last": last,
        "change_24h": change_24h,
        "rsi_1h": 58.0,
        "trend_4h": "up",
        "trend_1d": "up",
        "bid_depth_usd": 500_000.0,
        "ask_depth_usd": 400_000.0,
        "taker_buy_ratio": taker_buy_ratio,
        "vpin": 0.3,
        "funding_8h": -0.0002,
        "regime": regime,
    }


def _make_advisor(api_response: dict, api_key: str = "test-key") -> AIAdvisor:
    """Build AIAdvisor with mocked Anthropic client returning given response dict."""
    advisor = AIAdvisor(target_size_usd=200.0, min_confidence=0.65, api_key=api_key)
    # Force provider so tests are independent of env-var availability
    advisor.provider = "anthropic"
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=f'{{"action": "{api_response["action"]}", "confidence": {api_response["confidence"]}, "reason": "{api_response["reason"]}"}}')]
    mock_msg.usage = MagicMock(input_tokens=500, output_tokens=50)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    advisor._client = mock_client
    return advisor, mock_client


# ── Core signal tests ─────────────────────────────────────────────────────────

class TestLongSignal:
    def test_ai_advisor_long_signal_high_confidence(self) -> None:
        """action=long + confidence 0.8 → ENTER_LONG with correct size."""
        advisor, mock_client = _make_advisor(
            {"action": "long", "confidence": 0.8, "reason": "strong momentum"}
        )
        state = _make_market_state()
        sig = advisor.evaluate_ai(state)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.confidence == pytest.approx(0.8)
        assert sig.target_size_usd == 200.0
        assert "strong momentum" in sig.reason
        mock_client.messages.create.assert_called_once()

    def test_ai_advisor_long_exact_threshold_enters(self) -> None:
        """confidence == min_confidence (0.65) → ENTER_LONG (boundary inclusive)."""
        advisor, _ = _make_advisor(
            {"action": "long", "confidence": 0.65, "reason": "at threshold"}
        )
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.ENTER_LONG


class TestExitSignal:
    def test_ai_advisor_exit_signal(self) -> None:
        """action=exit → EXIT regardless of confidence."""
        advisor, _ = _make_advisor(
            {"action": "exit", "confidence": 0.9, "reason": "momentum reversal"}
        )
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.EXIT
        assert "momentum reversal" in sig.reason


class TestHoldSignal:
    def test_ai_advisor_below_confidence_holds(self) -> None:
        """action=long but confidence < min_confidence → HOLD."""
        advisor, _ = _make_advisor(
            {"action": "long", "confidence": 0.50, "reason": "weak setup"}
        )
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.HOLD

    def test_ai_advisor_hold_action_holds(self) -> None:
        """action=hold → HOLD."""
        advisor, _ = _make_advisor(
            {"action": "hold", "confidence": 0.55, "reason": "uncertain"}
        )
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.HOLD


# ── Cache tests ───────────────────────────────────────────────────────────────

class TestCaching:
    def test_ai_advisor_caches_responses(self) -> None:
        """Same market_state hash → only 1 API call (cache hit on 2nd call)."""
        advisor, mock_client = _make_advisor(
            {"action": "long", "confidence": 0.75, "reason": "cached"}
        )
        state = _make_market_state()
        # First call — hits API
        sig1 = advisor.evaluate_ai(state)
        # Second call with identical state — should use cache
        sig2 = advisor.evaluate_ai(state)
        # Only 1 API call total
        assert mock_client.messages.create.call_count == 1
        # Both signals identical
        assert sig1.action == sig2.action
        assert sig1.confidence == sig2.confidence

    def test_different_state_bypasses_cache(self) -> None:
        """Different market_state → new API call once rate limit expires."""
        advisor, mock_client = _make_advisor(
            {"action": "long", "confidence": 0.75, "reason": "fresh"}
        )
        state1 = _make_market_state(last=60_000.0)
        state2 = _make_market_state(last=61_000.0)  # different price → different hash
        advisor.evaluate_ai(state1)
        # Expire rate limit so state2 triggers a real API call
        advisor._last_call_ts["BTC-USDT"] = time.time() - 61.0
        advisor.evaluate_ai(state2)
        assert mock_client.messages.create.call_count == 2


# ── Rate limit tests ──────────────────────────────────────────────────────────

class TestRateLimit:
    def test_ai_advisor_rate_limit_holds(self) -> None:
        """2nd call within RATE_LIMIT_S → HOLD (rate_limit reason)."""
        advisor, mock_client = _make_advisor(
            {"action": "long", "confidence": 0.8, "reason": "test"}
        )
        state = _make_market_state()
        # First call succeeds
        advisor.evaluate_ai(state)
        # Inject a different state to bypass cache, but ticker is same → rate limited
        state2 = _make_market_state(last=99999.0)  # different price → different hash
        sig = advisor.evaluate_ai(state2)
        # Rate limit kicks in (same ticker, within 60s)
        assert sig.action == SignalAction.HOLD
        assert "rate_limit" in sig.reason
        # Still only 1 API call
        assert mock_client.messages.create.call_count == 1

    def test_rate_limit_expired_allows_call(self) -> None:
        """After RATE_LIMIT_S elapses, next call goes through."""
        advisor, mock_client = _make_advisor(
            {"action": "hold", "confidence": 0.5, "reason": "ok"}
        )
        state = _make_market_state()
        advisor.evaluate_ai(state)
        # Manually expire rate limit
        advisor._last_call_ts["BTC-USDT"] = time.time() - 61.0
        state2 = _make_market_state(last=99999.0)
        advisor.evaluate_ai(state2)
        # 2 API calls made
        assert mock_client.messages.create.call_count == 2


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_ai_advisor_api_error_holds(self) -> None:
        """API exception → HOLD with api_error reason (no crash)."""
        advisor = AIAdvisor(target_size_usd=200.0, api_key="test-key")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network timeout")
        advisor._client = mock_client
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.HOLD
        assert "api_error" in sig.reason

    def test_missing_api_key_raises(self) -> None:
        """No API key, provider=anthropic → RuntimeError on _build_client."""
        advisor = AIAdvisor(api_key="")
        advisor.provider = "anthropic"  # Force provider so _build_client checks Anthropic key
        advisor._client = None          # Ensure lazy-init path runs
        advisor._api_key = ""           # Ensure instance key is empty
        # Remove ANTHROPIC_API_KEY from env so _build_client sees no key
        env_patch = {"ANTHROPIC_API_KEY": ""}
        with patch.dict("os.environ", env_patch):
            import os as _os
            _os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY not set"):
                advisor._get_client()


# ── Fallback evaluate() ───────────────────────────────────────────────────────

class TestCandle:
    def test_evaluate_candle_fallback_holds(self) -> None:
        """evaluate(candles) → HOLD (AI path requires evaluate_ai)."""
        advisor = AIAdvisor()
        candle = Candle(
            timestamp_ms=1_000_000,
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0,
        )
        sig = advisor.evaluate([candle])
        assert sig.action == SignalAction.HOLD


# ── Pure helper tests ─────────────────────────────────────────────────────────

class TestParseResponse:
    def test_parse_response_valid_json(self) -> None:
        result = _parse_response('{"action": "long", "confidence": 0.8, "reason": "ok"}')
        assert result == {"action": "long", "confidence": 0.8, "reason": "ok"}

    def test_parse_response_invalid_json(self) -> None:
        result = _parse_response("not json at all")
        assert result["action"] == "hold"
        assert result["confidence"] == 0.0

    def test_parse_response_unknown_action(self) -> None:
        result = _parse_response('{"action": "SELL_SHORT", "confidence": 0.9, "reason": "short"}')
        assert result["action"] == "hold"

    def test_parse_response_confidence_clamped(self) -> None:
        result = _parse_response('{"action": "long", "confidence": 1.5, "reason": "over"}')
        assert result["confidence"] == 1.0

    def test_parse_response_markdown_wrapped(self) -> None:
        text = '```json\n{"action": "exit", "confidence": 0.7, "reason": "reversed"}\n```'
        result = _parse_response(text)
        assert result["action"] == "exit"

    def test_parse_response_reason_truncated(self) -> None:
        long_reason = "x" * 200
        result = _parse_response(f'{{"action": "hold", "confidence": 0.3, "reason": "{long_reason}"}}')
        assert len(result["reason"]) <= 100


class TestBuildPrompt:
    def test_build_prompt_contains_ticker(self) -> None:
        state = _make_market_state(ticker="ETH-USDT")
        prompt = _build_prompt(state)
        assert "ETH-USDT" in prompt

    def test_build_prompt_contains_price(self) -> None:
        state = _make_market_state(last=3500.0)
        prompt = _build_prompt(state)
        assert "3,500" in prompt or "3500" in prompt

    def test_build_prompt_requests_json(self) -> None:
        prompt = _build_prompt(_make_market_state())
        assert "JSON" in prompt or "json" in prompt.lower()

    def test_build_prompt_spot_only_rule(self) -> None:
        prompt = _build_prompt(_make_market_state())
        assert "SPOT" in prompt or "spot" in prompt.lower()


class TestStateHash:
    def test_state_hash_deterministic(self) -> None:
        state = _make_market_state()
        h1 = _state_hash(state)
        h2 = _state_hash(state)
        assert h1 == h2

    def test_state_hash_different_states(self) -> None:
        state1 = _make_market_state(last=60_000.0)
        state2 = _make_market_state(last=61_000.0)
        assert _state_hash(state1) != _state_hash(state2)

    def test_state_hash_is_string(self) -> None:
        h = _state_hash(_make_market_state())
        assert isinstance(h, str)
        assert len(h) == 32  # MD5 hex digest


class TestCostEstimation:
    def test_cost_estimation_positive(self) -> None:
        cost = _estimate_cost(500, 50)
        assert cost > 0.0
        # ~$0.0006 per call
        assert cost < 0.01

    def test_cost_zero_tokens(self) -> None:
        cost = _estimate_cost(0, 0)
        assert cost == 0.0


# ── Provider auto-detect + fallback tests ────────────────────────────────────

class TestProviderAutoDetect:
    def test_provider_auto_detect_anthropic(self) -> None:
        """ANTHROPIC_API_KEY set → provider=anthropic."""
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": ""},
            clear=False,
        ):
            advisor = AIAdvisor()
            assert advisor.provider == "anthropic"

    def test_provider_auto_detect_openai(self) -> None:
        """No ANTHROPIC_API_KEY, OPENAI_API_KEY set → provider=openai."""
        env = {"OPENAI_API_KEY": "sk-oai-test"}
        # Temporarily remove ANTHROPIC_API_KEY if present
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                advisor = AIAdvisor()
                assert advisor.provider == "openai"

    def test_provider_env_var_forces_openai(self) -> None:
        """AI_PROVIDER=openai forces OpenAI even if ANTHROPIC_API_KEY is set."""
        with patch.dict(
            "os.environ",
            {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "sk-oai-test", "ANTHROPIC_API_KEY": "sk-ant-test"},
            clear=False,
        ):
            advisor = AIAdvisor()
            assert advisor.provider == "openai"

    def test_provider_env_var_forces_anthropic(self) -> None:
        """AI_PROVIDER=anthropic forces Anthropic."""
        with patch.dict(
            "os.environ",
            {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-test"},
            clear=False,
        ):
            advisor = AIAdvisor()
            assert advisor.provider == "anthropic"


def _make_openai_response(text: str) -> MagicMock:
    """Build a mock openai ChatCompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = text
    return mock_resp


class TestOpenAIProvider:
    def test_openai_provider_long_signal(self) -> None:
        """OpenAI provider: action=long + confidence 0.75 → ENTER_LONG."""
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}, clear=False):
            advisor = AIAdvisor(target_size_usd=200.0, min_confidence=0.65)
        # Inject mock OpenAI client
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_openai_response(
            '{"action": "long", "confidence": 0.75, "reason": "strong bid"}'
        )
        advisor._client = mock_client
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.confidence == pytest.approx(0.75)
        mock_client.chat.completions.create.assert_called_once()

    def test_openai_provider_uses_correct_model(self) -> None:
        """OpenAI provider call uses OPENAI_MODEL (gpt-4o-mini)."""
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}, clear=False):
            advisor = AIAdvisor()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_openai_response(
            '{"action": "hold", "confidence": 0.3, "reason": "uncertain"}'
        )
        advisor._client = mock_client
        advisor.evaluate_ai(_make_market_state())
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == OPENAI_MODEL


class TestAnthropicCreditFallback:
    def test_anthropic_credit_fail_fallback_openai(self) -> None:
        """Anthropic credit error → auto-switch to OpenAI, retry succeeds → ENTER_LONG."""
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-oai-test", "AI_PROVIDER": ""},
            clear=False,
        ):
            advisor = AIAdvisor(target_size_usd=200.0, min_confidence=0.65)
            assert advisor.provider == "anthropic"

        # Mock Anthropic client that raises a credit error
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.side_effect = Exception("insufficient credit balance")
        advisor._client = mock_anthropic

        # Mock OpenAI client that succeeds
        mock_openai_resp = _make_openai_response(
            '{"action": "long", "confidence": 0.8, "reason": "fallback ok"}'
        )
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = mock_openai_resp

        with patch("src.strategies.ai_advisor.AIAdvisor._switch_to_openai_fallback") as mock_switch:
            def _do_switch(self_ref=None):
                advisor.provider = "openai"
                advisor._client = mock_openai
                return True
            mock_switch.side_effect = _do_switch

            sig = advisor.evaluate_ai(_make_market_state())

        assert sig.action == SignalAction.ENTER_LONG
        assert "fallback ok" in sig.reason

    def test_anthropic_non_credit_error_does_not_fallback(self) -> None:
        """Non-credit Anthropic error (e.g. network) → HOLD, no OpenAI fallback."""
        advisor = AIAdvisor(target_size_usd=200.0, api_key="sk-ant-test")
        advisor.provider = "anthropic"
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network timeout")
        advisor._client = mock_client

        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.HOLD
        assert "api_error" in sig.reason

    def test_anthropic_credit_error_no_openai_key_raises(self) -> None:
        """Anthropic credit error + no OPENAI_API_KEY → propagates (no silent swallow)."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            advisor = AIAdvisor(target_size_usd=200.0, api_key="sk-ant-test")
            advisor.provider = "anthropic"
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("credit balance insufficient")
        advisor._client = mock_client

        # Should return HOLD (evaluate_ai catches all exceptions at top level)
        sig = advisor.evaluate_ai(_make_market_state())
        assert sig.action == SignalAction.HOLD
        assert "api_error" in sig.reason
