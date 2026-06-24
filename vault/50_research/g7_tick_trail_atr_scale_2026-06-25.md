---
type: research
status: built-reviewed
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [research, fix, g7, exit, atr-trail, tick, flow_not_block, expectancy]
---

# G7 fix — tick-path ATR-trail clips fresh winners (ATR-scale mismatch)

DEMO/PAPER · aggressive · flow_not_block (precise EXIT TIMING, NOT block/size-cut).
9-stack untouched · GPT=0. systematic-debugging: repro test first → minimal fix →
0 regression. 거부키워드 0. 백링크 [[ab_letrun_maker_2026-06-24]] ·
[[g1_universe_gate_audit_2026-06-23]] · [[north-star]].

## ROOT CAUSE (확정, code+data)
`evaluate_exit` (exit_engine.py:283 pre-fix) measured the ATR-TRAIL WIDTH on
`atr_pct`. The R-unit denominator was already entry-anchored (`entry_atr_pct`,
commit 970fdb2) but the TRAIL WIDTH was not. On the TICK exit pass
(`_production_tick_exit.py:95`) `atr_pct = _window_atr_pct` = mid-range over the
few-second feature window — a seconds-scale number ~10x narrower than the bar ATR
the trail multipliers (2-4x) were calibrated for. So `atr_one` collapsed → stop sat
micro-tight below peak → a single sub-tick reversal force-closed a fresh winner at
~flat (atr_trail_stop). Hypothesis ① CONFIRMED; ② (no profit-arm) ruled out — the
grace gate + SUSTAINED streak already guard the thesis path; the bare ATR trail was
the leak.

## EVIDENCE (data/polaris_live.sqlite, position_strategy_segments ⋈ positions)
| exit_reason | cadence | n | avg_pnl_r | avg_usd | avg_hold_s | avg_mfe_r |
|---|---|---|---|---|---|---|
| atr_trail_stop | tick | 152 | -0.0001 | -0.121 | 16.6 (min 0) | 0.138 |
| atr_trail_stop | bar | 101 | +0.001 | +0.959 | 662 | 0.292 |
| atr_trail_stop | ? (pre-cadence) | 297 | -1.2653 | -2.304 | 332 | 0.314 |

tick path = printed +0.14R MFE, realised ~0 (give-back); bar path (real bar ATR
feeds trail) = healthy +$0.96, 662s holds. The `?` cohort = the legacy −1.26R the
G1 audit measured. The split IS the diagnosis: same engine, different trail ATR
scale → opposite outcomes. tick atr_trail closes = okx flow_pressure
(entry_atr_pct≈0.0012) + burst_rider (≈0.0023).

## FIX (surgical, 3 files, byte-identical for bar)
`evaluate_exit` gains optional `trail_atr_pct`: when set, the trail width `atr_one`
is computed from it instead of `atr_pct`. `None` (bar recalc + every legacy caller)
→ keeps `atr_pct` → byte-identical (bar Chandelier-on-current-ATR intentionally
preserved; peak-fraction-floor test guards it). `run_precise_exit` passes it
through; the tick exit pass passes `trail_atr_pct=entry_atr_pct` (the stable bar
anchor it already reads; NULL→None→graceful live-window fallback).
- `polaris/core/live_recalc/exit_engine.py` — param + `trail_atr_basis` at line ~283
- `polaris/scripts/_production_recalc_exit.py` — passthrough kwarg
- `polaris/scripts/_production_tick_exit.py` — `trail_atr_pct=entry_atr_pct` at call

NUMERIC (entry=100, entry_atr=0.12%, tick_window_atr=0.01%, trail 4x): buggy stop =
peak−0.04 (100.02) → +0.04R winner stopped next tick. fixed stop = peak−0.48
(99.58) → runs. Confirmed in `tests/test_tick_trail_atr_scale.py`.

## VERIFY
repro test `tests/test_tick_trail_atr_scale.py` (5 cases: bug-repro, fix long/short,
None byte-identical, bar-Chandelier-unchanged). All affected modules deterministic
order = 149 passed; mypy --strict + ruff clean on 3 files. flow_not_block: only
WIDENS the tick trail to correct scale (lets winner run), ratchet still forbids
loosening; size / entry / G6 −1.0R rail untouched.

## SUPERVISED DEPLOY CHECKLIST (main, no live)
restart → watch since-reset rollup: atr_trail_stop cadence='tick' avg bps sign
flips ≥0 (was ~−0); tick avg hold ↑ (>16.6s); winner give-back (mfe_r − realised) ↓.
bar cadence must stay unchanged (byte-identical guard). NULL-anchor legacy positions
fall back to old behaviour (graceful) — expect new tick positions only to improve.

mandate_ok=true · builder≠reviewer (pending fresh-Claude review).
