# 08 — Signal Engine & Data Collection 분석 & 개선 플랜

> 실제 코드 분석 기반
> (engine.py, providers.py, providers_extended.py, bayesian.py,
>  ml_signal.py, alpha_features.py, quality.py,
>  candle_cache.py, data_collector.py, store.py,
>  trade_stats.py, unified_schema.py)
>
> 결론 요약: 시그널 엔진은 의도대로 잘 작동 중.
> 깃허브에서 더 가져올 것 없음. 내부 개선 3개가 더 임팩트 큼.

---

## PART 1 — 시그널 엔진

### 1-A. 전체 평가: 의도대로 잘 작동 ✅

**Contrarian 철학 전체 일관**

| Provider | 로직 | 비고 |
|----------|------|------|
| Sentiment | 군중 long → short | (50 - lp) * 2 |
| Funding | 양수 funding → short | longs pay = 군중 long |
| LSRatio | high ratio → short | (1 - ratio) * 50 |
| Taker | 군중 buying → short | (1 - ratio) * 60 |
| FearGreed | 공포 → long | (50 - fg) * 2 |
| MacroRegime | VIX↑ → long, HY spread↑ → long | 복합 weighted composite |
| COT | 극단 percentile → 반대 | (50 - pct_rank) * 1.8 |
| Bayesian prior | risk_on → down(0.60), crisis → up(0.62) | 레짐별 사전확률 |

**9단계 게이트 시퀀스 탄탄함**
```
stale_price_gate (10min)
→ composite scoring (14 providers, group-aware)
→ contrarian remap (sweet spot boost 25-45 → 1.15x, overheat damp)
→ score threshold (regime-adaptive, CRISIS=20, 기본=45)
→ direction check
→ F&G anchor lock (F&G<25 → long only, F&G>80 → short only)
→ factor count / agreement gate
→ crypto gates (strength, funding gate)
→ trend gate (non-crypto, mom_2m 역방향 0.6x damp)
→ F&G extreme boost (F&G<20 → 1.3x, F&G>85 → 1.3x)
→ quality gate (Bayesian pattern learning)
→ Bayesian direction gate (동의 +10%, 반대 0.85x)
→ PASS
```

**Provider 효과 피드백 루프 작동 중**
```python
# quality.py provider_effectiveness → compose() 자동 반영
if wr > 0.55: weight * 1.2   # 좋은 provider 부스트
if wr < 0.40: weight * 0.8   # 나쁜 provider 패널티
```

**Bayesian predictor 설계 좋음**
Triple Barrier 레이블 (trail→+1, stop→-1, time→0) + 지수가중 recency (0.95^n) + 30 outcomes마다 likelihood 업데이트 + 레짐별 prior 분리 + 데이터 blending (max 85% data).

**MLSignalProvider 안전하게 shadow mode 운영 중**
weight=0으로 composite에 영향 없음. 모든 asset group _GROUP_PROVIDERS에 등록됨.

---

### 1-B. 실제 이슈

#### 🔴 이슈 1: TechnicalSignal MTF 데이터 — Dead Code 가능성

TechnicalSignal이 읽으려는 MTF 키들:
```python
rsi_1h, rsi_4h, rsi_1d
ema_cross_1h, ema_cross_4h, ema_cross_1d
adx_1h, adx_4h, adx_1d
mfi_1h, mfi_4h, vol_ratio_1h
```

이 키들이 `_tech_cache`에 실제로 채워지는지 candle_tech.py 미확인. 안 채워지면 전부 default (rsi=50, adx=0)으로 폴백 → MTF 로직 전체 무효화.

**영향:**
- tf_consensus 항상 50 (중립) → amplify/dampen 효과 없음
- adx_regime_mtf 항상 "ranging" → score * 1.15 과적용 (실제 trend 무시)
- TF Divergence 항상 div=0 → 부스트 기회 없음

**검증:**
```bash
grep "rsi_1h\|rsi_4h\|adx_1h" data/invasion.log | head -5
# 아무것도 없으면 MTF 데이터 없는 것
```

**수정 방향 (candle_tech.py 추가):**
```python
for ticker in tickers:
    tech_1h = compute_technicals(get_candles(ticker, "HOUR"))
    tech_4h = compute_technicals(get_candles(ticker, "HOUR_4"))
    tech_1d = compute_technicals(get_candles(ticker, "DAY"))
    cache = _tech_cache.setdefault(ticker, {})
    cache.update(tech_1h)                             # 기본 키 (rsi, adx 등)
    cache["rsi_1h"]      = tech_1h.get("rsi", 50)
    cache["adx_1h"]      = tech_1h.get("adx", 0)
    cache["rsi_4h"]      = tech_4h.get("rsi", 50)
    cache["adx_4h"]      = tech_4h.get("adx", 0)
    cache["rsi_1d"]      = tech_1d.get("rsi", 50)
    cache["adx_1d"]      = tech_1d.get("adx", 0)
    cache["mfi_1h"]      = tech_1h.get("mfi", 50)
    cache["mfi_4h"]      = tech_4h.get("mfi", 50)
    cache["vol_ratio_1h"]= tech_1h.get("vol_ratio", 1.0)
```

---

#### 🟡 이슈 2: CrossPairSignal 철학 충돌

현재: 상관 페어가 같은 방향 → confirmation boost (1.2x). 그런데 우리 시그널은 contrarian이라 상관 페어도 같은 방향 = 군중이 모두 쏠림 = 오히려 반전 신호. weight=8이라 실제 영향은 작음.

**수정 (안전):**
```python
# providers.py CrossPairSignal
weight = 5.0  # 8.0 → 5.0
```

---

#### 🟡 이슈 3: quality.py `_auto_adjust_min_score` 자기강화 루프 위험

```python
# 50 트레이드마다 자동 조정
if wr < 0.35:
    _optimal = max(_optimal, int(bucket_s) + 10)  # min_score 올림
_new = max(30, min(60, _optimal))
```

위험: min_score↑ → 시그널 감소 → 낮은 score bucket 샘플 부족 → 더 올림 → 루프.

**수정:**
```python
_new = max(30, min(50, _optimal))  # 상한 60 → 50
```

---

#### 🟡 이슈 4: bayesian.py numpy import 미사용

```python
def _update_likelihoods(self):
    import numpy as np  # ← import만 하고 실제 미사용
    # _weighted_stats는 순수 Python으로 구현됨
```

수정: `import numpy as np` 줄 삭제.

---

#### 🟢 확인 필요: wq_alpha1, wq_alpha6

_GROUP_PROVIDERS에 등록은 됐는데 engine에 add_provider로 실제 인스턴스가 들어갔는지 확인:
```bash
grep "wq_alpha" data/invasion.log | head -5
# 없으면 provider 등록 누락
```

---

### 1-C. 시그널 엔진 — 깃허브 리서치 결론

**결론: 없음. 우리 구조가 이미 더 정교함.**

| 검토한 레포 | 판단 | 이유 |
|-------------|------|------|
| WorldQuant 101 Alphas | ❌ | 주식 편향, wq_alpha1/6 이미 shadow mode 있음 |
| Kalman Filter | ❌ | Bayesian predictor가 동일 역할, 더 경량 |
| mlfinlab CUSUM | ❌ | 20s/30s 스캔 구조와 구조적 불일치 |
| AlphaGen (RL alpha) | ❌ | 연구용, 실시간 불가 |
| TradingAgents / FinMem | ❌ | LLM 멀티에이전트, 주식/연구용 |

WorldQuant 101 수식 아이디어는 새 provider 설계 시 참고 가능. 직접 복붙 불필요.

---

## PART 2 — 데이터 수집

### 2-A. 캔들 수집 구조 평가

**현재 우선순위 (get_candles)**
```
1. 로컬 JSON 파일 캐시  (data/candles/, TTL=2h mtime 기반)
2. Yahoo Finance         (yfinance, 전 자산 130개+ 매핑)
3. Alpaca API           (US 주식/ETF fallback)
4. Exchange API client  (Capital.com 등)
5. WS tick → synthetic  (60s 버킷, 마지막 수단)
```

**잘 된 것들 ✅**
- 5계층 fallback, 어떤 자산에도 대응
- YAHOO_TICKERS 130개+ 수동 검증 완료 (FX, crypto, commodity, index, 주식)
- 4H 자동 리샘플 (1H x4 → 4H)
- 파일 mtime 기반 TTL (stale 자동 무효)
- `resolve_yahoo_ticker()` OKX swap명 자동 변환 (BTC-USDT-SWAP → BTC-USD)

---

### 2-B. 캔들 수집 — 실제 이슈

#### 🔴 이슈 1: save()의 SQLite timestamp 가짜

```python
# candle_cache.py save()
rows.append({
    "ts": time.time() - (len(candles) - i) * 60,  # ← 60초 고정 간격 역산 (가짜)
    "ticker": name, "resolution": resolution,
    ...
})
```

실제 캔들은 1H/4H 간격인데 60초로 역산 → SQLite candles 데이터 시간 완전 틀림. 그래서 unified_schema.py에서 candles 테이블 제거됨.

**현재 상태:** 캔들은 JSON 파일만 실 사용, SQLite candles 없음. 당장 급하지 않음.

**나중에 SQLite 복원 시 수정:**
```python
# _fetch_yahoo 에서 timestamp 포함
for dt, row in data.iterrows():
    ts = dt.timestamp()  # pandas Timestamp → unix
    candles.append({"ts": ts, "o": o, "h": h, "l": l, "c": cl, "v": v})

# save() 에서 resolution별 간격 역산
_RES_SEC = {"MINUTE": 60, "HOUR": 3600, "HOUR_4": 14400, "DAY": 86400}
interval_sec = _RES_SEC.get(resolution, 3600)
"ts": c.get("ts") or (time.time() - (len(candles) - i) * interval_sec),
```

---

#### 🟡 이슈 2: OKX 크립토 — Yahoo보다 OKX native 캔들이 정확

현재: BTC-USDT-SWAP → resolve → BTC-USD → Yahoo Finance (최대 15분 delay, spot 가격).

개선: OKX REST `/api/v5/market/candles` → 실시간, swap 가격.

exchange/okx/public.py에 `candles()` 이미 있음. get_candles()에서 0순위 추가:

```python
def get_candles(client=None, name="", resolution="HOUR_4", count=50, adapter=None):
    # 0. OKX native candles (크립토, 가장 정확)
    if "-USDT-SWAP" in name or "-USDT" in name:
        try:
            from ..exchange.okx.public import OKXPublic
            okx = OKXPublic()
            okx_candles = okx.candles(name, resolution, count)
            if okx_candles and len(okx_candles) >= 10:
                save(name, okx_candles, resolution)
                return okx_candles[-count:]
        except Exception:
            pass
    # 1. 로컬 캐시 (기존)
    ...
```

---

#### 🟢 이슈 3: 4H 리샘플 경계 조건

```python
for i in range(0, len(candles) - 3, 4):
    chunk = candles[i:i + 4]
    if len(chunk) == 4:  # 딱 4개 아니면 버림
```

마지막 1~3개 캔들 항상 버려짐. 영향 작음(마지막 4H 캔들 하나 없는 정도).

---

### 2-C. DataCollector — 수집기 현황

```
Fast (5분):
  ✅ CNN Fear & Greed           → cnn_fear_greed
  ✅ CoinGlass funding + OI     → coinglass_funding, coinglass_oi
  ✅ Binance funding + position → binance_funding, binance_positioning
  ✅ Alternative.me crypto F&G  → alt_fear_greed (+ 1d/1w ago 히스토리)

Slow (30분):
  ✅ FRED macro                 → vix, hy_spread, move_index, yield_curve
  ✅ yFinance macro             → yf_vix, yf_dxy, yf_spy, yf_gld, yf_btc
  ✅ DeFi Llama TVL             → defi_tvl_b

Weekly (24h):
  ✅ COT 리포트                 → large_spec_net, commercial_net, pct_rank_3y
  ✅ Myfxbook 포지셔닝          → long_pct, short_pct (48+ FX pair)
  ✅ Blockchain.info            → btc_hash_rate_eh

Track B Shadow (lazy, 동작 미검증):
  ? EDGAR filings        ? ApeWisdom          ? Finviz screener
  ? FINRA short interest ? Alpaca News        ? Santiment
  ? CryptoPanic          ? ForexFactory Cal   ? OANDA Position Book
  ? EIA Petroleum        ? Baker Hughes       ? USDA WASDE
  ? CBOE VIX Term        ? CBOE Put/Call      ? Sentiment Weekly
```

**Track B 동작 확인:**
```bash
grep "EDGAR\|ApeWisdom\|Santiment\|ForexFactory\|CBOE" data/invasion.log | tail -20
# 아무것도 없으면 lazy init 문제 또는 collect_trackb() 미호출
```

**결론: 이미 매우 풍부함. 외부에서 더 가져올 게 없음.**

---

### 2-D. DataStore (SQLite) — 평가

**잘 된 것들 ✅**
- WAL + NORMAL synchronous → 동시성 최적화
- Singleton 패턴 → 연결 낭비 없음
- unified_schema.py SSOT → DDL 분산 없음
- ALTER TABLE ADD COLUMN 안전 처리
- INSERT OR IGNORE → 중복 안전

**unified_schema.py에서 제거된 테이블들 (이유 명확)**
- `candles` — 가짜 timestamp 문제, JSON 파일로 대체
- `tick_snapshots` — write-only (580K+ 행, reader 없음)
- `_schema_version` — `_meta.schema_version`과 중복

---

### 2-E. trade_stats.py — 블록 기준 평가

```python
MIN_TRADES = 30   # 30 트레이드 이상
BLOCK_WR = 25     # WR 25% 미만 → 블록
```

**현재 문제:** N=30은 통계적으로 작음. Raw WR 사용 (Bayesian shrinkage 없음). 초기 운이 나쁜 티커가 너무 일찍 블록될 수 있음.

**개선 옵션 A (단순):**
```python
MIN_TRADES = 50   # 샘플 크기 상향 — statistical stability
BLOCK_WR = 20     # 진짜 나쁜 것만 블록
```

**개선 옵션 B (quality.py 패턴 적용):**
```python
global_wr = total_wins / total_trades
k = 20  # shrinkage factor
adjusted_wr = (n * raw_wr + k * global_wr) / (n + k)
if n >= MIN_TRADES and adjusted_wr < 0.20:
    blocked_tickers.append(name)
```

---

## PART 3 — 우선순위 정리

### 즉시 — 검증 (로그 확인, 5분)
```
[ ] MTF 키: grep "rsi_1h\|rsi_4h\|adx_1h" data/invasion.log | head
[ ] wq_alpha: grep "wq_alpha" data/invasion.log | head
[ ] Track B: grep "EDGAR\|ApeWisdom\|Santiment" data/invasion.log | tail -20
```

### 즉시 — 버그 픽스 (30분)
```
[ ] bayesian.py: import numpy as np 삭제
[ ] quality.py: _auto_adjust_min_score 상한 60→50
[ ] CrossPairSignal: weight 8.0→5.0
```

### 중기 — 캔들 품질 개선 (2~3시간)
```
[ ] _fetch_yahoo: 실제 timestamp 포함 (dt.timestamp())
[ ] get_candles: OKX swap ticker → OKX native candles 0순위로
[ ] candle_tech.py: MTF 키 (rsi_1h, rsi_4h, adx_1h 등) 저장 확인 & 추가
```

### 중기 — 블록 기준 조정 (1시간)
```
[ ] trade_stats.py: MIN_TRADES 30→50, BLOCK_WR 25→20
[ ] 또는 Bayesian shrinkage 적용 (옵션 B)
```

### 장기
```
[ ] SQLite candles 테이블 복원 (timestamp 수정 후)
[ ] Track B 수집기 중 유효한 것 선별 → 시그널 provider 연결
```
