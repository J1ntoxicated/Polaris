---
title: Writer-migration completion — residual lock resolution
date: 2026-07-09
tags: [db-writer, lock-contention, busy_timeout, live_recalc]
context: DEMO/PAPER only · aggressive bias preserved · flow_not_block
decision: (a)busy_timeout arbiter + instrument-first + recalc micro-batch; reject loop/CF async migration
---

## Verdict
**(a) busy_timeout alignment as the structural arbiter, gated on db_writer
batch-hold instrumentation, + recalc lock-count coalescing.**
Reject loop-tick(:1229) AND counterfactual(:297) async migration.

## Why (evidence)
- Lock rate 20/40min sparse, retried, zero functional harm (fills/equity OK,
  WAL 4.4M bound). No emergency → we have time to do it evidence-first.
- All 3 victims write on the loop `conn` (or `connect()` PRAGMA path) → one env
  knob `POLARIS_DB_BUSY_TIMEOUT_MS` raises the SQLite-native busy_handler wait
  for all three at once. Standard contention arbiter, NOT a hotpatch, NO
  semantic change: every victim stays **synchronous** → read-your-writes intact.
- ② batch-hold time is **unmeasured** (only counters, no timing). Blind-cranking
  the timeout = guessing (violates root_cause_evidence_based). 5000ms is already
  huge vs a ≤64-job batch (~ms). If locks persist at 5000ms the collision may be
  immediate `SQLITE_BUSY_SNAPSHOT` (timeout can't fix) — must be measured, not
  assumed. Instrument first, then size the value.

## Rejected (explicit)
- **loop:1229 async** — tick reads back its own persisted rows for downstream
  gates same-tick; queue handoff breaks same-tick visibility. Semantic risk +
  not needed. FORBIDDEN per mandate.
- **counterfactual:297 async** — `BEGIN IMMEDIATE` is already busy_timeout-
  arbitrated AND self-healing (resolve_attempts unincremented on rollback → next
  60s pass retries). Migration = surface for zero gain.
- **blind timeout crank to tens of s** — synchronous loop tick inherits worst
  stall = timeout; value must be measured-p99×margin bounded.
- **BATCH_MAX↓ now** — premature pre-measurement.

## Per-file change spec (env-knob, no hardcode)
1. `polaris/storage/db_writer.py` `_commit_batch` — wrap BEGIN→COMMIT in a
   monotonic timer; post-COMMIT `logger.debug("[db_writer] batch %d jobs %.1fms")`
   + rolling `batch_commit_ms_max` on instance. Pure observability, fills ②. No
   behavior/counter change.
2. `polaris/storage/schema.py` — **no code change**. `POLARIS_DB_BUSY_TIMEOUT_MS`
   (default 5000, line 320) already feeds `connect()`+`connect_ro()` → covers
   loop/focus/db_writer conns uniformly. Operational lever: after ≥1 measured
   window set = ceil(p99_hold_ms × 3, floor 5000). Dashboard `mode=ro` readers
   (server.py, no PRAGMA) = untouched (readers don't contend WAL write lock).
3. `polaris/core/live_recalc/tick_recalc.py` — add `mark_positions_dirty(conn,
   entries)` = single multi-row `INSERT…VALUES(…),(…) ON CONFLICT DO UPDATE`
   (last-reason-wins preserved). Keep single-mark for event callers.
4. `polaris/scripts/_production_recalc.py` (~888-903) — hoist the per-position
   `mark_position_dirty` OUT of the `await _evaluate_position` loop: collect all
   `position_id` first, one `mark_positions_dirty` flush BEFORE evaluation.
   Collapses ≤50 lock-acquisitions/cycle → 1. Synchronous → dirty visible to the
   async recalc sweep exactly as before; no await inside the txn (shared-conn safe).

## Test requirements
- db_writer: caplog@DEBUG asserts duration line + correct job count; regression
  assert `batches_committed`++ & futures resolve post-COMMIT.
- busy_timeout: `connect()`/`connect_ro()` read-back honors env override.
- recalc micro-batch: (i) equivalence — N batched marks == N individual
  (dirty_reason/ts, dup position_id→most-recent wins); (ii) read-your-writes —
  same-conn SELECT sees all marks after flush; (iii) single-txn assertion;
  (iv) hypothesis idempotency on repeated flush.
- **adversarial (key)**: db_writer batches + 1 direct writer under contention →
  direct writer must SUCCEED (retry within busy_timeout), not raise
  OperationalError. Negative result = if it reproduces immediate BUSY_SNAPSHOT,
  timeout is insufficient → escalate to broader coalescing. Evidence-driven.
