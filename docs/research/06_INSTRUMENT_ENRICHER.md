# 06 — Instrument Enricher

> Phase 2 진행 중 추가.
> ML 피처 및 AI 컨텍스트 품질 향상을 위해 `instrument_profiles` 테이블을 풍부하게 채우는 수집기.

---

## 목적

현재 `instrument_profiles` 에는 거래소 메타(fee, leverage, trading_hours)만 있음.
아래 데이터를 추가로 채워야 ML 피처 빌더(Task 2-B)와 AI 컨텍스트(context_builder.py)가 제대로 작동함.

---

## 추가할 컬럼 (unified_schema.py)

```sql
ALTER TABLE instrument_profiles ADD COLUMN sector TEXT;          -- L1/L2/DeFi/Meme/AI/FX_major 등
ALTER TABLE instrument_profiles ADD COLUMN corr_btc REAL;       -- BTC 상관계수 (-1~1)
ALTER TABLE instrument_profiles ADD COLUMN avg_atr_pct REAL;    -- 평균 ATR% (30일)
ALTER TABLE instrument_profiles ADD COLUMN session_peak TEXT;   -- 'london'/'ny'/'asia' (FX용)
ALTER TABLE instrument_profiles ADD COLUMN coingecko_id TEXT;   -- CoinGecko ID (크립토용)
ALTER TABLE instrument_profiles ADD COLUMN enriched_at INTEGER; -- 마지막 enrichment unix ts
```

---

## 신규 파일

**`invasion/data/collectors/instrument_enricher.py`**

---

## 수집 소스 & 로직

### 크립토 (OKX)

```
소스 1: OKX Public API (이미 일부 수집 중 — 보강)
  GET /api/v5/public/instruments?instType=SWAP
  → ctVal, tickSz, minSz, lever (최대 레버리지)

소스 2: CoinGecko API (무료, API 키 불필요)
  Step 1: GET https://api.coingecko.com/api/v3/coins/list
          → symbol → coingecko_id 매핑 테이블 생성
          (BTC-USDT-SWAP → 'btc' → 'bitcoin')
  Step 2: GET https://api.coingecko.com/api/v3/coins/{coingecko_id}
          → categories: ["Layer 1", "Smart Contract Platform", ...]
          → 첫 번째 카테고리를 sector로 저장

소스 3: 우리 DB candles → 직접 계산
  avg_atr_pct = mean(ATR(14) / close * 100) — 최근 30일
  corr_btc    = pearsonr(ticker_close, btc_close)[0] — 최근 30일
```

### FX (Capital.com)

```
소스 1: Capital.com markets API (이미 있음 — 보강)
  → spread, pip size 등 추가

소스 2: 우리 DB candles → 세션별 변동성 계산
  london_vol = std(candles where hour in 7~16 UTC)
  ny_vol     = std(candles where hour in 13~22 UTC)
  asia_vol   = std(candles where hour in 0~9 UTC)
  session_peak = 가장 높은 변동성 세션명

sector는 고정값으로:
  EUR/USD, GBP/USD 등 → 'FX_major'
  XAU/USD, XAG/USD   → 'commodity_metal'
  US30, NAS100       → 'index_us'
  GER40, UK100       → 'index_eu'
```

### 주식/ETF (Alpaca)

```
소스 1: FinanceDatabase (pip install financedatabase)
  import financedatabase as fd
  equities = fd.Equities()
  info = equities.select(symbol='AAPL')
  → sector, industry, country

소스 2: Alpaca assets API (이미 있음)
  → fractionable, shortable, easy_to_borrow
```

---

## 실행 주기

```
하루 1회 (history_sync tick, 3600s bg) 에 얹기
또는 별도 enricher tick 등록:
  sched.register(86400, enricher.tick, 'enricher', background=True)
```

---

## param_registry 추가

```
enricher_enabled: true
enricher_coingecko_rate_limit: 1.5   # 초당 요청 간격 (무료 티어 제한)
enricher_corr_lookback_days: 30
enricher_atr_lookback_days: 30
```

---

## 구현 스텝

```
1. unified_schema.py — 컬럼 6개 ADD COLUMN (migration 필요)

2. data/collectors/instrument_enricher.py 신규
   class InstrumentEnricher:
       def tick(self, ctx): ...
       def _enrich_crypto(self, tickers): ...
       def _enrich_fx(self, tickers): ...
       def _enrich_stocks(self, tickers): ...
       def _fetch_coingecko_categories(self, coingecko_id): ...
       def _calc_corr_btc(self, ticker): ...
       def _calc_avg_atr(self, ticker): ...
       def _calc_session_peak(self, ticker): ...

3. ticks/history_sync.py 또는 ticks/data_collector.py 에서 호출:
   enricher = InstrumentEnricher()
   enricher.tick(ctx)

4. param_registry에 키 추가

5. context_builder.py 에서 sector, corr_btc, avg_atr_pct 읽어서 AI 컨텍스트에 포함
```

---

## 검증

```bash
# enricher 단독 실행
python3 -c "
from invasion.data.collectors.instrument_enricher import InstrumentEnricher
e = InstrumentEnricher()
e._enrich_crypto(['BTC-USDT-SWAP', 'ETH-USDT-SWAP'])
"

# DB 결과 확인
python3 -c "
import sqlite3
conn = sqlite3.connect('data/invasion.sqlite')
rows = conn.execute(
    'SELECT ticker, sector, corr_btc, avg_atr_pct, enriched_at FROM instrument_profiles LIMIT 10'
).fetchall()
for r in rows: print(r)
"
```

---

## 주의사항

- CoinGecko 무료 티어: 분당 30회 제한 → `enricher_coingecko_rate_limit: 1.5s` 간격 필수
- corr_btc 계산 시 BTC 캔들 데이터 없으면 스킵 (None 유지)
- unified_schema.py 컬럼 추가 후 기존 DB migration 필요 — ADD COLUMN은 SQLite 지원함
- enriched_at 기준 24시간 이내 티커는 재수집 스킵
