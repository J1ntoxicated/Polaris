#!/usr/bin/env python3
"""post_edit hook — 코드 변경 감지 시 40_components 갱신 알림.

Claude Code PostToolUse (Edit/Write) 에서 호출. stdin으로 tool input JSON 수신.

ADR-004: 모든 코드 변경은 40_components/ curated note 갱신 + codex 외부 리뷰 의무.
이 hook은 차단하지 않고 알림만 (P3 Validation Boundary는 pre_commit이 강제).

Exit code: 항상 0 (informational only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COMPONENTS_DIR = PROJECT_ROOT / "vault" / "40_components"

# 코드 변경으로 간주할 확장자
CODE_EXTS = {".py", ".ts", ".js", ".rs", ".go", ".sh"}
# Vault 변경은 무시 (vault-curator 책임)
SKIP_PATHS = {"vault/", "docs/", ".claude/"}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # parse 실패 시 silent

    tool_name = data.get("tool_name", "")
    if tool_name not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return 0

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return 0

    # Vault/docs/.claude 변경은 무시
    rel = file_path.replace(str(PROJECT_ROOT) + "/", "")
    for skip in SKIP_PATHS:
        if rel.startswith(skip):
            return 0

    # 코드 확장자만 알림
    ext = Path(file_path).suffix
    if ext not in CODE_EXTS:
        return 0

    # 알림 메시지 (stderr — Claude에 표시)
    print(
        f"[post_edit] 코드 변경 감지: {rel}\n"
        f"  → 40_components/{Path(file_path).stem}.md 갱신 권장 (ADR-004 codex 리뷰 의무)\n"
        f"  → codex-debate-partner agent 호출하여 외부 리뷰 받기",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
