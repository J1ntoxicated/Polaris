"""Layer 2 — Shared GPT client (OpenAI Chat Completions, async via httpx).

Spec source: vault/30_components/layer-2-per-gate-pipeline.md (Q6 prompt design).

Migration note (Jin mandate 2026-05-07): replaces ``_haiku_client.py``.
Anthropic API = Claude Code 개발용만, code 내부 LLM = OpenAI GPT.
- Codex CLI review = 그대로 codex (gpt-5.5)
- ANTHROPIC_API_KEY 코드 내부에서 사용 X

Model split (per ADR-004 §Phase + Jin 2026-05-07 model update):
- **P0 gates (G1/G3/G4)** cheap fast: ``gpt-5-mini``
- **P1 gates (G6/G7/G8)** heavy 결정: ``gpt-5.5``

The wrapper exposes an ``Anthropic-shaped`` client so every existing
``client.messages.create(...)`` call in the gate agents keeps working
unchanged. Internally we POST to ``/v1/chat/completions`` via httpx
(no ``openai`` SDK dependency — Polaris standard pattern).

OpenAI does NOT support Anthropic-style 5-min ephemeral prompt cache;
``make_system_prefix`` therefore returns a plain content list (no
``cache_control``) and we rely on ``reasoning_effort=minimal`` /
short prompts for latency control.

Tests inject ``client_factory`` to mock; production picks up
``OPENAI_API_KEY`` from ``.env``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

__all__ = [
    "DEFAULT_TIMEOUT_SEC",
    "GPT_BASE_URL",
    "GPT_P0_MODEL",
    "GPT_P1_MODEL",
    "GPTCallResult",
    "GPTCallSpy",
    "GPTClient",
    "GPTClientFactory",
    "call_gpt",
    "default_gpt_factory",
    "extract_text",
    "make_system_prefix",
]

# Latest GPT models (Jin verify 2026-05-07: gpt-5.5 + gpt-5.5-2026-04-23 work;
# gpt-5.5-mini does NOT exist — fall back to gpt-5-mini for P0).
GPT_P0_MODEL: Final[str] = "gpt-5-mini"
GPT_P1_MODEL: Final[str] = "gpt-5.5"

GPT_BASE_URL: Final[str] = "https://api.openai.com/v1"
# Per-call timeout (sec) — keeps each gate under the L2 latency budget.
DEFAULT_TIMEOUT_SEC: Final[float] = 30.0

GPTClientFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class GPTCallResult:
    """Wrapped GPT response (text + telemetry).

    Shape mirrors the legacy ``HaikuCallResult`` so caller code (validator,
    watcher, reflector, scanner) is import-rename only — no field rewires.
    """

    text: str
    parsed: dict[str, Any] | None
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0  # OpenAI returns 0 unless prompt-caching is auto-applied
    model: str = GPT_P0_MODEL
    error: str | None = None


def make_system_prefix(
    *,
    role: str,
    decision_enum: list[str],
    cell_summary: str,
    baseline_summary: str,
    recent_trades_summary: str,
) -> list[dict[str, Any]]:
    """Build the shared system prefix.

    Returns an Anthropic-shaped content-block list so the existing call sites
    pass it untouched to ``client.messages.create(system=...)``. The internal
    OpenAI shim flattens it back to a single ``role:'system'`` chat message.

    DEMO/PAPER context (Jin mandate 2026-05-07, mockup variant B picked):
    Polaris v2 runs on OKX SPOT demo + Capital CFD demo, both simulated
    venues with virtual capital. Without the DEMO clause GPT defaults to a
    real-money safety bias and over-KILLs (mockup measured 50% PASS / 35%
    KILL on control vs 75% PASS / 20% KILL with the DEMO clause —
    aggressive-bias-aligned, KILL discriminator preserved at 40% on
    originally-KILL replay set).
    """
    body = (
        f"# Role\n{role}\n"
        "**This is DEMO/PAPER trading on OKX simulated environment. "
        "Capital is virtual. Real-money safety arguments are INVALID — "
        "false negatives (skipping good trades) cost more than false "
        "positives.**\n"
        f"# Decision enum\n{', '.join(decision_enum)}\n"
        f"# Cell matrix snapshot\n{cell_summary}\n"
        f"# Ticker baseline\n{baseline_summary}\n"
        f"# Recent trades\n{recent_trades_summary}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}]


def _flatten_system_blocks(system: Any) -> str:
    """Collapse Anthropic-shape system blocks into a single string."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        pieces: list[str] = []
        for b in system:
            if isinstance(b, dict) and "text" in b:
                pieces.append(str(b["text"]))
            elif isinstance(b, str):
                pieces.append(b)
        return "\n".join(pieces)
    return str(system)


def _flatten_user_messages(messages: Any) -> str:
    """Concatenate Anthropic-shape user messages into one prompt string."""
    if not messages:
        return ""
    out: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and "text" in b:
                    out.append(str(b["text"]))
                elif isinstance(b, str):
                    out.append(b)
    return "\n".join(out)


def _is_gpt5(model: str) -> bool:
    """gpt-5.x family uses ``max_completion_tokens`` and default temperature."""
    return model.lower().startswith("gpt-5")


def _resolve_reasoning_effort(model: str, requested: str | None) -> str:
    """Resolve the reasoning_effort value for a gpt-5.x model.

    gpt-5.5 dropped ``'minimal'`` (returns 400 "does not support 'minimal'";
    supported: none/low/medium/high/xhigh) so we map minimal→``'none'`` for that
    family (equivalent: skip chain-of-thought so the token budget goes to the
    answer). gpt-5-mini (P0 gates) still accepts ``'minimal'``. Explicit non-
    minimal overrides are never rewritten. (forensic 2026-05-29 P0.)
    """
    effort = str(requested or "minimal")
    if effort == "minimal" and "5.5" in model.lower():
        return "none"
    return effort


class _GPTMessages:
    """``client.messages.create(...)`` shim — Anthropic-shape wrapper around OpenAI."""

    def __init__(self, parent: GPTClient) -> None:
        self._parent = parent

    async def create(self, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or self._parent.model)
        max_tokens = int(kwargs.get("max_tokens") or 400)
        temperature = float(kwargs.get("temperature", 0.0))
        timeout = float(kwargs.get("timeout") or DEFAULT_TIMEOUT_SEC)

        system_text = _flatten_system_blocks(kwargs.get("system"))
        user_text = _flatten_user_messages(kwargs.get("messages"))

        chat_messages: list[dict[str, Any]] = []
        if system_text:
            chat_messages.append({"role": "system", "content": system_text})
        chat_messages.append({"role": "user", "content": user_text})

        # gpt-5.x uses max_completion_tokens; gpt-4.x uses max_tokens.
        token_key = "max_completion_tokens" if _is_gpt5(model) else "max_tokens"
        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            token_key: max_tokens,
        }
        if _is_gpt5(model):
            # gpt-5.x: ``reasoning_effort='minimal'`` skips chain-of-thought
            # token consumption so the entire ``max_completion_tokens`` budget
            # goes to the actual JSON answer (otherwise reasoning_tokens
            # silently eat the cap and ``content`` returns empty string).
            # Caller may override via ``reasoning_effort`` kwarg. gpt-5.5 dropped
            # 'minimal' → mapped to 'none' (see _resolve_reasoning_effort).
            payload["reasoning_effort"] = _resolve_reasoning_effort(
                model, kwargs.get("reasoning_effort")
            )
        else:
            # gpt-4.x accepts a custom temperature; gpt-5.x does not.
            payload["temperature"] = temperature

        url = f"{GPT_BASE_URL}/chat/completions"
        api_key = self._parent.api_key
        # NOTE: never log the api_key; only the bearer header carries it.
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return _OpenAIResponseAdapter(data)


@dataclass(slots=True)
class _Block:
    """Anthropic ``ContentBlock`` shim with a ``.text`` attribute."""

    text: str


@dataclass(slots=True)
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0


class _OpenAIResponseAdapter:
    """Adapt OpenAI chat-completion JSON to the Anthropic ``Message`` shape."""

    def __init__(self, data: dict[str, Any]) -> None:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message", {}) or {}
            text = str(msg.get("content") or "")
        else:
            text = ""
        self.content = [_Block(text=text)]
        usage = data.get("usage") or {}
        self.usage = _Usage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_input_tokens=0,
        )
        # Preserve raw payload for debugging (never logged by default).
        self._raw = data


class GPTClient:
    """Async OpenAI Chat Completions client wrapped in an Anthropic-shaped API.

    Exposes ``client.messages.create(...)`` so gate agents keep their existing
    call shape; internally POSTs to ``/v1/chat/completions`` via httpx.
    """

    def __init__(self, *, api_key: str | None = None, model: str = GPT_P0_MODEL) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing from environment")
        # Stored privately so __repr__ can omit it (no accidental log leak).
        self.api_key = key
        self.model = model
        self.messages = _GPTMessages(self)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"GPTClient(model={self.model!r})"


def default_gpt_factory(model: str | None = None) -> GPTClient:
    """Production factory — returns ``GPTClient`` keyed from ``OPENAI_API_KEY``.

    Raises ``RuntimeError`` if ``OPENAI_API_KEY`` is missing — the caller
    must dispatch to a Python fallback at that point per L2 spec.
    """
    return GPTClient(model=model or GPT_P0_MODEL)


def extract_text(response: Any) -> str:
    """Extract plain text from an Anthropic-shaped ``Message`` response.

    Tolerates real ``Message`` objects, dict mocks, and the OpenAI adapter.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        return ""
    pieces: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text", "")
        if text:
            pieces.append(str(text))
    return "".join(pieces)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction — tolerant of ```json fences."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_API_KEY_REDACTION_PREFIXES: Final[tuple[str, ...]] = ("sk-", "Bearer ")


def _sanitize_error_text(text: str) -> str:
    """Strip any potential API-key substrings from an error/repr string.

    Defense-in-depth: even though httpx normally redacts the Authorization
    header from exception messages, we explicitly scrub anything that looks
    like an OpenAI key (``sk-...``) or a bearer token before persistence.
    """
    if not text:
        return text
    out = text
    for prefix in _API_KEY_REDACTION_PREFIXES:
        idx = 0
        while True:
            pos = out.find(prefix, idx)
            if pos < 0:
                break
            # Walk to the end of the token (whitespace/quote/comma boundary).
            end = pos + len(prefix)
            while end < len(out) and out[end] not in (" ", "\t", "\n", "'", '"', ",", ")", "}"):
                end += 1
            out = out[:pos] + prefix + "REDACTED" + out[end:]
            idx = pos + len(prefix) + len("REDACTED")
    return out


async def call_gpt(
    *,
    client: Any,
    system_prefix: list[dict[str, Any]],
    user_prompt: str,
    max_tokens: int = 400,
    temperature: float = 0.0,
    model: str = GPT_P0_MODEL,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    reasoning_effort: str | None = None,
) -> GPTCallResult:
    """Single GPT call wrapped with timing + JSON parse + error capture.

    Caller is responsible for fail-closed / fail-open policy on ``error`` set.
    Aggressive bias preserved: errors do not throttle the loop — they bubble
    up as ``error`` and the caller decides PASS-vs-KILL per gate spec.

    ``reasoning_effort``: gpt-5.x only — overrides the default ``"minimal"``
    applied by the OpenAI shim. Pass ``"low"`` / ``"medium"`` / ``"high"``
    when a gate wants more reasoning at the cost of latency. Ignored for
    gpt-4.x (silently dropped by the shim).
    """
    started = time.monotonic()
    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prefix,
        "messages": [{"role": "user", "content": user_prompt}],
        "timeout": timeout_sec,
    }
    if reasoning_effort is not None:
        create_kwargs["reasoning_effort"] = reasoning_effort
    try:
        response = await client.messages.create(**create_kwargs)
    except Exception as exc:  # noqa: BLE001 — bound to caller's failure policy
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Sanitize: redact ``sk-...`` / ``Bearer ...`` substrings before
        # the repr surfaces in gate_events.error_text or logs.
        err_text = _sanitize_error_text(repr(exc))
        return GPTCallResult(
            text="", parsed=None, latency_ms=elapsed_ms, error=err_text,
            model=model,
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = extract_text(response)
    parsed = _try_parse_json(text)

    usage = getattr(response, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0)) if usage else 0
    out_tok = int(getattr(usage, "output_tokens", 0)) if usage else 0
    cached_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0

    return GPTCallResult(
        text=text,
        parsed=parsed,
        latency_ms=elapsed_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        model=model,
    )


@dataclass(slots=True)
class GPTCallSpy:
    """Test double that records every ``call_gpt`` invocation.

    Use via ``GPTCallSpy().factory()`` and assert on ``spy.calls`` from
    inside the test.
    """

    response_text: str = "{}"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def factory(self) -> Awaitable[Any]:
        async def _create(**kwargs: Any) -> Any:
            self.calls.append(kwargs)

            class _LocalBlock:
                text = self.response_text

            class _Resp:
                content = [_LocalBlock()]
                usage = None

            return _Resp()

        class _Messages:
            async def create(_self, **kwargs: Any) -> Any:
                return await _create(**kwargs)

        class _Client:
            messages = _Messages()

        async def _factory() -> Any:
            return _Client()

        return _factory()
