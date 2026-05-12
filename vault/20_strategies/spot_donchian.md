---
type: strategy
strategy_id: spot_donchian
status: active
date_created: 2026-05-07
tags: [strategy, okx, spot, signal-generator, breakout, p0-day4]
related: [[ADR-008]], [[layer-7-strategy-isolation]]
---

# Spot Donchian — OKX SPOT 1H

## Role
Trend-quality-gated breakout (Donchian 40 + ADX filter).

## Trigger
- ``close > donchian_high(window=40)``
- AND ``adx_14 > 20``

## Frozen P0 params
| Param | Value |
|---|---|
| ``window`` | 40 |
| ``adx_period`` | 14 |
| ``adx_threshold`` | 20 |

## Metadata
- ``timeframe = 1H``
- ``warmup_bars = 45``
- ``max_positions = 3``
- ``gross_cap = 0.20``
- ``per_symbol_cap = 0.07``
- ``expected_holding_bars = 24``
- ``asset_class = spot``
- ``venue = okx``
- ``correlation_group_id = spot_breakout``

## Output
``RawSignal(side="long", strength = clamp(0.5 + (adx-20)/40, 0.5, 1.0),
ttl_bars=6)``.

## Risk
- ADX filter blocks chop-zone false breaks.
- Lifecycle (entry/exit) handed off to AI gates.

## File
``polaris/strategies/spot_donchian.py``.
