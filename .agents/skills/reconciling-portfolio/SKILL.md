---
name: reconciling-portfolio
description: Use to reconcile internal position/cash/exposure state against venue-reported balances and positions. Detects drift (internal vs venue), normalizes via PositionLedger and OrderStateNormalizer, computes USD-equivalent aggregate (read-only dashboard model), and emits checkpoint snapshot on drawdown thresholds (-8% / -20% / -35%).
---

# reconciling-portfolio (P0 skill)

## When to use
- Periodic (1min during active trading)
- Post-fill event
- Drawdown checkpoint (-8% intraday / -20% rolling 5d / -35% venue equity)
- Manual Jin reconciliation

## Inputs
- internal state (positions / orders / fills / cash from SQLite)
- venue state (OKX `GET /api/v5/account/balance` + Capital `GET /api/v1/accounts`)

## Process

1. Fetch venue balances + positions
2. Diff internal vs venue
3. PositionLedger.normalize() → unified internal model
4. OrderStateNormalizer → {pending, partial, filled, cancelled, rejected}
5. Compute USD-equivalent aggregate (FX rate via Capital)
6. Drift detection:
   - position size delta > 1% → alert
   - cash delta > 0.5% → alert
   - missing fill (venue has, internal X) → backfill
7. Drawdown checkpoint trigger (snapshot only, 실행 차단 X — [[ADR-002]])

## Outputs
- reconciliation report (vault `40_ops/recon_<date>.md` if drift found)
- drawdown snapshot (vault `50_research/forensic/<event_id>_<date>.md` if checkpoint crossed)
- USD-equivalent dashboard model (read-only)

## Drawdown checkpoints (snapshot only)
- intraday -8%: snapshot + 원인 태깅
- rolling 5d -20%: feature dump + freeze-copy
- venue equity -35%: full position state freeze
- **NO 실행 차단** (Jin manual only)

## Failure handling
- Venue API down → use last known state, retry 1min
- Drift > 5% → emergency reconcile + forensicist trigger
- Cross-venue netting X (separate per venue, aggregate read-only)

## Cross-ref
- [[ADR-002]] drawdown checkpoint (snapshot only)
- [[ADR-003]] Per-Venue Adapters (PositionLedger, OrderStateNormalizer)
- agent: forensicist (checkpoint trigger D 메커니즘)
