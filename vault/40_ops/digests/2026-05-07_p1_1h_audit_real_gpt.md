---
type: runtime
status: active
date_created: 2026-05-07
tags: [digest, p1, 1h-audit, real-gpt, day8]
related: [[ADR-002-vision|ADR-002]], [[active-autonomous-vision]], [[layer-2-per-gate-pipeline]]
---

# 1H Live Audit — Production Paper Loop (PID 9417, real GPT)

## Process
- PID **9417** running, 1h7m elapsed, RSS 105MB, 0.6% CPU
- Models: gpt-5-mini (P0 G1/G3/G4) + gpt-5.5 (P1 G6/G7/G8)
- `reasoning_effort='minimal'` mandatory for gpt-5.x

## Day 7 (Haiku KILL fixture) vs Day 8 (real GPT)

| Metric | Day 7 1h12m | Day 8 1h7m | 변화 |
|---|---|---|---|
| Fills | 2,652 | 388 | -85% |
| Closed | 663 | 146 | -78% |
| Notional | $481,780 | $484,393 | ≈ |
| PnL | +$602.23 | **-$569.72** | 음수 |
| Real OKX | 1,668 (63%) | 248 (64%) | 비율 동일 |
| Capital fills | 0 | 0 | 동일 |
| Cell pool | 3 | **46 (27 ticker)** | 15× ↑ |
| Distinct regimes | 1 | **4** | dynamic ✓ |
| AllocatorFence | 0 | **238 reserv** | wire ✓ |
| G3 KILL ratio | 100% Haiku | 66% real GPT | 정상화 |
| G3 PASS | 0 | 5,784 | real decision |
| G8 lessons | 0 | 157 REFLECTED | wire ✓ |
| regime_state | 0 | 54 entries | dynamic ✓ |
| fault_events | 0 | 0 | isolation healthy |

## Architectural validation ✅

- Dynamic Universe (27 tickers) ✓
- Dynamic Regime (4 types) ✓
- Cell pool growth (3 → 46) ✓
- AllocatorFence wire (238 reserv) ✓
- G8 Reflector wire (157 lessons) ✓
- Real GPT decisions (KILL/PASS/MODIFY) ✓
- Learner real WR (regime_mult 0.9-2.1 range, ceiling 제거) ✓
- 거부 키워드 0건 ✓

## Issue / Day 9+ Backlog

1. **PnL -$569.72 (1h)**: G6/G7 always HOLD → close 시점 손실. Real position state ADJUST/EXIT 결정 wire 필요.
2. **Capital fills 0**: Capital strategies (FX/XAU/Session) emit X. signal frequency tune 필요.
3. **G3 KILL 66%**: real GPT 보수. PASS 50% target 권고. KILL 사유 cell_matrix score 낮은 ticker — 자연.
4. **GPT cost**: ~$3-5/h → 24h ~$72-120. Token budget + cache optimization (P1.x).

## Log
- 7MB / 43k lines (1h7m) — 24h projection ~150MB

## Sources
- DB live: `data/polaris.sqlite`
- log: `data/paper/polaris_runtime.log`
- Day 7 audit: `vault/40_ops/digests/2026-05-07_p1_1h_live_audit.md`
- functional review: `vault/40_ops/digests/2026-05-07_p1_functional_review.md`
