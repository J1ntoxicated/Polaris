---
type: research
status: recorded
date_created: 2026-06-26
tags: [research, backfilled-frontmatter]
---

# Dynamic-focus ground charge-rate tune — 2026-06-26

DEMO/PAPER. flow_not_block / aggressive preserved — this WIDENS observation only;
no entry/size/exit touched. Builder ≠ reviewer (fresh adversarial review = APPROVE).

## Live bottleneck (bot PID 65786, ae6ac6e)
Dynamic focus working (candidate sweep scans universe=1882 → picks 198/200), but the
data ground charged too slowly so the sweep selected on partial data:
1. **bars 435/1882** — `_static_ground_producer` bar walk at 16-wide reached only
   ~435 instruments before the 600s ceiling (~7.4s / Yahoo history fetch, 1D=2y).
   Sweep scored ActivationScore on partial bars (366/1650).
2. **ticker_ground=0** — `refresh_ticker_ground` ran only AFTER each ~600s bar walk,
   so EdgeScore stayed 0 during the first walk → sweep had no direction.
3. **yfinance DEBUG flood** — yfinance + peewee per-call DEBUG = 92% of the live log
   under -vv (disk + logging-lock pollution).

## Fix (single commit, branch `feat/symbol-sparkline` worktree → main)
- **#1 charge-rate** — `STATIC_GROUND_PARALLEL_DEFAULT` 16→32 (still Semaphore-bounded
  + within-period frame-cache + 600s total-timeout; ~260 req/min, under Yahoo
  throttle). Now **env-tunable live** via `POLARIS_STATIC_GROUND_PARALLEL` (wired at
  the producer call site → producer `parallel` → ingest). ~2× per-cycle reach.
- **#2 decouple ground** — split `_ticker_ground_producer` (new) off the bar walk.
  Materializes EdgeScore on its OWN 180s cadence (`TICKER_GROUND_REFRESH_SEC`),
  reads the live AltDataCache (not stored bars), so direction fills WHILE bars
  charge. Bar producer is now BARS-ONLY. Both share one conn safely: every txn
  window (bar SAVEPOINT..RELEASE, ground BEGIN..COMMIT) is await-free under the
  autocommit connection, so the single event loop can never interleave them
  (verified line-by-line in adversarial review).
- **#3 log flood** — added `yfinance`,`peewee` to the WARNING noisy-logger
  suppression in `logging_config.py` (mirrors the e48abd8 websockets-silence
  pattern). Polaris' own `polaris.*` DEBUG untouched.

## Charge-rate estimate (before → after)
- bars reach/cycle: ~435/1882 → ~870/1882 projected (2× width, same 600s ceiling).
- EdgeScore latency: 0 until first ~600s walk done → first refresh immediate,
  re-warm every 180s, independent of bars.
- log noise: 92% yfinance/peewee DEBUG → silenced to WARNING.

## Verify
- TDD: 4 new tests (log silence, ground-producer independence, bars-only split,
  parallel forwarding) RED→GREEN. 79 affected-module tests pass.
- `mypy --strict` clean, `ruff` clean. Rejection-keyword sweep = 0.
- 3 pre-existing failures (cell_routing/layer0 rank) confirmed failing at ae6ac6e
  baseline too — NOT introduced here.

## Non-blocking guard preserved
Both producers off the tick deadline (`asyncio.create_task`); `_run_tick` never
awaits them; teardown cancels + awaits both. Yahoo IP-block guard intact.
