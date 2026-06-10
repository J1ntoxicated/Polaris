---
type: digest
status: active
phase: P1
day: ignition
date_created: 2026-05-07
tags: [digest, p1, logging, observability, paper-loop, ignite_p1]
related: [[layer-2-per-gate-pipeline]], [[layer-3-sizing-risk]], [[layer-4-cell-matrix]], [[layer-5-learner-network]], [[ADR-007-learner-network|ADR-007]]
---

# P1 verbose logging patch — every core decision is now observable

## Why

Jin mandate 2026-05-07: "로그에 안 찍히는건 없는거니까 다 찍히게 하고."
24h paper-loop ignition surfaced **silent core layers**: gate orchestrator,
sizing engine, learner base, cell matrix, isolation, live-recalc, and the
venue adapters all ran without emitting structured logs. Tracing a kill /
fail-open / sizing decision required SQL spelunking after the fact — too slow
for the 24h watchdog.

## What landed

### `polaris/logging_config.py` (new, 88 LOC)

- `setup_polaris_logging(level, log_file)` — single entry point used by
  `ignite_p1`. Format: `<ISO-UTC ts>.<ms>Z [LEVEL] <module>:<line> <msg>`.
- UTC `Formatter.converter` so logs sort deterministically across hosts.
- httpx / httpcore / asyncio noise pinned to WARNING; Polaris core stays at
  DEBUG / INFO so the operator-facing layer is uncluttered.
- Public constants: `DEFAULT_FORMAT`, `DEFAULT_DATEFMT`, `DEFAULT_LOG_FILE`.

### Core layer logger calls (every layer, ≥ 1 call site each)

- **Layer 0** `polaris/core/universe/discovery.py`, `watchlist.py` — OKX
  ticker fetch count, Capital nav fetch result, 4-axis filter survival
  (rejected reasons aggregated), dynamic_focus bucket distribution.
- **Layer 1** `polaris/core/data/baseline.py`, `ingest.py` — per-baseline
  recompute (DEBUG), per-batch ingest summary (INFO).
- **Layer 2** `polaris/core/pipeline/gate_orchestrator.py` — orchestrator
  start/end + **every gate transition** (`[G3 okx/BTC-USDT] decision=PASS
  model=haiku latency=0ms RAW→VALIDATED next=G4`). Lifecycle invariant
  violation now WARNING-level, not silent KILL.
- **Layer 3** `polaris/core/sizing/engine.py` — full T4 breakdown per
  signal: base × cont × tier × cell × listing → proposed → final +
  binding cap + Kelly + cold-start flag. Fill-rate cut emits a WARNING.
- **Layer 4** `polaris/core/cell_matrix/routing.py` — cell n_eff / score /
  pool size / quartile / mult per routing decision; trade-close updates.
- **Layer 5** `polaris/core/learners/base.py`, `scheduler.py` — per-trade
  learner update (n_eff before/after), hourly commit summary, triple-block
  emission (WARNING), rollback (WARNING), scheduler tune cycle bracketing.
- **Layer 6** `polaris/core/live_recalc/{tick_recalc,regime_flip,strategy_swap}.py`
  — recalc cycle decision counts, regime FLIP (immediate_crisis WARNING /
  confirmed_2x INFO), swap apply path.
- **Layer 7** `polaris/core/isolation/{worker,circuit_breaker,allocator_fence}.py`
  — strategy tick OK/FAULT, circuit fault → mode transition (WARNING),
  reset, fence reservation create.
- **Venues** `polaris/venues/okx/adapter.py`,
  `polaris/venues/capital/{adapter,session}.py` — order POST + RESP
  (clOrdId / dealRef logged, **never** API key / passphrase / CST /
  X-SECURITY-TOKEN), candle fetch count, capital login + 401 retry +
  anti-idle ping start.

### `polaris/scripts/ignite_p1.py`

- `--verbose` / `-v` flag (count: `-v`=INFO, `-vv`=DEBUG).
- `--log-file` argument (default `data/paper/polaris_runtime.log`).
- `setup_polaris_logging` invoked first thing in `main()` so the boot
  banner itself is captured.
- INFO logs at every bootstrap milestone: started_ts / db / paper /
  duration / tick / learner_interval / layer0 focus rows / scheduler
  ready / paper-loop entry + exit (with elapsed seconds).

### `polaris/scripts/smoke_paper_loop.py`

- All 28 `print()` calls migrated to `logger.info` / `logger.warning` /
  `logger.error`. Tick header, signal-emitted count, fill-persist
  failures, summary block — all routed through logging.

### `tests/test_logging_config.py` (new, 7 tests)

- Writes file ✓ / format is ISO-UTC ✓ / DEBUG/INFO/WARN/ERROR all
  propagate ✓ / INFO filters DEBUG ✓ / `force=True` swaps handlers
  cleanly ✓ / `log_file=None` keeps stdout-only ✓ / format constants
  exported ✓.
- Total suite: **415 pass** (408 prior + 7 new), zero regression.

## Verify

10 s after re-launch (PID 57257):

- 142 lines in `data/paper/polaris_runtime.log` ≫ 100 threshold.
- Layer coverage: ignite ×5, scheduler ×3, learner ×23, orchestrator ×21,
  G[0-9] gate transitions ×105, T4 sizing ×21, cell ×28, okx ×14.
- Layer 0 universe / 1 ingest / 6 recalc-flip-swap / 7 circuit-fence /
  Capital adapters log lines = 0 in this 10 s window because their code
  paths are not yet exercised by the smoke loop (Layer 0 long-cycle
  refresh runs in P1.x, smoke uses cached bars; Capital adapter needs
  `.env` creds; live-recalc fires on dirty marks). Instrumentation is
  in place — they will emit when those paths run.
- Rejected-keyword scan (12 weeks / 90d / regulatory / professional risk
  / monthly review / regrets / posture standard): **0 hits**.
- ruff clean / mypy strict clean.

## Re-launch

```
nohup python3 -u -m polaris.scripts.ignite_p1 \
  --paper --duration 86400 --tick 5 \
  --full-pipeline --real-roundtrip \
  --db data/polaris.sqlite -vv \
  --log-file data/paper/polaris_runtime.log \
  > data/paper/ignite_p1_24h.log 2>&1 &
```

PID 57257 captured to `data/paper/ignite_p1.pid`. Both
`polaris_runtime.log` (rich, all INFO/DEBUG/WARN/ERROR) and
`ignite_p1_24h.log` (stdout mirror) are growing in lock-step.

## Security invariants enforced

- API key / OKX passphrase / Capital CST / X-SECURITY-TOKEN are **never**
  passed to a logger call. Capital session login logs only `account_id`
  and the deadline ts. OKX adapter logs `clOrdId` and `ordId` only.
- Comments in `logging_config.py` and venue adapters call out the
  no-secret rule so future contributors don't accidentally regress it.

## Aggressive-bias preservation

This patch is observability-only. Zero defensive logic added: no extra
guards, no rate limits, no kill switches. Every log call is a `logger.X`
on the existing decision; the decision itself is unchanged.
