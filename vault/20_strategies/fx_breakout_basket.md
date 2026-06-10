---
type: strategy
strategy_id: fx_breakout_basket
status: active
date_created: 2026-05-07
tags: [strategy, capital, fx, signal-generator, breakout, p0-day4]
related: [[ADR-008-7-strategies-signal-generator-role|ADR-008]], [[layer-7-strategy-isolation]]
---

# FX Breakout Basket — Capital CFD 1H

## Role
Trend basket on FX majors (Donchian 40 + ADX filter, 30× leverage).

## Symbols
``EURUSD / GBPUSD / AUDUSD / USDJPY / USDCAD``

## Trigger
- Per symbol: ``close > donchian_high_40`` AND ``adx_14 > 20``.

## Frozen P0 params
| Param | Value |
|---|---|
| ``window`` | 40 |
| ``adx_period`` | 14 |
| ``adx_threshold`` | 20 |
| ``basket`` | 5 |
| ``leverage_max`` | 30× |

## Metadata
- ``timeframe = 1H``
- ``warmup_bars = 45``
- ``max_positions = 5``
- ``gross_cap = 0.36`` (12% × 5 pairs)
- ``per_symbol_cap = 0.12``
- ``expected_holding_bars = 24``
- ``asset_class = fx``
- ``venue = capital``
- ``correlation_group_id = cfd_fx_trend``

## Output
``RawSignal(side="long", strength scales with ADX,
venue_constraints={"leverage_max": 30.0}, ttl_bars=6)``.

## Risk
- 30× lev → small fractional risk → liquidation gap = AI Pre-Entry Watcher
  responsibility.

## File
``polaris/strategies/fx_breakout_basket.py``.
