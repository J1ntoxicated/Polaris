"""Pipeline env flags — W3 AI-free cutover (in-loop LLM = 0).

Spec SSOT: ``.claude/plans/organic_ops_ai_free_2026-06-11.md`` §1 W3
(Jin 2026-06-11 — the bot is fully AI-free in the hot path by default).

``POLARIS_AI_FREE`` (default **ON**): G3/G4/G7 return their deterministic
technical decisions as the PRIMARY decision (``model_used="python"``), zero
GPT calls, and the ``gate_shadow_events`` comparison log naturally stops
(GPT absent → nothing to compare; schema/rows untouched). Measured basis
(2026-06-11, 12k shadow events): agreement G3 89.5% / G4 86.3% / G7 90.9%,
disagreements almost entirely gpt=KILL vs tech=PASS/PROCEED — promotion
RAISES pass-through (flow_not_block direction).

``POLARIS_AI_FREE=0`` (legacy opt-out): the GPT gate paths run
byte-identical, shadow logging included. Read fresh per call (no caching)
so tests inject either mode via env.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "AI_FREE_ENV",
    "AI_JUDGE_MODE_ACTIVE",
    "AI_JUDGE_MODE_ENV",
    "AI_JUDGE_MODE_SHADOW",
    "CONVICTION_PYRAMID_ACTIVE_ENV",
    "G6_PROBE_TIGHTEN_ENV",
    "ai_free_mode",
    "ai_judge_mode",
    "conviction_pyramid_active",
    "g6_probe_tighten_mode",
]

AI_FREE_ENV: Final[str] = "POLARIS_AI_FREE"
G6_PROBE_TIGHTEN_ENV: Final[str] = "POLARIS_G6_PROBE_TIGHTEN"
CONVICTION_PYRAMID_ACTIVE_ENV: Final[str] = "POLARIS_CONVICTION_PYRAMID_ACTIVE"
AI_JUDGE_MODE_ENV: Final[str] = "POLARIS_AI_JUDGE_MODE"
# AI-judge (#32) mode tokens. shadow = judge runs + logs only, the deterministic
# decision still ACTS (pass-through preservation verify). active = the judge's
# NON-BLOCKING refinement is reflected on top of the deterministic decision.
AI_JUDGE_MODE_SHADOW: Final[str] = "shadow"
AI_JUDGE_MODE_ACTIVE: Final[str] = "active"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def ai_free_mode(env_value: str | None = None) -> bool:
    """True iff in-loop GPT is retired (deterministic G3/G4/G7 primary).

    Default ON: unset/empty env → True (the operator opts OUT via
    ``POLARIS_AI_FREE=0``). ``env_value`` is injectable for pure tests;
    ``None`` reads the process env.
    """
    raw = os.getenv(AI_FREE_ENV) if env_value is None else env_value
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in _TRUTHY


def g6_probe_tighten_mode(env_value: str | None = None) -> bool:
    """True iff G6 consumes a probe TIGHTEN into an ADJUST_EXIT (tighten) directive.

    Default **OFF**: unset/empty → False, so the live loop is byte-identical until a
    supervised run opts IN via ``POLARIS_G6_PROBE_TIGHTEN=1``. flow_not_block — when
    ON, an adverse HOLD-band position whose latest probe action is TIGHTEN routes a
    TIGHTER trail to G7 (precise exit TIMING), never a block or size cut; the -1.0R
    rail / swap / widen window / entry / size are untouched. ``env_value`` injectable
    for pure tests; ``None`` reads the process env.
    """
    raw = os.getenv(G6_PROBE_TIGHTEN_ENV) if env_value is None else env_value
    if raw is None or raw.strip() == "":
        return False
    return raw.strip().lower() in _TRUTHY


def conviction_pyramid_active(env_value: str | None = None) -> bool:
    """True iff the G6 conviction-stack writer runs ACTIVE (real DB INSERT +
    add-on signal), not shadow-only.

    Default **OFF**: unset/empty → False — the writer ALWAYS judges +
    logs the would-stack decision to ``gate_shadow_events`` (shadow-first,
    sizing-change ``/debate`` consensus —
    ``vault/50_research/debates/conviction_pyramid_addon_notional_2026-07-10.md``);
    only the real ``position_conviction_layers`` write + add-on signal wait
    for ``POLARIS_CONVICTION_PYRAMID_ACTIVE=1``, flipped after the shadow
    distribution is reviewed (log-drop rate / duplicate-layer rate / cap-
    binding distribution). ``env_value`` injectable for pure tests; ``None``
    reads the process env.
    """
    raw = os.getenv(CONVICTION_PYRAMID_ACTIVE_ENV) if env_value is None else env_value
    if raw is None or raw.strip() == "":
        return False
    return raw.strip().lower() in _TRUTHY


def ai_judge_mode(env_value: str | None = None) -> str:
    """Resolve the AI-judge (#32) mode — ``shadow`` (default) or ``active``.

    The AI judge consumes the bot's OWN information (technicals + alt-data
    evidence + regime + ground + events) and emits a per-ticker, structurally
    NON-BLOCKING verdict (no KILL/BLOCK path exists in its verdict type).

    - ``shadow`` (default — unset/empty/unrecognized → shadow): the judge runs
      and its verdict is logged for measurement, but the DETERMINISTIC decision
      still acts. This verifies pass-through preservation (the live loop is
      byte-identical to the no-judge path).
    - ``active``: the judge's verdict is REFLECTED, but only in the non-blocking
      direction (flow-additive refinement on top of the deterministic decision;
      it can never remove flow, never KILL, never cut size).

    Default is ``shadow`` (fail-safe — an unknown / typo'd value never silently
    activates the judge). ``env_value`` injectable for pure tests; ``None`` reads
    the process env.
    """
    raw = os.getenv(AI_JUDGE_MODE_ENV) if env_value is None else env_value
    if raw is not None and raw.strip().lower() == AI_JUDGE_MODE_ACTIVE:
        return AI_JUDGE_MODE_ACTIVE
    return AI_JUDGE_MODE_SHADOW
