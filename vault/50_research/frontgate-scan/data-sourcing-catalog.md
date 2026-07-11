---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, data-sourcing, catalog, feeds]
---

# Frontgate 데이터 소싱 카탈로그 — 조사 전량 (채택·보류·기각)

3도메인 스카우트(equity-events · news-social · crypto-native) 취합. 실행 계획 → [[data-sourcing-plan]].
TS등급: A=초/ms 정밀 원천 시각 · B=버킷/일간 or pub-ingest 미분리 · C=약함/취약.

## 채택 (개통 순서는 plan 기준)
| 소스 | 비용 | 인증 | 리밋 | TS | 백필 | 신호 |
|---|---|---|---|---|---|---|
| SEC EDGAR submissions | 무료 | User-Agent만 | 10 req/s | A | 전체 | G3 이벤트-섀도우 + PEAD 선행조건 |
| DefiLlama Stablecoins | 무료 | 없음 | 미명시(TTL 자율) | B | prevDay/Week | G1 유동성 레짐 + G3 넷민팅 |
| Finnhub earnings cal | 무료 tier | 키(신규 가입) | 60/min | B | 과거+forward | G4 이벤트 근접 + 컨센서스 EPS(SUE) |
| Coinglass(기존 모듈) | $29/mo | CG-API-KEY | 티어별(구독 시 확인) | A | 24h | G2 청산-플러시 + G3 펀딩 다이버전스 |
| GDELT GKG 2.0 | 무료 | 없음 | fair-use | B | 2015~ CSV | G2 이벤트-톤(심볼 매핑 선행) |
| EDGAR getcurrent ATOM | 무료 | 없음 | 10 req/s | A | 당일만 | G4 filing-arrival 트리거 |

## 가동 중 (기존 ACTIVE — 참고)
Alpaca News(`news_sentiment.py`, B — created_at vs ingest 감사 미완=P7 전제) · okx_funding(A) · binance_deriv(A) · crypto_fg(B) · fred_macro.

## 보류
- StockTwits streams/symbol: 비인증 curl 403(Cloudflare, 2026-07-11 라이브 확인) — 구 "무인증 200/hr" 가정 무효 가능성 高,
  firestream 포털 앱 등록+키 발급 후 재검증. TS B(포스트 `created_at` 분리 가능). G4 워치셋 한정 호출 설계 유지.
- RSS 와이어(BusinessWire/GlobeNewswire/PRN): TS A 최상류이나 Alpaca News가 동일 와이어를 벤더 경유 재수집 — 중복 부담 > 신규 엣지.
  개통 조건: provenance 감사에서 Alpaca TS 결함 발견 시만(dedup=story URL/headline 정규화 해시 설계 예고).

## 기각
- FMP earnings calendar: 유료벽 이동(무료 250 req/day가 해당 엔드포인트 미커버) — Finnhub와 중복 비용.
- Alpaca Corporate Actions: 배당/분할/합병만, earnings 일정 명시적 제외 — 데이터 클래스 불일치.
- Nasdaq `api.nasdaq.com` 스크레이프: 비공식·ToS/리밋 미공개 — 동일 데이터 클래스 대비 취약.
- Reddit OAuth: 2026-05 이후 비인증 전면 403 + 상업 유료($0.24/1k, 승인 수 주) + 유니버스 불일치(canon 태깅) — 3중 사유.
- X/Twitter API: 무료 tier 폐지, pay-per-use($0.005/read)만 — 무료/저가 원칙 밖.
- LunarCrush v4: Discover 무료 "극도 제한"·리밋 미공개, 실사용 최저 $90/mo — 저가 원칙 밖.
- Whale Alert: API 개발자 계정 가격 비공개(소비자 앱 ≠ API) — 예산 불확실로 보류성 기각.
- CryptoQuant 넷플로: 상위 플랜($99/mo+) 게이팅 추정 + Coinglass와 포지셔닝 축 중복.
- Blockchain.info Charts: BTC 단일자산 — 멀티코인 유니버스 비정합.
- FinGPT/GDELT 풀 NLP 스택: 내부 LLM=GPT 고정 원칙 충돌(canon 기각 재확인).

## 검증 각주 (실존/불확실 구분)
- 라이브 검증: EDGAR `acceptanceDateTime` 초정밀(curl 200) · DefiLlama 실응답 필드 · StockTwits 403.
- 문서 검증: Finnhub 필드/60 calls/min(2026 docs) · EDGAR 10 req/s · Reddit 100qpm · X pay-per-use.
- 불확실 표기: Alpaca News ingest-vs-publish gap 미검증 · GDELT 리밋 공식 미명시 · Coinglass Hobbyist 정확 리밋(구독 시 확인).

관련: [[scan-event-models]] · [[scan-frameworks]] 우선순위 리스트의 소싱 항목을 본 카탈로그가 대체/구체화.
