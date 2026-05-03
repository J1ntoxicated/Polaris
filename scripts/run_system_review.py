#!/usr/bin/env python3
"""Manual SystemReview trigger — dry-run / live / single-reviewer.

Usage:
  python3 scripts/run_system_review.py                    # dry-run (default, SAFETY_MODE ON)
  python3 scripts/run_system_review.py --live             # SAFETY_MODE OFF, execute() 허용
  python3 scripts/run_system_review.py --reviewer=provider
  python3 scripts/run_system_review.py --json             # machine-readable output

북극성 안전:
- --live 옵션은 per-reviewer execute() 를 **실제 실행**. Provider retire 등 구조적 변경 가능.
- 기본 dry-run 은 SAFETY_MODE=True 상태 그대로 → execute() 차단, alert 만 emit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    p = argparse.ArgumentParser(description="SystemReview manual trigger")
    p.add_argument("--live", action="store_true",
                   help="SAFETY_MODE=False — execute() 허용 (위험)")
    p.add_argument("--reviewer", default=None,
                   help="단일 reviewer 만 실행 (provider/exit/pipeline/ai_stage/cost/sizing/regime/strategy/wsfeed/orphan)")
    p.add_argument("--json", action="store_true", help="JSON output")
    args = p.parse_args()

    # SAFETY_MODE 동적 조작
    import invasion.evolution.subsystem_reviewer as sr
    if args.live:
        sr.SAFETY_MODE = False
        print("⚠️  SAFETY_MODE=False — execute() 허용. 위험 — confirm: y/N", file=sys.stderr)
        if input().strip().lower() != "y":
            print("Aborted.", file=sys.stderr)
            sys.exit(1)
    # else: SAFETY_MODE stays True (default) → alert only

    eng = sr.SystemReviewEngine()
    if args.reviewer:
        eng.reviewers = [r for r in eng.reviewers if r.name == args.reviewer]
        if not eng.reviewers:
            print(f"Unknown reviewer: {args.reviewer}", file=sys.stderr)
            sys.exit(1)

    reports = eng.run()

    if args.json:
        out = []
        for r in reports:
            out.append({
                "subsystem": r.subsystem,
                "n_entities": r.n_entities,
                "n_findings": len(r.findings),
                "n_auto_executed": sum(1 for f in r.findings if f.auto_executed),
                "error": r.error,
                "findings": [
                    {
                        "entity": f.entity, "severity": f.severity,
                        "metric": f.metric, "value": f.value,
                        "threshold": f.threshold,
                        "action": f.action, "auto_executed": f.auto_executed,
                        "reason": f.reason,
                    }
                    for f in r.findings
                ],
            })
        print(json.dumps(out, indent=2, default=str))
        return

    # Human-readable
    print(f"\n=== SystemReview — SAFETY_MODE={'ON' if sr.SAFETY_MODE else 'OFF (LIVE)'} ===\n")
    total_f = 0
    total_auto = 0
    for r in reports:
        auto = sum(1 for f in r.findings if f.auto_executed)
        print(f"[{r.subsystem:10}] entities={r.n_entities:3} findings={len(r.findings):2} "
              f"auto_exec={auto:2} {r.error[:50] if r.error else ''}")
        for f in r.findings:
            mark = "[EXEC]" if f.auto_executed else "[ALERT]"
            sev = f"{'!!!' if f.severity == 'HIGH' else '!!' if f.severity == 'MED' else '!'}"
            print(f"   {sev} {mark} {f.entity[:38]}: {f.reason[:110]}")
        total_f += len(r.findings)
        total_auto += auto

    print(f"\nTOTAL: {total_f} findings, {total_auto} auto-executed")
    print(f"Log: data/subsystem_review.jsonl")
    print(f"Alerts: .claude/harness_alerts/subsystem_*.md")


if __name__ == "__main__":
    main()
