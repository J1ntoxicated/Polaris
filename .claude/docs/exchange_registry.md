# Exchange Registry — 거래 시간 / 티커 / 특징 SSOT

> Jin 04-19 03:40 — 각 exchange 별 특성 정리. `ops-exchange-registry` advisor 참조 대상. 값 drift 시 Harness 가 갱신.

## OKX (primary test bed, crypto)

| 항목 | 값 |
|---|---|
| **Market hours** | 24/7 (중단 없음) |
| **Asset group** | crypto (spot + perpetual swap) |
| **Universe** | ~290 instruments (log: "Scanning 290 OKX instruments") |
| **Fee (paper)** | maker/taker 0.08%/0.1% (paper simul, real spec 확인 필요) |
| **Min size** | instrument 별 contract_val |
| **API** | REST + WS (funding, ticker, trade) |
| **Rate limit** | REST 20 req/2s public, WS 무제한 구독 |
| **Strategy family eligible** | crypto_momentum_reversal, crypto_contrarian, whale_fade, etc. (family_seeds 참조) |
| **Settlement currency** | USDT, USDC |

## Capital.com (forex/indices/commodity)

| 항목 | 값 |
|---|---|
| **Market hours** | 주말 휴장 (Sat 00:00 UTC ~ Sun 22:00 UTC 전후). instrument 별 Timetable 상이 |
| **Asset group** | forex, indices, commodity |
| **Universe** | ~1197 instruments (log: "Capital 1197 instruments") |
| **Fee** | spread-based (bid/ask), commission 0 |
| **Min size** | instrument 별 minDealSize |
| **API** | REST + WS (epics subscribe) |
| **Rate limit** | 60 req/60s general, 10 req/sec order |
| **Strategy family eligible** | contrarian_commodity, indices_specialist, 등 |
| **Settlement** | USD (account base) |

## Alpaca (US stocks / ETF)

| 항목 | 값 |
|---|---|
| **Market hours** | Mon-Fri 09:30-16:00 ET (NYSE), 주말/공휴일 휴장. Live clock via `/v2/clock` (30s cache, hardcoded fallback) |
| **Asset group** | stock, ETF |
| **Universe** | paper account 전체 US listed |
| **Fee (paper)** | commission-free |
| **Min size** | 1 share (fractional 가능) |
| **API** | REST + WS (Trade/Quote stream) |
| **Rate limit** | 200 req/min (paper) |
| **Strategy family eligible** | stock_specialist, etf_specialist, 등 |
| **Settlement** | USD, T+2 (paper simul) |

## Binance (data-only, not for trading)

| 항목 | 값 |
|---|---|
| **Purpose** | funding/OI/kline feed 보조, **거래 X** |
| **Market hours** | 24/7 |
| **Usage** | cross-exchange signal confirmation, OKX 대비 data |

## 주요 Timetable 참고

- **Forex**: Sun 22:00 UTC (시드니 open) ~ Fri 22:00 UTC (NY close). 48h 휴장 (Sat full + Sun part).
- **US Stocks**: Mon-Fri 14:30-21:00 UTC (NYSE regular). pre/post market 제외.
- **Crypto**: 365d × 24h. Halving/fork 외 중단 없음.

## Cross-exchange impact checklist (OKX-tested → Alpaca/CAP)

| 영역 | 주의점 |
|---|---|
| Strategy family | `allowed_exchanges` frozenset (family_seeds.py) 재확인 |
| Exit logic | TIME exit hold_sec 이 market_closed 기간 포함 시 skew |
| Fee / slippage | OKX 0.1% vs Alpaca 0% vs CAP spread — PnL 계산 차이 |
| Size | contract (OKX) vs share (Alpaca) vs CFD lot (CAP) |
| Liquidation | OKX 강제청산 vs Alpaca 마진콜 vs CAP stop-out |

## 갱신 규정

- 신규 exchange 추가 / 시간대 변경 / 티커 pool 급변 → Harness 업데이트
- 봇 코드 (`family_seeds.py`, `exchange/*.py`) 와 본 문서 불일치 → `harness-drift-detector` 발견 시 갱신
