---
type: research
status: active
date_created: 2026-07-02
tags: [audit, gates, counterfactual, telemetry]
related: ["[[ai-hooks-audit-verdict]]", "[[layer-2-per-gate-pipeline]]", "[[ADR-011-ai-free-cutover|ADR-011]]"]
---

# G1-G8 게이트 가치 실측 (H4)

> 최근 3일 gate_events + 전 기간 counterfactual. [[layer-2-per-gate-pipeline]] [[ADR-011-ai-free-cutover|ADR-011]]

## 통과 구조 (3일)
- G1/G2 100% PASS(5,401) · G3 KILL 0(MODIFY 531=9.8%) · G4 KILL 129(2.4%, 전부 stale_book) · G5 100% SIZED → **생존율 97.6%**
- 전 게이트 model_used='python', 토큰 0 (ADR-011 커토버 — 결정사항, 무죄)
- 실동작 게이트 = G6/G7 (3일 ADJUST_EXIT 434 · EXIT_NOW 19)

## 결정적 배선 갭 2건
1. **G3 strength_scalar 사이저 미도달** — `signal_validator.py:270-279` 스탬프 vs `_sizer_payload.py:185`는 `raw_signal.strength` 직사용, 소비자 grep 0 → **G3 = 순수 텔레메트리 no-op** (MODIFY 531건 사이징 영향 0)
2. **shadow 관찰 레그** — legacy gpt_decision 중단은 의도(POLARIS_AI_FREE, 문서화). 실질 갭 = judge:* technical_flags 9,327행의 **agreement 리더 전무** (shadow_acceptance는 legacy만 보고 호출자 0)

## 선별가치 반사실 실측 (음성)
- KILL 코호트(n=88): fwd_r 1h **+0.203** / 4h +0.089
- PASS 코호트(n=9,530): 1h **-0.679** / 4h -0.674
- → 유일 필터가 양(+)신호를 죽이고 음(-)을 통과 — 선별 정보가치 0 내지 역. (신호 자체 품질은 별건 — 검증엣지=저빈도 추세 메모리 project_validated_edge_is_slow_trend_not_scalp 맥락)
- gate_kill_counterfactuals **15,234행 축적·소비 0** — 판정용 데이터 완성, 판정 로직 부재
- ⚠️ 반사실 코호트 n=88 표본 편향(시간대/티커 집중) 미검증 — 재캘리브 전 층화 검증 필요

## H4 = 부분 (무익 확인 / '비용만'은 틀림)

관련: [[ai-hooks-audit-verdict]] [[judge-probe-reality]]
