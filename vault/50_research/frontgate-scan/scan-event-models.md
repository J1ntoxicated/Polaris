---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, events, sentiment, news]
---

# Scan — 이벤트/뉴스/소셜 센티먼트 모델

우리 관점 정리(원문 요약 아님). 전 후보 = 스코어/컨텍스트 입력, 차단 0.
핵심: **뉴스 센티먼트는 이미 가동 중** — `polaris/core/altdata/news_sentiment.py`
(Alpaca News + gpt-5-mini, ~15분 캐던스, per-symbol {sentiment, relevance, magnitude}).

## 후보 (우리 기준 태깅)
| 후보 | 증거 | 데이터 | 신호형태 | 지연 | 우리 처분 |
|---|---|---|---|---|---|
| 뉴스 센티먼트 conviction | A(가동 피드) | 있음 | 연속 스코어 | 15m | **P7** — 타임스탬프 감사+dedup 선행 |
| FinBERT (ProsusAI/finBERT, arXiv 2019) | A | HF 무료 | pos/neu/neg 확률 | ms | lexicon 폴백 업그레이드만(신규 엣지 아님) |
| PEAD/SUE (Livnat & Mendenhall 2006) | A(방법)/미검증(데이터) | 컨센서스 EPS 부재 | SUE 연속 | 이벤트 | 리스트 밖 — 섀도우 이벤트 로그만 |
| StockTwits API | B/C | 무료 tier 월 1k콜 | bull/bear% + 볼륨 | 실시간 | G4 워치셋 한정 컨텍스트 성분 |
| GDELT 톤 | B | 대용량 ETL | 일간 톤 | 15m | 보류(티커 매핑 파이프라인 없음) |
| Reddit/WSB | C | PRAW | 언급빈도 | 분 | 제외(유니버스 커버리지 불일치) |
| FinGPT (AI4Finance) | B | GPU LoRA | 스코어 | 분 | 제외(내부 LLM=GPT 고정, 인프라 중복) |

## R1 디베이트 반영 (수용)
- **PEAD 강등**: 컨센서스 EPS 부재 시 SUE = EDGAR actual 프록시일 뿐("unexpected" 아님).
  8-K accepted-time ≠ 시장 최초 정보시각, 프레스릴리스가 EDGAR 선행, 장외 발표→익영업일
  바 매핑 미검증. 타임스탬프 provenance 확보 전 = 이벤트 섀도우 로그만, 근일 톱리스트 제외.
- **타임스탬프 감사 의무**: ingestion-time vs publication-time 정렬 감사 + 신디케이트
  중복 제거 선행. forward-return 상관은 ingestion 시각 기준만 신뢰.

## 게이트 매핑
- G2: 기존 추세 전략 conviction 입력(연속) + 뉴스-이벤트 아키타입 후보
- G3: 밸리데이터 컨텍스트 스코어 — 기존 gpt-5-mini 집계 재사용, 신규 GPT 콜 0
- G4: StockTwits 볼륨 스파이크 = 워치 컨텍스트 성분 (C등급 단독 사용 금지)

통합 설계 → [[integration-blueprint]] · 실험 계획 → [[experiment-roadmap]]
