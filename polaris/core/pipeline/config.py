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
    "G6_PROBE_TIGHTEN_ENV",
    "ai_free_mode",
    "g6_probe_tighten_mode",
]

AI_FREE_ENV: Final[str] = "POLARIS_AI_FREE"
G6_PROBE_TIGHTEN_ENV: Final[str] = "POLARIS_G6_PROBE_TIGHTEN"
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
