# INVASION SYSTEM ARCHITECTURE

## Stack
- **Language**: Python 3.11+ | **DB**: SQLite WAL | **AI**: Gemini (primary) + Claude (critical)
- **Dashboard**: 2-window terminal UI (operations.py LEFT + intel.py RIGHT)

## Exchange Routing (exchange/router.py)

| Asset Group | Exchange | Mode |
|-------------|----------|------|
| crypto | OKX | Live perp futures |
| forex, index, commodity | Capital.com | Live CFD |
| shares, stock, etf | Alpaca | Paper (US stocks/ETF) |
| (data only) | Binance | WS market data |

Note: OKX tokenized equity perps → `utils/groups.py`가 _SHARES/_ETF로 분류 → Alpaca로 라우팅.

## Layer Architecture

```
EXCHANGE  OKX WS + Binance WS + Cap WS + Alpaca REST
  -> TickHistory ring buffer + REST data_collector
CONFIG    config/themes.py (asset themes) + config/computed.py (Tier 4, auto-adjust disabled)
GATE      trade/gate_matrix.py — 7 active gates: H1, H3, H4, H5, H9, H11, H13 (S-series + H2/H6-H8/H10/H12/H14-H17 removed)
SIGNAL    43 providers (5 files: providers/extended/external/microstructure/onchain) -> CompositeScorer -> contrarian remap -> AI augment
TRADE     TradePipeline: sizing chain (multi-multiplier) -> execute_fn
          -> exit_monitor -> ExitEngine (STOP/TRAIL/TIME)
          -> ai_controller -> _close_position -> DB
          -> market_closed: deferred close + portfolio remove + long cooldown
STRATEGY  StrategyRouter (softmax) -> TournamentEngine (Elo)
          -> StrategyEvolver (mutation types: Gaussian/Bayesian/AI/Structural)
REGIME    Dynamic per-group: independent regimes per asset group + Crisis Escalation
          ALL regimes are ATTACK — no defensive mode
```

## 4-Tier Parameter Classification

| Tier | Type | Source | Persisted | AI Adjustable |
|------|------|--------|-----------|---------------|
| 1 FROZEN | Code constants | Code only | No | No |
| 2 CONFIG | Schema defaults | schema.py seed | Yes | No |
| 3 DYNAMIC | AI Governor | preg + history | Yes | Yes (hourly) |
| 4 COMPUTED | Real-time calc | config/computed.py | No | N/A |

## Decision Architecture — Cell Matrix SSOT (100% 메트릭스화 Pivot)

Plan: [`.claude/plans/cell-matrix-100pct-pivot.md`](../.claude/plans/cell-matrix-100pct-pivot.md) — 5 phase roadmap.

**원칙**: 모든 trade decision = `cell_matrix` lookup. cell 은 8-dim SSOT:
`(exchange × group × session × regime × strategy × direction × ticker × liquidity_tier)`

| Decision | Before (global / hardcode) | After (cell-aware) |
|----------|----------------------------|---------------------|
| Sizing | 11 multiplier chain (regime/session/ticker/tier) | `cell_score_mult` 단일 (axes 흡수) |
| Exit threshold | `preg("trail_activate" / "bep_activate" / "max_hold_sec")` global | `cell.optimal_trail / bep / max_hold` per cell |
| Direction | `if score > 0: "long" else "short"` hardcode | `cell_score_long` vs `cell_score_short` 비교 |
| Provider weight | composer global `preg("provider_weight_*")` | `cell × provider` matrix weight |
| Strategy 선택 | global Elo tournament | cell × strategy Elo |

**preg ↔ cell 관계**:
- `preg` = global default fallback (cell sample 부족 시 safe path)
- `cell` = per-cell learned override (hourly learner 가 업데이트)
- FROZEN 영역만 hardcode 허용 (`clean_data_epoch`, `kill_switch`, safety invariants)

**closed learning loop**: cell_matrix → sizing/exit/direction 결정 → trade outcome → hourly_stats → cell update.

## Data Flow: Signal -> Trade

```
WS price -> unified_scan + okx_scan_tick
  -> get_market_data() -> pipeline.scan_cycle(data, execute_fn)
    -> computed.refresh_all() -- Tier 4 recalc
    -> GateMatrix.evaluate_safety() -- Hard gates
    -> SignalEngine.evaluate() -- providers, composite
    -> GateMatrix.evaluate_signal() -- Soft gates
    -> AI augment -> AI advise -> AI judge
    -> GateMatrix.evaluate_entry() -- cooldown, stale, final gates
    -> _calc_size() -- multi-multiplier sizing chain
    -> execute_fn() -> router -> Adapter -> Position
```

## Data Flow: Position -> Exit

```
exit_monitor.tick() -> ExitEngine.check(pos, price, regime)
  -> STOP: pnl <= hard_stop (regime-adaptive)
  -> TRAIL: BEP zone -> multi-tier interpolation -> profit cap
  -> TIME: early_flat -> stale -> max_hold -> decay
  -> ai_controller override (TIGHTEN/KILL)
  -> _close_position() -> DB -> cooldown -> bus.publish
  -> market_closed: MarketClosedError -> portfolio.remove() -> long cooldown (no retry)
```

## Scheduler
- Tick jobs registered in `boot/run.py` via `sched.register()`
- Intervals range from 1s (exit_monitor) to 86400s (enricher)
- 25 active jobs — current list: `grep "sched.register" invasion/boot/run.py`

## Key Data Files

| File | Purpose |
|------|---------|
| `data/live_config.json` | Hot-reload config |
| `data/portfolio_state.json` | Portfolio SSOT |
| `data/invasion.sqlite` | Trade history, metrics (WAL) |
| `data/invasion.log` | Application log (rotating) |
| `data/strategies/*.json` | Per-strategy state + Elo |
| `data/param_history.jsonl` | Param audit trail |
| `data/regime_presets.json` | Per-regime parameter overrides |

## Key Modules
- Current module list: see `CLAUDE.md` Canonical File Map
- Signal providers: `grep -c "class.*Signal" invasion/signals/providers*.py invasion/signals/ml_signal.py` (currently 26)
- Provider files: providers.py (8 base), providers_extended.py (12), providers_onchain.py (5), ml_signal.py (1)
- Current gate count: 27 gates — H1-H17 + S1-S4,S7-S12 (S5 F&G anchor, S6 trend gate removed)
- Current strategy count: `ls data/strategies/*.json | wc -l`
- Current tick job count: `grep -c "sched.register" invasion/boot/run.py` (currently 25)
- Current collector count: `find invasion/data/collectors/ -name "*.py" ! -name "__init__.py" | wc -l` (currently 27)

## EventBus
- `trade.entered` (pipeline → ai, tournament)
- `trade.closed` (pipeline → tournament, evolver)
- `regime.changed` (regime → param_orch, pipeline)

## Dashboard (2 windows)
- **operations.py** (LEFT): positions, trades, strategy, regime, market overview
- **intel.py** (RIGHT): logs, AI decisions, config, provider chain
- Layout details: read the actual dashboard code

## Clean Data Epoch
- `clean_data_epoch = 1775839507` (2026-04-11 02:45 AEST)
- All analysis uses trades after this timestamp only
