#!/usr/bin/env python3
"""
session_start.py — Polaris harness hook (SessionStart)

Mandatory: read vault/_NOW.md (Tier 0) + INDEX.md catalog.
Output context block is injected into the model session.

Spec ref: ADR-001 vault structure, vault/05_process implicit via _NOW.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VAULT = PROJECT_ROOT / "vault"
NOW_PATH = VAULT / "_NOW.md"
INDEX_PATH = VAULT / "INDEX.md"


def read_safely(path: Path) -> str:
    if not path.exists():
        return f"[missing: {path.relative_to(PROJECT_ROOT)}]"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"[read error {path.name}: {exc}]"


def main() -> int:
    parts: list[str] = []
    parts.append("=== Polaris Vault — SessionStart mandatory read ===")
    parts.append("")
    parts.append("--- vault/_NOW.md (Tier 0) ---")
    parts.append(read_safely(NOW_PATH))
    parts.append("")
    parts.append("--- vault/INDEX.md (catalog) ---")
    parts.append(read_safely(INDEX_PATH))
    parts.append("")
    parts.append("=== End SessionStart context ===")

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
