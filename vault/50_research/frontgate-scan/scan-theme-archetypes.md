---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, archetypes, trend, rotation, squeeze]
---

# Scan — 티커 특성 → 매매 아키타입

우리 관점 정리. 하우스 엣지 = 저빈도 추세/모멘텀 → slow-trend 정합 후보 우선.

## 후보
| 후보 | 증거 | 데이터 | 우리 처분 |
|---|---|---|---|
| TSMOM 12-1 (Moskowitz/Ooi/Pedersen 2012 JFE) | A | 있음 | **P2** — `tsmom_12_1_multiasset.py` 실존, 활성화 |
| 섹터/듀얼 모멘텀 로테이션 (Quantpedia 1928-2009) | A | 있음(섹터 ETF) | **P8** — G1 라우팅 전용, `index_dual_momentum_rotation.py` 실존 |
| TTM Squeeze (Carter / LazyBear) | B | 있음(BB20/2 + KC20/1.5ATR) | **P9** — deterministic, 이벤트 섀도우 선행 |
| Supertrend + EMA/ADX 스택 | B | 있음 | P1 로스터 활성화 멤버 · ADX → G7 엑싯 강도 컨텍스트 |
| Anchored VWAP 리버전/트렌드 | B | 있음(1m) | G4 타이밍 성분으로 이관(P6) |
| Gap-and-Go/Fade · ORB | B | 프리마켓 RVOL 흐름 미확인 | slow-trend 비정합 → 섀도우 후행 |
| 골드 매크로 (DXY/실질금리 상관) | C→승격(데이터 해금) | DFII10 1줄 부족 | **P5** — `fred_macro.py` `_SERIES` 추가로 해금 |
| 펀딩 캐리 (arXiv 2506.08573 · MDPI 14(2):346) | A | OKX 데모 = SPOT 전용, 퍼프 불가 | 보류(상품 접근성 선행) |
| Engle-Granger 페어 | B | 크로스티커 슬롯 없음 | 보류(L7 격리 슬롯 ADR 선행) |
| SMC / Order Block | C | 존 라벨링 필요 | 제외 |

## R1 디베이트 반영
- **TSMOM 파라미터 마이닝 경고 수용**: 문헌 표준(12-1, 월간, vol-norm) 고정으로 먼저
  발화. per-ticker 튜닝은 shadow-delta 증거 축적 후 단계 적용 — 근거 데이터 기반 조정이지
  사전 그리드 서치 아님(티커별 tailoring 원칙과 양립).
- **로스터 활성화 보완**: slow-trend 멤버(TSMOM·rotation·52wk·Supertrend) 우선 발화,
  ORB/GapGo는 섀도우 후행. INERT 함정 — 등록≠발화, dispatch 적대검증 의무.
- 섹터 로테이션 = XS-랭크 위 독립 방향 증폭기 중첩 금지, G1 라우팅 가치로 잔류(순위 하향).

통합 설계 → [[integration-blueprint]] · 실험 계획 → [[experiment-roadmap]]
