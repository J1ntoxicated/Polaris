---
entity_type: adr
entity_id: ADR-009v2
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[ADR-009]]", "[[ADR-001]]", "[[ADR-007]]", "[[INSIGHT-007]]", "[[INSIGHT-032]]"]
mode: dev
reviewed_by: pending
ack_by: pending
ack_at: null
maturity: provisional
tags: [type/adr, status/provisional, scope/spot+perp, priority/p1, polaris]
---

# ADR-009v2 — SPOT + PERP Dual Mode (Provisional, Jin ack 대기)

## Status
- provisional: 2026-05-04
- Jin ack: pending
- Live implementation: NOT YET (spec only)
- expires: 2026-08-04 (3-month re-validate)

## Context: Why revisit ADR-009 now?

Live readiness audit (`scripts/live_readiness_audit.py`, 2026-05-04 실행):

| Metric | Value | Signal |
|---|---|---|
| Total closed trades | 778 | Adequate sample |
| Paper PnL | -$132.86 | Negative |
| Overall EV/trade | -0.097% | Below fee floor |
| Overall win rate | 17.9% | Far below 40% threshold |
| Live readiness score | 22/100 | NOT READY |
| Estimated live PnL | -$242.20 | Paper + $109 slippage drag |

ADR-009 anticipated this: "SPOT-only가 수학적 불가능으로 확인되면 PERP 검토 ADR 별도". 778 trades now confirm the pattern. This ADR-009v2 defines the PERP spec (code X — spec only, Jin ack required before implementation).

## Decision (provisional)

**Extend SPOT operations with optional PERP paper simulation.**

SPOT remains primary. PERP paper layer adds leverage-amplified simulation to:
1. Validate whether the same alpha sources (TSMOM, VolumeBurst) are viable with leverage
2. Measure live EV vs fee + funding rate cost
3. Provide evidence base for eventual live PERP decision (Phase 4+)

**No live PERP trading until separate Jin-ack ADR.**

## PERP Entry Conditions (strict, more conservative than모태 ADR-011)

| Condition | Threshold | Rationale |
|---|---|---|
| Backtest Sharpe | >= 0.5 | Stricter than SPOT promotion gate (0.3) |
| Leverage | 5x max | 모태 IG 30-100x caused liquidation risk; 5x = 20% drop buffer |
| Funding rate | <= -0.05% (8h) | Carry advantage required (pay negative funding = get paid) |
| Liquidation distance | > SL × 2 | Ensures SL triggers before liquidation |
| Min paper n before PERP | 30 trades | Sample adequacy (live_readiness_audit threshold) |

## Proposed New Infrastructure (code X until Jin ack)

### src/data/okx_perp_ws.py
- OKX SWAP WebSocket subscriber (instType=SWAP, e.g. BTC-USDT-SWAP)
- Separate from spot okx_ws.py — no shared state
- Pure function: parse_swap_tick(raw_msg) -> dict | None

### src/paper/perp_runner.py
- Perp paper simulation (leverage-aware PnL, funding cost deduction)
- Extends runner.py but with leverage_factor param
- Funding cost: fetched from binance_funding.py every 8h per position
- Liquidation guard: reject entry if entry_price * (1 - 1/leverage) < SL

### src/strategies/perp_tsmom.py
- TSMOM (Moskowitz 2012) adapted for SWAP with leverage
- Same signal logic as HYPO-032, different sizing (leverage × base_size)

### src/strategies/perp_volume_burst.py
- VolumeBurst adapted for SWAP with leverage
- Same signal logic as HYPO-008, different exit profile (wider TP/SL for 5x)

## Expected Effects (theoretical)

| Scenario | Paper EV | With 5x leverage | With funding (-0.05%) |
|---|---|---|---|
| TSMOM (paper +0.487%/trade) | +0.487% | +2.44% (5x) | +2.49% (carry bonus) |
| VolumeBurst (paper +0.048%/trade) | +0.048% | +0.24% (5x) | +0.29% |
| Net live (after perp fee + funding + slippage) | varies | varies | requires separate perp audit |

Note: 5x leverage = 5x PnL but also 5x drawdown. PERP not a substitute for positive alpha — it amplifies existing edge.

## Risks

### Risk 1: Liquidation
- 5x leverage: 20% adverse move from entry = liquidation
- Mitigation: SL at 4% (TSMOM) → liquidation at 20%, SL triggers first at 4%
- Funding rate spike can narrow buffer (must monitor 8h rate)

### Risk 2: Funding Rate Cost
- Current BTC 8h rate +0.01% = 0.03%/day = 0.9%/month = eats thin edges
- Entry condition: funding <= -0.05% ensures we are PAID to hold
- Exit if funding crosses +0.05% (cost reversal trigger)

### Risk 3: Live Divergence × Leverage
- Paper slippage model: +0.08% friction per trade
- With 5x leverage, +0.08% real slippage = effectively 0.4% on leveraged position
- Requires separate perp live audit before live decision

### Risk 4: Cross-liquidation on portfolio
- Multiple PERP positions: correlated liquidation risk in BTC crash
- Mitigation: max 2 PERP positions open simultaneously; no correlated pairs

## Implementation Sequence (pending Jin ack)

1. Jin ack this ADR-009v2
2. src/data/okx_perp_ws.py + tests (TDD, pure parser)
3. src/paper/perp_runner.py + tests (leverage + funding math)
4. HYPO-037-PERP-TSMOM paper only (60 days, separate from SPOT paper)
5. Separate perp_live_readiness_audit.py at 60-day mark
6. Live decision ADR-NNN (Jin ack required, separate from this ADR)

## Rollback Path

- If PERP paper n=30 trades shows EV < SPOT + friction: archive HYPO-037, do not proceed
- If funding rate environment unfavorable (positive funding > 0.05% persistent): pause PERP paper
- This ADR expires 2026-08-04 — must re-validate or supersede

## Relationship to ADR-009

ADR-009 said: "3개월 후 재validate". This ADR-009v2 is that re-validation, triggered by 778-trade paper evidence. ADR-009 remains in effect for all SPOT decisions. ADR-009v2 adds PERP layer as provisional extension.

## Related

- [[ADR-009]] — SPOT-only 결정 (superseded for PERP spec only)
- [[ADR-001]] — SPOT-first fresh start (still applies to live)
- [[ADR-007]] — Paper sizing freedom
- [[INSIGHT-007]] — OKX fee 함정
- [[INSIGHT-032]] — OKX fee 0.7%/side paper
- [[HYPO-032]] — TSMOM (would become perp_tsmom base)
- [[HYPO-008]] — VolumeBurst (would become perp_volume_burst base)
