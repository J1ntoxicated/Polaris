---
type: strategy
strategy_id: rsi_bb_pullback
status: active
date_created: 2026-05-07
tags: [strategy, okx, spot, signal-generator, mean-reversion, p0-day4]
related: [[ADR-008]], [[layer-7-strategy-isolation]]
---

# RSI-BB Pullback — OKX SPOT 15m bar

## Role
Pullback dip-buyer inside an uptrend (mean reversion + trend filter).

## Trigger
- ``rsi_14 < 30``
- AND ``last_low <= bb_lower(20, 2σ)``
- AND ``close > ma_200`` (trend filter — only inside uptrend)

## Frozen P0 params
| Param | Value |
|---|---|
| ``rsi_period`` | 14 |
| ``rsi_threshold`` | 30 |
| ``bb_window`` | 20 |
| ``bb_std`` | 2 |
| ``trend_filter_ma`` | 200 |

## Metadata
- ``timeframe = 15m``
- ``warmup_bars = 205``
- ``max_positions = 4``
- ``gross_cap = 0.18``
- ``per_symbol_cap = 0.06``
- ``expected_holding_bars = 8``
- ``asset_class = spot``
- ``venue = okx``
- ``correlation_group_id = spot_mean_reversion``

## Output
``RawSignal(side="long", strength scales with depth below RSI 30,
ttl_bars=4)``.

## Risk + Aggressive bias
- Trend filter blocks counter-trend dip buying.
- AI Adaptive Exit handles winner extension.

## File
``polaris/strategies/rsi_bb_pullback.py``.
