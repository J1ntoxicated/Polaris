---
description: Switch to /alpha mode — paper trade live monitoring + per-gate orchestration. Hard-exclusive base mode.
argument-hint: "[optional venue/strategy filter]"
---

# /alpha — Live Paper Trade Mode

Hard-exclusive base mode (mutually exclusive with `/dev`, `/forensic`).

## Activates
- 7 per-gate agents (universe-scanner / signal-validator / pre-entry-watcher / entry-sizer / position-monitor / adaptive-exit / post-trade-reflector)
- risk-officer (deterministic Python, hard cap fence)
- All P0 skills (running-paper-loop, gating-pipeline, sizing-positions, executing-orders, governing-risk, reconciling-portfolio, discovering-universe, signaling-strategies)

## Discipline
- Aggressive bias preserved
- Daily target 0.75% / stretch 1.25%
- Drawdown checkpoint = snapshot only (실행 차단 X)
- Auto-stop = 없음 (Jin manual only)

## NOT for
- Code edit → use `/dev`
- Forensic root cause → use `/forensic`

Filter: $ARGUMENTS
