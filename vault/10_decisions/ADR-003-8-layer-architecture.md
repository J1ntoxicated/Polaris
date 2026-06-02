---
type: ADR
adr_id: ADR-003
aliases: [ADR-003]
status: active
date_created: 2026-05-06
tags: [adr, architecture, 8-layer]
related: [[active-autonomous-vision]], [[ADR-004]], [[ADR-005]], [[ADR-006]], [[ADR-007]], [[ADR-008]]
reviewed_by: codex+jin (round 2 T2 + round 3 D1 isolation + Jin clarification)
---

# ADR-003 — 8-Layer Active Autonomous Architecture

## Decision

Polaris architecture = **8 layers** (Common Core 5 + Strategy 1 + Isolation 1 + Per-venue adapters):

### Layer 0 — Dynamic Universe Discovery
- OKX SPOT: `GET /api/v5/market/tickers?instType=SPOT` → 510 instruments
  - Filter: 24h vol > $30M (learner-tunable) + state=live + USDT-quote
  - Active: ~100-150 watchlist
  - Refresh: 매 5min, event-driven on listing change
- Capital CFD: `marketnavigation` tree → 5 카테고리
  - P0 universe: forex (~70) + indices (~30) + commodity (~20) + crypto CFD (~50) = ~170
  - Shares ~5000 = P2 후보
  - Refresh: 매 10min
- Total active: ~270-320 instruments

**파일**: `polaris/core/universe/{discovery.py, watchlist.py}`

### Layer 1 — Canonical Market Model + Ticker Baseline
- 모든 venue → unified bar/event stream (OHLCV + ts + venue + symbol + tick deltas)
- Ticker baseline 5 metric (T11 archive): ATR / size / signal / volume / pnl_std
- `normalize(ticker, metric, raw)` API (Universal cross-ticker comparable)

**파일**: `polaris/core/data/{canonical.py, baseline.py, normalize.py}`

### Layer 2 — Per-Gate AI Agent Pipeline (LangGraph-style)
See [[ADR-004]] for full spec.

```
Universe Scanner [Haiku] → Strategy Signal Gen [Python] → 
Signal Validator [Haiku] → Pre-Entry Watcher [Haiku 30s] → 
Entry Sizer [Sonnet/Python P0] → Position Monitor [Sonnet/Python P0] → 
Adaptive Exit [Sonnet/Python P0] → Post-Trade Reflector [Sonnet/Python template P0]
```

**파일**: `polaris/core/pipeline/{gate_orchestrator.py, gate_state.py, agents/*.py}`

### Layer 3 — Sizing + Risk Engine
See [[ADR-005]] for full spec.

```
notional = base × continuous_scalar(0.75-1.5×) × tier_amplifier(1.5/2/3×) × cell_routing_mult
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```

**파일**: `polaris/core/sizing/{engine.py, kelly.py, amplifier.py, cluster_cap.py}`

### Layer 4 — Cell Matrix
See [[ADR-006]] for full spec. P0 = 4-dim 압축, P1 = 8-dim full.

**파일**: `polaris/core/cell_matrix/{schema.py, score.py, routing.py}`

### Layer 5 — Learner Network 7
See [[ADR-007]] for full spec. 6 T11 + 1 AI feedback. Hourly auto-tune.

**파일**: `polaris/core/learners/{base.py, session.py, regime.py, max_hold.py, profit_target.py, trail.py, bep.py, ai_feedback.py}`

### Layer 6 — Live Recalc + Self-Correction
- Per-tick: position exit_params 재계산 (Universal 3-layer formula)
- Regime flip: 활성 position size/exit 자동 조정
- Mid-trade strategy swap: AI Position Monitor → cell_matrix 참조 → swap

**파일**: `polaris/core/live_recalc/{tick_recalc.py, regime_flip.py, strategy_swap.py}`

### Layer 7 — Strategy Isolation Primitives
Round 3 D1 — 7 mechanisms:
1. Per-strategy process boundary (subprocess / asyncio task / separate worker)
2. Per-strategy state namespace (`positions/{sid}`, `orders/{sid}`, `risk/{sid}`)
3. Immutable portfolio inputs (read-only account snapshot)
4. Strategy-scoped circuit breaker (예외 / order reject / NaN sizing / stale data → strategy_id HALT)
5. Global allocator hard fence (T4 산출 후 중앙 allocator 강제)
6. Idempotent order keys (`strategy_id + symbol + timeframe + signal_ts`)
7. Kill-switch granularity (default `kill(strategy_id)`, global stop = exchange/session 장애 only)

**파일**: `polaris/core/isolation/{worker.py, namespace.py, circuit_breaker.py, allocator_fence.py}`

### Per-Venue Adapters (NOT unified "Exchange")
- `MarketDataProvider` (Capital, OKX)
- `ExecutionProvider` (Capital, OKX)
- `PositionLedger` (정규화 internal model)
- `OrderStateNormalizer` ({pending, partial, filled, cancelled, rejected})

**파일**: `polaris/venues/{okx,capital}/{adapter.py, signing.py, constraint_translator.py, session.py}`

## Unified SQLite Schema (venue 컬럼 single)
```sql
CREATE TABLE positions(venue TEXT, symbol TEXT, strategy_id TEXT, ...);
CREATE TABLE fills(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE orders(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE signals(strategy_id, signal_id, correlation_group, ...);
CREATE TABLE events(timestamp, type, agent, mode, payload_json);
CREATE TABLE cell_matrix(exchange, strategy, ticker, regime, n_trades, win_rate, avg_pnl, score);
CREATE TABLE learner_state(learner_id, key, value, updated_at);
CREATE TABLE universe(venue, symbol, vol_24h, last_updated, active);
```

## Phase
- P0 (5-7d sprint): Layers 0/1/4/5/7 + L2 4 Haiku gate + L3 sizing + L6 stub + 7 strategies signal generator + venue adapters REST
- P1 (week 2-3): L2 Sonnet upgrade + L4 8-dim full + L5 7 learner full + L6 self-correction full + WebSocket
- P2 (week 4+): cross-venue arb + 3rd venue + ELO + Conviction stacking

## Sources
- Round 2 T2 (canonical layer + unified schema)
- Round 3 D1 (isolation primitives 7 mechanism)
- Jin clarification 21:30 (active autonomous)
- T11 archive (universal normalize / cell matrix / learner / live recalc)
