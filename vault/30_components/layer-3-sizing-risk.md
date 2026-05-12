---
type: component
component: layer-3-sizing-risk
status: active
date_created: 2026-05-06
tags: [layer-3, sizing, risk, t4, kelly, cell-routing, cluster-cap]
related: [[ADR-003]], [[ADR-005]], [[ADR-006]], [[layer-0-universe-discovery]], [[layer-1-canonical-baseline]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 3 — Sizing + Risk Engine

## Decision (codex 합의 R1)

**Cell routing mult = sizing factor (clip 전 통합)**. T4 = `proposed = base × continuous × tier × cell × listing_watchdog` → hard-cap headroom min() 1회. **CS-3 + sparse + listing = triple cold start, dampener 추가 X (watchdog 0.5× 만으로 floor 3.0%/3.5%)**. **`cluster_id` ≠ `underlying_group_id`** (cluster = multi-underlying 묶음). **fill-rate cut 70%, hysteresis 60% 재진입**. **Hard cap = headroom min() (순차 절단 X), audit 우선순위만 정의**.

## Detail Spec

### Q1 — T4 + cell routing 통합 위치 (clip 전)
```
proposed_risk_pct = base × continuous_scalar × tier_amplifier × cell_routing_mult × listing_watchdog_mult
final_risk_pct    = min(proposed,
                        single_trade_cap,
                        per_symbol_remaining,
                        underlying_remaining,
                        cluster_remaining,
                        track_remaining,
                        venue_daily_remaining,
                        total_daily_remaining)
final_notional    = final_risk_pct × equity × leverage(venue)
```

`cell_routing_mult` 는 sizing **factor** (composition 안 깨짐). cap 은 마지막 `min()` 1회. 재-clip 거부 (cell=1.3 일 때 cap 경계 동작 왜곡).

### Q2 — Cold start triple (CS-3 + sparse + listing)
| Layer | 기여 | 효과 |
|---|---|---|
| CS-3 (n<20 strategy trades) | single cap = **6%/7%** | Kelly off |
| Sparse cell (n<5) | cell_routing_mult = **1.0** | neutral |
| Listing watchdog (<24h) | listing_mult = **0.5** | half size |

**Triple 동시**: `proposed = 6/7% × 1.0 × 0.5 = 3.0%/3.5%` floor.
- 추가 dampener X. 이미 watchdog 0.5× 가 충분.
- aggressive 보존: 차단 X, 신규 listing 탐색 유지.

### Q3 — `cluster_id` ≠ `underlying_group_id`
```
underlying_group_id  = "crypto:BTC"          # cross-venue shared gross cap
cluster_id           = "crypto:BTC+ETH"      # multi-underlying 묶음
```

**P0 = config-first** (`risk_caps.yaml`), learner-tunable cap = P1 이후 (안전).

Universe refresh 시 `underlying_group_id → cluster_id[]` membership 재평가만. cluster 자체 정의는 자동 변경 X.

### Q4 — Fill-rate cut 70% + hysteresis 60%
- venue별 ceiling 다름 (OKX 8% / Capital 9%) → fill-rate 분모에 이미 반영. P0 threshold = **공통 70%**.
- Cut 후 즉시 재진입 X. hysteresis: `fill_rate < 60%` → 재진입 가능.
- "약한 signal" 정렬:
```python
priority = (
    signal_strength,                          # 1차: 약한 순
    0 if cell_quartile == "bottom" else 1,    # tie: bottom 우선 컷
    0 if is_listing_watch else 1,             # tie: watchdog 먼저
    -staleness_seconds                         # tie: 오래된 것 먼저
)
```
- cell bottom 단독 컷 X (signal_strength 가 1차).

### Q5 — Hard cap = headroom min() (순차 절단 X)
**Audit 우선순위** (로그용, 실제 집행은 min()):
```
single-trade → per-symbol → underlying gross → cluster → per-track → venue daily → total daily
```

cluster cap 은 `T4 제안 size 산출 후`, `order submit 전`, `open positions 포함` 적용.

## Implementation notes

### File layout (P0)
```
polaris/core/sizing/
├── t4.py             # compute_proposed_risk_pct + compute_final_order_risk_pct
├── schema.py         # SizingProposal / SizingFinal dataclass
├── cluster_cap.py    # cluster_id resolve + headroom
├── cell_router.py    # cell_routing_mult (n<5 → 1.0, quartile)
└── kelly.py          # CS-3 (n<20 → off, single 6%/7%)
polaris/core/risk/
├── fill_rate.py      # 70% cut + 60% hysteresis
└── caps.py           # headroom min() composer
polaris/config/
└── risk_caps.yaml    # cluster + watchdog + fill_rate config
```

### Schema (SQLite)
```sql
CREATE TABLE strategy_risk_state (
  venue TEXT, strategy TEXT,
  closed_trades INTEGER DEFAULT 0,
  kelly_p REAL, kelly_q REAL, kelly_fraction REAL,
  win_streak INTEGER DEFAULT 0,
  hit_rate_10 REAL DEFAULT 0.0,
  updated_ts INTEGER,
  PRIMARY KEY (venue, strategy)
);

CREATE TABLE position_risk_state (
  venue TEXT, symbol TEXT,
  instrument_id TEXT, underlying_group_id TEXT,
  cluster_id TEXT, strategy TEXT, track TEXT,
  signal_strength REAL,
  open_risk_pct REAL, notional_usd REAL,
  opened_ts INTEGER,
  PRIMARY KEY (venue, symbol, strategy, opened_ts)
);
```

### risk_caps.yaml
```yaml
clusters:
  crypto:BTC+ETH:
    members: [crypto:BTC, crypto:ETH]
    gross_cap_pct: 0.40
  cfd:XAU+INDICES:
    match_asset_classes: [metal, index]
    gross_cap_pct: 0.50
  cfd:FX_MAJORS:
    members: [fx:EURUSD, fx:GBPUSD, fx:USDJPY, fx:AUDUSD,
              fx:USDCAD, fx:USDCHF, fx:NZDUSD]
    gross_cap_pct: 0.60

underlying_gross_caps:
  crypto:BTC: 0.60

listing_watchdog:
  age_hours: 24
  size_mult: 0.5
  max_concurrent_positions: 1

fill_rate:
  cut_threshold: 0.70
  resume_threshold: 0.60   # hysteresis

cold_start:
  n_threshold: 20
  single_default_pct: 0.06
  single_amplified_pct: 0.07
```

### Function signatures
```python
# t4.py
def compute_proposed_risk_pct(*, base_risk_pct, continuous_scalar,
    tier_amplifier, cell_routing_mult, listing_watchdog_mult=1.0) -> float

def compute_final_order_risk_pct(*, proposed_risk_pct,
    single_trade_cap_pct, per_symbol_remaining_pct,
    underlying_remaining_pct: float | None,
    cluster_remaining_pct: float | None,
    track_remaining_pct, venue_daily_remaining_pct,
    total_daily_remaining_pct) -> float

# cell_router.py
def resolve_cell_routing_mult(*, exchange, strategy, ticker, regime,
    cell_n_trades: int | None, cell_score: float | None,
    active_score_distribution: list[float]) -> float
def is_sparse_cell(*, cell_n_trades: int | None) -> bool

# cluster_cap.py
def resolve_cluster_id(*, underlying_group_id, asset_class, symbol,
    config) -> str | None
def compute_remaining_headrooms(*, candidate, open_positions,
    venue_daily_used_pct, total_daily_used_pct, config) -> dict[str, float | None]

# fill_rate.py
def compute_fill_rate(*, used_risk_pct, venue_daily_ceiling_pct) -> float
def rank_cut_candidates(open_positions: list) -> list

# kelly.py (CS-3)
def kelly_fraction_or_fallback(*, n_closed, p, q, k=0.5,
    cs3_default=0.06, cs3_amplified=0.07, amplifier_on=False) -> float
```

### P0 vs P1 split
- **P0**: T4 + cell routing factor + CS-3 + listing watchdog 0.5× + cluster cap config-first + fill-rate 70/60 + headroom min() composer.
- **P1**: cluster cap learner-tunable + Sonnet entry-sizer (size 협상) + 8-dim cell routing.
- **P2**: ELO winner-only +0.05/100 trades (max 3.0×).

## Risk + Aggressive Mitigation

### Risk
가장 큰 위험 = cap 순차 절단 → `cell` / `listing_watchdog` / `cluster` 중복 절단 → aggressive bias 남는 척하면서 실제 fill 급감.

### Aggressive Mitigation
- `base × continuous × tier × cell × listing` 까지는 **완전 개방** (composition 깨짐 X).
- 마지막에 hard headroom `min()` **1회만**.
- Triple cold start 차단 X — `6/7% × 0.5` floor 만 적용 = aggressive trial 유지.
- fill-rate cut = signal_strength 1차 (cell bottom 단독 컷 X) → 진짜 약한 신호만 컷.
- cluster cap = config-first (learner 가 cap 자체 만지기 P1 이후) → 안전.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L3_r1_response.md`, gpt-5.4)
- ADR-005 §T4 + CS-3 + cluster + fill-rate + ATR stops
- ADR-006 §cell quartile + sparse n<5 → 1.0
- [[layer-0-universe-discovery]] (underlying_group_id + listing watchdog 0.5×)
- [[layer-1-canonical-baseline]] (baseline_p50 ratio)
