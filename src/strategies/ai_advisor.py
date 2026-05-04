"""AI Advisor Strategy — Claude API (or OpenAI fallback) 실시간 시장 분석 (P6 hybrid).

Jin mandate: '원래 의도는 AI 개입 실시간 분석으로 거래'.
모태 (auto_invasion_mk1) advisor 핵심 패턴 인수 — Polaris SPOT 전용 재설계.

Pure core (evaluate_ai, _build_prompt, _parse_response):
  - market_state dict → Signal (deterministic given same API response)
  - No I/O: cache check uses time.time() but is read-only against module state

Shell boundary (Anthropic/OpenAI client, _cache write):
  - API call (I/O) — Anthropic primary, OpenAI fallback on credit error
  - _cache dict 갱신 (side effect)

Provider logic (AI_PROVIDER env var or auto-detect):
  - AI_PROVIDER=anthropic → force Anthropic
  - AI_PROVIDER=openai   → force OpenAI
  - (unset)              → ANTHROPIC_API_KEY? Anthropic : OPENAI_API_KEY? OpenAI : None
  - Anthropic credit/balance error → auto-fallback to OpenAI (if OPENAI_API_KEY set)

Cost profile:
  - Anthropic: claude-haiku-4-5-20251001, ~$0.0006/call
  - OpenAI fallback: gpt-4o-mini, ~$0.0002/call
  - 1 call per (ticker × 60s) = 3 tickers × 60 calls/h = 180 calls/h

Rate limit: 1 API call per (ticker, hypo_id) per 60s (enforced via _last_call_ts).
Cache: same market_state hash → cached 5min (API cost 절감).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
OPENAI_MODEL = "gpt-4o-mini"
AI_MODEL = ANTHROPIC_MODEL   # Backwards-compat alias (cost tracker uses this)
CACHE_TTL_S = 300          # 5min cache per unique market state hash
RATE_LIMIT_S = 60          # 1 call per ticker per 60s
MAX_TOKENS = 200           # Response bounded (JSON only — no need for 4K)
DEFAULT_MIN_CONFIDENCE = 0.72  # Tuned: 0.75 → too strict (LONG 0.3%), 0.72 targets 5-15% LONG
DEFAULT_TARGET_SIZE_USD = 200.0
COST_LOG_INTERVAL_S = 3600.0  # Hourly cost report
DECISION_STATS_LOG_INTERVAL_S = 3600.0  # Hourly bias distribution log

# Error substrings that signal Anthropic credit exhaustion → trigger OpenAI fallback
_CREDIT_ERROR_SUBSTRINGS = ("credit", "balance", "quota", "insufficient", "overloaded")

# ── Module-level cost tracker ─────────────────────────────────────────────────

_cost_tracker: dict = {
    "total_calls": 0,
    "total_cost_usd": 0.0,
    "window_calls": 0,
    "window_start": 0.0,
}

# ── Module-level decision distribution tracker ────────────────────────────────

_ai_decision_counts: dict[str, int] = {"long": 0, "hold": 0, "exit": 0}
_ai_stats_window_start: float = 0.0

# Rough per-token costs (Haiku 4.5 pricing as of 2026-05)
_COST_PER_INPUT_TOKEN = 0.0008 / 1000   # $0.0008 per 1K input
_COST_PER_OUTPUT_TOKEN = 0.004 / 1000   # $0.004 per 1K output


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate API call cost in USD. Pure."""
    return (input_tokens * _COST_PER_INPUT_TOKEN
            + output_tokens * _COST_PER_OUTPUT_TOKEN)


def _track_call(input_tokens: int = 500, output_tokens: int = 50) -> None:
    """Record API call in cost tracker. Shell — mutates module-level dict."""
    cost = _estimate_cost(input_tokens, output_tokens)
    _cost_tracker["total_calls"] += 1
    _cost_tracker["total_cost_usd"] += cost
    _cost_tracker["window_calls"] += 1
    now = time.time()
    if _cost_tracker["window_start"] == 0.0:
        _cost_tracker["window_start"] = now
    elapsed = now - _cost_tracker["window_start"]
    if elapsed >= COST_LOG_INTERVAL_S:
        rate = _cost_tracker["window_calls"] / max(elapsed / 3600, 0.001)
        hourly_cost = rate * _estimate_cost(500, 50)
        logger.info(
            f"[AI-COST] 1h window: {_cost_tracker['window_calls']} calls "
            f"({rate:.1f}/h) est ${hourly_cost:.3f}/h "
            f"| cumulative ${_cost_tracker['total_cost_usd']:.3f} "
            f"({_cost_tracker['total_calls']} calls)"
        )
        _cost_tracker["window_calls"] = 0
        _cost_tracker["window_start"] = now


def _track_decision(action: str) -> None:
    """Record AI decision in distribution counter and emit hourly stats log. Shell.

    Args:
        action: One of 'long', 'hold', 'exit'.
    """
    global _ai_stats_window_start
    if action in _ai_decision_counts:
        _ai_decision_counts[action] += 1
    now = time.time()
    if _ai_stats_window_start == 0.0:
        _ai_stats_window_start = now
    elapsed = now - _ai_stats_window_start
    if elapsed >= DECISION_STATS_LOG_INTERVAL_S:
        total = sum(_ai_decision_counts.values())
        if total > 0:
            long_pct = 100 * _ai_decision_counts["long"] / total
            hold_pct = 100 * _ai_decision_counts["hold"] / total
            exit_pct = 100 * _ai_decision_counts["exit"] / total
            bias_warn = " ⚠ LONG BIAS DETECTED" if long_pct > 60 else ""
            logger.info(
                f"[AI-STATS] last 1h distribution: "
                f"LONG={long_pct:.0f}% ({_ai_decision_counts['long']}) "
                f"HOLD={hold_pct:.0f}% ({_ai_decision_counts['hold']}) "
                f"EXIT={exit_pct:.0f}% ({_ai_decision_counts['exit']})"
                f"{bias_warn}"
            )
        _ai_decision_counts["long"] = 0
        _ai_decision_counts["hold"] = 0
        _ai_decision_counts["exit"] = 0
        _ai_stats_window_start = now


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _state_hash(market_state: dict) -> str:
    """Deterministic MD5 hash of market_state for cache key. Pure."""
    return hashlib.md5(
        json.dumps(market_state, sort_keys=True, default=str).encode()
    ).hexdigest()


def _build_prompt(market_state: dict) -> str:
    """Build Claude prompt from market_state dict. Pure.

    Args:
        market_state: keys: ticker, last, change_24h, rsi_1h, trend_4h,
            trend_1d, bid_depth_usd, ask_depth_usd, taker_buy_ratio, vpin,
            funding_8h, regime.

    Returns:
        Formatted prompt string for Claude API.
    """
    ticker = market_state.get("ticker", "UNKNOWN")
    last = market_state.get("last", 0.0)
    change_24h = market_state.get("change_24h", 0.0)
    rsi_1h = market_state.get("rsi_1h", "N/A")
    trend_4h = market_state.get("trend_4h", "unknown")
    trend_1d = market_state.get("trend_1d", "unknown")
    bid_depth = market_state.get("bid_depth_usd", 0.0)
    ask_depth = market_state.get("ask_depth_usd", 0.0)
    taker_buy = market_state.get("taker_buy_ratio", 0.5)
    vpin = market_state.get("vpin", 0.0)
    funding = market_state.get("funding_8h", 0.0)
    regime = market_state.get("regime", "unknown")

    return (
        f"You are a quantitative analyst evaluating {ticker} for paper trading.\n"
        f"Current market state:\n"
        f"- Price: ${last:,.4f} | 24h change: {change_24h:+.2f}%\n"
        f"- Multi-TF: 1H RSI={rsi_1h}, 4H trend={trend_4h}, 1D trend={trend_1d}\n"
        f"- Order book: bid_depth=${bid_depth:,.0f}, ask_depth=${ask_depth:,.0f}\n"
        f"- Recent flow: taker_buy_ratio={taker_buy:.2f}, vpin={vpin:.2f}\n"
        f"- Funding rate: {funding * 100:+.3f}% (8h)\n"
        f"- Market regime: {regime}\n"
        f"\n"
        f"Respond with JSON only (no markdown, no explanation):\n"
        f'{{\"action\": \"long\" | \"exit\" | \"hold\", '
        f'\"confidence\": 0.0-1.0, \"reason\": \"<50 chars\"}}\n'
        f"\n"
        f"Decision rules:\n"
        f"- SPOT only (no short selling)\n"
        f"- Fee: 0.14% round-trip — edge must exceed this\n"
        f"- TP ~1.5% / SL ~0.7% (asymmetric profile)\n"
        f"- Choose 'long' ONLY if confidence >= 0.72 and setup shows a clear edge\n"
        f"- Choose 'exit' to close existing position if momentum clearly reverses\n"
        f"- Default to 'hold' when uncertain, sideways, or mixed signals\n"
        f"- Most situations = 'hold'. Only LONG on high conviction.\n"
    )


def _parse_response(text: str) -> dict:
    """Parse Claude response JSON → action/confidence/reason dict. Pure.

    Handles:
    - Valid JSON: {"action": "long", "confidence": 0.72, "reason": "..."}
    - Markdown-wrapped JSON (strips ```json ... ```)
    - Partial JSON (falls back to safe HOLD)

    Args:
        text: Raw text from Claude API response.

    Returns:
        dict with keys: action (str), confidence (float), reason (str).
        Always safe — never raises. Defaults to HOLD on any parse failure.
    """
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()
    try:
        data = json.loads(text)
        action = str(data.get("action", "hold")).lower()
        if action not in ("long", "exit", "hold"):
            action = "hold"
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        reason = str(data.get("reason", ""))[:100]
        return {"action": action, "confidence": confidence, "reason": reason}
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"[AI-ADVISOR] Response parse failed ({e}): {text[:80]!r}")
        return {"action": "hold", "confidence": 0.0, "reason": "parse_error"}


# ── AIAdvisor Strategy ────────────────────────────────────────────────────────

class AIAdvisor(Strategy):
    """Claude AI-driven entry/exit advisor.

    pure: false (shell — Anthropic API I/O)
    code_path: src/strategies/ai_advisor.py
    test_path: tests/strategies/test_ai_advisor.py

    Shell responsibilities:
    - Anthropic client instantiation (API key from env)
    - Cache dict write (_cache)
    - Rate limit tracking (_last_call_ts)
    - Cost tracking (via _track_call)

    Pure responsibilities:
    - _build_prompt: market_state → prompt str
    - _parse_response: text → dict
    - Decision logic: dict → Signal
    """

    name = "ai_advisor"
    min_window = 1   # fallback evaluate() uses candles but ai-path doesn't need many

    def __init__(
        self,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        api_key: Optional[str] = None,
    ):
        self.target_size_usd = target_size_usd
        self.min_confidence = min_confidence
        # Cache: hash → (timestamp, parsed_response dict)
        self._cache: dict[str, tuple[float, dict]] = {}
        # Rate limit: ticker → last API call timestamp
        self._last_call_ts: dict[str, float] = {}
        # Lazy-init client to avoid import failures when library not installed
        self._client = None

        # ── Provider resolution (auto-detect if AI_PROVIDER unset) ──────────
        provider = os.environ.get("AI_PROVIDER", "").lower().strip()
        if not provider:
            if os.environ.get("ANTHROPIC_API_KEY"):
                provider = "anthropic"
            elif os.environ.get("OPENAI_API_KEY"):
                provider = "openai"
            # else: no keys → provider="" → _client stays None
        self.provider: str = provider

        # Store API key for the resolved provider (api_key param → Anthropic only)
        if provider == "anthropic":
            self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        elif provider == "openai":
            self._api_key = os.environ.get("OPENAI_API_KEY", "")
        else:
            self._api_key = api_key or ""

    def _get_client(self):
        """Lazy-init API client (Anthropic or OpenAI). Shell — I/O."""
        if self._client is None:
            self._client = self._build_client(self.provider)
        return self._client

    def _build_client(self, provider: str):
        """Instantiate provider client. Shell — import + env access."""
        if provider == "anthropic":
            try:
                from anthropic import Anthropic  # type: ignore[import]
            except ImportError as e:
                raise RuntimeError(
                    "anthropic package not installed — run: pip install anthropic"
                ) from e
            key = self._api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            return Anthropic(api_key=key)
        elif provider == "openai":
            try:
                from openai import OpenAI  # type: ignore[import]
            except ImportError as e:
                raise RuntimeError(
                    "openai package not installed — run: pip install openai"
                ) from e
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not set")
            return OpenAI(api_key=key)
        else:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

    def _switch_to_openai_fallback(self) -> bool:
        """Switch provider to OpenAI if OPENAI_API_KEY is available.

        Returns True if switch succeeded, False if no key available.
        Shell — mutates self.provider / self._client.
        """
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            return False
        try:
            from openai import OpenAI  # type: ignore[import]
        except ImportError:
            return False
        self.provider = "openai"
        self._api_key = openai_key
        self._client = OpenAI(api_key=openai_key)
        logger.warning("[AI-ADVISOR] Anthropic credit 부족 → OpenAI gpt-4o-mini fallback")
        return True

    def evaluate(self, window: list[Candle]) -> Signal:
        """Candle-based fallback — returns HOLD (use evaluate_ai for AI signal).

        Satisfies Strategy ABC contract. Realtime runner uses evaluate_ai instead.
        """
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="ai_advisor: use evaluate_ai with market_state",
        )

    def evaluate_ai(self, market_state: dict) -> Signal:
        """Main entry — Claude API analysis → Signal.

        Shell function (API I/O, cache write).

        Args:
            market_state: dict with keys: ticker, last, change_24h,
                rsi_1h, trend_4h, trend_1d, bid_depth_usd, ask_depth_usd,
                taker_buy_ratio, vpin, funding_8h, regime.

        Returns:
            Signal with action ENTER_LONG / EXIT / HOLD.

        Raises:
            RuntimeError: if ANTHROPIC_API_KEY not set and no mock client.
        """
        ticker = market_state.get("ticker", "UNKNOWN")
        now = time.time()
        ts_ms = int(now * 1000)

        # Cache check first: same market state → reuse cached response (5min TTL).
        # Cache takes priority over rate limit — if we already have the answer,
        # return it without an API call (no rate limit penalty either).
        state_hash = _state_hash(market_state)
        cached = self._cache.get(state_hash)
        if cached is not None:
            cache_ts, response = cached
            if now - cache_ts < CACHE_TTL_S:
                logger.debug(f"[AI-ADVISOR] {ticker} cache hit (age {now - cache_ts:.0f}s)")
                return self._response_to_signal(response, ts_ms)

        # Rate limit: 1 API call per ticker per 60s (applied only when cache misses)
        last_call = self._last_call_ts.get(ticker, 0.0)
        if now - last_call < RATE_LIMIT_S:
            remaining = RATE_LIMIT_S - (now - last_call)
            logger.debug(f"[AI-ADVISOR] {ticker} rate-limited ({remaining:.0f}s remaining)")
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"rate_limit:{remaining:.0f}s",
            )

        # API call
        prompt = _build_prompt(market_state)
        try:
            raw_text = self._call_api(prompt)
        except Exception as e:
            logger.error(f"[AI-ADVISOR] {ticker} API error: {e}")
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"api_error:{type(e).__name__}",
            )

        response = _parse_response(raw_text)
        # Update cache and rate limit
        self._cache[state_hash] = (now, response)
        self._last_call_ts[ticker] = now

        signal = self._response_to_signal(response, ts_ms)
        logger.info(
            f"[AI-DECISION] {ticker} → {response['action'].upper()} "
            f"conf={response['confidence']:.2f} reason='{response['reason']}'"
        )
        return signal

    def _call_api(self, prompt: str) -> str:
        """Call current provider API and return raw response text. Shell — I/O.

        On Anthropic credit/balance error, auto-switches to OpenAI and retries once.

        Args:
            prompt: Formatted prompt string.

        Returns:
            Raw text response from API.

        Raises:
            Exception: Propagated if both providers fail or fallback unavailable.
        """
        client = self._get_client()

        if self.provider == "anthropic":
            try:
                msg = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_text = msg.content[0].text if msg.content else ""
                input_tokens = getattr(msg.usage, "input_tokens", 500) if hasattr(msg, "usage") else 500
                output_tokens = getattr(msg.usage, "output_tokens", 50) if hasattr(msg, "usage") else 50
                _track_call(input_tokens, output_tokens)
                return raw_text
            except Exception as e:
                err_lower = str(e).lower()
                is_credit_err = any(s in err_lower for s in _CREDIT_ERROR_SUBSTRINGS)
                if is_credit_err and self._switch_to_openai_fallback():
                    # Retry with OpenAI
                    return self._call_api(prompt)
                raise

        elif self.provider == "openai":
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = resp.choices[0].message.content or ""
            _track_call(500, 50)  # Approximate; OpenAI token usage not always available
            return raw_text

        else:
            raise RuntimeError(f"Unknown provider: {self.provider!r}")

    def _response_to_signal(self, response: dict, ts_ms: int) -> Signal:
        """Convert parsed response dict → Signal. Tracks decision distribution.

        Args:
            response: dict from _parse_response (action, confidence, reason).
            ts_ms: current timestamp in ms.

        Returns:
            Signal with appropriate action.
        """
        action_str = response["action"]
        confidence = response["confidence"]
        reason = response["reason"]

        if action_str == "long" and confidence >= self.min_confidence:
            _track_decision("long")
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=reason[:100],
            )
        if action_str == "exit":
            _track_decision("exit")
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.EXIT,
                confidence=confidence,
                reason=reason[:100],
            )
        # hold or long below confidence threshold
        _track_decision("hold")
        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.HOLD,
            confidence=confidence,
            reason=reason[:100],
        )
