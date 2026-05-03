# Dashboard Redesign v2 — Operations (거래) / Intel (심층) 재분리

> Jin 2026-04-19 05:16 "Operations = 거래/시그널/전략, Intel = 쓸데없는 거 분리". 기존 `dashboard_lifecycle_mockup.md` 의 발전판 (Intel 재구성 추가).

## 현 섹션 인벤토리 (read-only audit)

| 섹션 | LOC | 현 위치 | 판정 | 근거 |
|---|---|---|---|---|
| banner | 139 | ops:2-4 | **keep ops** | 거래 정체성 (regime/attack/balance) |
| north_star_bar | 150 | ops:5-7 | **keep ops** | 북극성 dampen/block 즉시 감지 |
| positions | 430 | ops:8-21 | **keep ops** (축소) | Open positions live — lifecycle ③ |
| signal_flow | 307 | ops:8-21 RW | **keep ops** | Signal funnel — lifecycle ① |
| trade_flow | 323 | ops:22-35 | **keep ops** | Entry/close — lifecycle ②④ |
| pipeline_flow | 268 | ops:22-35 RW | **→ intel** | Debug map, not decisioning |
| strategy | 239 | ops:36-49 | **keep ops** (축소) | Strategy leaderboard — lifecycle ⑤ |
| provider_perf | 70(inline) | ops:50-55 | **→ intel** | Per-provider WR 조사용 |
| trade_quality+winners | 416 | ops:36-55 RW | **split**: winners→ops, quality→intel | Winners = ops 핵심, Kelly/Ghost = intel |
| market_overview | 236 | ops:56-64 | **축소 keep** | Fear&Greed/VIX/macro 요약만 |
| alert_panel | 134 | intel:55-64 | **keep intel** | Squad health |
| arch_flow | 191 | intel:12-36 RW | **keep intel** | 에러/구조 메타 |
| ai_cost | 83 | intel:LW | **keep intel** | AI 예산 조사 |
| provider_chain | 405 | 없음 (교체됨) | **→ intel 복귀** | Per-provider WR/weight 상세 |

## Operations (LEFT) v2 — 거래 lifecycle 만

```
Row 1:       padding (1)
Row 2-4:     BANNER (3)                    identity · balance · 24h
Row 5-7:     NORTH STAR (3)                regime · dampen · block · asym · alerts
Row 8-17:    SIGNAL | GATE | EXEC | OPEN (10, 4-col)
Row 18-31:   MONITORING LIVE | CLOSES (14, 2-col)
Row 32-42:   STRATEGY LEADERBOARD (11, full-width)   ← pipeline_flow 자리
Row 43-50:   WINNERS / LOSERS (8, 2-col)             ← trade_quality 부분
Row 51-58:   MARKET OVERVIEW (8, full-width 축소)     ← 12→8
Row 59-64:   (예비 6)
Row 65-66:   FOOTER (2)
Total: 1+3+3+10+14+11+8+8+6+2 = 66
```

**핵심 변경** (vs 현재): pipeline_flow 14 → intel 이관 / provider_perf 6 → intel / trade_quality 10 → winners 8 만 keep / market_overview 12→8.

## Intel (RIGHT) v2 — 심층/메타/진단

```
Row 1:       padding (1)
Row 2-3:     HEADER (2)                    PID · uptime · balance · DB
Row 4-11:    LW: AI_COST+WS_FEEDS+SLIP | RW: REGIME COMPASS (8)
Row 12-25:   PIPELINE_FLOW (14)            ← ops 에서 이관
Row 26-35:   AI DECISIONS | CONFIG+PARAMS (10, 2-col)
Row 36-47:   PROVIDER CHAIN (12)           ← 복귀 + 축소
Row 48-55:   TRADE QUALITY/KELLY (8)       ← Ghost/Anomalies
Row 56-63:   ALERT SQUAD (8)               ← 현 10 → 8 축소
Row 64:      CROSS-EXCHANGE status (1)     ← ops-exchange-registry 기반
Row 65-66:   FOOTER (2)
Total: 1+2+8+14+10+12+8+8+1+2 = 66
```

**핵심 변경** (vs 현재): ASYMMETRY (1) 삭제 / pipeline_flow (14) 이관 받음 / provider_chain (12) 복귀 / cross-exchange (1) 신설.

## 구현 작업 분해 (요약)

1. north_star_bar — 변경 없음
2. operations.py / intel.py layout 재정의 (~80 LOC each)
3. pipeline_flow.py — 이관 (import 만 이동)
4. provider_chain.py — intel 재 wiring
5. cross_exchange.py 신규 (~60 LOC)
6. winners.py 신규 (~80 LOC)

예상 LOC: +140 신규 / ~160 이동 / ~40 삭제.

## Harness 판단 필요 지점 & 후속 단계 → [dashboard_redesign_appendix.md](dashboard_redesign_appendix.md)

- pipeline_flow 이관 확정? / winners 분리 확정? / cross-exchange 1 행 vs 3 행?
- Phase A/B/C 작업 분해 + cross-exchange impact (OKX 중심) 상세 appendix 참조
