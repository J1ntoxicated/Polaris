---
type: research
status: active
date_created: 2026-07-02
tags: [audit, stores, shadow, maker]
related: ["[[ai-hooks-audit-verdict]]", "[[gate-pipeline-value-audit]]", "[[layer-5-learner-network]]"]
---

# 스토어 센서스 (H5 = 부분)

## 분류 (11개)
- **ALIVE 3**: ticker_technicals · ticker_ground(judge payload 소비) · regime_state(G3/swap/[[dashboard]])
- **ALIVE-display 2**: entry_admission_shadow · weekend_shadow_orders (표시 전용, 의사결정 소비 0)
- **WRITE-ONLY 무덤 3**: market_events **17,512행 reader 0** (regime_flip 100%, 24h 1,913 INSERT — WAL 단일-writer 경합 기여) · ai_lessons · meta_labels (주석 명시 의도적 원장)
- **0행 인프라 3**: maker_fill_shadow(데드락) · benchmark_results · replay_runs (6/23 빌드 후 1회도 미실행 — G3 read-model dormant)

## 최대 발견 — maker_fill_shadow 순환 데드락
- 배선 정상(`_production_pipeline.py:630`)이나 쓰기조건 = OKX post-only 실체결(`_okx_post_only.py:322`)
- 봉쇄 3중: ① 주말maker shadow-first 실주문 억제(d5c98a1) ② 6/28 이후 OKX 실체결 25건 전부 TREND→marketable-limit 분기 ③ 주중 REVERSION OKX 발화 0
- 그런데 주말maker **승격이 이 테이블 축적에 게이트** → 닭-달걀, **#91 A/B 표본 영구 0**
- 해제: probe-size post-only 소량 실주문 허용 or 승격 게이트 조건 대체 (흐름 생성, 차단 아님)

## 유지비
- 디스크 ~2.3MB 미미; 실비용 = market_events INSERT 경합 + '판정 데이터 있는데 판정 없음' 기회비용(gate_kill_counterfactuals 15k행 동일)

관련: [[ai-hooks-audit-verdict]] [[gate-pipeline-value-audit]]
