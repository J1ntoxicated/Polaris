---
type: strategy
strategy_id: tsmom
status: active
date_created: 2026-05-07
tags: [strategy, okx, spot, signal-generator, p0-day4, momentum]
related: [[ADR-008]], [[layer-7-strategy-isolation]]
---

# TSMOM 20-bar — OKX SPOT 1H rebalance

## Role
Cross-sectional basket momentum signal generator. Top-N selection = orchestrator.

## Trigger
- ``momentum_20bar > 0`` per symbol (return over 20 1H bars).
- Orchestrator picks ``top_n = 5`` per cycle.

## Frozen P0 params
| Param | Value |
|---|---|
| ``lookback_bars`` | 20 |
| ``top_n`` | 5 (basket cap) |

## Metadata
- ``timeframe = 1H``
- ``warmup_bars = 25``
- ``max_positions = 5``
- ``gross_cap = 0.32``
- ``per_symbol_cap = 0.08``
- ``expected_holding_bars = 24``
- ``asset_class = spot``
- ``venue = okx``
- ``correlation_group_id = spot_cross_sectional_momo``

## Output
``RawSignal(side="long", strength = clamp(0.5 + 5×momentum, 0.4, 1.0),
ttl_bars=4)``.

## Risk
- Cross-sectional drawdown reduced via basket-of-N (orchestrator side).
- Lifecycle = AI gates; no built-in stop.

## File
``polaris/strategies/tsmom.py``.
