#!/usr/bin/env python3
"""post_stop hook — 작업 종료 시 _NOW 갱신 점검.

Claude Code Stop event 에서 호출. stdin으로 session 정보 JSON 수신.

검사:
1. _NOW.md 24h 미갱신 시 warn
2. 긴급 bypass 24h 사후 follow-up 미완 시 warn

Exit code: 항상 0 (informational).
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
NOW_FILE = PROJECT_ROOT / "vault" / "_NOW.md"
EMERGENCY_LOG = PROJECT_ROOT / "vault" / "50_runtime" / "emergency_bypass_log.md"


def _check_now_age() -> int:
    if not NOW_FILE.exists():
        return 0
    text = NOW_FILE.read_text(encoding="utf-8")
    m = re.search(r"^last_modified:\s*(\S+)", text, re.MULTILINE)
    if not m:
        return 0
    try:
        last_date = _dt.date.fromisoformat(m.group(1).strip().split("T")[0])
    except ValueError:
        return 0
    age_days = (_dt.date.today() - last_date).days
    if age_days >= 1:
        print(
            f"[post_stop] WARN: vault/_NOW.md 마지막 수정 {age_days}일 전. 갱신 권장.",
            file=sys.stderr,
        )
    return 0


def _check_emergency_followup() -> int:
    if not EMERGENCY_LOG.exists():
        return 0
    text = EMERGENCY_LOG.read_text(encoding="utf-8")
    now = _dt.datetime.now()
    overdue = []
    for line in text.splitlines():
        # | timestamp | reason | followup_due |
        m = re.match(r"\|\s*(\S+T\S+)\s*\|.*\|\s*(\S+T\S+)\s*\|", line)
        if not m:
            continue
        try:
            due = _dt.datetime.fromisoformat(m.group(2))
        except ValueError:
            continue
        if due < now:
            overdue.append(m.group(1))
    if overdue:
        print(
            f"[post_stop] WARN: 긴급 bypass {len(overdue)}건 follow-up 만료 "
            f"(첫번째: {overdue[0]}). 24h 사후 산출물 (provisional ADR + lessons + codex 리뷰) 의무.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    _check_now_age()
    _check_emergency_followup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
