---
type: research
status: built-reviewed
date_created: 2026-06-26
date_updated: 2026-06-26
tags: [layer-0, candidate-sweep, activation, capital, flow-not-block, per-symbol, built]
---

# Capital Activation Per-Symbol — BUILT (#39)

Implements the debate-converged spec [[capital_activation_2026-06-26]] (GPT+Gemini
both signed). Root: Capital emit 0.13% because the sweep's activation was
venue-inflow + Yahoo-daily — blind to intraday-active majors (EURUSD 9s tick,
US100), so it picked calm wide-daily agriculturals.

## What changed
- **New bounded per-symbol accumulator** `_SymbolActivation` (`polaris/core/data/_tick_activation.py`, split from `quote_writer.py` for ≤500 LOC). In-mem only (NO disk), keyed `instrument_id` (cross-venue safe), 60s buckets / 600s window (≤10 buckets, O(1) append), map LRU-capped at `ACTIVATION_MAX_SYMBOLS=4000` (retention guard — no infinite growth). Exposes `ticks_600s` / `mid_120s_ago` / `mid_high_600s` / `mid_low_600s` / `last_mid` via `QuoteTickWriter.activation_metrics(iid)`.
- **activation = 5-component ADDITIVE clamped blend** (`_candidate_sweep_motion.py`): tick_rate 0.30 + short_move 0.25 + intraday_range 0.15 + spread_tradeability 0.20 + daily_vol 0.10. Live motion = 0.70; Yahoo daily-vol demoted 0.45→0.10. spread = ADDITIVE term (tight→1, wide→0), NEVER a multiplier/filter.
- **candidate = cold_start_mult × clamp01(0.75×activation + 0.25×edge)** — additive (was gate `act×(0.5+0.5×edge)`). Cold-start 0.5× is the ONLY ≤1 mult (9-stack ban honored). An active major with empty edge keeps 0.75× activation (no longer zeroed).
- **edge tech-dominant** `clamp01(0.35×sentiment + 0.65×tech)`; missing component → `EDGE_MISSING_COMPONENT_DEFAULT=0.5` NEUTRAL (sparse Capital COT/sentiment no longer drags a major below neutral).
- **Wiring**: `_score_universe` reads per-symbol motion (writer accumulator) + per-symbol `quote_ticks.spread_bps`; `quote_writer` threaded `refresh_focus_watchlist → _sweep_focus → select_candidate_focus` (optional `activation_provider`, `hasattr`-degrade to neutral). All thresholds env-named (`POLARIS_SWEEP_ACT_*`).

## Verify
- TDD: 17 new tests (accumulator bounded/instrument_id/disk-X/LRU + 5-component additive + edge missing→0.5 + candidate additive no-9-stack + spread additive). 70 relevant pass.
- ruff clean · mypy --strict clean · all 6 source files ≤500 LOC.
- Fresh adversarial review (builder≠reviewer): NO blockers/majors — 9-stack/flow_not_block/boundedness/look-ahead all PASS; rejection-keyword sweep 0 hits.

## Expect
Capital focus head shifts to active liquid majors (EURUSD/US100…) → more emit/entry.
Env thresholds (FLOOR/HOT/SPREAD bps) are live-calibration targets.
