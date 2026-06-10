---
type: component
component: layer-1-canonical-baseline
status: active
date_created: 2026-05-06
tags: [layer-1, canonical, baseline, normalize, t11]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[ADR-006-cell-matrix|ADR-006]], [[layer-0-universe-discovery]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 1 — Canonical Market Model + Ticker Baseline

## Decision (codex 합의 R1)

모든 venue → unified canonical bar/quote_tick/event stream (separate tables). Ticker baseline 5-metric: `atr_pct_15m / entry_notional_usd / abs(signal.score) / rolling_24h_notional_usd / pnl_r_std`. `normalize()` API = T11 ratio `raw / baseline_p50`. cold start = 1.0 neutral. Cross-venue baseline = `instrument_id` 우선 (`underlying_group_id` fallback only).

## Detail Spec

### Q1 — Canonical bar/event spec (separate tables)
- **`bars`** (1m / 5m / 15m / 1H, P0 = 15m primary): OHLCV + venue + symbol + instrument_id + bar_interval + ts + notional_usd + trade_count + vwap + bid_close + ask_close + spread_bps_close
- **`quote_ticks`** (best bid/ask snapshot, throttle 1Hz): bid + ask + mid + spread_bps + bid_size + ask_size + last_trade_price/size
- **`market_events`** (ad-hoc: listing change, halt, regime flip): type + payload_json
- **`signals`** (raw + validated, separate from bars): strategy_id + signal_id + thesis + score
- 단일 `events` table 안 함 (hot-path 조회 패턴이 다름).
- 명확 분리: "시장 상태" (bars, quote_ticks) vs "실행 비용" (spread, swap fee → ledger only).

### Q2 — Ticker baseline 5-metric
| # | Metric | 정의 | Update cadence |
|---|---|---|---|
| 1 | `atr_pct` | `15m ATR(14) / close`, % of price (절대 USD 아님) | 15m bar close |
| 2 | `entry_notional_usd` | 실 entry fill notional USD median | per fill |
| 3 | `signal_score` | `abs(raw_signal.score)` ticker 통합 (전략별 X) | per signal emit |
| 4 | `rolling_24h_notional_usd` | 시장 거래대금 (size proxy 별도 분리) | 15m bar close |
| 5 | `pnl_r_std` | ticker 별 최근 `min(30d, 20 closed trades)` R-multiple std | per trade close |

**Baseline window**:
- `atr / size / signal / volume`: 7d rolling, store p50 + p75
- `pnl_std`: 30d 또는 20 trades 확보 시점까지

### Q3 — `normalize()` API (T11 ratio_to_baseline)
- **방법**: `normalized = raw / baseline_p50` (T11 핵심)
  - z-score / minmax / percentile 모두 reject — aggressive bias 와 ratio 가 가장 잘 맞음.
- **출력 범위**: `[0, +inf)`, `1.0 = baseline / >1 = expansion / <1 = compression`
- **Cold start fallback chain**: `instrument_id → underlying_group_id → asset_class → 1.0 neutral`
- **Window**: rolling (atr/size/signal/volume = 7d, pnl_std = 30d)

```python
def normalize(
    instrument_id: str,
    metric: Literal["atr","size","signal","volume","pnl_std"],
    raw_value: float,
    *,
    asof_ts: int | None = None,
) -> float

def get_baseline(
    instrument_id: str,
    metric: str,
    *,
    asof_ts: int | None = None,
) -> BaselineValue | None
```

### Q4 — Cross-venue normalization (instrument_id 우선)
- Primary baseline = `instrument_id` (OKX `BTC-USDT` ≠ Capital `BTCUSD`).
- `underlying_group_id` baseline = cold-start fallback + regime context **only**. Primary 로 쓰면 Capital spread/carry 가 OKX spot 오염.
- **ATR baseline = mid-price bars** (spread 제외). spread 는 `quote_ticks.spread_bps` + `bars.spread_bps_close` 별도.
- **Capital CFD funding/swap**: market baseline 에 X. trade ledger `net_pnl_r` + `pnl_std` 에만 반영.
- 결과: "시장 상태" (atr/volume) ≠ "실행 비용" (spread/swap) 분리 → cross-venue 비교 왜곡 최소화.

### Q5 — Storage (SQLite WAL + separate tables)
- TimescaleDB 거부 (P0 과함). `data/polaris.sqlite` WAL 모드.
- Hot path: `latest N bars per (instrument_id, bar_interval)` → composite PK + index 필수.
- **Retention**:
  - `quote_ticks`: 24h
  - `bars`: 30d (15m), 7d (1m)
  - `signals`: 30d
  - `ticker_baseline_samples`: 30d
  - `ticker_baseline_state`: latest only (P50 + P75 + sample_count)

## Implementation notes

### File layout (P0)
```
polaris/core/data/
├── canonical.py     # bar/event normalization (venue → unified schema)
├── baseline.py      # ticker_baseline_state read/write + 5-metric calc
├── normalize.py     # normalize() + get_baseline() API
└── schema.py        # Bar / QuoteTick / Signal dataclass
polaris/storage/
└── market_store.py  # SQLite WAL CRUD (bars/quote_ticks/signals/baseline)
```

### Schema (SQLite WAL)
```sql
CREATE TABLE bars (
  instrument_id TEXT,
  underlying_group_id TEXT,
  venue TEXT, symbol TEXT,
  bar_interval TEXT,         -- 1m/5m/15m/1H
  ts INTEGER,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, notional_usd REAL,
  trade_count INTEGER, vwap REAL,
  bid_close REAL, ask_close REAL, spread_bps_close REAL,
  source TEXT,
  PRIMARY KEY (instrument_id, bar_interval, ts)
);
CREATE INDEX idx_bars_venue_symbol ON bars(venue, symbol, bar_interval, ts DESC);

CREATE TABLE quote_ticks (
  instrument_id TEXT,
  venue TEXT, symbol TEXT,
  ts INTEGER,
  bid REAL, ask REAL, mid REAL, spread_bps REAL,
  bid_size REAL, ask_size REAL,
  last_trade_price REAL, last_trade_size REAL,
  source TEXT,
  PRIMARY KEY (instrument_id, ts)
);

CREATE TABLE ticker_baseline_state (
  instrument_id TEXT,
  underlying_group_id TEXT,
  metric TEXT,               -- atr/size/signal/volume/pnl_std
  baseline_p50 REAL,
  baseline_p75 REAL,
  sample_count INTEGER,
  lookback_sec INTEGER,
  updated_ts INTEGER,
  PRIMARY KEY (instrument_id, metric)
);

CREATE TABLE ticker_baseline_samples (
  instrument_id TEXT, metric TEXT, ts INTEGER, value REAL,
  PRIMARY KEY (instrument_id, metric, ts)
);

CREATE TABLE market_events (
  ts INTEGER, type TEXT,     -- listing_change/halt/regime_flip
  venue TEXT, symbol TEXT, payload_json TEXT,
  PRIMARY KEY (ts, type, venue, symbol)
);
```

### Dataclass
```python
@dataclass
class Bar:
    instrument_id: str; underlying_group_id: str
    venue: str; symbol: str; bar_interval: str
    ts: int
    open: float; high: float; low: float; close: float
    volume: float; notional_usd: float
    trade_count: int; vwap: float
    bid_close: float; ask_close: float; spread_bps_close: float
    source: str

@dataclass
class QuoteTick:
    instrument_id: str; venue: str; symbol: str
    ts: int
    bid: float; ask: float; mid: float; spread_bps: float
    bid_size: float; ask_size: float
    last_trade_price: float; last_trade_size: float

@dataclass
class BaselineValue:
    metric: str
    p50: float; p75: float
    sample_count: int
    lookback_sec: int
    updated_ts: int
```

### P0 vs P1 split
- **P0**: 15m bars + quote_ticks (1Hz throttle) + 5-metric baseline + `normalize()` ratio + SQLite WAL.
- **P1**: 1m bars 추가 + WebSocket bar/tick stream + baseline learner (window auto-tune) + cross-venue baseline alignment.

## Risk + Aggressive Mitigation

### Risk
가장 큰 위험 = Capital CFD 비용 구조 (spread + swap) 가 market volatility baseline 에 섞여서 OKX spot 과 잘못 비교. ATR 부풀려 size 축소 또는 신호 왜곡.

### Aggressive Mitigation
- ATR = mid-price bars 로만 계산 (spread 분리).
- Spread/swap = execution cost + `net_pnl_r` 쪽에만 태움.
- Baseline expansion 신호 (raw/baseline_p50 > 1) 는 공격적으로 유지 (ratio 가 z-score 보다 폭발 신호 잘 잡음).
- Cold start = 1.0 neutral (block X, aggressive trial 유지).

## Sources
- codex round 1 (`/tmp/polaris_phase0/L1_r1_response.md`, gpt-5.4)
- ADR-003 §Layer 1
- ADR-004 §gate 입력 (cell_matrix + ticker_baseline)
- T11 archive `ticker_baseline.py` 256 LOC (handoff_unified_2026_04_21_T11_northstar_dynamic.md)
- [[layer-0-universe-discovery]] (instrument_id + underlying_group_id 정의)

## Implementation status

| field | value |
|---|---|
| date | 2026-05-06 (codex round 1 fixes applied) |
| phase | P0 Day 1 |
| status | implemented + 4 P0 blockers fixed + 58 tests + smoke green |
| files | `polaris/core/data/{schema.py, canonical.py, baseline.py, normalize.py}` + `polaris/storage/schema.py` |
| tests | `tests/test_layer1_canonical.py` (24 cases incl. 2 hypothesis property) |
| LOC | canonical ~210, baseline ~190, normalize ~165, schema ~115 |
| API | `normalize(conn, *, instrument_id, metric, raw_value, underlying_group_id, asset_class, asof_ts) -> float` (3-step cold-start: instrument → underlying_group → asset_class) ; pure ratio `ratio_to_baseline(raw, p50)` (rejects negative raw) |
| cold-start chain | `instrument_id → underlying_group_id → asset_class → 1.0 neutral` (codex R1 fix — `asset_class` step + column added) |
| schema | bars + idx_bars_venue_symbol; quote_ticks; ticker_baseline_state (instrument/group/asset_class) + idx_baseline_group_metric + idx_baseline_class_metric; ticker_baseline_samples; market_events; signals (codex R1 fix — Signal dataclass added) |
| guards | `ratio_to_baseline` returns 1.0 for non-finite raw, negative raw (domain `[0,+inf)`), and non-positive p50; `asof_ts` plumbed through `get_baseline` / `normalize` |
| deviations | `BaselineValue.p25` is computed via reflection (`2·p50 − p75`) since spec stores only p50/p75 (used by property tests for invariant). |
| review | codex round 1 = REJECT → all 4 P0 blockers fixed → see `vault/50_research/debates/2026-05-06_p0_day1_codex_review.md` |
| next | Day 2: bar/tick ingest from venue adapters → populate `bars` + `ticker_baseline_samples` |

### Day 5 patch — fill_normalizer

| field | value |
|---|---|
| date | 2026-05-07 |
| files | `polaris/core/data/fill_normalizer.py` |
| API | `normalize_okx_fill(payload, *, strategy_id, expected_price=None) -> Fill` and `normalize_capital_confirm(payload, *, strategy_id, pip_value_usd, expected_price=None, fee_usd=0.0, leverage=1.0) -> Fill` |
| Fill dataclass | `venue / instrument_id / strategy_id / side / size_usd / fill_price / fee_usd / slippage_bps / ts_ms / order_id / client_order_id / base_qty / quote_qty / state` |
| codex P1 fix | `_parse_capital_ts` forces UTC for naive ISO; `normalize_capital_confirm` includes `leverage` factor in `size_usd` (CFD gross notional). Regression tests added. |
| tests | `tests/test_fill_normalizer.py` (8 cases) |
