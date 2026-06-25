---
type: review
status: active
date_created: 2026-06-24
tags: [code-review, audit, demo-paper]
---

# Code review — 2026-06-24

Scope: whole-code read-only review, 3 parallel reviewers, DEMO/PAPER, no fixes.

## Findings

1. **P0 — EOD flatten bypasses canonical close ledger.**
   `polaris/scripts/reconcile_orphans.py:490`, `:526`, `:562` call venue closes
   directly for Alpaca/Capital, then `:505` marks only the in-memory trade closed.
   Missing: close fill, `positions.status/closed_ts/pnl_r`, `position_risk_state`
   delete, learner/cell outcome. Restart can rehydrate DB-open positions whose
   venue exposure was closed. Fix: route EOD through `close_specific_position`.

2. **P1 — Capital orphan guard keys by epic, not deal id.**
   `reconcile_orphans.py:79` returns tracked symbols; `:280-290` skips every venue
   position whose epic is tracked. Multiple same-epic deals are possible, so
   one tracked epic can hide another orphan deal. Fix: track open
   Capital `positions.deal_id` and compare venue `dealId`.

3. **P1 — Live payload leaves track/daily cap usage at zero.**
   `_read_portfolio_state()` hydrates `open_positions` but returns
   `venue_daily_used_pct=0`, `total_daily_used_pct=0`, `track_used_pct={track:0}`
   (`polaris/core/pipeline/_sizer_payload.py:131-136`). `compute_size()` trusts
   those fields at `polaris/core/sizing/engine.py:498-501`, making several
   cap slots inert. Add `build_sizer_payload(conn=...)` aggregation tests.

4. **P1 — Bar live path silently clamps post-T4 notional.**
   `polaris/scripts/_production_run_signal.py:307-309` rewrites G5
   `final_notional_usd` into `$10..$5,000`, while tick/replay use T4 output
   directly. This breaks live/replay/tick sizing parity and can cap winners or
   inflate residual headroom. Keep submitted notional equal to G5.

5. **P1 — T4 multiplier spec is internally inconsistent.**
   `compute_proposed()` multiplies base, continuous, tier, cell, listing, and L5
   product (`polaris/core/sizing/engine.py:218-248`). Current session mandate and
   vault L3/ADR disagree on whether listing/L5 belong in T4. Treat as decision
   conflict before fixing; do not silently normalize one side.

6. **P2 — Partial close accounting frees neither risk-state nor cumulative R.**
   `_persist_partial_close()` updates only `positions.qty` and in-memory qty
   (`polaris/scripts/_production_close_helpers.py:344-377`), leaving
   `position_risk_state` at original notional; final `pnl_r` uses final-price
   full-position R before only dollar PnL is sliced (`:145-154`).

7. **P2 — Tooling reproducibility gaps.**
   `pyproject.toml` configures async pytest mode but does not declare the async
   pytest plugin. Dashboard/replay wrappers call `python3` instead of the venv.
   `tools/ops/botctl.py:253` can accept fresh mtime even if the log did not grow.

## Verification
- `python3 -m pytest tests/test_position_risk_state_persist.py tests/test_pipeline_full_g4_g7.py -q`
  -> 27 passed.
- Sweep: pre-existing historical/catalog hits; this note avoids reproducing them.
