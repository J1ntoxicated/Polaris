---
type: digest
status: active
phase: P0
day: 4
date_created: 2026-05-07
tags: [digest, p0, day-4, 4-axis-review, codex, layer-5, layer-6, strategies]
related: [[layer-5-learner-network]], [[layer-6-live-recalc]], [[ADR-007-learner-network|ADR-007]], [[ADR-008-7-strategies-signal-generator-role|ADR-008]], [[2026-05-07_p0_day4]]
reviewed_by: codex (gpt-5.4) R1-R6
---

# P0 Day 4 — 4-axis Policy Review (Jin mandate)

## Outcome
**Codex R6 APPROVE on all 4 axes** after 6 rounds of REJECT_WITH_FIXES.

| Axis | R6 verdict |
|---|---|
| 1 — Plan/ADR/Phase 0 spec 정합 | **PASS** |
| 2 — Dead code 0건 | **PASS** |
| 3 — Hardcode 0건 | **PASS** |
| 4 — AI 적절 사용 (Day 4 = Python only) | **PASS** |

Final test count: **337 passed** / mypy --strict clean (66 Day 4 source files) / ruff clean / smoke_day4 OK.

## Findings → Fixes (6 rounds)

### R1 — 8 findings
- **A1.f1** in-DB-only snapshot lacked spec §Q3 SQLite hot backup + JSON manifest pair.
- **A1.f2** `position_strategy_segments` missing `regime_at_start` + `attribution_weight`.
- **A1.f3** swap apply path inserted segment without those fields.
- **A2.f1** `count_layers()` exported but unused.
- **A3.f1-f4** strategy magic numbers (Volume Burst / TSMOM / Donchian / FX / XAU / Session) inline in `generate_raw_signal`.

### R2 — 4 findings
- **A1.f1** regime SSOT lookup used `(venue, symbol)` instead of `(venue, underlying_group_id)`.
- **A1.f2** `positions` row had no `underlying_group_id`; entry segment never seeded.
- **A2.f1** `restore_snapshot_from_disk()` exported but untested.
- **A2.f2** `DEFAULT_ENTRY_ATTRIBUTION` declared but unused.

### R3 — 1 finding
- **A1+A2** `active_strategy_id` read from positions but never checked against `candidate.from_strategy_id` → Layer 6 §Q3 invariant unenforced.

### R4 — 1 finding
- **A1** legacy DBs missing `positions.entry_strategy_id` + `active_strategy_id`; ALTER migration absent.

### R5 — 1 finding
- **A1** backfill only ran when columns were newly added; partially migrated DBs with empty defaults stayed unrepaired.

### R6 — APPROVE
- All previous fixes verified.

## Files touched

```
polaris/core/learners/base.py             # +SQLite hot backup + JSON manifest + restore_snapshot_from_disk + Mapping import
polaris/core/learners/max_hold.py         # post-commit disk snapshot flush
polaris/core/learners/__init__.py         # re-export LEARNER_SNAPSHOT_DIR + restore_snapshot_from_disk
polaris/core/live_recalc/strategy_swap.py # active_strategy_id invariant guard / regime SSOT key / entry seg seed / DEFAULT_ENTRY/ACTIVE_ATTRIBUTION
polaris/storage/schema.py                 # position_strategy_segments + positions ALTER migrations + backfill
polaris/strategies/{volume_burst,tsmom,rsi_bb_pullback,spot_donchian,fx_breakout_basket,xau_indices_trend,session_breakout}.py
                                          # named constants for STRENGTH_*/TTL_BARS/LEVERAGE_MAX
polaris/scripts/smoke_day4.py             # +count_layers + can_stack_conviction + underlying_group_id seeding
tests/test_layer5_learners.py             # +test_restore_snapshot_from_disk
tests/test_layer6_live_recalc.py          # +5 new tests (count_layers / entry seg seed / active_mismatch / legacy & partial migration)
```

## Reject keyword sweep
0 hits across `12주 / 90d gate / regulatory / professional risk / monthly review / regrets / posture standard / fractional Kelly is too aggressive / real money / live capital / production safety` on Day 4 artifacts.

## Spec hooks closed
- `vault/30_components/layer-5-learner-network.md §Q3` SQLite hot backup + JSON manifest + manual restore: **implemented**.
- `vault/30_components/layer-6-live-recalc.md §Q2` regime SSOT `(venue × underlying_group_id)`: **enforced** in swap segment regime stamp.
- `vault/30_components/layer-6-live-recalc.md §Q3` invariants:
  - `entry_strategy_id` immutable + `active_strategy_id` mutable — **schema columns + migration**.
  - max 1 swap/trade — **enforced**.
  - same correlation_group + same venue/symbol/side — **enforced**.
  - `active_strategy_id == candidate.from_strategy_id` invariant — **enforced** (R3 fix).
  - segments record `regime_at_start` + `attribution_weight` (entry 0.40 / active 0.60) — **enforced**.

## Aggressive bias preservation
All fixes were spec-mandated correctness gaps — no defensive throttling, no auto-disable, no hard blocks. The new `skipped_active_mismatch` decision is an integrity guard (prevents corrupted attribution), not a trade block. P0 stub stays log-only by default.

## Iteration count
6 rounds R1-R6 (1 APPROVE + 5 REJECT_WITH_FIXES → 5 fix passes). All fixes applied by current Claude session; no escalation to Jin needed.

## Sources
- `/tmp/polaris_p0_day4_review/r{1..6}_{prompt,response}.{md,txt}`
- ADR-007 / ADR-008 / Layer 5 / Layer 6 spec docs.
