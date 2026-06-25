# Startup bottleneck fix — `populate_capital_proxies` sequential → parallel

**Date** 2026-06-25 · **Type** digest (perf / startup incident root-cause)
**Component** [[layer-0-universe-discovery]] · Capital proxy fetch
**File** `polaris/venues/capital/market_proxy.py` · DEMO/PAPER · flow_not_block

## Incident root cause
Two bot restarts (2026-06-24) were misread as "hung". Real cause: L0 startup
blocked in `populate_capital_proxies`, which fetched the 24h chart for **~1658
Capital epics one-at-a-time** (`for ins in instruments`). Each call has
`REST_TIMEOUT_SEC=15s`; when Capital is slow the sequential walk stretches to
tens of minutes before the tick engine can trade (zero fills during the block).
Worse-case grows linearly with API latency — a latent time-bomb.

## Fix (latency-only, breadth untouched)
Sequential loop → **bounded-concurrency parallel**:
- `asyncio.Semaphore(CAPITAL_PROXY_MAX_CONCURRENCY=12)` + `asyncio.gather` over
  one shared `httpx.AsyncClient` (connection pool reuse).
- Concurrency 12 respects Capital's ~10 req/s per-account REST ceiling
  (concurrency ≠ req/s — each call carries real round-trip latency); higher
  risks a 429 ban. 429 → caught as `httpx.HTTPError` per-row (no retry storm).
- **Total-timeout backstop** `CAPITAL_PROXY_TOTAL_TIMEOUT_SEC=300.0` via
  `asyncio.wait_for`: on timeout, pending tasks cancelled, processed rows keep
  proxy values, unprocessed keep default zeros — **startup proceeds**.
- Per-instrument best-effort preserved (failure → original zeros → 4-axis
  rejects cleanly). Input order + identity preserved (index-aligned output).

## Wall-clock before/after (estimate)
- Sequential, slow API worst case: 1658 × 15s ≈ **7 h** (typical ~0.5s/req ≈ 14 min).
- Parallel conc=12: worst case ≈ (1658/12) × 15s ≈ **35 min**, hard-capped at
  **300s** by the total-timeout (then degrade-never-halt). Typical ≈ **~70s**.

## flow_not_block compliance
All 1658 epics still enqueued + evaluated — only wall-clock bounded. No breadth
narrowing, no entry block, no live-price / sizing / WS touch. Pure startup fix.

## Verification
TDD (7 new tests in `tests/test_capital_proxy.py`): parallel==sequential result,
semaphore bound, per-row failure keeps zeros, total-timeout degrades without
halt, bounds sanity. Full suite **2689 passed**. `mypy --strict` + `ruff` clean.
Fresh adversarial review (builder≠reviewer) → **APPROVE**, 0 blockers,
rejection-keyword sweep **0 hits**.
