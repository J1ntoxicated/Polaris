#!/usr/bin/env python3
"""
pre_tool_use.py — Polaris harness hook (PreToolUse)

policy_engine matrix check (3-layer: matrix + validator + event log).

P0 stub: log + allow-by-default. Deny only on hardcoded destructive list
unless explicit confirm flag in env (POLARIS_DESTRUCTIVE_CONFIRM=1).

Full Layer 1 (mode × agent × action_class) matrix lookup = P1 (when
polaris/core/policy_engine.py lands).
"""

from __future__ import annotations

import json
import os
import re
import sys


DESTRUCTIVE_PATTERNS = (
    re.compile(r"\brm\s+-rf?\b"),
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+--force\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
)


def is_destructive(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        for pat in DESTRUCTIVE_PATTERNS:
            if pat.search(cmd):
                return f"destructive bash pattern: {pat.pattern}"
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    destructive = is_destructive(tool_name, tool_input)
    if destructive and os.environ.get("POLARIS_DESTRUCTIVE_CONFIRM") != "1":
        out = {
            "decision": "block",
            "reason": (
                f"Polaris policy_engine: {destructive}. "
                "Set POLARIS_DESTRUCTIVE_CONFIRM=1 + mint ADR before retry."
            ),
        }
        print(json.dumps(out))
        return 0

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "info": "ok"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
