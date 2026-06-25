# STEP① Static-Ground Expansion — full-universe bars + per-ticker sentiment/event ground

2026-06-25 · DEMO/PAPER · OBSERVATION coverage only (flow_not_block, gates nothing) · builder≠reviewer APPROVE-WITH-NITS, 0 blocker

## Problem (Jin "맨날 들어오는 애들만")
Hot path (`_run_tick → ingest_bars_per_timeframe`) bar-ingests only the FOCUS subset
(`FOCUS_CYCLE_TARGET`=120, tier-gated) — the binding 5s REST+DB cost. So only
**276 / 1882 active** instruments ever got bars/ground; the candidate sweep (②) could only
see those same few. The TOEHOLD for Jin's pipeline (①정적그라운드→②스윕→③와칭→④검증→게이트→체결).

## What shipped (file:line)
- NEW `polaris/scripts/_static_ground.py`:
  - `ingest_static_ground_bars` — walks `read_active_universe` (WHOLE active set, not
    focus), fetches Yahoo multi-resolution bars (`1D/1H/15m`; NOT 1m — heavy 7d window, hot
    path owns focus 1m) via existing `fetch_bars_one` (Yahoo PRIMARY). Semaphore(16) +
    per-cycle `asyncio.wait_for` total-timeout (Yahoo IP-block guard, degrade-never-halt).
    Reuses existing `_YF_FRAME_CACHE` → hot-path overlap = cache hit. Per-symbol SAVEPOINT guard.
  - `refresh_ticker_ground` — runs EXISTING `fuse_evidence` per active group → per-ticker
    `ticker_ground` row (no new source/pipeline; fuser applied to the whole universe). Single
    `BEGIN/COMMIT` over the ~1882-row walk (event-loop-stall fix). Graceful-empty when no source
    covers a ticker. `cache=None` → no-op.
  - `read_ticker_ground` — the ②후보스윕 input accessor (per instrument_id, None if absent).
- NEW table `ticker_ground` (PK=instrument_id LWW, bounded by universe size) — DDL in
  `schema_ddl_altdata.py`, wired into `schema.ALL_DDL`. Additive/idempotent.
- `production_paper_loop.py` — `_static_ground_producer` background task (mirrors
  `_layer0_producer`/`_altdata_producer`): off the tick deadline, spawned after adapters +
  altdata cache, torn down in `finally`. Cadence `STATIC_GROUND_REFRESH_SEC`=900s (first walk =
  one-time fill, then incremental via frame cache). gpt gated by `ai_free_mode()`. 5 counters + summary.

## Live before/after (DB copy, real Yahoo network)
- BARS: **276 → 1650 active-with-bars** (1882 active). Full 1D fill = 1588 instruments /
  353,369 bars / **141s**, no timeout, 0 unguarded errors. 1D by venue (active): alpaca
  1308/1500, capital 72/163, okx 219/219. Uncovered tail = genuinely delisted/unmappable Yahoo
  symbols (`.WS` warrants, exotic FX) → graceful fallback/skip.
- GROUND: **1882/1882 active tickers** materialized from the fuser (crypto→crypto_fg,
  fx/equity→macro vix/hy_spread); 0.062s wrapped (vs ~86ms unwrapped — BEGIN-wrap win).

## Live-probe-surfaced bug (caught BEFORE commit)
Real Yahoo NaN close → SQLite binds NaN as NULL → `IntegrityError: NOT NULL bars.close` in
`persist_bars`, unguarded in the first cut → aborted the whole gather + poisoned the shared
txn. Fixed: per-symbol `SAVEPOINT ground_persist` → `ROLLBACK TO`/`RELEASE` (bad batch skipped,
walk survives). TDD regression `test_fill_persist_error_does_not_abort_walk` added.

## Adversarial review (fresh Claude, builder≠reviewer) → APPROVE-WITH-NITS, 0 blocker
Highest-risk concern (shared-conn txn corruption) verified SAFE: every explicit txn block in
the tree is await-free, so under autocommit two txns can never be open at once; the new
SAVEPOINT/BEGIN blocks take no `await` inside. MAJOR (per-row autocommit stall) FIXED via the
single BEGIN-wrap. NITs noted; bars per-symbol commit kept (yields between symbols, 900s cadence).

## Guardrail sweep (all PASS)
- flow_not_block: gates NO entry/size/exit. Semaphore/timeout/frame-cache = fetch-efficiency only.
- 9-stack: ZERO sizing touched. Hot path UNTOUCHED (separate task + cadence).
- runtime-LLM=OpenAI (yahoo resolve gated by ai_free_mode); Anthropic absent. Rejection keywords: 0.
- TDD 11 tests · mypy --strict + ruff clean · 3236 pass (2 pre-existing stale-cap fails + 4
  worktree-path debate-harness errors, all unrelated; verified on base).

## Next (TOEHOLD only)
②후보스윕 reads `read_ticker_ground` + the now-covered bars → today's candidates · ③무브 와칭 ·
④검증→게이트→체결. STATIC_GROUND_RESOLUTIONS / PARALLEL / REFRESH = /debate calibration targets.
