---
type: strategy
strategy_id: xau_indices_trend
status: active
date_created: 2026-05-07
tags: [strategy, capital, indices, commodity, signal-generator, trend, p0-day4]
related: [[ADR-008-7-strategies-signal-generator-role|ADR-008]], [[layer-7-strategy-isolation]]
---

# XAU/Indices Trend — Capital CFD 1H

## Role
Donchian breakout + 20-day momentum confirmation, 20× leverage.

## Symbols
``XAUUSD / US500 / US100 / GER40``

## Trigger
- ``close > donchian_high(30)``
- AND ``momentum_20bar > 0`` (20-bar return)

## Frozen P0 params
| Param | Value |
|---|---|
| ``donchian`` | 30 |
| ``momentum_lookback`` | 20 |
| ``leverage_max`` | 20× |

## Metadata
- ``timeframe = 1H``
- ``warmup_bars = 35``
- ``max_positions = 4``
- ``gross_cap = 0.40`` (16% × 4 sym)
- ``per_symbol_cap = 0.16``
- ``expected_holding_bars = 48``
- ``asset_class = commodity``
- ``venue = capital``
- ``correlation_group_id = cfd_index_commodity_trend``

## Output
``RawSignal(side="long", strength scales with momentum,
venue_constraints={"leverage_max": 20.0}, ttl_bars=6)``.

## Risk
- 20× lev with structural cap (40% gross) — cluster cap also applies
  ([[layer-3-sizing-risk]]).
- AI Pre-Entry Watcher checks liquidation distance.

## File
``polaris/strategies/xau_indices_trend.py``.
