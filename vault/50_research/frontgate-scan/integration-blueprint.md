---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, blueprint, integration, gates, shadow, t4]
---

# Frontgate 통합 블루프린트 — 게이트별 설계 (계획 전용, 코드 0)

불변 조건: ① 전 항목 = 연속 스코어/컨텍스트/타이밍/사이징-값 입력(차단 0)
② 사이징 접점 = 기존 T4 continuous scalar(0.75–1.5) 값 설정만(9-stack 불변)
③ 신규 GPT 콜 0(뉴스 = 기존 gpt-5-mini 15분 캐던스 재사용) ④ 공통 진입로 =
`gate_shadow_events` behavior-0 섀도우 → agreement 측정 → 승격.

## 핵심 발견 — 신규 빌드 아닌 기존 확장
| 후보 | 기존 실파일 | 상태 |
|---|---|---|
| 뉴스 센티먼트 | `core/altdata/news_sentiment.py` | 가동 중 |
| 매크로(골드) | `core/altdata/fred_macro.py` (~30시리즈) | DFII10 1줄만 부족 |
| 메타라벨 | `core/learners/meta_label.py` (`meta_labels`) | 라벨 수집 중, 모델 미구축 |
| 레짐 | `regime_fit.py` + `learners/regime.py` + `altdata/fuser.py` | 스택 가동 중 |
| slow-trend 전략 | `strategies/tsmom_12_1_multiasset.py` 외 6+ | 파일 실존, 미활성 |
| 유동성 랭킹 | `universe/_ranking.py` z-composite | 기구현 |
| 섀도우 인프라 | `pipeline/agents/_shadow_rules.py` + `shadow_log.py` | 패턴 확립 |

## G1 — `universe/_ranking.py` + `watchlist.py`
- XS-모멘텀+52wk-high **단일 lagged 랭크 컴포짓**: `RANK_SCORE_W_*` 모멘텀 z-항 1개
  추가 → 티어 자동 흡수. t-1 close 랭크 → 익일 반영. IBD RS = 대체 공식 옵션(택일).
- 섹터/듀얼 모멘텀: 유니버스 클러스터링/컨텍스트 라우팅 전용(방향 가중 중첩 금지).
- RVOL z: watchlist cadence composite 1개 항만. 결측 히스토리 = 중립 z(0).

## G2 — `strategies/` + `agents/strategy_signal_gen.py`
- 로스터 활성화: `STRATEGY_REGISTRY` 등록 + **dispatch 발화경로 적대검증**(INERT 함정,
  등록≠발화). slow-trend(TSMOM·rotation·52wk·Supertrend) 우선, ORB/GapGo 섀도우 후행.
- TSMOM 12-1: 문헌 고정(12-1 월간 vol-norm) 선발화 → shadow-delta 후 per-ticker 튜닝.
- DFII10: 골드 전략 conviction 컨텍스트, FRED release-time-aware 소비.
- 뉴스 conviction: 타임스탬프 감사(publication vs ingestion) + 신디케이트 dedup 선행.

## G3/G5 — `agents/signal_validator.py` + `confidence.py`
- 캘리브레이션: Platt(pair 500–1k) → isotonic(1.5k–3k + bin 커버리지), 롤링 OOS,
  PAV 자체 구현(~50 LOC, 의존성 0), `posterior.py` NIG 교차검증. 양방향 교정.
- 메타라벨 2차 모델: 라벨 게이트(pooled 2k+ · 전략군 500+ · purged) 충족 시 기존 T4
  scalar 값 설정(`regime_fit.regime_scalar` 패턴, floor 0.75). 미달 = 섀도우 병기.
- HMM: 병렬 레짐 라벨 컬럼 섀도우만(기존 스택 가동 중, 저순위).

## G4 — `agents/pre_entry_watcher.py`
- VWAP/AVWAP: known-at-time 앵커만(세션 시작·시그널 발생일, 사후 피벗 금지).
  `regime_fit.confirm_tightness` 지연 패턴 — 지연≠거부(flow 유지).
- TTM Squeeze: squeeze-on=감시 유지, release=deterministic 트리거 watch-피처.
- StockTwits: 워치셋 심볼 한정 컨텍스트 성분(월 1k콜 내, 단독 사용 금지).

관련: [[layer-0-universe-discovery]] · [[layer-2-per-gate-pipeline]] · [[layer-3-sizing-risk]]
실험 순서/승격 기준 → [[experiment-roadmap]] · 카탈로그 → [[scan-curation-ranking]] 외 4편
