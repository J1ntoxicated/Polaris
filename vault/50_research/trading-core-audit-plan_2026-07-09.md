---
type: research
title: Trading-Core 6-Axis Audit → Build Plan
date: 2026-07-09
tags: [audit, sizing, gating, exit, per-ticker, build-plan, wave]
related: ["[[trade_mess_full_audit_2026-07-02_verdict]]", "[[layer-4-cell-matrix]]", "[[ADR-003-8-layer-architecture]]", "[[trading-core-audit-plan_2026-07-09_buildgroups]]"]
status: active
---

# Trading-Core 6-Axis Audit → Build Plan (2026-07-09)

DEMO/PAPER · virtual EARN bypass. 6축 감사 종합 → 거래직결 향상안. 우선순위 = Jin 목표(매우활발 · 안잃음 · 검증가속) 직결도. 빌드 스펙 상세 → [[trading-core-audit-plan_2026-07-09_buildgroups]].

## 판정표 (축별 현황 / 핵심문제 / 심각도)

| 축 | 현황 | 핵심 문제 (거래직결) | 심각도 |
|---|---|---|---|
| G1-G4 gating | G1/G2 100% pass-through, G3/G4 deterministic | G3 MODIFY strength_scalar 사이저 미도달 (fold 존재·producer 미배선, 07-02→미해결) | HIGH |
| G5 entry/sizing | virtual EARN, fills 이산상수로 붕괴 | strategy_risk_state writer 부재→Kelly永cold·tier永1.0x; Capital $10 flat(T4/1000) | HIGH |
| G6/G7 exit | 3일 120 closed | peak-lock giveback(mfe 0.69R→pnl −0.05R capture); G7 judge dormant(7일 4콜·TIGHTEN 0) | MED |
| ticker matching | 26전략 등록 | L4 cell-matrix 상시 cold ×1.0(pool<20, eligible 7셀); stale_book 글로벌60s; universe 커버구멍 | HIGH |
| AI judge | active, 볼륨 16배↓ | REFINE_TIMING 21.5% inert; token/latency DB 소실; G7 robust-gate lockout 미해소 | MED |
| vault loop | write살아있음·read절반끊김 | counterfactual 15.6k행 reader 0 (KILL>PASS +0.39R@24h); market_events 42k write-only | MED |

핵심 수렴: "built-but-unwired" 3건(G3 scalar / strategy_risk_state / Capital translate)이 최상위 — 코드는 존재, producer→consumer 배선만 끊김. 활성화가 곧 안잃음+활발+검증 동시 충족.

## 잔여 큐 판정 (SOLVE 1 / DEFER 4)
- **stale_book → SOLVE (BG4).** counterfactual 근거 + flow_not_block + per-ticker mandate 3중 정합, discrete·리뷰가능 변경.
- **counterfactual auto-reader → DEFER.** 계측→라이브결정 폐루프 = 아키텍처(피드백 불안정 위험), /debate 필요. 실행가능분(stale_book adverse)은 BG4가 흡수하므로 지금 auto-tuner 불요.
- **REFINE_TIMING consumer → DEFER.** "한 윈도우 대기 후 진입"=진입지연, 매우활발 방향과 역행. ROI 낮음. (토큰절감엔 emit 중단이 나으나 judge-vocab 변경이라 별건.)
- **R-budget flip → DEFER, BG2 이후 재검.** 98% CAP_DOMINATED=single_trade는 Kelly=0이 전 사이즈를 캡에 핀고정한 증상. BG2가 Kelly/tier 활성화→캡 아래 분산되면 R-budget이 실제 바인딩. 원인(BG2) 전 증상 손대면 헛수고.
- **market_events reader → DEFER.** 거래직결 아님(regime_flip 텔레메트리 무덤). WAL 단일writer 경합은 db-writer-reader-split 트랙 소관, retention은 DB위생 트랙.

## per-ticker tailored 위반 → 티커별화 방안 (일률→취합데이터)
- **stale_book STALE_TICK_MAX_SEC=60 글로벌** → 심볼별 median tick-interval baseline(기존 `baseline_p50_spread_bps` 패턴 재사용, Layer0 universe stat). 저유동 Capital 티커의 정상 케이던스가 글로벌60s에 걸려 KILL되는 일률오분류 제거. = **BG4**.
- **L4 cell-matrix cold→uniform ×1.0** → parent3(exchange×strategy×regime) 풀 백오프: 티커풀<20이면 중립1.0 대신 strategy×regime tailored 강등(parent 테이블 이미 유지중). L4 activation-gate 스펙변경이라 **/debate 후 별 wave**(이번 미착수). 근거: per-ticker mandate 민감 + very-active 볼륨이 풀을 부분 자가치유(볼륨회복 후 재측정이 선행). 100% 죽은 배선 3건보다 후순위.
- **G3 MODIFY 0.85 fixed 트림** → phase1 배선(BG1), phase2 트림폭을 티커 유동성/ATR tailored(별 wave).
- **L3 base sizing 티커-uniform** → 티커차원은 cell(위)+Capital per-epic(BG3)로 진입; AI 게이트(G4 spread baseline)는 이미 tailored — 결정론 레이어와의 불균형을 위 3건이 좁힘.
