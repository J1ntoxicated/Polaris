---
type: digest
status: complete
date_created: 2026-05-07
tags: [day9, f10, timeframe, capital, root-cause, paper-loop]
links: [[production_paper_loop]] [[Capital]] [[fx_breakout_basket]] [[xau_indices_trend]] [[session_breakout]] [[tsmom]] [[spot_donchian]] [[rsi_bb_pullback]] [[volume_burst]]
---

# P1 Day 9 — F10 Timeframe Hardcode Fix

## Root cause (pre-fix)

`polaris/scripts/production_paper_loop.py:241-244` hardcoded `timeframe="1m"`
when building every `MarketView`, regardless of `strategy.metadata.timeframe`.

Pre-F10 audit (PID 26451 2h13m): fills 9,601 (OKX 9,591 / Capital 10) =
**0.1% Capital fill share**. The Day 7/8 0% Capital symptom was not a Capital
adapter or auth failure — it was that every Capital strategy
(`fx_breakout_basket=1H`, `xau_indices_trend=1H`, `session_breakout=5m`)
received the wrong canvas (1m bars) and silently no-op'd through warmup +
Donchian/ADX gates because their lookbacks (`WINDOW + 5` 1H bars vs.
`WINDOW + 5` 1m bars = ~30min of microstructure) were structurally
mis-shaped for the trigger.

## Fix

1. **`_production_layers.py`**:
   - `fetch_bars_one(bar_interval=...)` forwards the interval to OKX `bar`
     query param + Capital `resolution` token (mapping
     `CAPITAL_RESOLUTION_BY_INTERVAL`).
   - `ingest_bars_for_focus(bar_interval=...)` propagates to every fetch.
   - Added `TIMEFRAME_FETCH_CADENCE_SEC = {1m: 5, 5m: 30, 15m: 60, 1H: 300}`
     so per-tf fetches honour their natural cadence (not every 5s tick).

2. **`production_paper_loop.py`**:
   - `_strategies_by_timeframe()` buckets the 7 strategies by
     `metadata.timeframe`.
   - Per-bucket fetch (delegated to `ingest_bars_per_timeframe`) +
     `read_recent_bars(bar_interval=tf)` + per-bucket
     `MarketView(timeframe=tf)`.
   - Regime computation kept on 1m bars (Layer 6 SSOT — must not oscillate
     with strategy timeframe). The 1m bucket is force-augmented to cover
     every focus venue (R1 P1-1 fix) so Capital symbols feed the regime
     SSOT even though no Capital strategy lives at 1m.
   - `ProdLoopState` adds `last_fetch_monotonic_by_tf` /
     `bars_persisted_by_tf` / `signals_by_tf` for forensic dashboards.
   - Cadence keyed per-(timeframe, venue) inside
     `ingest_bars_per_timeframe` so a failing venue retries next tick
     (R2 P1 fix). Aggregate per-tf key kept for introspection only.

## Strategy timeframe matrix (post-F10)

| Strategy | Venue | timeframe | Cadence |
|---|---|---|---|
| volume_burst | okx | 1m | 5s |
| tsmom | okx | 1H | 300s |
| rsi_bb_pullback | okx | 15m | 60s |
| spot_donchian | okx | 1H | 300s |
| fx_breakout_basket | capital | 1H | 300s |
| xau_indices_trend | capital | 1H | 300s |
| session_breakout | capital | 5m | 30s |

Note: ADR-008 §timeframe lists fx_breakout_basket and xau_indices_trend as
"4h" candidates for P2; canonical `BAR_INTERVALS = {1m, 5m, 15m, 1H}` does
not yet include 4h, so v1 freezes at 1H.

## Tests

- `tests/test_production_paper_loop_timeframe.py` (16 tests, all green):
  - metadata matrix lock
  - source-level `timeframe="1m"` hardcode lint
  - per-venue fetch_bars_one forwarding
  - Capital resolution mapping table
  - 1m + 1H ingest persistence
  - cadence gating (first-call + boundary)
  - `read_recent_bars` interval partitioning
  - FX breakout 1H canvas wiring (no shape error)
- Regression: 2 day-tests (test_day6 / test_day7 ignite paper) updated to
  spike volume on the final 1m bar so the now-correctly-isolated
  volume_burst strategy fires (previously they leaked into wrong-timeframe
  strategies that emitted off the same 1m bars).

## Verification

- **584 pytest pass, 1 skipped** (full suite) — includes 19 F10
  timeframe tests, F11 supervisor tests, and parallel-agent F1+F2 deltas.
- `mypy --strict polaris/` clean (105 files), ruff clean.
- Aggressive bias preserved — no defensive throttle, no filter added; the
  fix only routes bars to the correct strategies.

## Codex external review

**R1 = REJECT_WITH_FIXES** (3 P1):

1. **P1-1 — Capital regime starvation**: 1m bucket only included OKX
   strategies' venues, so Capital symbols never got fresh 1m bars and
   Layer 6 regime always fell through to "chop". Fix: force `1m` bucket
   to include every focus venue in `_run_tick`.
2. **P1-2 — baseline contamination**: `ingest_bars()` ran for every
   timeframe but baselines are minute-grained. Fix: 1m batches use
   `ingest_bars()` (persist + baseline), non-1m batches use `persist_bars()`
   only.
3. **P1-3 — cadence starvation gate**: cadence advanced unconditionally,
   so a transient zero-bar fetch deferred retry by the full cadence
   window. Fix: only advance `last_fetch_monotonic_by_tf[tf]` when
   `result["symbols"] > 0`.

**R2 = REJECT_WITH_FIXES** (1 P1):

- **R2 P1 — Partial-bucket starvation**: per-tf cadence advanced even when
  one venue in a mixed bucket failed (e.g. OKX 1H succeeded + Capital 1H
  returned zero → Capital deferred for 300s). Fix: cadence keyed per
  `(timeframe, venue)` with mirror to per-tf aggregate for introspection.
  A failing venue retries next tick, not after the full cadence window.
  6 new tests = 22 total green.

**R3 = APPROVE_WITH_NITS** (1 P2):

- **R3 P2 — Helper contract drift**: `_is_fetch_due` was reading the
  aggregate per-tf key while the live runtime gate moved to per-(tf,
  venue). Side-door risk if reused. Fix: removed `_is_fetch_due` + its 3
  unit tests; cadence is verified end-to-end via the runtime gate tests
  (`test_zero_bar_fetch_does_not_advance_cadence`,
  `test_partial_bucket_failure_does_not_starve_failing_venue`).

**Final**: APPROVE — 19 timeframe tests + 84 regression tests green;
mypy `--strict polaris/` clean (105 files); ruff clean. 3 codex review
rounds (R1 reject_with_fixes / R2 reject_with_fixes / R3 approve_with_nits
→ all addressed).

## Followups

- **F11 (parallel sub-agent)** wired Layer 7 supervisor + circuit breaker
  into the same fan-out (covered in F11 digest, not this one).
- **30s smoke verify**: pending Capital demo round-trip with 1H bars to
  confirm fill-rate lift; needs Capital session creds + production loop
  restart. Code-level verification confirmed by codex R3 + 19 unit tests
  exercising the partial-bucket / zero-bar / mixed-venue cadence cases.
- **4h interval**: requires `BAR_INTERVALS` extension + OKX/Capital
  resolution tokens; defer to P2 strategy spec rev.
- **Asset-class fallback (R2 P2 nit)**: 1m baseline path classifies all
  non-OKX bars as `"forex"`. With Capital indices/commodities now hitting
  this path (R1 P1-1 forced 1m bucket), they get tagged "forex" baselines.
  Acceptable per codex review; address only if baselines diverge per
  asset_class downstream.

## Aggressive bias check

- 0 defensive throttles introduced.
- 0 reject keywords (no filter / block / skip / cap added beyond
  pre-existing G8 split).
- The cadence gate is a fetch optimization, not a signal gate — strategies
  see every closed bar at their declared timeframe.

## Files changed

- `polaris/scripts/production_paper_loop.py` (+91 LOC: timeframe bucket
  helper + 1m augmentation + by-tf state; `_is_fetch_due` removed in R3)
- `polaris/scripts/_production_layers.py` (+136 LOC: per-(tf,venue)
  ingest helper + bar_interval routing + Capital resolution mapping +
  baseline-1m-only branch)
- `tests/test_production_paper_loop_timeframe.py` (+618 LOC, 19 tests)
- `tests/test_day6_codex_review_fixes.py` (volume-spike fixture fix)
- `tests/test_day7_ignition_health.py` (volume-spike fixture fix)
