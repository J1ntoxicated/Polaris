---
type: research
status: shipped
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [entrance-judge, lean, parent2-prior, dead-code-wiring, flow_not_block]
---

# Entry-evidence chain wiring — D1+D2 lean seam + D3 parent2-prior writer

**Source**: dead-code audit `w4wb7123o` (DISCONNECTED [A]+[C-prior]) · [[code_review_2026-06-24]].
**Problem**: built-but-unwired seams — the bot held evidence it never spent.

## D1+D2 — EntranceJudge lean seam (3 dead lenses → live)
`entrance.py` is a 5-lens design but production fed only 2 (liquidity+ATR);
`technical`/`regime`/`altdata` defaulted neutral forever → focus ranked half-blind.
Pure wiring, ZERO new params — existing weights `_W_TECHNICAL=0.7`/`_W_REGIME=0.6`/
`_W_ALTDATA=0.4` reused.
- NEW `core/probes/entrance_leans.py` (pure, 174 LOC): 3 builders → per-`instrument_id`
  signed lean ∈[-1,+1] from data the loop ALREADY holds.
  - `technical` = live-tick mid-drift (`quote_writer.feature_window`) → tanh.
  - `regime` = `regime_state` SSOT `direction_bias` (bull +1 / bear -1; signal-family-FREE,
    so usable at Layer-0 focus cadence where no strategy has fired yet).
  - `altdata` = already-fused `fuse_evidence` label scores → one signed tilt
    (bull−bear norm; dominant crisis → -1), broadcast group→instruments.
- WIRED `_production_layers.py refresh_focus_watchlist`: +`quote_writer`/`altdata_cache`
  params, builds 3 maps, passes to `judge_universe`. `_fused_scores` adapter (fail-soft→None).
- `production_paper_loop.py`/`_production_state.py`: `state.altdata_cache` (mirrors
  `state.quote_writer`). First refresh = None → neutral (loop spawns L0 before WS/cache).
- **9-stack safe**: leans feed `opportunity_score` (rank) ONLY — never a sizing mult.
  Absent/thin/error datum → omitted (neutral) → flow_not_block, never raises into refresh.

## D3 — strategy_regime_prior runtime writer (parent2 seed)
Reader `posterior._load_parent_prior:271` + DDL existed but ZERO runtime writer (test
fixtures only) → parent2 hierarchical seed permanently the flat weak default.
- NEW `maybe_update_strategy_regime_prior` (posterior.py): folds closed-trade
  (strategy, regime, R) into the strategy×regime NIG prior, Welford-online.
  Running mean recovered from the FIXED default seed (κ0=1/μ0=0) — closed-form parity
  (verified bit-identical, max err 8.5e-14).
- WIRED inside `_safe_update_posterior` (close fan-out) from the SAME cost-adjusted
  `pnl_r_net` the child cell folds (unit-consistent — review fix; was raw R).
  Fail-open; never read by sizing (9-stack intact).

## Verification
- TDD: 14 lean-builder + 4 prior-writer + 3 focus-integration + 1 close-charge tests.
- Full suite 3106 pass (2 fail = PRE-EXISTING `test_layer0_*` cap drift, unrelated —
  concurrent universe-cap builder; confirmed by stash-and-rerun).
- mypy --strict + ruff clean. fresh-Claude adversarial review APPROVE_WITH_NITS
  (0 blocker; NIG math verified, raw→net fix applied, keyword sweep 0).

## /debate deferred (lens weight retune)
`_W_*` weights are first-pass; `entrance.py:88` already flags "/debate calibration target".
Retuning the 5-lens fusion weights with live opportunity_score outcome data = /debate.
Out of scope here: D4 (news→AltDataView field), D5 (conviction-stacking).
