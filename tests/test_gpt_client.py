"""Layer 2 — GPT client (OpenAI httpx wrapper) unit tests.

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md
- 2026-05-07 Anthropic→OpenAI migration (Jin mandate)

Test surface (mirrors the legacy ``_haiku_client.py`` contract):
- ``call_gpt`` happy path with mocked client
- ``call_gpt`` error capture (httpx.HTTPStatusError → graceful)
- ``StubGPTClient`` always-PASS
- ``default_gpt_factory`` returns ``GPTClient(model=GPT_P0_MODEL)``
- gpt-5 vs gpt-4 token-key dispatch
- API key never logged (sanity)
- Property: response.text always str
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPT_P0_MODEL,
    GPT_P1_MODEL,
    GPTCallResult,
    GPTClient,
    _flatten_system_blocks,
    _flatten_user_messages,
    _is_gpt5,
    _sanitize_error_text,
    _try_parse_json,
    call_gpt,
    default_gpt_factory,
    extract_text,
    make_system_prefix,
)
from polaris.scripts._smoke_gpt_stub import StubGPTClient

# ---------------------------------------------------------------------------
# Stub client (used in many tests)
# ---------------------------------------------------------------------------


class _SpyClient:
    """Records every ``messages.create`` call for assertion."""

    def __init__(self, response_text: str = '{"decision": "PASS"}') -> None:
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:  # noqa: ANN001
                outer.calls.append(kwargs)
                response_text = outer.response_text

                class _Block:
                    text = response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------


def test_default_factory_p0_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    client = default_gpt_factory()
    assert client.model == GPT_P0_MODEL
    assert isinstance(client, GPTClient)


def test_default_factory_explicit_p1_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    client = default_gpt_factory(model=GPT_P1_MODEL)
    assert client.model == GPT_P1_MODEL


def test_factory_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        default_gpt_factory()


def test_token_key_gpt5_vs_gpt4() -> None:
    assert _is_gpt5("gpt-5-mini")
    assert _is_gpt5("gpt-5.5")
    assert _is_gpt5("gpt-5.5-2026-04-23")
    assert _is_gpt5(GPT_P0_MODEL)
    assert _is_gpt5(GPT_P1_MODEL)
    assert not _is_gpt5("gpt-4o-mini")
    assert not _is_gpt5("gpt-4-turbo")


def test_make_system_prefix_returns_anthropic_block_shape() -> None:
    prefix = make_system_prefix(
        role="x", decision_enum=["PASS", "KILL"],
        cell_summary="c", baseline_summary="b", recent_trades_summary="r",
    )
    assert isinstance(prefix, list) and len(prefix) == 1
    assert prefix[0]["type"] == "text"
    assert "PASS, KILL" in prefix[0]["text"]
    # OpenAI shim does not need cache_control — must NOT include it.
    assert "cache_control" not in prefix[0]


def test_make_system_prefix_contains_demo_unlock() -> None:
    """Codex review nit: DEMO/PAPER context must be present so GPT does
    not default to a real-money safety bias and over-KILL signals.

    Mockup-validated: this clause shifts gpt-5-mini from ~50% PASS
    (control) to ~75% PASS while preserving 30-40% KILL on the
    originally-KILL replay set (variant B). See
    `tools/g3_prompt_mockup.py` and
    `data/paper/g3_mockup_*.json` for evidence.
    """
    prefix = make_system_prefix(
        role="Polaris Signal Validator",
        decision_enum=["PASS", "KILL", "MODIFY"],
        cell_summary="{}", baseline_summary="{}",
        recent_trades_summary="[]",
    )
    text = prefix[0]["text"]
    # DEMO/PAPER unlock language present (case-sensitive — guards against
    # accidental softening to "DEMO/paper" or similar regression).
    assert "DEMO/PAPER" in text
    assert "virtual" in text
    assert "false negatives" in text
    # No defensive / rejection keywords slip in.
    assert "reject" not in text.lower()
    assert "block" not in text.lower()


def test_flatten_system_blocks_handles_str_and_list() -> None:
    assert _flatten_system_blocks("plain") == "plain"
    assert _flatten_system_blocks([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert _flatten_system_blocks(None) == ""


def test_flatten_user_messages_handles_string_and_block_content() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": [{"type": "text", "text": "more"}]},
    ]
    assert _flatten_user_messages(msgs) == "hi\nmore"


def test_extract_text_handles_anthropic_and_dict_shape() -> None:
    class _Block:
        text = "hello"

    class _Resp:
        content = [_Block()]

    assert extract_text(_Resp()) == "hello"
    assert extract_text({"content": [{"text": "world"}]}) == "world"
    assert extract_text(None) == ""


def test_try_parse_json_strips_code_fences() -> None:
    assert _try_parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _try_parse_json('```\n{"a": 2}\n```') == {"a": 2}
    assert _try_parse_json("not json") is None
    assert _try_parse_json("[1, 2, 3]") is None  # only dict accepted


# ---------------------------------------------------------------------------
# call_gpt happy path + error capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_gpt_happy_path_returns_parsed_json() -> None:
    client = _SpyClient(response_text='{"decision": "PASS"}')
    res = await call_gpt(
        client=client,
        system_prefix=make_system_prefix(
            role="r", decision_enum=["PASS"],
            cell_summary="", baseline_summary="", recent_trades_summary="",
        ),
        user_prompt="go",
        max_tokens=50,
    )
    assert isinstance(res, GPTCallResult)
    assert res.error is None
    assert res.parsed == {"decision": "PASS"}
    assert isinstance(res.text, str)
    assert res.latency_ms >= 0
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_call_gpt_credit_error_handled() -> None:
    """httpx.HTTPStatusError must NOT propagate — caller decides fail policy."""

    class _BoomClient:
        class messages:
            @staticmethod
            async def create(**_kw: Any) -> Any:
                resp = httpx.Response(402, json={"error": "credit balance"})
                raise httpx.HTTPStatusError(
                    "402 Payment Required",
                    request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                    response=resp,
                )

    res = await call_gpt(
        client=_BoomClient(),
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
    )
    assert res.error is not None
    assert res.parsed is None
    assert res.text == ""
    assert "402" in res.error or "Payment" in res.error


@pytest.mark.asyncio
async def test_call_gpt_non_dict_json_returns_none_parsed() -> None:
    client = _SpyClient(response_text='[1,2,3]')
    res = await call_gpt(
        client=client,
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
    )
    assert res.error is None
    assert res.parsed is None  # only dict accepted
    assert res.text == "[1,2,3]"


# ---------------------------------------------------------------------------
# StubGPTClient (smoke fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_gpt_always_pass_validator_prompt() -> None:
    stub = StubGPTClient()
    resp = await stub.messages.create(
        model="any",
        max_tokens=100,
        system="Polaris Signal Validator — PASS / KILL / MODIFY only.",
        messages=[{"role": "user", "content": "validate this"}],
    )
    text = resp.content[0].text
    parsed = json.loads(text)
    assert parsed["decision"] == "PASS"


@pytest.mark.asyncio
async def test_stub_gpt_returns_proceed_for_watcher_prompt() -> None:
    stub = StubGPTClient()
    resp = await stub.messages.create(
        model="any",
        max_tokens=100,
        system="Polaris Pre-Entry Watcher — PROCEED or KILL only.",
        messages=[{"role": "user", "content": "watcher prompt PROCEED|KILL"}],
    )
    text = resp.content[0].text
    parsed = json.loads(text)
    # Watcher prompts contain both PROCEED and KILL but NOT PASS.
    assert parsed["decision"] == "PROCEED"


@pytest.mark.asyncio
async def test_stub_gpt_call_count_increments() -> None:
    stub = StubGPTClient()
    assert stub.call_count == 0
    await stub.messages.create(model="x", system="", messages=[{"role": "user", "content": "a"}])
    await stub.messages.create(model="x", system="", messages=[{"role": "user", "content": "b"}])
    assert stub.call_count == 2


# ---------------------------------------------------------------------------
# Property + security
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_text_always_str_property() -> None:
    """Empty / None content paths must still yield a str (never raise)."""

    class _EmptyClient:
        class messages:
            @staticmethod
            async def create(**_kw: Any) -> Any:
                class _R:
                    content: list[Any] = []
                    usage = None

                return _R()

    res = await call_gpt(
        client=_EmptyClient(),
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
    )
    assert isinstance(res.text, str)
    assert res.text == ""


def test_api_key_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security: GPTClient.__repr__ must not leak the api_key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-DO-NOT-LEAK")
    client = default_gpt_factory()
    assert "sk-secret-DO-NOT-LEAK" not in repr(client)
    assert client.model in repr(client)


# ---------------------------------------------------------------------------
# codex 2026-05-07 P1 fixes
# ---------------------------------------------------------------------------


def test_sanitize_error_text_redacts_sk_keys() -> None:
    """P1.3: API-key fragments must be redacted before persistence."""
    text = "RuntimeError: bad call sk-proj-Abc123XYZ leaked into the stack"
    out = _sanitize_error_text(text)
    assert "sk-proj-Abc123XYZ" not in out
    assert "sk-REDACTED" in out


def test_sanitize_error_text_redacts_bearer_tokens() -> None:
    text = "401 with header: Authorization: Bearer eyJhbGciOiJIUzI1NiI"
    out = _sanitize_error_text(text)
    assert "eyJhbGciOiJIUzI1NiI" not in out
    assert "Bearer REDACTED" in out


def test_sanitize_error_text_passthrough_when_no_secret() -> None:
    text = "HTTPStatusError: 402 Payment Required"
    assert _sanitize_error_text(text) == text


def test_sanitize_error_text_handles_empty() -> None:
    assert _sanitize_error_text("") == ""


@pytest.mark.asyncio
async def test_call_gpt_forwards_reasoning_effort_kwarg() -> None:
    """P1.1: caller can override reasoning_effort via call_gpt(...)."""
    client = _SpyClient(response_text='{"decision": "PASS"}')
    await call_gpt(
        client=client,
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
        reasoning_effort="high",
    )
    assert len(client.calls) == 1
    assert client.calls[0].get("reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_call_gpt_default_does_not_set_reasoning_effort_kwarg() -> None:
    """When the caller passes None (default), the kwarg is not forwarded.

    The OpenAI shim adds its own ``minimal`` default; ``call_gpt`` does not
    duplicate that — it only forwards when the caller explicitly overrides.
    """
    client = _SpyClient(response_text='{"decision": "PASS"}')
    await call_gpt(
        client=client,
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
    )
    assert "reasoning_effort" not in client.calls[0]


@pytest.mark.asyncio
async def test_call_gpt_error_path_sanitizes_api_key() -> None:
    """P1.3: if an exception body contains an sk-... fragment, redact it."""

    class _LeakClient:
        class messages:
            @staticmethod
            async def create(**_kw: Any) -> Any:
                raise RuntimeError("api auth: sk-proj-LeakyKey-12345 expired")

    res = await call_gpt(
        client=_LeakClient(),
        system_prefix=[{"type": "text", "text": "x"}],
        user_prompt="y",
    )
    assert res.error is not None
    assert "sk-proj-LeakyKey-12345" not in res.error
    assert "sk-REDACTED" in res.error


# ---------------------------------------------------------------------------
# Real OpenAI call — env-flag-gated (only runs when POLARIS_GPT_LIVE=1).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("POLARIS_GPT_LIVE") != "1",
    reason="set POLARIS_GPT_LIVE=1 to enable live OpenAI hit",
)
@pytest.mark.asyncio
async def test_gpt_complete_real_api() -> None:
    """Live smoke against OpenAI — costs a few tokens.

    Validates the wire format end-to-end (httpx POST → /v1/chat/completions →
    OpenAIResponseAdapter → call_gpt → GPTCallResult). Only runs when the
    operator opts in via POLARIS_GPT_LIVE=1 in the environment.
    """
    client = default_gpt_factory(model=GPT_P0_MODEL)
    res = await call_gpt(
        client=client,
        system_prefix=[
            {"type": "text", "text": "Reply with literal JSON: {\"decision\": \"PASS\"}"}
        ],
        user_prompt="Reply now.",
        max_tokens=20,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    assert res.error is None, f"live call failed: {res.error}"
    assert isinstance(res.text, str)
    assert res.latency_ms > 0
