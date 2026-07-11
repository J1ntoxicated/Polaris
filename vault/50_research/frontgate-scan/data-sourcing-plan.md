---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, data-sourcing, feeds, provenance]
---

# Frontgate 데이터 소싱 플랜 — 척후병 피드 개통 순서

DEMO/PAPER 가상계정 · aggressive bias 보존 · 전 피드 = EVIDENCE 입력(차단 0 · 신규 multiplier 0).
원칙: 무료 즉시 → 저가 → 보류. **타임스탬프 provenance = 1급 기준**(publication vs ingestion 분리).
조사 전량(기각 포함) → [[data-sourcing-catalog]] · 봇 내부 LLM = OpenAI GPT만(신규 GPT 콜 0 설계).

## 1. SEC EDGAR submissions — 무료·무키, 즉시
- 절차: 가입 0 — `User-Agent: "Polaris <email>"` 헤더만(라이브 200 확인). `company_tickers.json` 티커→CIK 맵 1회 캐시.
- 모듈: `polaris/core/altdata/sec_edgar_events.py` — `AltDataCollector` 계약(ttl 300s, equity, 오류→`{}`),
  8-K/10-Q 필터, 10 req/s 클라이언트 페이싱(`okx_funding.py`의 httpx 패턴 동형).
- 스토리지: 신규 `frontgate_events(source, symbol, event_id, pub_ts, ingest_ts, payload_json, UNIQUE(source,event_id))` — 이벤트류 공용 테이블.
- TS 감사: `acceptanceDateTime` 초정밀(A등급) vs 자체 ingest_ts 분포 측정. R1 유의 유지 —
  acceptance ≠ 시장 최초 시각(프레스릴리스 선행 가능), forward-return 상관은 ingestion 시각 기준만.
- 신호: G3 이벤트-섀도우 컨텍스트 + G4 filing-arrival 워치(getcurrent ATOM 병용).
  **[[scan-event-models]] R1이 못박은 PEAD/SUE 섀도우 해제 선행조건을 이 피드가 충족.**

## 2. DefiLlama Stablecoins — 무료·무키, 즉시
- 절차: 가입 0. `stablecoins.llama.fi/stablecoins` 실응답 필드 검증 완료(circulating/PrevDay/PrevWeek).
- 모듈: `altdata/defillama_stables.py` — ttl 3600s+, crypto, 유니버스 quote 스테이블(USDT/USDC/DAI) 필터,
  24h 넷민트율 순수함수(`binance_deriv.compute_oi_change_24h` 동형 패턴).
- 스토리지: 신규 `altdata_snapshots(source, symbol, ts, metrics_json)` — prevDay 한계 → 자체 스냅샷 축적으로 백필 확장.
- TS 감사: 상태 스냅샷형(FRED/FNG 동급) — pub-vs-ingest lag 이슈 자체 없음, 일간 해상도만 기록(B).
- 신호: G1 랭킹 유동성 레짐 + G3 넷민팅=risk-on 컨텍스트 — 기존 4모듈이 못 보던 "유동성 유입" 축 최초.

## 3. Finnhub earnings calendar — 무료 tier, 가입 1회
- 절차: Finnhub 가입 → `.env` `FINNHUB_API_KEY` 신규(60 calls/min, 기존 보유 키 아님).
- 모듈: `altdata/earnings_calendar.py` — ttl 21600s, equity, `{earnings_date, hour, eps_estimate, eps_actual, surprise_pct}`.
- 스토리지: `frontgate_events` 공용(event_id=`symbol:date`).
- TS 감사: date+hour(bmo/amc/dmh) 버킷 = B등급 — actual 공표 "순간"은 EDGAR acceptance(#1)와 결합해 보완.
- 신호: G4 프리엔트리 이벤트 근접 워치 + **컨센서스 EPS 공급 → SUE 산출 가능**(#1과 페어로 PEAD 재상정 조건 완성).

## 4. Coinglass 키 개통 — $29/mo Hobbyist, 저가 (코드 변경 0줄)
- 절차: 구독 → `.env` `COINGLASS_API_KEY` 값만 — `coinglass.py` 완성 상태(현 INACTIVE, `fetch()`→`{}`). Jin 사전 1줄 고지 후 개통.
- 스토리지: 기존 cache 경로 그대로. 청산 rows = ms epoch(A등급), 24h 백필 한계 → `altdata_snapshots` 선택 축적.
- 신호: G2 청산-플러시 반전 conviction(**유일 소스**, Binance는 포지셔닝 비율만) + G3 거래소간 펀딩 다이버전스.

## 5. GDELT GKG 2.0 — 무료·무키, integration-cost 후순위
- 절차: 가입 0. `lastupdate.txt` → 15분 배치 3파일(export/mentions/gkg).
- 모듈: `altdata/gdelt_gkg.py`(ttl 900s) + **회사명→심볼 룩업 레이어 신설 필요** — canon 보류 사유가 이 매핑 비용(access 문제 아님).
- TS 감사: 15분 버킷(B등급) — 초단위 publish 미검증, 이벤트-톤 상관은 버킷 시각 기준만.
- 신호: G2 이벤트-톤 신규 후보. 매핑 레이어 빌드 완료가 개통 트리거.

## 보류 & 공통
- StockTwits: 비인증 403(Cloudflare, 2026-07-11 확인) — firestream 포털 앱 등록 후 재검증. RSS 와이어: Alpaca News provenance 감사 결함 발견 시만 백스톱.
- 공용 선행: `news_sentiment.py` `created_at` vs ingest를 `frontgate_events`에 기록 시작 = P7(뉴스 conviction) 전제 감사.
- 승격 공통: 전 피드 `gate_shadow_events` behavior-0 섀도우 → agreement 측정 → [[experiment-roadmap]] 숫자 기준으로 승격. 통합 설계 → [[integration-blueprint]].
