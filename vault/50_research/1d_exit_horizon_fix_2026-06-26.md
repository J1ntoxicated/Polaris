---
type: research
status: recorded
date_created: 2026-06-26
tags: [research, backfilled-frontmatter]
---

# 1D-exit horizon respect — kill intraday-noise thesis_cut churn (2026-06-26)

Re-applied APPROVED fix (commit `2eab87d`, stale base) onto main tip `73bc6b9`,
adapted to the evolved exit module split (thesis logic moved `exit_engine.py` →
`exit_thesis.py`; new flat `EXIT_THESIS_DEADBAND` 0.001 + `broken_streak`
SUSTAINED gate + 25s GRACE that the original base lacked).

## Root cause (live forensic — DEMO/PAPER)
Alpaca equity (`equity_tsmom` / `equity_gap_go` / `equity_rsi_bb_pullback`, all
`timeframe="1D"`) churned at 5-12min hold, win 0-8.8%, NET -$1,162. 174/179
closes were `thesis_cut`, median `pnl_r=-0.03R` (noise), median hold 298s — the
-1.0R rail nowhere near. The bar-recalc feeds `momentum_drift` over the last ~10
1m bars (a ~10-MINUTE window), so a DAILY thesis was declared BROKEN by intraday
noise then CUT because fractionally red. Separately `_loser_timeout_for_strategy`
truncated the 1D stale-loser floor (2×86400=172800s) to the 1H cap (3600s).

## FIX A — `polaris/scripts/_production_recalc_exit.py`
`_loser_timeout_for_strategy`: a ≥4H/1D timeframe-class (one bar ≥
`bar_seconds("4H")`=14400s) is EXEMPT from the 1H `LOSER_TIMEOUT_CAP_SEC` (3600s)
so a daily thesis respects its own horizon (172800s floor). scalp/1H still capped
(tsmom / xau_indices_trend → 3600s). Equity is EOD-flattened → the session rail
is the real backstop; G6 -1.0R rail + ATR-trail (cut real losers far earlier)
untouched. flow_not_block: only LENGTHENS a sideways-drift timeout; never defers
the stop, never blocks/sizes/halts.

## FIX B — `polaris/core/live_recalc/exit_thesis.py`
New env const `EXIT_THESIS_DRIFT_FLOOR` (`POLARIS_EXIT_THESIS_DRIFT_FLOOR`, default
0.0015 = 0.15%) in `exit_params.py`, re-exported via `exit_engine.py`.
`_assess_health` gains `held_seconds` / `horizon_seconds` (threaded from
`assess_thesis`). While `held < horizon`, the momentum-ONLY reversal must clear
`|momentum_drift| >= floor` to count as BROKEN. The flat deadband (0.10%) stays
the universal 1-tick floor; the drift-floor is the ADDITIONAL horizon-scoped
materiality bar that binds in the 0.10%–0.15% band (a long-horizon thesis
tolerates more intraday wiggle than a scalp). CORROBORATED breaks (OFI-opposes /
regime-flip-against) and large adverse drift are NEVER gated → a genuine break
still CUTs instantly. None horizon / None held / past-horizon = INERT (proven
byte-identical: 216/216 inert grid cases match pre-fix BROKEN classification).
CORRECTS a misclassification — does NOT defer a genuine break. -1.0R rail (in the
G6 caller) invariant. Asymmetry preserved; flow_not_block.

## Verify
TDD red→green (new tests fail on unfixed production: FIX A 3600 vs 172800, FIX B
CUT vs no-CUT, env ImportError). 8 FIX-B + 1 FIX-A new tests; noise probe
recalibrated `-0.0008`→`-0.0012` (clears 0.001 deadband, below 0.0015 floor) to
exercise the NEW gate, not the pre-existing deadband. mypy --strict + ruff clean.
Full suite 3314 passed; the 2 fail + 4 err (layer0 universe/alpaca sentinel,
run_debate missing external binary) PRE-EXIST on clean 73bc6b9 — unrelated.
Fresh adversarial review (builder≠reviewer). DEMO/PAPER.
