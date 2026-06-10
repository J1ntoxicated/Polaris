---
type: ADR
adr_id: ADR-005
aliases: [ADR-005]
status: active
date_created: 2026-05-06
tags: [adr, sizing, kelly, cell-routing, risk]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-006-cell-matrix|ADR-006]], [[aggressive-bias]]
reviewed_by: codex+jin (round 3 D2 + D4 + Jin sign-off MED/LOW)
---

# ADR-005 — Sizing Formula + Cell Routing

## Decision

**T4 공식 (anti-collapse + tier amplifier + cell routing + L5 learner wire 2026-05-26)**:

```
L5_product = clip_product(clip_ind(session_mult) × clip_ind(regime_mult) × clip_ind(triple_block_mult))
proposed   = base × continuous_scalar(0.75-1.5×) × tier_amplifier(1.5/2/3×)
             × cell_routing_mult × listing_watchdog_mult × L5_product
clipped    = min(proposed, hard_caps)
final      = clipped × leverage(venue)
```

- 1 continuous scalar BEFORE notional clip (anti-collapse)
- 1 tier amplifier (3승 1.5× / 5승 2.0× / 8+승 3.0× / 1패 R1 reset)
- 1 cell routing mult (top quartile **×1.5** / bottom quartile ×0.5 / mid ×1.0)  ([[layer-4-cell-matrix]] Phase 0 patch — top ×1.3 → ×1.5 amplify)
- 1 listing_watchdog (age<24h → ×0.5)
- 1 L5 learner product = session × regime × triple_block, each `clip_ind=[0.3,3.0]`, product `clip=[0.1,5.0]` (sparse/disabled → 1.0 neutral, never block)
- All other = HARD MAX (소프트 dampener X)
- v1 9-stack collapse 영구 봉쇄

## Hard Caps (DEMO aggressive)

| Param | 값 |
|---|---|
| per-symbol cap (spot, OKX) | 50% |
| per-symbol cap (CFD, Capital) | 35% |
| per-symbol absolute ceiling | 50% |
| Track A gross cap | 60% |
| Track B gross cap | 80% |
| Track A daily venue risk | 8% |
| Track B daily venue risk | 9% |
| Total daily risk absolute ceiling | 10% |
| max single-trade risk (default) | **8%** |
| max single-trade risk (amplifier on) | **9%** |
| single-trade absolute ceiling | **9%** |
| Kelly fractional k | **0.50** |

**Priority**: hard MAX > Kelly. Kelly 산출치가 single cap 초과 시 절단.

## Cold Start (CS-3 Bootstrap)

데모 첫 trades = historical p/q 입력 부재:
- `n < 20` (closed trades per strategy): Kelly off, single-trade risk = **6% default / 7% amplifier on**
- `n >= 20`: Kelly on, 본 cap (8%/9%) 적용
- Kelly 입력 = 전략별 rolling estimator + clamp (급변폭 제한)

## Tier Amplifier Trigger Gate

| Streak | Amp | Trigger 조건 |
|---|---|---|
| 3 wins | 1.5× | n≥8, hit≥75% (n=8-9), 이후 n≥10 hit≥70% |
| 5 wins | 2.0× | n≥10 AND hit≥70% |
| 8+ wins | 3.0× | n≥10 AND hit≥70% |
| 1 loss | reset 1.0× | binary |

## Symbol-Cluster Cap

중앙 allocator 가 strategy cap 차감 **이전에** symbol-cluster cap 먼저 차감:
- `BTC/ETH cluster` (spot 동시 노출): 합산 한도 = 40%
- `XAU/indices cluster` (CFD 동시 노출): 합산 한도 = 50%
- `FX majors cluster` (CFD 동시 노출): 합산 한도 = 60%

## Risk-Budget Fill-Rate Cut

venue daily risk ceiling (8%/9%) 빠른 도달 시:
- `risk budget fill-rate` = 사용 risk / daily ceiling
- fill-rate ≥ 70% → 가장 약한 signal_strength 부터 즉시 컷
- 손익 무관

## Cell Routing Mult ([[ADR-006-cell-matrix|ADR-006]] 참조)

- top quartile (cell_score 상위 25%): **×1.5 amplify** (Phase 0 L4 patch — 상방 개방, hard cap 체인 보호)
- bottom quartile (하위 25%): ×0.5 suppress
- middle 50% 또는 신규 cell (n<5): ×1.0
- placement: cell mult 는 **clip 전** 통합 (Phase 0 L3 합의 — composition 깨짐 방지)

`final_size = T4_size × cell_routing_mult` 후 hard MAX 절단.

## ATR Stop/TP (per-strategy default, AI override 가능 — [[ADR-004-per-gate-ai-pipeline|ADR-004]] gate 7)

| Strategy | Stop ATR | TP ATR | Window |
|---|---|---|---|
| Volume Burst | 1.8 | 2.5 | 10 |
| TSMOM | 2.5 | 4.0 | 14 |
| RSI-BB Pullback | 2.0 | 3.0 | 14 |
| Spot Donchian | 2.5 | 4.0 | 14 |
| FX Breakout | 2.0 | 3.5 | 14 |
| XAU/Indices | 2.5 | 4.0 | 14 |
| Session Breakout | 1.0 | 3.0 | 10 |

## 9% Cap 정합 예시

| Base | 1.5× | 2.0× | 3.0× | 절단 |
|---|---|---|---|---|
| 2% | 3% | 4% | 6% | 미도달 |
| 3% | 4.5% | 6% | 9% | 3.0× 정확 |
| 4% | 6% | 8% | 9% (12→9) | 3.0× 절단 |
| 5% | 7.5% | 9% (10→9) | 9% (15→9) | 2.0×+ 절단 |

## Phase
- P0: T4 + Cold Start CS-3 + symbol-cluster cap + fill-rate cut + cell routing 4-dim
- P1: cell routing 8-dim full + Sonnet entry-sizer
- P2: ELO winner-only sizing 증액 (cap-bound, +0.05/100 trades, max 3.0×)

## Sources
- Round 3 D2 (k=0.5, single 8%/9%, hard MAX > Kelly)
- Round 3 D4 (tier amplifier 1.5/2/3×, R1 reset)
- Jin sign-off (CS-3 / fill-rate cut / cluster cap)
- 2026-05-26 L5→L3 wire — `.claude/plans/p0_l5_l3_sizing_wire.md` (session/regime/triple_block multiplied into T4, individual+product clip)
