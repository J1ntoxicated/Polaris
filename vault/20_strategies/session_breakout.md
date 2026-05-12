---
type: strategy
strategy_id: session_breakout
status: active
date_created: 2026-05-07
tags: [strategy, capital, indices, fx, signal-generator, session, p0-day4]
related: [[ADR-008]], [[layer-7-strategy-isolation]]
---

# Session Breakout — Capital CFD 5m

## Role
Open-window ATR breakout (first 30 min after session open).

## Symbols
``US500 / US100 / EURUSD / GBPUSD``

## Trigger
- inside ``open_window_minutes = 30`` after session open
- AND ``close > session_open_price + ATR(14) × 1.5``

## Frozen P0 params
| Param | Value |
|---|---|
| ``open_window_minutes`` | 30 |
| ``atr_period`` | 14 |
| ``atr_mult`` | 1.5 |
| ``leverage_max`` | 20× |

## Metadata
- ``timeframe = 5m``
- ``warmup_bars = 19``
- ``max_positions = 2``
- ``gross_cap = 0.20``
- ``per_symbol_cap = 0.10``
- ``expected_holding_bars = 12``
- ``asset_class = index``
- ``venue = capital``
- ``correlation_group_id = cfd_session_event``

## Output
``RawSignal(side="long", strength scales with ATR-multiple excess,
venue_constraints={"leverage_max": 20.0}, ttl_bars=3)``.

## Risk
- Session-event scoped — outside window returns ``None``.
- AI Adaptive Exit handles winner extension; default holding ≤ 12 bars.

## File
``polaris/strategies/session_breakout.py``.
