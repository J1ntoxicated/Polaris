---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, oss, frameworks, license, feeds]
---

# Scan — OSS 퀀트 인프라/데이터 광맥

우리 관점 정리. 원칙: 라이브 봇에 프레임워크 이식 X — 참조/오프라인 도구/데이터만.

## 후보
| 후보 | 라이선스 | 우리 처분 |
|---|---|---|
| microsoft/qlib (Alpha158) | MIT | 후순위 — handler 참조, 오프라인 IC 측정부터 |
| AQR Value & Momentum Everywhere 팩터 (Asness/Moskowitz/Pedersen 2013 JF) | 공개 데이터 | **오프라인 벤치마크 트랙** — `fuser.py` 레짐 제안 교차검증 |
| vectorbt (polakowo) | Apache-2.0 + Commons Clause | 개발용 파라미터 스윕 하네스(내부 사용 무제한) |
| alphalens (quantopian) | Apache-2.0 | 팩터 IC 분석 오프라인 도구 후보 |
| QuantConnect/Lean VWAP 실행모델 | Apache-2.0 | **코드 참조만**(vendoring 불요) — P6 근거 |
| OpenBB | AGPLv3 | 제외 — 카피레프트 + 현 데이터 격차와 불일치 |
| freqtrade | GPLv3 | 제외 — 카피레프트 + 스캘핑형(하우스 엣지 비정합) |
| zipline-reloaded | Apache-2.0 | 제외 — 자체 3-트랙 바 파이프라인과 중복 인프라 |
| Kaggle JaneStreet/Optiver 솔루션 | 개별 | 제외 — HF 피처/L2 오더북 데이터 없음 |
| Two Sigma Insights | — | 제외 — 코드/데이터 미공개, 재현 불가 |
| hudson-and-thames/mlfinlab | 상용(all rights reserved) | **vendoring 금지** — 논문 방법론 자체 구현 |
| skyte/relative-strength | 공개(유지보수 중단) | 공식 참조만 |

## 라이선스 지뢰 요약
- vendoring 금지: mlfinlab(상용) · AGPL/GPL(OpenBB·freqtrade) 라이브러리 통합 금지
- 참조-온리: QC Lean · skyte — 공식/구조만 차용, 코드 복사 없이 자체 구현
- 데이터는 자유: AQR 팩터 다운로드(코드 아님, 마찰 0)

## 신규 피드 획득 우선순위 (무료/저가)
1. DFII10 실질금리 — `fred_macro.py` `_SERIES` 1줄(FRED 키 보유, 비용 0) → P5 해금
2. 컨센서스 EPS(PEAD용) — Finnhub 무료 tier 실존/범위 WebFetch 검증 = 다음 리서치 웨이브
3. StockTwits 무료 tier — G4 워치셋 한정 호출로 레이트리밋 회피
4. AQR 팩터 월간 다운로드 · 5. FinBERT HF 가중치 — lexicon 폴백 업그레이드로만

통합 설계 → [[integration-blueprint]] · 인덱스 → [[scan-event-models]]
