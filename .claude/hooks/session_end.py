#!/usr/bin/env python3
"""
session_end.py — Polaris harness hook (SessionEnd)

Append to vault/log.md only on material change. Material change =
code edit, ADR mint, decision, incident, new strategy, or trade event.

If no material change, skip (per Round 3 D3 + Jin sign-off).

Detection (P0 stub):
  - Read state hint file `data/runtime/session_dirty.flag` (touched by
    other hooks / skills when material change happens).
  - If present + non-empty, append a log line and clear the flag.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VAULT = PROJECT_ROOT / "vault"
LOG_PATH = VAULT / "log.md"
DIRTY_FLAG = PROJECT_ROOT / "data" / "runtime" / "session_dirty.flag"


def read_dirty() -> str | None:
    if not DIRTY_FLAG.exists():
        return None
    text = DIRTY_FLAG.read_text(encoding="utf-8").strip()
    return text or None


def append_log(summary: str) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"{timestamp} [{summary}]\n"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Polaris Log\n\n", encoding="utf-8")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line)


def refresh_model_usage_stats() -> None:
    """Best-effort model-usage watermark regen. Never affects hook exit code."""
    with contextlib.suppress(Exception):
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "tools" / "model_usage_stats.py"), "--write"],
            timeout=45,
            capture_output=True,
        )


def main() -> int:
    summary = read_dirty()
    if not summary:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionEnd",
                                                  "info": "no material change, skip log append"}}))
        refresh_model_usage_stats()
        return 0
    append_log(summary)
    try:
        DIRTY_FLAG.unlink()
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionEnd",
                                              "info": f"appended log: {summary}"}}))
    refresh_model_usage_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
