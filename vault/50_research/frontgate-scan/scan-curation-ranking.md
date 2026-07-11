---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, universe, ranking, momentum]
---

# Scan — G1 유니버스 큐레이션/랭킹 모델

우리 관점 정리. 통합점 = `universe/_ranking.py` z-composite + `watchlist.assign_tiers`.
전부 연속 스코어 성분 — 단독 하드 플로어/게이트화 금지(flow, no-block).

## 후보
| 후보 | 증거 | 데이터 | 신호형태 | 우리 처분 |
|---|---|---|---|---|
| XS-모멘텀+52wk-high 통합 랭크 (Jegadeesh&Titman 1993 JF · George&Hwang 2004 JF) | A | 있음(일봉 52주) | 횡단면 lagged 랭크 | **P3 채택** — 단일 컴포짓 |
| IBD RS percentile (skyte/relative-strength) | B | 있음 | 0-100 백분위 | P3의 대체 공식 옵션만(동일 슬롯 택일) |
| GBDT 랭커 (microsoft/qlib Alpha158) | B(우리 유니버스 미검증) | 피처 저장소 신규 | 연속 예측 스코어 | 후순위 — 오프라인 IC 측정부터 |
| RVOL z-score | C | 있음(분봉) | 연속 z | watchlist cadence composite 1개 항만 |
| 달러볼륨 유동성 랭킹 (QuantConnect 문서) | B | 있음 | 연속 스코어 | 기구현(z-composite) — 추가 작업 없음 |

## R1 디베이트 반영
- **슬롯 중복 해소**: XS-모멘텀·52wk-high·IBD RS = **단일 lagged 랭크 컴포짓 1개**로
  통합. t-1 close 랭크 → 익일 반영, 수정주가 패널 필수. 동일 슬롯 이중 탑재 금지.
- 섹터 로테이션은 이 랭크 위 방향 증폭기로 중첩 금지 — G1 유니버스/컨텍스트 라우팅 전용.

## 설계 요점
- `schema.py` `RANK_SCORE_W_*`에 모멘텀 z-항 1개 추가 → `rank_active_universe`가 흡수,
  티어는 `watchlist.assign_tiers` 퍼센타일 자동 반영. GPT 0, 침습 최소.
- 섀도우 = 후보랭크 vs 현행랭크 delta 컬럼만 기록 (behavior-0, 즉시 가동 가능).
- Capital 심볼 히스토리 깊이 불균일 → 결측 = 중립 z(0) 처리.

통합 설계 → [[integration-blueprint]] · 실험 계획 → [[experiment-roadmap]]
