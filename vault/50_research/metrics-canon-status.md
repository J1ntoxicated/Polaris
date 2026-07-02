---
type: research
status: active
date_created: 2026-07-02
tags: [audit, metrics, measurement, net-gross]
related: ["[[ai-hooks-audit-verdict]]", "[[trade_mess_full_audit_2026-07-02_verdict]]", "[[layer-4-cell-matrix]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]"]
---

# metrics-canon 현황 (H3 = 맞음)

> fills→positions→learner→rollup→digest→dashboard 원장 사슬. fee-canon(#46~48) 이후.

## 정직한 것 (코어)
- cell/learner/posterior/won 판정 = `compute_net_pnl_r` **양레그 실수수료 NET 단일 자** 일관 폴드
- 대시보드 일일 헤드라인(-344.71) = DB 직쿼리와 센트 일치 (양레그 NET)
- mfe_r/mae_r 샘플 재계산 byte-일치 · #46~48 병리(고아 fill/진입fee 누락/quote-ccy/ATR-floor) 재발 없음

## 혼재 3중 (표시층)
1. **digest GROSS**: `daily_digest.py:65-70` is_close=1 필터를 pnl·fee 양쪽 적용 → 진입fee 미표시. 06-30: **+6.05 표시 vs 실 net ≈ -108.9** (진입fee 57.48 증발)
2. **보드 win_rate/PF GROSS**: `snapshot_q_strategy.py:112-114` f.pnl_usd>0 → **27.6% vs net 8.6%** (gross 29승 vs net 9승) — 학습층 won과 3배 괴리, fee≈gross 스캘프가 승리로 표시
3. **since-reset dormant + 제3의 net**: measurement_resets 0행(06-27 리셋을 DB 교체로 수행, 스탬프 누락) → since_reset=None silent fallback. 그 코드의 net 정의(청산fee만)조차 헤드라인(양레그)과 상이

## 잠재 결함 1 (미발화)
- partial-close slice 폴드가 슬라이스마다 진입fee/슬리피지 **전액 재차감** (`_production_close_effects.py:99-144, 294-382`) → partial+final 2회 폴드 시 이중차감+관측 n 2배. 현 DB partial 0건

## 수정 방향
- NET 단일 자 통일(digest 양레그 net + 보드 NET win_rate + reset 행 스탬프) — 측정 정직화, 사이징 무변

관련: [[ai-hooks-audit-verdict]] [[dashboard]]
