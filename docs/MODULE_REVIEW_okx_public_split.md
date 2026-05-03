# MODULE_REVIEW — `invasion/exchange/okx/public.py` 1168L Split Plan (F-N17)

> architecture_advisor 발견: `invasion/exchange/okx/public.py` = **1168 LOC single file**.
> `code_size_limits.md` 기준 `>1000L = P0 분할`. 단일 `OKXPublic` class + module-level 상수/mapping.
> 본 문서는 저위험 incremental extraction 순서 제안 (behavior 보존 mechanical refactor).

---

## 1. 1168L Block Map

| Block | 라인 | 역할 | 추출 난이도 | Risk |
|-------|------|------|------------|------|
| B0 Constants + Mappings | 1-117 | `TICKER_TO_OKX`, `_BASE`, `get_all_okx_instruments`, `CRYPTO_TIERS`, `OKX_TO_TICKER` | — (public re-export 필수) | — |
| B1 Class Init + CB | 119-175 | `OKXPublic.__init__`, `cb_status` property | 저 (class wire) | Low |
| B2 Price Guard | 177-190 | `get_price` (stale check) | 저 | Low |
| B3 Auth Balance | 192-224 | `fetch_balance` (OKX_LIVE_API_KEY/SECRET) | 중 (auth secrets) | Med |
| B4 HTTP `_get` + CB | 226-277 | centralized api_queue + local CB fallback | 높 (전역 shared) | High |
| B5 Funding Data | 278-353 | `fetch_funding_rates`, `get_funding_trend` | 중 (cache 공유) | Low |
| B6 L/S Ratio | 355-403 | `fetch_ls_ratio`, `fetch_top_trader_ls` | 저 | Low |
| B7 Open Interest | 405-536 | `fetch_open_interest`, `_get_score_weights`, `fetch_oi_history` | 중 (history cache) | Low |
| B8 Liquidation | 538-621 | `estimate_liquidation_pressure`, `fetch_recent_liquidations` | 중 | Low |
| B9 Sentiment Aggregator | 623-907 | `get_crypto_sentiment` — 멀티소스 종합 (funding+LS+OI+liquid+taker) | 높 (AI/ML score 연동) | **High** |
| B10 Scan All (batch) | 909-1037 | `scan_all`, `_scan_all_inner` thread-pool dispatcher | 높 (concurrency orchestrator) | **High** |
| B11 Batch Price Fetch | 1039-1093 | `fetch_prices_fast` (SWAP/SPOT tickers endpoint) | 중 | Low |
| **B12 Candles** | **1094-1147** | **`fetch_candles` — OKX candle API + 5min TTL cache** | **저 (self-contained)** | **Low** |
| B13 Cache Persist | 1149-1168 | `_save_cache`, `_load_cache` (atomic_write_json) | 저 | Low |

**총 14 blocks. Low 9개 / Med 3개 / High 3개 (B4 HTTP, B9 Sentiment, B10 scan_all).**

---

## 2. 추출 순서 (Phased Rollout)

| Step | 작업 | 파일 | 완료 조건 |
|------|-----|------|----------|
| **S1** | **B12 candle extraction** → `public_candles.py` free function `fetch_candles_impl(session, cache, lock, _get_fn, ticker, bar, limit)` | `public_candles.py` | `fetch_candles` method wrapper delegation → 기존 caller 영향 없음 |
| S2 | B13 cache persist → `public_cache.py` (`save_cache`/`load_cache` pure fn) | `public_cache.py` | `_CACHE_FILE` identity 보존 |
| S3 | B11 `fetch_prices_fast` → `public_tickers.py` free function | `public_tickers.py` | 24h change/range_pos/vol_spike 값 identity |
| S4 | B6 L/S + B7 OI + B8 Liquidation → `public_positioning.py` (crowd-positioning family) | `public_positioning.py` | `get_crypto_sentiment` 에서 호출하는 서브컴포넌트 identity |
| S5 | B5 funding → `public_funding.py` | `public_funding.py` | funding history identity |
| S6 | B0 constants + mapping → `public_instruments.py` | `public_instruments.py` | re-export from `public.py` 유지 |
| S7 (defer) | B9 Sentiment aggregator — AI/ML 점수 연동 High-risk, S4-S6 완료 후 | — | cross-review 필수 |
| S8 (defer) | B10 scan_all — concurrency High-risk, 마지막 | — | parity: 동일 thread count / order 보장 |

**S1 만 이번 commit. S2-S8 은 후속 MSG.**

---

## 3. S1 추출 상세 (Candles)

**현재 (public.py:1096-1147)**:
```python
class OKXPublic:
    CANDLE_TTL = 300

    def fetch_candles(self, ticker, bar="1H", limit=50) -> list:
        # 1. cache hit check (TTL 300s)
        # 2. inst_id resolve (TICKER_TO_OKX → get_all_okx_instruments)
        # 3. self._get("/api/v5/market/candles", ...)
        # 4. parse OKX rows → list[dict{ts,o,h,l,c,v}]
        # 5. cache write under lock
```

**추출 후**:
- `public_candles.py` 신규 파일
  - `CANDLE_TTL = 300`
  - `def fetch_candles_impl(cache, lock, get_fn, ticker, bar="1H", limit=50) -> list` — pure impl
- `public.py:fetch_candles` — thin wrapper:
  ```python
  def fetch_candles(self, ticker, bar="1H", limit=50):
      from .public_candles import fetch_candles_impl
      return fetch_candles_impl(self._cache, self._lock, self._get, ticker, bar, limit)
  ```
- `CANDLE_TTL` class const → re-export from `public_candles` module (backward compat).

**검증**:
- `python3 -m py_compile invasion/exchange/okx/public.py invasion/exchange/okx/public_candles.py`
- `python3 -c "from invasion.exchange.okx.public import OKXPublic, TICKER_TO_OKX, OKX_TO_TICKER, get_all_okx_instruments, get_crypto_tier"`
- `python3 -c "import invasion.main"`
- Runtime sanity: `OKXPublic().fetch_candles('Bitcoin', '1H', 50)` returns non-empty list (network available).

---

## 4. Import Cycle Guard

- `public_candles.py` → `from .public import TICKER_TO_OKX, get_all_okx_instruments` (B0 상수 참조).
- `public.py` → `from .public_candles import fetch_candles_impl` **inside method** (lazy import, top-level 순환 회피).
- 최악의 경우: 외부 direct import `from invasion.exchange.okx.public import fetch_candles` (없음 — `grep` 결과 only `OKXPublic`, `TICKER_TO_OKX`, `OKX_TO_TICKER`, `get_all_okx_instruments`, `get_crypto_tier`).

---

## 5. 다음 Commit 범위

**이번 P1**: S1 만 (candle extraction + plan 문서).
**scope**: `invasion/exchange/okx/public.py` + `invasion/exchange/okx/public_candles.py` + `docs/MODULE_REVIEW_okx_public_split.md` (3 파일).
**후속**: S2-S8 각각 별도 MSG 로 단계별 진행 (High-risk block 은 cross-review 필수).
