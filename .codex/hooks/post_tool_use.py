#!/usr/bin/env python3
"""
post_tool_use.py — Polaris harness hook (PostToolUse)

Write event log to state_store (SQLite events table) + flag material
change for SessionEnd (vault/log.md append).

P0 stub: append to data/runtime/events.jsonl (single-file event log).
Full SQLite events table = P0 sprint Day 7.

Async lint: noop P0 (heavy lint = weekly cron, light lint = pre-commit).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENT_LOG = PROJECT_ROOT / "data" / "runtime" / "events.jsonl"
DIRTY_FLAG = PROJECT_ROOT / "data" / "runtime" / "session_dirty.flag"

MATERIAL_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": timestamp,
        "type": "post_tool_use",
        "tool": tool_name,
        "target": tool_input.get("file_path") or tool_input.get("command", "")[:120],
    }
    with EVENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    if tool_name in MATERIAL_TOOLS:
        DIRTY_FLAG.parent.mkdir(parents=True, exist_ok=True)
        target = tool_input.get("file_path", "<unknown>")
        DIRTY_FLAG.write_text(f"{tool_name}: {target}", encoding="utf-8")

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "info": "logged"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
