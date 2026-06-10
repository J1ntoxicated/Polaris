---
type: ADR
adr_id: ADR-008
aliases: [ADR-008]
status: active
date_created: 2026-05-06
tags: [adr, strategies, signal-generator]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[active-autonomous-vision]]
reviewed_by: codex+jin (round 3 D1 + Jin clarification 21:30)
---

# ADR-008 — 7 Strategies (Signal Generator Role Only)

## Decision

7 P1 strategies 동시 활성 (P1.0 day 1). 각 strategy 역할 = **`generate_raw_signal(market_view) → RawSignal | None` 만**. Lifecycle 결정 (entry/exit/swap) = AI gate ([[ADR-004-per-gate-ai-pipeline|ADR-004]]).

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

## 7 Strategies

### Track A — OKX SPOT (4)

| # | Strategy | Trigger | Timeframe | Per-strategy cap | Correlation group |
|---|---|---|---|---|---|
| 1 | [[volume_burst]] | vol z>2.5 + price break + liquidity floor | 1m bar | 24% | spot_intraday_event |
| 2 | [[tsmom]] 20-bar | cross-sect basket momentum | 1H rebalance | 32% | spot_cross_sectional_momo |
| 3 | [[rsi_bb_pullback]] | RSI<30 + BB lower touch + trend filter | 15m bar | 18% | spot_mean_reversion |
| 4 | [[spot_donchian]] | 40-bar high break + ADX>20 | 1H | 20% | spot_breakout |

### Track B — Capital CFD (3)

| # | Strategy | Symbols | Trigger | Lev | Per-strategy cap | Correlation group |
|---|---|---|---|---|---|---|
| 5 | [[fx_breakout_basket]] | EURUSD, GBPUSD, AUDUSD, USDJPY, USDCAD | Donchian 40 + ADX>20 | 30× | 12%/pair × 5 = 36% | cfd_fx_trend |
| 6 | [[xau_indices_trend]] | XAUUSD, US500, US100, GER40 | Donchian 30 + 20d momentum | 20× | 16%/sym × 4 = 40% | cfd_index_commodity_trend |
| 7 | [[session_breakout]] | US500, US100, EURUSD, GBPUSD | open ATR×1.5 break | 20× | 10%/trade × 20% concurrent | cfd_session_event |

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
    venue: str                   # "okx" / "capital"
    correlation_group_id: str
```

## Correlation Group → Concurrent Caps

| Group | Strategy | Max concurrent positions |
|---|---|---|
| spot_intraday_event | Volume Burst | 3 |
| spot_cross_sectional_momo | TSMOM | 5 |
| spot_mean_reversion | RSI-BB Pullback | 4 |
| spot_breakout | Spot Donchian | 3 |
| cfd_fx_trend | FX Breakout | 5 |
| cfd_index_commodity_trend | XAU/Indices | 4 |
| cfd_session_event | Session Breakout | 2 |

## File Layout

```
polaris/strategies/
├── base.py           # Strategy ABC + RawSignal + StrategyMetadata
├── volume_burst.py   (~50 LOC)
├── tsmom.py          (~70 LOC)
├── rsi_bb.py         (~60 LOC)
├── spot_donchian.py  (~50 LOC)
├── fx_breakout.py    (~60 LOC, basket logic)
├── xau_indices.py    (~60 LOC)
└── session_breakout.py (~50 LOC)
```

각 strategy ≤ 100 LOC (signal gen only, lifecycle X).

## P1.0 Day 1 동시 활성

7 strategy 모두 day 1 동시 활성 ([[ADR-003-8-layer-architecture|ADR-003]] Layer 7 isolation 보장):
- Per-strategy worker (Layer 7 mechanism 1)
- Per-strategy circuit breaker (mechanism 4)
- Idempotent order keys (mechanism 6)
- 첫 24h watchdog focus = segregation/wiring 검증 (성능 평가 X)

## Anti-pattern (재발 방지)
- 4-method lifecycle → signal generator only
- Strategy 가 sizing 결정 → Layer 3 sizing engine 만 (AI Entry Sizer 결정)
- Strategy 가 exit 결정 → Layer 6 Adaptive Exit AI ([[ADR-004-per-gate-ai-pipeline|ADR-004]] gate 7)
- 정적 ATR exit → Adaptive Exit override 가능 (winner 길게)

## Phase
- P0: 7 strategy raw signal generator + Volume Burst single-ticker smoke
- P1.0 day 1: 7 동시 활성 + 24h watchdog
- P1.1: 자연 운영, lever 변경 = [[ADR-002-vision|ADR-002]] §1 trigger 충족 시
- P2: ELO winner-only sizing 증액 ([[ADR-002-vision|ADR-002]] C 메커니즘)

## Sources
- Round 3 D1 (7 strategy 동시, isolation)
- Jin clarification 21:30 (signal generator only role)
- T11 archive: per-gate AI lifecycle 결정
