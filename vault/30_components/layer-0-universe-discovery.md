---
type: component
component: layer-0-universe-discovery
status: active
date_created: 2026-05-06
tags: [layer-0, universe, dynamic, discovery]
related: [[ADR-003]], [[ADR-004]], [[ADR-005]], [[ADR-006]], [[active-autonomous-vision]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 0 — Dynamic Universe Discovery

## Decision (codex 합의 R1)

하드코드 ticker list 폐기. OKX SPOT 5min REST poll + Capital CFD 10min REST poll → `active universe` (~270-320) → `focus watchlist` (dynamic 12-48, base 30) Haiku gate. **Coverage 는 넓게, execution attention 은 좁게**.

## Detail Spec

### Q1 — OKX rate limit & refresh
- `GET /api/v5/market/tickers?instType=SPOT` 5min poll = 단일 호출/300초. public IP `20/2s` 한도 대비 매우 안전 (288 calls/day).
- listing-change delta detect 추가 (이전 cycle vs 현재 cycle diff).
- WebSocket = P1 (bar/tick stream 입력용 보강), P0는 REST.
- Capital CFD `marketnavigation` tree 는 10min poll.

### Q2 — Active filter (4-axis hard filter)
P0 hard filter (모두 통과 = active):
```
vol_24h_usd >= 30_000_000        # learner-tunable threshold
spread_bps <= 10                 # 0.10% 이상 spread = exclude
atr_24h_pct >= 2.0               # dead ticker 제거
depth_10bps_usd >= 25_000        # top-of-book depth (10bps 안 매수 가능 USD)
state == "live"
quote_ccy in {"USDT"}            # OKX P0
```

각 threshold 독립 learner-tunable axis. 단일 weighted score 없음 (운영 단순).

### Q3 — Focus watchlist (dynamic target_size 12-48)
Haiku Universe Scanner 입력 = deterministic pre-rank 후 상위 후보. LLM 이 raw 270 해석 X.

**Pre-rank score** (deterministic):
```
rank_score = 0.35 * vol_z
           + 0.25 * signal_density_z
           + 0.20 * atr_pct_z
           + 0.10 * depth_z
           + 0.10 * cell_score_z
```

**Dynamic target_size**:
```
base = 30
+6 / +12 if recent 6-cycle signal density top-quartile is high
-6 / -12 if top-score mass is low
clip [12, 48]
```

3-bucket 분류: `core` (top quartile cell + active signal), `satellite` (mid), `listing_watch` (24h 미만 신규).

### Q4 — Eviction policy (2-tier)
- **Active universe eviction**: 유동성/거래 가능성 (Q2 hard filter 미충족) → 영구 X, 다음 refresh 즉시 재진입 가능.
- **Focus watchlist eviction**: bottom quartile cell + 28d 0 trades → 이번 cycle 만 focus 제외. 영구 X.

### Q5 — Cross-venue (instrument_id ≠ underlying_group_id)
- `instrument_id`: `okx:BTC-USDT`, `capital:BTCUSD` (별개 entity)
- `underlying_group_id`: `crypto:BTC` (공유)
- Allocator: `crypto:BTC` shared gross cap **60%** (cross-venue underlying)
- `crypto:BTC+ETH` cluster cap **40%** (ADR-005 유지)
- Per-symbol cap (venue-local) = ADR-005 그대로

### Q6 — Listing change auto-onboarding
- 신규 listing 자동 active universe 편입 (Q2 hard filter 통과 시).
- Cold start `cell_routing_mult = 1.0` default (ADR-006 n<5 default).
- **Listing watchdog 24h** (aggressive 보존, 무차단):
  - `listing_age_hours < 24` → `size_mult = 0.5`
  - `max_concurrent_positions = 1`
  - `spread_bps > 15` 또는 `depth_10bps_usd < 15k` → focus 진입 보류 (active 는 유지)
- 24h 경과 후 일반 규칙 승격.

## Implementation notes

### File layout (P0)
```
polaris/core/universe/
├── discovery.py        # OKX/Capital fetch + filter + delta detect
├── watchlist.py        # rank_score + dynamic target_size + eviction
└── schema.py           # UniverseInstrument + FocusSelection dataclass
polaris/core/pipeline/agents/
└── universe_scanner.py # Haiku gate (deterministic pre-rank → LLM 압축)
polaris/venues/okx/adapter.py    # fetch_spot_tickers, fetch_instruments
polaris/venues/capital/adapter.py # fetch_market_navigation
polaris/core/sizing/cluster_cap.py # underlying_group_id cap 적용
```

### Schema (SQLite, ADR-003 unified DB)
```sql
CREATE TABLE universe (
  venue TEXT, symbol TEXT,
  instrument_id TEXT, underlying_group_id TEXT,
  asset_class TEXT, quote_ccy TEXT, state TEXT,
  vol_24h_usd REAL, spread_bps REAL,
  atr_24h_pct REAL, depth_10bps_usd REAL,
  signal_density_7d REAL DEFAULT 0.0,
  listing_ts INTEGER, last_seen_ts INTEGER,
  is_active INTEGER DEFAULT 1, active_reason TEXT,
  PRIMARY KEY (venue, symbol)
);

CREATE TABLE watchlist_focus (
  cycle_ts INTEGER, venue TEXT, symbol TEXT,
  focus_score REAL, focus_rank INTEGER,
  target_bucket TEXT,  -- core|satellite|listing_watch
  evict_reason TEXT,
  PRIMARY KEY (cycle_ts, venue, symbol)
);
```

### Dataclass
```python
@dataclass
class UniverseInstrument:
    venue: str; symbol: str
    instrument_id: str; underlying_group_id: str
    asset_class: str; quote_ccy: str; state: str
    vol_24h_usd: float; spread_bps: float
    atr_24h_pct: float; depth_10bps_usd: float
    signal_density_7d: float
    listing_ts: int | None; last_seen_ts: int

@dataclass
class FocusSelection:
    cycle_ts: int; symbol: str; venue: str
    focus_score: float; rank: int
    bucket: Literal["core", "satellite", "listing_watch"]
```

### Function signatures
```python
# discovery.py
async def refresh_okx_universe(now_ts: int) -> list[UniverseInstrument]
async def refresh_capital_universe(now_ts: int) -> list[UniverseInstrument]
def detect_listing_changes(prev, curr) -> tuple[list[UniverseInstrument], list[str]]
def apply_active_filters(instruments, *, min_vol_24h_usd=30e6,
    max_spread_bps=10.0, min_atr_24h_pct=2.0, min_depth_10bps_usd=25e3) -> list
def compute_underlying_group_id(venue, symbol, asset_class) -> str

# watchlist.py
def score_focus_candidate(inst, *, cell_score, volume_z, atr_z,
    signal_density_z, depth_z, recent_activity_z) -> float
def compute_dynamic_target_size(active_count, *, baseline=30,
    min_target=12, max_target=48, recent_signal_density,
    top_score_concentration) -> int
def select_focus_watchlist(active_universe, *, target_size, cycle_ts) -> list[FocusSelection]
def should_evict_from_focus(*, cell_quartile, trades_28d, signal_hits_7d) -> bool

# universe_scanner.py (Haiku gate)
async def run_universe_scanner(cycle_ts, active_universe) -> list[FocusSelection]

# cluster_cap.py
def apply_underlying_group_caps(proposed_positions, open_positions) -> list
```

### Constants (P0)
```python
OKX_UNIVERSE_REFRESH_SEC = 300
CAPITAL_UNIVERSE_REFRESH_SEC = 600
FOCUS_TARGET_BASE = 30
FOCUS_TARGET_MIN = 12
FOCUS_TARGET_MAX = 48
NEW_LISTING_WATCH_HOURS = 24
NEW_LISTING_SIZE_MULT = 0.5
NEW_LISTING_MAX_CONCURRENT = 1
UNDERLYING_BTC_GROSS_CAP = 0.60  # cross-venue BTC underlying
```

### P0 vs P1 split
- **P0**: REST poll + 4-axis hard filter + dynamic focus + listing watchdog + Haiku scanner gate.
- **P1**: WebSocket bar/tick stream 보강 + learner-tunable threshold auto-tune + signal_density_z rolling window 길어짐.

## Risk + Aggressive Mitigation

### Risk
가장 큰 위험 = `active universe` 270 너무 빨리 확장 → signal generator + downstream gate 가 저품질 종목 흡수 → raw signal 수만 늘고 체결 품질 무너짐.

### Aggressive Mitigation
- 공격성 유지하되 진입 문 앞만 더 날카롭게.
- `active universe` 는 넓게 (4-axis hard filter 통과면 OK).
- `focus watchlist` + `listing watchdog` 에서만 강하게 압축.
- **Coverage 는 넓게, execution attention 은 좁게** = L0 의 정답.
- Listing watchdog 차단 X, size_mult 0.5 + max_concurrent 1 만 (aggressive bias 보존).

## Sources
- codex round 1 (`/tmp/polaris_phase0/L0_r1_response.md`, gpt-5.4)
- ADR-003 §Layer 0 (8-layer architecture)
- ADR-004 §gate 1 (Universe Scanner Haiku)
- ADR-005 §symbol-cluster cap (underlying_group_id 통합 위치)
- ADR-006 §cell routing (cell_score quartile)
- T11 archive (universal normalize, signal funnel)

## Implementation status

| field | value |
|---|---|
| date | 2026-05-06 (codex round 1 fixes applied) |
| phase | P0 Day 1 |
| status | implemented + 4 P0 blockers fixed + 58 tests + smoke green |
| files | `polaris/core/universe/{schema.py, discovery.py, watchlist.py}` + `polaris/storage/schema.py` |
| tests | `tests/test_layer0_universe.py` (37 cases incl. 2 hypothesis property) |
| smoke | OKX 182 + Capital 387 (P0 categories only) → filter 24 (OKX only — CFD lacks vol/depth proxy) → focus 24 |
| LOC | discovery ~480, watchlist ~290, schema 95 |
| spec API | `refresh_okx_universe`, `refresh_capital_universe`, `score_focus_candidate(s)`, `select_focus_watchlist`, `compute_dynamic_focus`, `apply_active_filters`, `detect_listing_changes(now_ts=)`, `merge_listing_timestamps`, `persist_universe(active_reason=)`, `persist_focus` |
| 4-axis filter | hard: spread/atr/vol/depth all required, no venue exemption (codex R1 fix) |
| Capital scoping | name-token whitelist `CAPITAL_P0_CATEGORY_TOKENS = (forex,currenc,indic,commod,metal,energ,crypto)`; Shares/ETFs auto-reject. Epic dedup across nodes. (codex R1 fix) |
| listing watchdog | `detect_listing_changes(now_ts=)` + `merge_listing_timestamps()` stamp `listing_ts`; bucket="listing_watch" for `<24h`. size_mult 0.5 + max_concurrent 1 = L3 (Day 3+). |
| `core` bucket | top-quartile cell AND active signal (cell_q75 / sig_q75 across active universe); rank fallback only on full cold start. |
| review | codex round 1 = REJECT → all 4 P0 blockers fixed → see `vault/50_research/debates/2026-05-06_p0_day1_codex_review.md` |
| next | Day 2: Layer 7 (isolation primitives) + Layer 4 (cell_matrix schema) + Capital CFD vol/depth proxy via chart endpoint (codex P1) |
