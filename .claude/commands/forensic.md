---
description: Switch to /forensic mode — incident root cause investigation, drawdown checkpoint analysis. Hard-exclusive base mode.
argument-hint: "[event_id or incident description]"
---

# /forensic — Forensic Investigation Mode

Hard-exclusive base mode (mutually exclusive with `/dev`, `/alpha`).

## Activates
- forensicist agent (root cause analysis)
- Read access to event log, trade history, market data, vault
- Write access ONLY to `vault/50_research/forensic/`

## Trigger conditions ([[ADR-002]] D 메커니즘)
- Drawdown checkpoint (-8% intraday / -20% rolling 5d / -35% venue equity)
- Same strategy + correlation_group 7d 내 ≥3 stop-loss
- Strategy circuit breaker HALT
- Manual Jin trigger

## Discipline
- 증거 기반 only (`feedback_root_cause_evidence_based`)
- Correlation ≠ causation (`feedback_correlation_not_causation`)
- 1회 review 단정 X (`feedback_no_single_review_verdict`)

## NOT for
- Code fix → use `/dev` after forensic completes
- Trading decision → forensic 권고 → analyst → Jin sign-off

Event/incident: $ARGUMENTS
