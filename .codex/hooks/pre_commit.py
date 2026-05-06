#!/usr/bin/env python3
"""pre_commit hook — vault_lint 통과 검증.

호출 방법:
1. Claude Code PreCommit (settings.json hooks)
2. Git pre-commit (.git/hooks/pre-commit → 이 스크립트 호출)

긴급 bypass: EMERGENCY=1 환경변수 설정 시 vault_lint warning 허용 + log 기록.

Exit codes:
    0 = pass (또는 EMERGENCY bypass)
    1 = vault_lint fail → commit 차단
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAULT_LINT = PROJECT_ROOT / "tools" / "vault_lint.py"
EMERGENCY_LOG = PROJECT_ROOT / "vault" / "50_runtime" / "emergency_bypass_log.md"


def _log_emergency(reason: str) -> None:
    """긴급 bypass 발생 시 vault에 기록."""
    EMERGENCY_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not EMERGENCY_LOG.exists():
        EMERGENCY_LOG.write_text(
            "---\n"
            "entity_type: log\n"
            "entity_id: emergency_bypass_log\n"
            "auto: true\n"
            "expires: never\n"
            "editable: false\n"
            'back_links: ["[[emergency_bypass]]", "[[_NOW]]"]\n'
            "mode: meta\n"
            "reviewed_by: none\n"
            "tags: [meta, emergency, log, polaris, mode/meta]\n"
            "---\n\n"
            "# Emergency Bypass Log (append-only)\n\n"
            "| timestamp | reason | followup_due |\n"
            "|---|---|---|\n",
            encoding="utf-8",
        )
    now = _dt.datetime.now().isoformat(timespec="seconds")
    due = (_dt.datetime.now() + _dt.timedelta(hours=24)).isoformat(timespec="seconds")
    with EMERGENCY_LOG.open("a", encoding="utf-8") as f:
        f.write(f"| {now} | {reason} | {due} |\n")


def main() -> int:
    if not VAULT_LINT.exists():
        print(f"[pre_commit] vault_lint not found: {VAULT_LINT}", file=sys.stderr)
        return 0  # lint 부재는 차단 X (bootstrap 단계)

    emergency = os.environ.get("EMERGENCY", "").strip() == "1"
    if emergency:
        reason = os.environ.get("EMERGENCY_REASON", "")
        if not reason.strip():
            print("[pre_commit] EMERGENCY=1 but EMERGENCY_REASON empty → fail (감사 추적 의무).", file=sys.stderr)
            return 1
        print(f"[pre_commit] EMERGENCY=1 — vault_lint warning 허용. Reason: {reason}")
        _log_emergency(reason)
        # 그래도 full lint 실행 (정보 수집), exit code는 0
        subprocess.run([sys.executable, str(VAULT_LINT), "--dry-run"])
        return 0

    # Full lint (Karpathy + Polaris contracts) — ADR-004 reviewed_by gate 포함
    result = subprocess.run([sys.executable, str(VAULT_LINT)])
    if result.returncode != 0:
        print("[pre_commit] vault_lint FAIL → commit 차단 (Karpathy + Polaris contracts).", file=sys.stderr)
        print("[pre_commit] 긴급 시: EMERGENCY=1 EMERGENCY_REASON='reason' git commit ...", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
