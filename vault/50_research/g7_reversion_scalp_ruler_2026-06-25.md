---
type: research
status: built-reviewed
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [research, fix, g7, exit, scalp, reversion, tick, flow_not_block, expectancy]
---

# G7 part 2 — micro_reversion scalp pnl_r on the wrong (window) ruler

DEMO/PAPER · aggressive · flow_not_block (precise EXIT TIMING, NOT block/size-cut).
9-stack untouched · GPT=0. systematic-debugging: repro test first → minimal fix →
0 regression. 거부키워드 0. 백링크 [[g7_tick_trail_atr_scale_2026-06-25]] (part 1) ·
[[ab_letrun_maker_2026-06-24]] · [[north-star]].

## ROOT CAUSE (확정, code+data)
`_run_exits` (`_production_tick_exit.py`) computes `pnl_r` on `_window_atr_pct` (the
few-second feature-window mid-range, seconds-scale). The MOMENTUM branch re-anchors
that `pnl_r` to the entry-bar ATR (`entry_atr_pct`) before the FSM — but the
REVERSION branch fed the raw window `pnl_r` straight into `_scalp_exit_decision`.
The window ATR is ~88-186x smaller than the entry-bar ATR, so `scalp_target`
(0.35R micro_reversion) + `scalp_stop` (-0.4R) fired on an inflated ruler: a real
+0.10% move read as 0.35R+ and banked instantly. G7 part 1 anchored the momentum
TRAIL WIDTH; the reversion scalp pnl_r DENOMINATOR was the still-open second leg.
The DB `positions.pnl_r`/`mfe_r` use the entry ruler → the TRIGGER ruler and the
RECORDED ruler disagreed (the trigger fired on a ruler the DB never shows).

## EVIDENCE (log close vs DB pnl_r, same trade_id — J225)
| position | log close (window자) | DB pnl_r (entry자) | 배율 |
|---|---|---|---|
| ...J225_1782345961 | scalp_target 0.39 | 0.004405 | 88x |
| ...J225_1782346832 | scalp_target 0.35 | 0.003326 | 105x |
| ...J225_1782350773 | scalp_target 0.35 | 0.001881 | 186x |

15 scalp closes ALL micro_reversion, log pnl_r 0.35-0.42 (window자 hitting target);
DB mfe_r 0.49-0.63 (winners cut at crumbs). avg 168s, 8 <30s = 즉시튕김. honest $:
J225 banked +$1.9/+$3.4/+$4.5 부스러기 instead of letting the revert run.

## FIX (surgical, 1 prod file, momentum byte-identical)
Reversion branch reads `SELECT entry_atr_pct FROM positions WHERE position_id=?` and
recomputes `scalp_pnl_r = compute_unrealized_pnl_r(... atr_pct=entry_atr_pct)` before
`_scalp_exit_decision` — same ruler as momentum + the DB. NULL/missing row → keep the
window `pnl_r` (legacy-graceful, byte-identical pre-anchor). Loss side (scalp_stop)
re-anchors SYMMETRICALLY (window자 inflated it too). Momentum diff = reversion-only.
- `polaris/scripts/_production_tick_exit.py` — reversion `scalp_pnl_r` re-anchor

NUMERIC (entry=100, entry_atr=1.2%, window_atr=0.05%): +0.04% → window 0.40R (trips
0.35 target) vs entry 0.017R (holds); -0.04% → window -0.50R (trips stop) vs entry
-0.017R (holds). 4 repro tests (target/stop/NULL-fallback/ruler-unit) FAIL on
window자, PASS on entry자; momentum byte-identity guarded by `test_momentum_exit_*`
+ part-1 suites. mypy --strict + ruff clean. flow_not_block: removes an erroneous
early-bank (winner flows); size / entry / G6 -1.0R rail untouched.

## DEFERRED (/debate — trading-param, NOT this commit)
scalp_target 0.35R + scalp_stop -0.4R now fire on the CORRECT (larger) entry자 move.
Whether 0.35R-entry자 is the right TP for a bounded mean-revert scalp (+ peak-lock
arm 1.0R vs measured 0.4-0.6R peaks) = recalibration → re-measure on the fixed ruler,
then /debate + backtest. This commit = ruler unification only.

mandate_ok=true · builder≠reviewer (fresh-Claude APPROVE, 0 blocker).
