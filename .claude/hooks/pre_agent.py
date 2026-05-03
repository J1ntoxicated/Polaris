#!/usr/bin/env python3
"""pre_agent hook — agent invoke 전 4 contract + 모드 책임 사전 검사.

Claude Code PreToolUse (Agent) 에서 호출. stdin으로 tool input JSON 수신.

검사:
1. subagent_type이 Polaris 4 agent 중 하나인지
2. 모태 _DEPRECATED agent 호출 시 차단 (ADR-005 violation)

Exit codes:
    0 = pass
    2 = 차단 (블로킹) — 상위 Claude에 reason 전달
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEPRECATED_DIR = PROJECT_ROOT / ".claude" / "agents" / "_DEPRECATED"

POLARIS_AGENTS = {
    "vault-curator",
    "code-implementer",
    "forensic-investigator",
    "codex-debate-partner",
}


def _deprecated_agent_names() -> set[str]:
    if not DEPRECATED_DIR.exists():
        return set()
    return {p.stem for p in DEPRECATED_DIR.glob("*.md")}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name != "Agent":
        return 0

    tool_input = data.get("tool_input", {})
    subagent = tool_input.get("subagent_type", "")
    if not subagent:
        return 0

    # superpowers / codex 등 외부 plugin agent는 통과
    if ":" in subagent:
        return 0

    # general-purpose, Explore, Plan 등 built-in 통과
    builtin = {"general-purpose", "Explore", "Plan", "statusline-setup"}
    if subagent in builtin:
        return 0

    # 모태 _DEPRECATED agent 차단
    deprecated = _deprecated_agent_names()
    if subagent in deprecated:
        print(
            f"[pre_agent] BLOCKED: '{subagent}' is a legacy _DEPRECATED agent. "
            f"Use one of Polaris 4 agents: {sorted(POLARIS_AGENTS)} (ADR-005)",
            file=sys.stderr,
        )
        return 2

    # Polaris 4 agent 외 agent 호출 시 warn (차단은 X)
    if subagent not in POLARIS_AGENTS:
        print(
            f"[pre_agent] WARN: '{subagent}' is not a Polaris-defined agent. "
            f"Polaris 4: {sorted(POLARIS_AGENTS)}. Proceeding.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
