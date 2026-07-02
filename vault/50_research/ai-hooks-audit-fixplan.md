---
type: research
status: active
date_created: 2026-07-02
tags: [audit, fixplan, ai-judge, gates, metrics]
related: ["[[ai-hooks-audit-verdict]]", "[[trade_mess_full_audit_2026-07-02_fixplan]]"]
---

# AI 후킹 감사 — Fix Plan (2026-07-02)

전제: DEMO/PAPER · aggressive 보존 · flow_not_block. 쌍둥이 fix plan: [[trade_mess_full_audit_2026-07-02_fixplan]].

## P0
1. **G7 axis-B lockout 해제** — recalc 경로 G7 payload에 cell_routing 배선(warmth 복구) + fuser ground label 기록 완화(CONVICTION_FLOOR 미달 시도 label 기록) → robustness 0.50 도달 → judge_exit(EXTEND/TIGHTEN rail) 첫 가동. (rail: 이미 구현된 rail의 도달 복구, EXTEND=수익 연장)
2. **표시층 NET 단일 자 통일** — digest 양레그 net 헤드라인 + 보드 win_rate/PF를 코어 won과 동일 NET 기준 + measurement_resets에 06-27 리셋 행 스탬프. (측정 정직화, 사이징 무변)
3. **G3 strength_scalar → 사이저 배선** — `_sizer_payload.py:185`가 validated strength_scalar 반영, 단 **T4 continuous scalar 슬롯 안 결합**(별도 mult 신설 금지, 9-stack 체크 의무). 산식 = **/debate 대상**.

## P1
4. **REFINE_TIMING 소비자 구현 or verdict 회수** — one-shot TTL nudge 배선하거나 프롬프트에서 제거(판정 16% no-op 회수).
5. **counterfactual 판정 리더 + G4 stale_book 재조정** — 15,234행 자동 판정 신설, KILL +0.203R 실측 기반 임계 재캘리브(층화 검증 선행). 죽이던 양(+)신호를 흐르게.
6. **judge:* technical_flags agreement 리더** — 9,327행 파싱 리더 신설, ADR-011 shadow 관찰 레그 복원.
7. **binance top_LS 재베이스라인** — 고정 1.30 → 롤링 percentile/z-score, 100% BULL 상수 제거.
8. **news passthrough + judge 총 데드라인** — fuser가 news_max_age_h 복사(axis-B news leg 부활) + judge 콜 asyncio.wait_for wall-clock 상한(495s 점유 제거, 결정적 폴백 즉시).

## P2
9. **maker_fill_shadow 데드락 해제** — probe-size post-only 소량 실주문 or 승격 게이트 조건 대체 (#91 A/B 표본 확보).
10. **관찰 원장 위생** — market_events reader 신설 or INSERT 중단(WAL 경합 회수) + gate_shadow_events에 latency_ms/token 열(#55·비용 실측).

## 미해결 (판단 필요)
- G4 반사실 n=88 표본 편향 층화 검증 → 재캘리브 전 필수
- G7 해제 시 shadow 선행 관찰 여부 · strength_scalar 산식·클램프(/debate) · REFINE 구현 vs 제거 · market_events 존폐
