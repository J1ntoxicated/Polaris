#!/usr/bin/env python3
"""
pre_state_transition.py — Polaris harness hook (PreStateTransition stub)

P0 = logging only (per Jin sign-off). Full impl P1 후반 — checkpoint /
strategy ramp / order state machine generalization.

For P0, simply emit a structured log entry to data/runtime/state_transitions.jsonl.

Spec ref: ADR-003 Layer 7 (per-strategy circuit breaker, kill-switch),
ADR-004 (gate state transitions). Future P1 work: policy_engine.check
on transition + ADR-005 hard cap pre-state validation.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_LOG = PROJECT_ROOT / "data" / "runtime" / "state_transitions.jsonl"


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    record = {
        "ts": timestamp,
        "hook": "PreStateTransition",
        "stub": True,
        "payload": payload,
    }

    STATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STATE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreStateTransition",
                                              "info": "logged (P0 stub)"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
