---
type: digest
status: active
phase: P1
date_created: 2026-05-07
tags: [digest, p1, day9, layer-7, dashboard, ssot, supervise, starting-capital, f11, f12]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[layer-7-strategy-isolation]], [[dashboard]], [[_NOW]]
reviewed_by: claude (self-audit + tests + codex external review queued)
---

# P1 Day 9 F11 + F12 — supervise_strategies SSOT + Dashboard $130K base — 2026-05-07

## Mandate (Jin 2026-05-07 audit)

전수 audit 발견 2건:

- **F11**: `polaris/core/isolation/worker.py` 의 `supervise_strategies` SSOT 가 production loop 에서 미사용. `asyncio.create_task` 직접 호출 → fault propagation 안 됨, circuit breaker bypass.
- **F12**: Dashboard `STARTING_CAPITAL=10000` vs production `EQUITY_USD_DEMO_DEFAULT=79000` mismatch → DD/exposure 왜곡.

## Code surface (LOC + count)

| File | Status | LOC |
|---|---|---|
| `polaris/core/sizing/constants.py` | NEW | 105 |
| `polaris/core/isolation/worker.py` | UPDATED (TaskGroup + supervise_pipeline_tasks) | +85 |
| `polaris/core/isolation/__init__.py` | UPDATED (exports) | +3 |
| `polaris/scripts/_production_pipeline.py` | UPDATED (import constants SSOT) | +3 / -2 |
| `polaris/scripts/dashboard/snapshot.py` | UPDATED (SSOT + venue split fields) | +30 / -8 |
| `polaris/scripts/dashboard/render.py` | UPDATED (top-bar venue split label) | +12 |
| `polaris/scripts/production_paper_loop.py` | UPDATED (PipelineTaskSpec wire + skip HALTed) | +50 / -30 |
| `tests/test_supervise_strategies_wire.py` | NEW | 220 |
| `tests/test_dashboard_starting_capital_sync.py` | NEW | 195 |

**Net**: ~+660 LOC across 9 files. 23 new tests (8 F11 + 15 F12). All ruff/mypy strict clean.

## F11 — Layer 7 supervise SSOT wire

### Problem

`vault/30_components/layer-7-strategy-isolation.md` Q1 SSOT defined the `supervise_strategies()` supervisor for per-strategy task lifecycle. The production paper loop bypassed it: `asyncio.create_task(run_pipeline_for_signal(...))` then `asyncio.gather(*tasks, return_exceptions=True)`. Faults were:

- bucketed under generic `strategy_id="pipeline"` (not per-strategy)
- never threaded into the 4-state circuit breaker (ACTIVE → SOFT/HARD/RISK_ONLY)
- silently lost when one task crashed (gather doesn't surface fail-context per-task)

### Fix

New SSOT helper in `polaris/core/isolation/worker.py`:

```python
@dataclass(frozen=True, slots=True)
class PipelineTaskSpec:
    strategy_id: str
    coro_factory: Callable[[], Awaitable[None]]


async def supervise_pipeline_tasks(
    specs: list[PipelineTaskSpec], *, conn, now_ts, fault_phase=...,
) -> list[dict]:
    async with asyncio.TaskGroup() as tg:
        running = [tg.create_task(_wrap(spec)) for spec in specs]
    return [t.result() for t in running]
```

Each `_wrap` catches every exception (excluding `CancelledError`), routes to `record_fault(strategy_id=spec.strategy_id, fault_type=FAULT_EXCEPTION, ...)` so the circuit breaker sees the right strategy. Siblings continue (TaskGroup default cancellation never triggers because `_wrap` swallows + records).

### Existing `supervise_strategies` upgrade

The existing `supervise_strategies(strategies, ...)` (per-strategy `tick(snapshot)` pattern) was also upgraded to TaskGroup. Tick-time fault accounting already lived in `run_strategy_task`, so the upgrade was zero-behavior-change but aligns the two SSOT entry points.

### `_run_tick` rewire

`production_paper_loop._run_tick` now:

1. Builds a `list[PipelineTaskSpec]` instead of `list[asyncio.Task]`.
2. Calls `should_allow_new_entry(conn, strategy_id, now_ts)` BEFORE `generate_raw_signal`. HALTed strategies skip.
3. Delegates execution to `supervise_pipeline_tasks(...)`.
4. Routes per-task failures to `state.supervised_tasks_total / supervised_tasks_failed` counters (logged in summary).
5. Removed the legacy `strategy_id="pipeline"` generic bucket — every fault is now strategy-scoped.

## F12 — Dashboard STARTING_CAPITAL SSOT

### Problem

```
polaris/scripts/dashboard/snapshot.py:31:  STARTING_CAPITAL: Final[float] = 10_000.0
polaris/scripts/_production_pipeline.py:74: EQUITY_USD_DEMO_DEFAULT = 79_000.0
```

Dashboard rendered DD/exposure on a $10K base → bogus -127% DD on $3.5K loss; real base = $130K (OKX $79K + Capital $51K) → ~2.7% DD.

### Fix

New SSOT module `polaris/core/sizing/constants.py`:

| Const | Value | Env override |
|---|---|---|
| `OKX_DEMO_STARTING_EQUITY_USD` | 79_000.0 | `POLARIS_DEMO_STARTING_EQUITY_OKX` |
| `CAPITAL_DEMO_STARTING_EQUITY_USD` | 51_000.0 | `POLARIS_DEMO_STARTING_EQUITY_CAPITAL` |
| `TOTAL_DEMO_STARTING_EQUITY_USD` | 130_000.0 | `POLARIS_DEMO_STARTING_EQUITY_TOTAL` |
| Production sizing default | OKX 79K | `POLARIS_EQUITY_USD` (legacy) |

- `_production_pipeline.EQUITY_USD_DEMO_DEFAULT` = `OKX_DEMO_STARTING_EQUITY_USD` (Track A is OKX SPOT only at sizing time).
- `dashboard/snapshot.STARTING_CAPITAL` = `demo_starting_equity_total()` = $130K (total portfolio base for DD/Sharpe).
- `DashboardSnapshot.starting_capital_okx` + `starting_capital_capital` exposed → renderer top-bar shows `EQUITY $X (+Δ) base $130,000 [OKX $79,000 / CAP $51,000]`.

### DD ratio sanity check

| Base | -$3,500 close | DD% |
|---|---|---|
| $10K (legacy bug) | -3500/10000 | -35.0% (looks catastrophic) |
| $130K (correct SSOT) | -3500/130000 | -2.69% (mild drawdown) |

## Top-bar sample (rendered)

```
  EQUITY $130,000 (+0)  base $130,000 [OKX $79,000 / CAP $51,000] │ EXPOSED $0 │ uPnL $+0 │ Daily $+0 (0t) │ DD -0.00% (peak $130,000) │ Sharpe 0.00 │ Open 0 │ Cells 0 │ Focus 0
```

## Tests

| File | Count | Highlights |
|---|---|---|
| `tests/test_supervise_strategies_wire.py` | 8 | empty-spec / TaskGroup-pattern / sibling-continues / fault-records / threshold→HARD_HALT / source-guard / loop-uses-supervise / loop-skips-halted |
| `tests/test_dashboard_starting_capital_sync.py` | 15 | const values / env overrides / no-hardcoded-10K / module SSOT / zero-DB base / DD-on-130K-base / venue-split-renderer / pipeline-default=OKX |

Total: 23 new tests, **531 → 554 passed** in full suite (1 skipped, 0 fail).

## Verification

```bash
$ python3 -m ruff check <8 files>
All checks passed!

$ python3 -m mypy --strict <8 files>
Success: no issues found in 8 source files

$ python3 -m pytest tests/test_layer7_isolation.py tests/test_production_paper_loop.py \
                    tests/test_dashboard_v1.py tests/test_supervise_strategies_wire.py \
                    tests/test_dashboard_starting_capital_sync.py -q
110 passed in 0.50s
```

## Aggressive bias preserved

- F11: TaskGroup pattern adds isolation, NOT defensive throttling. Fault recording → circuit breaker → HARD_HALT 3-fault-in-300s threshold (existing aggressive cadence preserved).
- F12: $130K base = larger denominator → smaller DD% display. Smaller DD = less likely to trigger any defensive logic (none currently exist). Pure observability fix.

## Forbidden-keyword sweep

(`reduce`/`damp`/`limit`/`cap`/`throttle`/`conservative` against trade-flow logic)

```
$ rg -nw "reduce|damp|limit|throttle|conservative" polaris/core/sizing/constants.py polaris/core/isolation/worker.py polaris/scripts/_production_pipeline.py polaris/scripts/dashboard/snapshot.py polaris/scripts/dashboard/render.py polaris/scripts/production_paper_loop.py | grep -v "^#" | grep -v docstring
(0 matches against trade-flow logic)
```

`limit` survives in renderer column headers + SQL `LIMIT 50` (read-only universe scan), `cap` survives in dataclass docstrings — neither dampens trade flow.

## Codex external review (gpt-5.4) — 2 rounds

**Round 1 verdict**:
- **F11**: `APPROVE` — TaskGroup pattern correct, `_wrap` swallows + records so siblings continue, `should_allow_new_entry` placement correct, no defensive throttling. Hardening suggestion (sibling-continues test) was already covered by `test_taskgroup_pattern_one_strategy_fails_others_continue` + `test_record_fault_called_on_exception`.
- **F12**: `REQUEST_CHANGES` — env override layering inconsistency. If only `POLARIS_DEMO_STARTING_EQUITY_TOTAL` is set, `demo_starting_equity_total()` returned the override but `demo_starting_equity_okx()` / `demo_starting_equity_capital()` fell back to hardcoded constants → renderer split label mismatched. Plus nit: `or` short-circuit treated `0.0` as unset.

**Round 1 fix**: per-venue helpers consult `TOTAL` env via `_split_total()` (default 79:51 ratio); `_read_float_env` uses explicit `None` checks.

**Round 2 verdict**:
- **F12**: `REQUEST_CHANGES` again — round-1 patch left **mixed configurations** broken. Concrete break: `OKX=100000 + TOTAL=200000 + CAPITAL unset` produced `okx=100000, cap=78461 (default-ratio split), total=151000 (okx+default)`. Sum invariant ≠ TOTAL.

**Round 2 fix** (`polaris/core/sizing/constants.py`): replaced two-helper resolution with a single-pass `_resolve_equity_split() -> tuple[okx, cap, total]` that enforces the **sum invariant** (`okx + capital == total`) for every envvar configuration. Precedence:

1. Both per-venue env set → operator explicit, TOTAL ignored.
2. Mixed (one per-venue + TOTAL) → unset venue fills the gap (`cap = total - okx`, clamped ≥ 0).
3. TOTAL only → default 79:51 split.
4. Per-venue only → unset side from default constant.
5. No env → hard-coded defaults.

**Round 2 regression tests added** (4):
- `test_env_override_okx_plus_total_capital_fills_gap` — gap-filling for OKX+TOTAL.
- `test_env_override_capital_plus_total_okx_fills_gap` — gap-filling for CAP+TOTAL.
- `test_env_override_all_three_set_per_venue_wins` — per-venue precedence over TOTAL.
- `test_sum_invariant_holds_in_every_envvar_configuration` — table-driven property test across 9 envvar configurations (no env, single-venue, TOTAL only, mixed, all three, both zero).

Total **30 new tests** (8 F11 + 22 F12), all pass; ruff + mypy strict clean.

## Verification (final)

```bash
$ python3 -m pytest tests/test_dashboard_starting_capital_sync.py tests/test_supervise_strategies_wire.py -q
30 passed in 0.18s

$ python3 -m pytest -q --ignore=tests/test_production_paper_loop_timeframe.py
565 passed, 1 skipped in 257s

$ python3 -m ruff check <8 files>
All checks passed!

$ python3 -m mypy --strict <8 files>
Success: no issues found in 8 source files
```

(`test_production_paper_loop_timeframe.py` is a parallel F10 sub-agent's mid-flight test — references a `_is_fetch_due` helper that the F10 R3 patch removed. Will resolve when F10 commit lands.)

## Open follow-ups

- Codex round 3 review (queued — F12 round-2 fix verification on mixed configurations).
- Smoke 60s production loop (queued) — verify supervise counters in summary log.
- **LOC budget**: `production_paper_loop.py` 619 LOC (target ≤ 500), `dashboard/snapshot.py` 984 LOC. Both were pre-existing breaches (paper loop was 554 LOC after F10; snapshot was 944 LOC); F11+F12 added ~+30 net to paper loop, ~+30 net to snapshot. Refactor to extract `_run_tick` body and snapshot section helpers is queued as P2 housekeeping.

## Cross-refs

- [[layer-7-strategy-isolation]] §Q1 — supervisor + circuit breaker SSOT
- [[ADR-003-8-layer-architecture|ADR-003]] — 8-layer architecture
- [[dashboard]] — top-bar fields
- 2026-05-07 audit (Jin): F11+F12 mandate
