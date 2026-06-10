---
type: runtime
status: active
date_created: 2026-05-07
tags: [digest, p0-sprint, cumulative-review, 8-layer-integration, codex]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[ADR-006-cell-matrix|ADR-006]], [[ADR-007-learner-network|ADR-007]], [[ADR-008-7-strategies-signal-generator-role|ADR-008]]
reviewed_by: codex (gpt-5.4 round 1)
---

# P0 Sprint Cumulative Coherence Review (8-layer integration)

## Verdict: **CONDITIONAL PASS — 4 P0 + 7 P1 cross-layer integration debt**

24h paper loop (PID 57257) gathers metrics from a **partial 8-layer assembly**:
fills + cell_matrix + 3 learners + gate_events all working, but Layer 0 ↔ Layer 2,
Layer 1 ingest, Layer 7 fence + supervisor + idempotent keys, and Layer 6 dirty
sweep are **dead code in the production path**. Tests prove every primitive
works in isolation (415/415); cumulative integration smoke (the running ignite)
reveals the cross-layer wiring gaps.

**Aggressive bias preserved** (0 defensive throttles introduced, 0 forbidden
keywords). The findings are about *missing wiring*, not regression of philosophy.

## Evidence — 24h paper loop DB snapshot (after 11 min)

| Table | Rows | Expected? |
|---|---|---|
| fills | 1,656 | yes |
| gate_events | 6,225 | yes |
| cell_matrix_p0 | **3** | no — only volume_burst/tsmom/spot_donchian × BTC-USDT × bull_trend |
| learner_state | 15 (only `:asia` and `:bull_trend` keys) | no |
| allocator_reservations | **0** | NO (Layer 7 fence not wired) |
| strategy_halts | **0** | NO (Layer 7 circuit breaker not wired) |
| strategy_fault_events | **0** | NO |
| positions | **0** | NO (Layer 1 + Layer 6 prereq) |
| orders | **0** | NO |
| signals | **0** | NO (signal ledger not persisted) |
| bars / quote_ticks | **0 / 0** | NO (Layer 1 ingest not driven) |
| position_live_recalc_state | **0** | NO (Layer 6 dirty mark unused) |
| position_risk_state | **0** | NO (means hard caps non-binding in prod) |
| regime_state | **0** | NO |
| rollback_candidates | **0** | known gap (drawdown checkpoint) |

## Findings (codex round 1, gpt-5.4, APPROVE)

### P0 blockers — fix before declaring P0 sprint complete

**A2 — Layer 7 AllocatorFence not wired** ([ADR-003 §mech-5][adr3], [layer-7 spec][l7])
- `polaris/core/isolation/allocator_fence.py::AllocatorFence` only used in `smoke_day2.py`.
- `smoke_paper_loop.py:399` jumps from G5 sizing → `simulate_open_fill()/persist_fill()` with no `check_and_reserve`.
- DB confirms: `allocator_reservations` = 0 rows after 11 min / 1,656 fills.
- **Fix**: wrap open-order seam — `build_order_key → register_order_intent → check_and_reserve → submit → confirm/release`.

**A3 — Per-strategy supervisor + circuit breaker not wired** ([ADR-003 §mech-1+4][adr3], [layer-7 spec][l7], [ADR-008 day-1 activation][adr8])
- `polaris/core/isolation/worker.py::supervise_strategies` and `circuit_breaker.py::record_fault` only called from tests.
- `smoke_paper_loop.py:545` uses raw `asyncio.create_task` + naked `try/except` (line 558-567), bypassing the spec-mandated supervisor + circuit breaker chain.
- DB confirms: `strategy_halts` = 0, `strategy_fault_events` = 0.
- **Fix**: replace manual fan-out with `supervise_strategies()`; gate every entry on `should_allow_new_entry()`.

**A5 — Layer 0 universe disconnected from Layer 2 dispatch** ([layer-0 spec][l0])
- `smoke_paper_loop.py:118` hardcodes `FOCUS = (BTC-USDT, ETH-USDT, EURUSD, GOLD)`.
- `ignite_p1.py:124` only counts persisted `universe` rows; never refreshes from venues, never injects into `run_smoke()`.
- 24h ignite logs `[ignite] layer0 universe focus rows=0` and runs anyway against the hardcoded 4.
- **Fix**: ignite_p1 refresh+select dynamic focus, then inject as parameter to `run_smoke(focus=…)`. Promote layer-0 `discover_universe` from skill-level to ignition-level.

**A6 — Layer 1 canonical persistence not driven by long-running loop** ([layer-1 spec][l1])
- `polaris/core/data/ingest.py` exists with full bar/tick ingest path.
- `smoke_paper_loop.py:580` fetches bars into memory only; never calls `persist_bars()` or `persist_ticks()`.
- `positions / orders / signals / bars / quote_ticks` all 0 rows after 11 min.
- **Cascading damage**: `_read_portfolio_state()` in [payload_builder.py:349][pb] returns zero usage → `headroom_min()` per-symbol/cluster/track caps are effectively non-binding. Hard cap composition is theoretically correct but practically vacant.
- **Fix**: per-tick `ingest_bars()` + persist position state on lifecycle transitions (SIZED → ACTIVE → CLOSED).

### P1 — should-fix this week

**A1 (PARTIAL) — `session_mult × regime_mult` not multiplied into T4** ([ADR-007][adr7])
- `engine.py:271 compute_size()` only multiplies `cell_routing_mult`. `resolve_final_size_mult()` defined in `learners/base.py:163` with no production caller.
- ADR-007 P0 lists session_mult/regime_mult as P0 priorities → live composition expected.
- Triple-block being non-live = ADR-007 P1 phase, expected (codex correction to my finding).
- **Fix**: extend G5 payload + T4 chain to multiply `session_mult × regime_mult × cell_routing_mult` at sizing time. Leave `ai_feedback` + triple-block behind P1 flag.

**A4 — Layer 6 stub primitives never invoked by paper loop** ([ADR-003 §L6][adr3], [layer-6 spec][l6])
- `mark_position_dirty / run_live_recalc_cycle / detect_regime_flip / evaluate_strategy_swap` only in `smoke_day4.py`.
- `position_live_recalc_state` 0 rows.
- **Fix**: persist positions, mark dirty on price/PnL boundaries, run 5s sweep task, seed/update `regime_state`.

**A7 — Regime + session axes hardcoded vacuous** ([ADR-006][adr6])
- `smoke_paper_loop.py:322 regime = "bull_trend"` and `:537 session = "asia"`.
- ADR-006 regime cardinality = 4. Production loop cannot exercise bear/chop/crisis cells.
- L5 learner_state has only `:asia` + `:bull_trend` keys → session_mult learner cannot learn `eu`/`us`.
- L4 top-quartile router never fires anyway (gate ≥20 cells, we have 3).
- **Fix**: derive regime from `regime_state` (codex consistency with A4); derive session from venue local clock.

**A8 (PARTIAL) — Effectively 3 strategies in production**
- `smoke_paper_loop.py:608 emitted[:3]` cap throttles every tick to top-3 emitted signals — even if 7 strategies fire, only 3 reach lifecycle.
- (My original finding incorrectly attributed this to RSI-BB filter / Capital stub bars; codex found the real cause is the per-tick cap.)
- **Fix**: remove the cap (or make it a configurable throughput limit applied AFTER allocator fence + isolation are wired, so back-pressure has a real path).

**Codex-added gaps not in my original list**

**X1 — Layer 5 `max_hold` learner unwired in production** ([ADR-007][adr7] P0)
- max_hold updated per close, but the paper loop never reads `get_mult()` to enforce holding-bars cap.
- Severity: P1.

**X2 — Layer 7 idempotent order keys (`register_order_intent` / `resolve_duplicate_intent`) not wired** ([ADR-003 §mech-6][adr3])
- No production caller. Becomes a real blocker the moment ignite_p1 starts submitting real orders rather than `simulate_open_fill`.
- Severity: P0/P1 boundary.

**X3 — G6 position_monitor uses local swap predicate, not Layer 6 SSOT** ([layer-6 spec][l6])
- `polaris/core/pipeline/agents/position_monitor.py:35 evaluate_strategy_swap` is a local function, not the Layer 6 module `polaris/core/live_recalc/strategy_swap.py::evaluate_strategy_swap`.
- Monitored positions bypass the Layer 6 ledger + invariants.
- Severity: P1.

### Refuted / downgraded by codex

**C1 — Kelly fraction not multiplied into T4 = NOT a spec deviation**
- ADR-005 §Priority: "hard MAX > Kelly" already describes Kelly as governing the single-trade cap, not as a sizing factor.
- Recommendation: **clarify ADR-005 text** to say "Kelly determines cap selection in P0; not a sizing chain factor". Doc-only.

### F-known-gaps cross-check (already documented)

- Drawdown checkpoint -8/-20/-35% snapshot trigger missing → `rollback_candidates` 0 rows confirmed.
- policy_engine matrix not wired (Haiku stub all-pass) → confirmed.
- Capital close-leg exception regression test parity → confirmed missing.
- Layer 0 long-running 5/10-min refresh → confirmed (already P1.x backlog).
- **Stale digest reference** in `2026-05-07_p0_sprint_complete.md:104`: test name was renamed `test_g8_p1_phase_forwards_haiku_client` → `test_g8_p1_phase_forwards_sonnet_model` (verify under read-only sandbox blocked; static evidence matches codex). **Recommend update the digest line.**

## Aggressive Bias Self-check (final, post-review)

- [x] No defensive throttle proposed in any of the 4 P0 fixes
- [x] No "12주 / 90d / monthly review / regulatory" keywords introduced
- [x] No demo-fund auto-stop, no daily KPI auto-disable
- [x] All proposed fixes preserve hard MAX `headroom_min()` 1-call contract
- [x] Cell mult clip-전 placement intact
- [x] Top quartile ×1.5 amplify intact
- [x] G8 P0/P1 model split intact

## Test Coverage Adequacy

- 415/415 pass, every primitive covered.
- Layer-by-layer dedicated test files exist (test_layer0..7_*.py).
- **Gap**: zero **end-to-end integration tests** asserting the wiring claims:
  - No test asserts `compute_size` multiplies session × regime × cell.
  - No test asserts `entry_sizer_gate` reserves through `AllocatorFence`.
  - No test asserts smoke loop drives `mark_position_dirty`.
  - This is exactly why per-day codex APPROVE missed the cumulative debt — codex reviewed Day N changes for self-consistency, not whether the overall stack assembled.
- **Recommendation**: add `tests/test_integration_p0_pipeline.py` that drives one tick of `smoke_paper_loop.run_smoke` against a memdb and asserts every spec invariant (fence reserved, cell mult applied, dirty marked, supervisor caught a fault).

## P0 sprint review cycle iteration count

- Codex round 1 (gpt-5.4): **APPROVE** (no REJECT — comprehensive findings, no further round needed).
- 11 spec-deviations confirmed (4 P0 + 7 P1).
- 1 finding refuted (C1 — Kelly).
- 3 codex-discovered gaps added (X1/X2/X3).

## Day 8+ Backlog (priority list)

### P0 (block P1.0 ignition success-claim, NOT block 24h paper loop)
1. **A2 — wire `AllocatorFence` between G5 SIZED and order submit** (`smoke_paper_loop.py`, `ignite_p1.py`)
2. **A3 — replace `asyncio.create_task` fan-out with `supervise_strategies()` + `record_fault` + `should_allow_new_entry()`** (smoke_paper_loop)
3. **A5 — `ignite_p1` dynamic focus refresh + inject into `run_smoke(focus=…)`** (Layer 0 → Layer 2)
4. **A6 — per-tick `ingest_bars()` + persist position lifecycle to `positions` / `orders` / `position_risk_state`** (Layer 1 + cascading hard cap binding)

### P1 (this week)
5. **A1 — multiply `session_mult × regime_mult` into T4 `compute_size`**
6. **A4 — drive Layer 6 dirty sweep from per-tick close path**
7. **A7 — derive regime from `regime_state`, session from venue clock** (depends on A4)
8. **A8 — remove `emitted[:3]` cap; let allocator fence apply back-pressure**
9. **X1 — `max_hold.get_mult()` consumed in monitor / max-hold cutoff**
10. **X2 — wire `register_order_intent` / `resolve_duplicate_intent` (becomes P0 once real-order submit is default)**
11. **X3 — G6 swap predicate → Layer 6 `live_recalc/strategy_swap.py::evaluate_strategy_swap`**

### P2 (doc only)
12. **C1-doc** — clarify ADR-005 §Priority "Kelly determines cap selection in P0; not a sizing chain factor"
13. **F-known-gaps** — update sprint complete digest test-name reference (`haiku_client` → `sonnet_model`)
14. **Integration test** — `tests/test_integration_p0_pipeline.py` end-to-end wiring asserts (prevents future cumulative debt)

## Recommended path

`ignite_p1` (PID 57257) keeps running — fixes are file-only and take effect on next ignition. **Day 8 priority** = land A2 + A3 + A5 + A6 in one PR (they're tightly coupled: A6 produces the `positions` rows that A2 reserves against, A3 supervises the workers that drive A5's per-symbol focus). After that PR, A1 + A4 + A7 + A8 form the Layer 5/6 self-correction loop closure.

## P0 sprint coherence verdict

**P0 sprint primitives = COMPLETE & VERIFIED (415/415, ruff/mypy clean, real demo trades).**
**P0 sprint integration = INCOMPLETE — 4 cross-layer wiring gaps that day-by-day reviews missed.**

Self-correcting cycle proven: cumulative review caught what 7 day reviews + 3 4-axis reviews + 55 codex calls did not, exactly because no review until now had standing to ask "do all 8 layers actually talk to each other in the paper loop?".

[adr3]: ../../10_decisions/ADR-003-8-layer-architecture.md
[adr6]: ../../10_decisions/ADR-006-cell-matrix.md
[adr7]: ../../10_decisions/ADR-007-learner-network.md
[adr8]: ../../10_decisions/ADR-008-7-strategies-signal-generator-role.md
[l0]: ../../30_components/layer-0-universe-discovery.md
[l1]: ../../30_components/layer-1-canonical-baseline.md
[l6]: ../../30_components/layer-6-live-recalc.md
[l7]: ../../30_components/layer-7-strategy-isolation.md
[pb]: ../../../polaris/core/pipeline/payload_builder.py
