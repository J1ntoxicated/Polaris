"""Smoke / dev — stub GPT client (no OpenAI API credit needed).

Mimics ``GPTClient`` (Anthropic-shaped ``messages.create``) so every gate
sees a deterministic permissive JSON response. Used by:

- ``smoke_paper_loop.py`` (Day 5+ smoke loop)
- ``tests/test_*`` (avoid live OpenAI calls in pytest)

Replaces ``StubHaikuClient`` after the 2026-05-07 Haiku → GPT migration.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["StubGPTClient"]


class StubGPTClient:
    """Permissive ``client.messages.create`` stub for the smoke loop.

    Inspects the system + user prompt for the decision-enum line so each
    gate sees a syntactically-valid permissive output:

      G3 (Validator)         -> "PASS"
      G4 (Pre-Entry Watcher) -> "PROCEED"
      G8 (Post-Trade)        -> "REFLECTED"
      G1 (Universe Scanner)  -> "{focus: [...]}"

    Unknown gates fall back to "PASS".
    """

    def __init__(self) -> None:
        self.call_count = 0
        outer = self

        def _pick_decision(system: str, user: str) -> str:
            txt = f"{system}\n{user}".upper()
            # Pre-Entry Watcher prompt: 'PROCEED|KILL'
            if "PROCEED" in txt and "KILL" in txt and "PASS" not in txt:
                return "PROCEED"
            # Post-trade reflector: REFLECTED tag
            if "REFLECTED" in txt:
                return "REFLECTED"
            return "PASS"

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:  # noqa: ANN401
                outer.call_count += 1
                # Concatenate the system blocks + user blocks for inspection.
                sys_block = ""
                sys_msg = kwargs.get("system", "")
                if isinstance(sys_msg, str):
                    sys_block = sys_msg
                elif isinstance(sys_msg, list):
                    sys_block = "\n".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in sys_msg
                    )
                user_block = ""
                msgs = kwargs.get("messages") or []
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    content = m.get("content")
                    if isinstance(content, str):
                        user_block += content
                    elif isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and "text" in b:
                                user_block += b["text"]
                decision = _pick_decision(sys_block, user_block)
                payload = {
                    "decision": decision,
                    "confidence": 0.7,
                    "reason": "smoke_stub",
                    "modify": {},
                    "size_pct": 0.05,
                    "exit": "HOLD",
                    "lesson": {
                        "thesis_correct": True,
                        "exit_quality": "ok",
                        "tags": ["smoke"],
                    },
                    "watch": {"price_target": 0.0, "ttl_bars": 5},
                    "focus": [],
                    "strength_scalar": 1.0,
                }

                class _Block:
                    text = json.dumps(payload)

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()
