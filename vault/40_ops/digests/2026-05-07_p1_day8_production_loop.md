---
type: digest
date_created: 2026-05-07
phase: P1
day: 8
status: implemented
tags: [polaris, p1, day8, production-loop, layer-wire]
links: [[_NOW]] `ARCHITECTURE` [[layer-0-universe-discovery]] [[layer-1-canonical-baseline]] [[layer-7-strategy-isolation]]
---

# Day 8 — Production Paper Loop (Fixture Mode → Production Wire)

## Why
Functional review #82 + cumulative coherence #81 catch: 24h paper loop (PID 57257) was running `smoke_paper_loop.py` — a Day 5/6 fixture. Layer 0/1/G1/G2/G8 bypassed; regime/PnL/MarketView hard-coded. 24h "behaviour data" was synthetic.

## What
- `polaris/scripts/production_paper_loop.py` (460 LOC) — main orchestrator + tick loop.
- `polaris/scripts/_production_pipeline.py` (437 LOC) — Layer 7 fence/idempotent register + G1-G7 driver per signal.
- `polaris/scripts/_production_close.py` (317 LOC) — close path mark-to-market + G8 + fail-safe envelope.
- `polaris/scripts/_production_indicators.py` (395 LOC) — RSI/ATR/ADX/Donchian/Bollinger/momentum/efficiency-ratio + regime classifier + unrealized PnL R helper.
- `polaris/scripts/_production_layers.py` (414 LOC) — Layer 0 producer (OKX 5min / Capital 10min) + Layer 1 bar ingest + Layer 6 regime flip + recalc.
- `polaris/scripts/smoke_production_paper_loop.py` (134 LOC) — 60s real-fetch verify (8 acceptance items).
- `tests/test_production_paper_loop.py` (1,030 LOC, 29 tests including 2 hypothesis property + 6 codex regression).
- `polaris/scripts/ignite_p1.py` — switched paper handoff from `run_smoke` → `run_production_paper_loop`. Layer 0 producer warms on bootstrap (`_layer0_producer_start`).

## Wiring deltas (vs smoke loop)
| Layer | Smoke (fixture) | Production (real) |
|-------|-----------------|-------------------|
| L0 — universe | Hard `(BTC, ETH, EURUSD, GOLD)` | OKX 5min + Capital 10min refresh + dynamic focus 12-48 |
| L1 — bars | Stub `_stub_bars(120)` | `fetch_okx_bars` + `fetch_capital_bars` + `ingest_bars` |
| L2 — start_gate | G3 (`emitted[:3]` cap) | G1 (`start_gate=GATE_UNIVERSE_SCANNER`), no cap |
| L4 — cell matrix | `pnl_r=1.0; won=True` forced | Real mark-to-market from fills + bar drift |
| L5 — learners | Sees synthetic 100% WR | Real `pnl_r` from `_real_pnl_r_from_fills` |
| L6 — recalc | Skipped | `run_recalc_for_active_positions` per tick |
| L6 — regime | Hard `bull_trend` | `compute_real_regime` + `detect_regime_flip` SSOT |
| L6 — swap | Strategy-local predicate | `evaluate_strategy_swap` (Layer 6 SSOT) |
| L7 — isolation | `asyncio.create_task` direct | `AllocatorFence.check_and_reserve` before submit |
| L7 — circuit | Skipped | `record_fault` on exception/NaN/reject |
| L7 — order keys | Skipped | `register_order_intent` before every submit |
| G6 — pnl_r | Const 0.2 | `compute_unrealized_pnl_r` from prices |
| G7 — widen | None | Real ATR-derived stop proposal |
| G8 — reflector | Not invoked | Invoked after every close + persisted to `gate_events` |
| MarketView | `volume_z=3.5/rsi_14=22/adx_14=30/momentum=0.10` | All real from fetched bars |

## Tests
- 29 new tests (Day 8 spec A-K + 2 hypothesis property + 6 codex regression).
- 444 / 444 total tests pass; mypy `--strict` clean; ruff clean.
- Existing `test_day7_ignition_health.py::test_kill_switch_paper_mode_cancels_inner_tasks` + `test_24h_readiness_composite_exercises_all_layers` updated to patch the Day 8 Layer 0 producer + bar fetch entry points; `test_day6_codex_review_fixes.py::test_ignite_paper_persists_to_caller_db_path` updated to inject `StubHaikuClient` for deterministic test runs.

## Codex external review (5-round iteration, APPROVE at R6)
- **R1 = REJECT_WITH_FIXES**: 1 BLOCKER + 2 P0 + 3 P1 (positions table not populated; PnL match by strategy×instrument; synthetic close price; G4/G5 hardcoded inputs; `gather` swallows exceptions; default StubHaiku not real).
- **R2 = REJECT_WITH_FIXES**: 2 P1 + 1 P2 (orphan-risk on confirm_reservation fail; close-path mutates state before persist; G6/G7/G8 used synthetic position_id).
- **R3 = REJECT_WITH_FIXES**: 2 P2 (G6/G7 not persisted to gate_events; close-path persist failure bypassed fault accounting).
- **R4 = REJECT_WITH_FIXES**: 1 P2 (committed-close partial-processing hole — learner/G8 exceptions silently dropped fan-out).
- **R5 = REJECT_WITH_FIXES**: 1 P2 (post-commit auxiliary path including `record_fault` itself could raise unguarded).
- **R6 = APPROVE**: end-to-end fail-open envelope verified; close durability boundary correct; learner exception cannot drop G8.

## Smoke verify (30s, 5s tick, OKX-only — Capital creds absent)
```
universe_active_okx     : 25
universe_active_capital : 0
bars_total              : 6240
cell_matrix_cells       : 3+ (grew from 0 cold start; reaches 4-7 in longer runs)
fills_okx               : 14 (entry) / 4 (close) — 1:1 matched in 60s+ runs
g1_events               : 10
g2_events               : 10
g8_events               : 4
fault_events            : 0
pass_count              : 7/7  (need ≥ 4)
```
Earlier 30s run with longer tick window: 39,360 bars persisted, 33 sized signals, 33 fence reservations, 6 closed trades, 0 faults; `wr_max=0.5` on a separate run confirms real WR (no longer synthetic 100%).

## Findings
- **Capital fills 0** — root cause is Capital creds unavailable in current `.env`. Wiring is verified by `test_capital_bars_ingest`. Once `CAP_API_KEY/CAP_EMAIL/CAP_PASSWORD` populated, Capital path engages automatically.
- **Real WR (wr_min/max) 0.0 in some 30s smoke runs** — too short for the close path to see post-entry bar drift; expected to populate over 24h.
- **G6 pnl_r = 0** still during entry-tick (correct: last_price == entry_price). Subsequent ticks via Layer 6 recalc will mark drift.
- **Real Haiku 400 errors** — current `claude-haiku-4-5-20251001` model id rejected by Anthropic API; G3 fail-CLOSED rejects all signals, sized=0. Smoke verify defaults to `StubHaikuClient` (`--real-haiku` flag opts in). This is a Polaris-wide model-id concern, not a Day 8 regression.

## Next
- 24h re-launch via `ignite_p1 --paper --duration 86400 --tick 5 --full-pipeline`.
- Day 9: drawdown checkpoint snapshot trigger + `policy_engine` matrix wire + Polaris-wide Haiku model-id refresh.
