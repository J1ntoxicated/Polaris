---
type: ADR
adr_id: ADR-008
aliases: [ADR-008]
status: active
date_created: 2026-05-06
date_updated: 2026-06-28
tags: [adr, strategies, signal-generator]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[active-autonomous-vision]]
reviewed_by: codex+jin (round 3 D1 + Jin clarification 21:30)
---

# ADR-008 — 22 Strategies (Signal Generator Role Only)

> 📌 **UPDATE 2026-06-28 (dual-SSOT dispatch fix)**: registry = **22 strategies**; LIVE bar dispatch = the **17** whose `metadata.dispatch_eligible=True`, DERIVED from STRATEGY_REGISTRY (no hand-synced literal — drift structurally impossible). SSOT = `polaris/strategies/__init__.py`. KILL = `dispatch_eligible=False` (no-emit, NOT un-register — open-position close path preserved): rsi_bb_pullback (fee-fatal 15m crypto reversion) + ema_crossover (fee-fatal 1H crypto cross: gross +$0.12 < OKX taker fee $2.37/round-trip) + supertrend / connors_rsi2 / cci_reversion (no OOS/fee evidence). ema_crossover's DEFERRED KILL judgement LANDED 2026-06-28 once its live read confirmed it is fee-fatal — same no-emit dispatch pattern as rsi_bb_pullback. UN-registered (module read-only, not in registry): volume_burst, tsmom (cross-sym), spot_donchian, fx_range_fade, equity_tsmom, equity_rsi_bb, equity_gap_go.

## Decision

22 strategies registered; 17 dispatch-eligible (validated fee-beaters; ema_crossover KILLed 2026-06-28, fee-fatal). Each strategy's role = **`generate_raw_signal(market_view) → RawSignal | None` only**. Lifecycle (entry/exit/swap) = AI gate ([[ADR-004-per-gate-ai-pipeline|ADR-004]]). Dispatch eligibility = the per-strategy `dispatch_eligible` flag (the single source of truth; the bar pipeline filters the registry on it).

## Role Redefinition

**이전 (정적 4-method)** — 폐기:
```python
class Strategy:
    def should_enter(...) -> bool
    def compute_size(...) -> float
    def update_position(...) -> Action
    def should_exit(...) -> bool
```

**신 (signal generator only)**:
```python
class Strategy(ABC):
    metadata: StrategyMetadata
    def generate_raw_signal(market_view: MarketView) -> RawSignal | None: ...
```

Lifecycle = [[ADR-004-per-gate-ai-pipeline|ADR-004]] 8 gate. Strategy 는 signal 만 emit, 나머지는 AI 가 결정.

## 22 Strategies (registry) — 18 dispatch-eligible

DISPATCH = registry filtered on `metadata.dispatch_eligible` (no separate literal).

### Track A — OKX SPOT/crypto

| Strategy | tf | dispatch | note |
|---|---|---|---|
| bar_breakout_run | 1D | ✅ | Donchian-40 + ROC-10 breakout (wave1) |
| okx_donchian_55_breakout | 1D | ✅ | wave1 fee-beating survivor |
| tsmom_12_1_multiasset | 1D | ✅ | wave1 (equity legs inert until SIP) |
| macd_ema_trend_pullback | 1D | ✅ | wave1 |
| donchian_turtle_breakout | 1D | ✅ | wave1 turtle |
| weekend_thin_book_flush_maker | 1H | ✅ | #77 verified crypto maker |
| weekend_funding_capitulation_maker | 1H | ✅ | #80 funding edge (shadow-first) |
| rsi_bb_pullback | 15m | ❌ KILL | fee-fatal 15m crypto reversion |
| ema_crossover | 1H | ❌ KILL | fee-fatal 1H crypto cross (gross +$0.12 < fee $2.37); deferred KILL landed 2026-06-28 |
| supertrend | 1H | ❌ KILL | unvalidated |

### Track B — Capital CFD

| Strategy | tf | dispatch | note |
|---|---|---|---|
| fx_breakout_basket | 1H | ✅ | named verified FX leg (maker-entry fix pending) |
| xau_indices_trend | 1H | ✅ | |
| session_breakout | 5m | ✅ | edge real, exit-capture FIX #64 pending |
| gold_trend_chandelier_1d | 1D | ✅ | wave2 |
| gold_riskoff_trend_amplify | 1D | ✅ | wave2 |
| gold_breakout_1h | 1H | ✅ | wave2 |
| index_52w_high_momentum | 1D | ✅ | wave2 |
| index_dual_momentum_rotation | 1D | ✅ | wave2 |
| cci_reversion | 1H | ❌ KILL | unvalidated |

### Track C — Alpaca US equity (inert until SIP #42)

| Strategy | tf | dispatch | note |
|---|---|---|---|
| equity_52wk_high_breakout | 1D | ✅ | wave2 (inert-data, degrade-never-crash) |
| equity_vol_expansion_pocket_pivot | 1D | ✅ | wave2 (inert-data) |
| connors_rsi2 | 1D | ❌ KILL | unvalidated |

> caps/correlation 정확값 = `polaris/strategies/__init__.py` + StreamConfig SSOT (이 표는 역할/식별 + dispatch 상태 목적).

## RawSignal Schema

```python
@dataclass
class RawSignal:
    signal_id: str               # uuid
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    strength: float              # 0-1 (validator → scalar 0.5-1.5×)
    sizing_hint: float           # 0-1 (entry-sizer 참고용)
    ttl_bars: int
    thesis_tag: str              # human-readable (post-trade reflector 학습 입력)
    correlation_group: str       # spot_intraday_event / cfd_fx_trend / ...
    venue_constraints: dict      # symbol-specific (min_qty, tick_size)
    created_at_bar: int
    tags: dict[str, str]
```

## StrategyMetadata

```python
@dataclass
class StrategyMetadata:
    timeframe: str               # "1m", "15m", "1H"
    warmup_bars: int             # 최소 indicator warmup
    max_positions: int           # concurrent
    gross_cap: float             # per-strategy %
    per_symbol_cap: float        # per-symbol % within strategy
    expected_holding_bars: int
    asset_class: str             # "spot" / "fx" / "index" / "commodity"
    venue: str                   # "okx" / "capital" / "alpaca"
    correlation_group_id: str
    dispatch_eligible: bool = True   # SSOT — bar pipeline dispatches iff True;
                                     # False = no-emit KILL (still registered,
                                     # open-position close path preserved)
```

## Correlation Group → Concurrent Caps

Per-group concurrent caps + correlation_group_id = `metadata` per strategy (the
SSOT). Each registry strategy declares its own `correlation_group_id`; see the
roster table above for the active set (KILLed groups' strategies stay registered,
no-emit). Live concurrency is governed by the per-symbol/per-strategy risk caps
([[layer-3-sizing-risk]]).

## File Layout

```
polaris/strategies/
├── base.py           # Strategy ABC + RawSignal + StrategyMetadata (dispatch_eligible)
├── __init__.py       # STRATEGY_REGISTRY (22) — the dispatch SSOT
└── <one module per strategy>  # each ≤ ~100 LOC, signal-gen only (lifecycle X)
```

## Dispatch (dual-SSOT fix 2026-06-28)

Live bar dispatch (`_production_tick._all_strategies`) is DERIVED from the
registry: `[cls() for cls in STRATEGY_REGISTRY.values() if cls.metadata.dispatch_eligible]`.
No second hand-synced literal → registry-add + flag is the single source; drift
(registered ≠ dispatched INERT, or kill ≠ removal zombie) is structurally
impossible and guarded by `tests/test_dispatch_ssot.py`. Layer 7 isolation
(per-strategy worker / circuit breaker / idempotent keys) is unchanged.

## Anti-pattern (재발 방지)
- 4-method lifecycle → signal generator only
- Strategy 가 sizing 결정 → Layer 3 sizing engine 만 (AI Entry Sizer 결정)
- Strategy 가 exit 결정 → Layer 6 Adaptive Exit AI ([[ADR-004-per-gate-ai-pipeline|ADR-004]] gate 7)
- 정적 ATR exit → Adaptive Exit override 가능 (winner 길게)
- 별도 dispatch 리터럴 (registry ≠ dispatch) → silent INERT / kill≠removal 좀비. 봉쇄 = `dispatch_eligible` SSOT + `test_dispatch_ssot.py`.

## Sources
- Round 3 D1 (signal generator only, isolation)
- Jin clarification 21:30 (signal generator only role)
- T11 archive: per-gate AI lifecycle 결정
- 2026-06-28 dual-SSOT dispatch fix (#56 strategy restructure)
