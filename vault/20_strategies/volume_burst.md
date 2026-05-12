---
type: strategy
strategy_id: volume_burst
status: active
date_created: 2026-05-07
tags: [strategy, okx, spot, signal-generator, p0-day4]
related: [[ADR-008]], [[layer-7-strategy-isolation]], [[layer-2-per-gate-pipeline]]
---

# Volume Burst — OKX SPOT 1m bar

## Role
**Signal generator only** ([[ADR-008]]). Lifecycle = AI gate pipeline.

## Trigger
- ``volume_z >= 2.5``
- AND ``close > prior_high(lookback=20)``
- AND ``atr_pct >= 0.05%`` (liquidity floor — micro-caps blocked)

## Frozen P0 params
| Param | Value | Note |
|---|---|---|
| ``vol_z_threshold`` | 2.5 | rolling z over 20-bar volume |
| ``lookback`` | 20 | high look-back window |
| ``atr_floor_pct`` | 0.0005 | 0.05% fraction |

## Metadata (StrategyMetadata)
- ``timeframe = 1m``
- ``warmup_bars = 25``
- ``max_positions = 3``
- ``gross_cap = 0.24`` (24%)
- ``per_symbol_cap = 0.08``
- ``expected_holding_bars = 15``
- ``asset_class = spot``
- ``venue = okx``
- ``correlation_group_id = spot_intraday_event``

## Output
``RawSignal(side="long", strength scales with vol_z above threshold,
sizing_hint matches strength, ttl_bars=10)``.

## Risk + Aggressive bias
- No defensive ATR exit baked in (Adaptive Exit AI handles).
- Strength capped at 1.0; floor 0.6 keeps high-conviction default.

## Test fixture
``tests/test_strategies_signal_gen.py::test_volume_burst_emits_signal_on_break``.

## File
``polaris/strategies/volume_burst.py``.
