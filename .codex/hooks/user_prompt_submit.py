#!/usr/bin/env python3
"""
user_prompt_submit.py — Polaris harness hook (UserPromptSubmit)

Parse mode commands at start of prompt:
  /dev      — code work, build, refactor
  /alpha    — paper trade live monitoring
  /forensic — incident investigation
  /debate   — overlay (codex external review)

Modes /dev /alpha /forensic = hard-exclusive (one at a time).
/debate = overlay (additive on top of any base mode).

P0 stub: write current mode to data/runtime/mode.flag for other hooks
+ skills to read. Full policy_engine matrix wiring = P1.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODE_FILE = PROJECT_ROOT / "data" / "runtime" / "mode.flag"

BASE_MODES = ("dev", "alpha", "forensic")
OVERLAY_MODES = ("debate",)
MODE_PATTERN = re.compile(r"^\s*/(dev|alpha|forensic|debate)\b", re.IGNORECASE)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    prompt = payload.get("prompt", "") or ""

    match = MODE_PATTERN.match(prompt)
    if not match:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                  "info": "no mode change"}}))
        return 0

    mode = match.group(1).lower()
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if mode in BASE_MODES:
        MODE_FILE.write_text(json.dumps({"base": mode, "overlay": []}), encoding="utf-8")
        info = f"base mode → {mode}"
    elif mode in OVERLAY_MODES:
        current = {"base": "dev", "overlay": []}
        if MODE_FILE.exists():
            try:
                current = json.loads(MODE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if mode not in current.get("overlay", []):
            current.setdefault("overlay", []).append(mode)
        MODE_FILE.write_text(json.dumps(current), encoding="utf-8")
        info = f"overlay add → {mode} (base={current.get('base')})"
    else:
        info = "unknown mode (matched but unhandled)"

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"[Polaris mode] {info}",
            "info": info,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
