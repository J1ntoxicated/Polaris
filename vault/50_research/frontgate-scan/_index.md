---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, research-index, universe, gates]
---

# Frontgate Scan — 전면 게이트 강화 리서치 인덱스 (2026-07)

DEMO/PAPER 가상계정 · aggressive bias 보존 · 이번 웨이브 = 계획/조사만(코드 0).
전 후보 = 스코어/컨텍스트/타이밍/사이징-값 입력 (차단 필터 0 · 신규 multiplier 0).
디베이트: 코덱스 R1 교차검증 완료 — BREAK 7건 수용, 부분수용 2, 기각 0.

## 문서 지도
- [[scan-event-models]] — 이벤트/뉴스/소셜 센티먼트 (FinBERT·PEAD·StockTwits·GDELT)
- [[scan-curation-ranking]] — G1 유니버스 랭킹 (XS-모멘텀·52wk-high·IBD RS·GBDT·RVOL)
- [[scan-theme-archetypes]] — 티커 아키타입 (로테이션·TTM Squeeze·Supertrend·AVWAP·골드매크로)
- [[scan-validator-models]] — G3/G4/G5 (메타라벨·캘리브레이션·HMM·VWAP 타이밍·비용모델)
- [[scan-frameworks]] — OSS 인프라/데이터 (Qlib·AQR·vectorbt·라이선스 지뢰)
- [[integration-blueprint]] — 게이트별 통합 설계 (핵심; 기존 코드 재사용 맵)
- [[experiment-roadmap]] — 우선순위 10 단계별 실험 계획 (섀도우→PROVE→승격 숫자 기준)

## 최종 수렴 우선순위 (요약)
1 기존 로스터 활성화(G2) · 2 TSMOM 12-1(G2) · 3 XS-모멘텀+52wk 통합 랭크(G1)
4 확률 캘리브레이션(G5) · 5 DFII10 골드 컨텍스트(G2) · 6 VWAP/AVWAP 타이밍(G4)
7 뉴스 센티먼트 conviction(G2/G3) · 8 섹터 로테이션(G1) · 9 TTM Squeeze(G2+G4)
10 메타라벨 2차 모델(G3/G5)

## 핵심 발견
스캔 후보 다수가 이미 코드베이스에 실존 — 신규 빌드가 아니라 활성화/확장이 1순위
(slow-trend 전략 파일 6+, news_sentiment 가동 중, fred_macro 1줄, meta_label 수집 중).
공통 진입로 = `gate_shadow_events` behavior-0 섀도우 → agreement 측정 → 승격.

## 리스트 밖 처분
- PEAD: 타임스탬프 provenance 검증된 이벤트 피드 확보 시 재상정 (섀도우 이벤트 로그만 즉시)
- AQR 팩터: 오프라인 벤치마크 트랙 · HMM/RVOL: 저순위 섀도우 유지

관련: [[ADR-003-8-layer-architecture|ADR-003]] · [[layer-2-per-gate-pipeline]]
