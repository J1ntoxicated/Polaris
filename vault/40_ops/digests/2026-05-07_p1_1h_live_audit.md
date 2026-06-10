---
type: runtime
status: active
date_created: 2026-05-07
tags: [digest, p1, live-audit, ignite-p1]
related: [[ADR-002-vision|ADR-002]], [[layer-2-per-gate-pipeline]], [[2026-05-07_p1_functional_review]]
---

# P1 24h Paper Loop — 1h Live Audit

## Process state
- PID **57257** running, **1h12m elapsed**, RSS 58MB, 0% CPU
- Log file: `data/paper/polaris_runtime.log` 9MB / 52,086 lines (~12 lines/sec sustained)
- 24h projection: ~180-200MB (real OKX noise above 95MB initial estimate)

## Fill metrics (1h)

| Metric | Value |
|---|---|
| Total fills | 2,652 |
| Closed | 663 |
| Notional | $481,780 |
| PnL | **+$602.23** |

### Real vs Simulated (1h)
- REAL OKX: **1,668 fills (63%) / $303,340 notional**
- Simulated: 984 fills (37%) / $178,440
- REAL Capital: **0** (Day 7 smoke 0.2% 와 일관 — Capital strategies 미트리거)

## Cell matrix
3 cells (volume_burst / tsmom / spot_donchian × BTC-USDT × bull_trend)
- n_eff growth: ~96 → 350+ (1h × 660 closed trades)
- score: 0.183 → 0.267 (ceiling, synthetic 100% win)
- **pool=3 → quartile=cold 영구** (#82 functional review caught — fixture mode 결과)

## Learner snapshots
- session_mult / regime_mult / max_hold: **6 hourly snapshots each** ✓
- Hourly trigger 작동 검증
- 단 mult value clip ceiling (synthetic 100% win — real signal 학습 X)

## 거부 키워드 sweep
0 hits ✓ (real-money safety / regulatory cap / monthly review / 90d / regrets/ / posture standard / professional risk / fractional Kelly is too aggressive)

## ⚠ Architectural validation 가치

**Real OKX trade 작동** (1,668 real demo fills, $303k notional) — Day 5/6 smoke 검증 외에 1h 실측 가동 증거.

**그러나** functional review (#82) + cumulative review (#81) 가 catch:
- L0 dynamic universe / L1 bars / L4 cell pool expansion / L6 live recalc / G6/G7 active = **fixture mode 미작동**
- Cell matrix 3 cells 영구 (BTC × 3 strat × bull_trend) — pool growth X
- Learner mult = synthetic 100% win clip ceiling
- **24h 데이터 architectural validation 가치 = 0**

## Day 8 P0 (확정)

`smoke_paper_loop.py` 가 fixture, 별도 `production_paper_loop.py` 신규 작성:
1. L0 producer schedule (OKX 5min / Capital 10min refresh) → universe populate
2. L1 bar ingest call (production 내부) → bars/baseline populate
3. start_gate=G1 (Universe Scanner 부터)
4. G8 reflector close path 호출
5. `_close_oldest_trade.pnl_r` real mark-to-market (fills 테이블 query)
6. MarketView 실제 데이터 (smoke hardcoded 값 X)
7. regime 동적 계산
8. G6 caller real position state, G7 widen_proposal real supply

## Open question (Jin 결정)
- 옵션 A: 즉시 stop + Day 8 production loop fix + re-launch
- 옵션 B: 24h 가동 유지 (continuity / token warm) + Day 8 production loop 작성 + 완성 후 swap
- 옵션 C: 즉시 stop + Day 8 production loop 완성 후 24h 가동 시작

추천 B (continuity 유지, real OKX session warm).

## Sources
- 24h paper loop PID 57257 (1h12m) — `data/paper/polaris_runtime.log`
- DB: `data/polaris.sqlite` (live)
- functional review: `vault/40_ops/digests/2026-05-07_p1_functional_review.md`
- cumulative review: `vault/40_ops/digests/2026-05-07_p0_sprint_cumulative_review.md`
