---
type: digest
status: active
phase: P1
day: functional-review
date_created: 2026-05-07
tags: [digest, p1, functional-review, behavior-vs-intent, paper-loop, ignite_p1]
related: [[layer-0-universe-discovery]], [[layer-1-canonical-baseline]], [[layer-2-per-gate-pipeline]], [[layer-3-sizing-risk]], [[layer-4-cell-matrix]], [[layer-5-learner-network]], [[layer-6-live-recalc]], [[layer-7-strategy-isolation]]
---

# P1 functional review — spec intent vs. live 24h paper-loop behavior

## Context

- Loop: PID 57257, 18 min elapsed at audit time, 233 ticks, ~792 strategy emits.
- Mode: `ignite_p1 --paper --duration 86400 --tick 5 --full-pipeline --real-roundtrip`.
- DB: `data/polaris.sqlite` (WAL).
- Log: `data/paper/polaris_runtime.log` (20,656 lines).
- Code is pristine (mypy strict + ruff clean, 406 tests passing). Question
  here is **not** code quality — it is whether the wired behavior matches the
  spec intent of L0-L7. Every finding below is evidence-grounded from the
  live DB + log; no speculation.

## TL;DR — verdict per layer

| Layer | Intent | Live behavior | Verdict |
|---|---|---|---|
| L0 Universe | 270-320 → 100-150 → 12-48 dynamic focus | `universe`=0 rows · `watchlist_focus`=0 rows · loop runs against hardcoded 4-tuple `FOCUS` | NOT WIRED |
| L1 Canonical | bars + ticks + events + 5-metric baseline | `bars`=0 · `quote_ticks`=0 · `market_events`=0 · `ticker_baseline_state`=0 | NOT WIRED |
| L2 Pipeline | G1→G2→…→G8 (8 gates) | only G3-G7 emit; **G1, G2, G8 = 0 events** | PARTIAL |
| L2 G3/G4 | Haiku KILL/PASS · Haiku KILL/PROCEED | `gate_id=3` PASS=1413/1413 (100%) · `gate_id=4` PROCEED=1413/1413 (100%) — `model_used=haiku` but **stub client, latency_ms=0** | INTENT VIOLATED (no real Haiku, no kills) |
| L2 G6 | HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY | `gate_id=6` HOLD=1413/1413 (100%) — caller hardcodes `unrealized_pnl_r=0.2`, never trips any branch | INTENT VIOLATED |
| L2 G7 | widen / tighten / HOLD | `gate_id=7` HOLD=1413/1413 (100%) — caller never supplies `widen_proposal`, default-HOLD branch always wins | INTENT VIOLATED |
| L2 G8 | Reflector → `ai_lessons` + cell delta + learner feedback | `ai_lessons`=0 rows | NOT WIRED |
| L3 Sizing | continuous_scalar 0.75-1.5 · tier_amplifier 1.5/2/3 · cell mult | continuous_scalar = 0.85 (volume_burst) / 1.00 (tsmom) / 0.88 (donchian) — **observed in log, working** · `tier_amp=1.0` always (no streak tracker wired) · `cell_mult=1.0` always (cold quartile) · `kelly=0` cold-start gate works | PARTIAL |
| L4 Cell Matrix | 8-dim (or P0 4-dim) + dynamic quartile activation gate (n_eff≥20) | only **3 cells** populated (`okx × {volume_burst, tsmom, spot_donchian} × BTC-USDT × bull_trend`); all 3 have n_eff > 150 (eligible) but `pool=3` < min for quartile spread → `quartile=cold` permanently · routing.py defends correctly but the matrix stays degenerate | NOT EXPANDING |
| L5 Learner | WR-driven mult tune + clip + adaptive_learner_attack triple | 4 learner types × 15 keys, but **all WR=1.0** because `_close_oldest_trade` hardcodes `pnl_r=1.0, won=True` (line 491-494) → mult slammed to clip ceiling (regime/session 1.4 max, max_hold 20.0). Pure feedback loop on synthetic wins, no signal. | INTENT VIOLATED |
| L6 Live Recalc | tick recalc dirty + regime confirm + swap candidate + conviction stack | `position_live_recalc_state`=0 · `regime_state`=0 · `position_strategy_segments`=0 · `position_conviction_layers`=0 — never invoked from the smoke loop | NOT WIRED |
| L7 Isolation | per-strategy asyncio task · circuit breaker | tasks DO fire isolated (line 600 `asyncio.create_task` per (strategy, focus)) — `strategy_halts`=0 · `strategy_fault_events`=0 (no faults injected → no observation) | WORKING (no stress) |

## Root cause — single architectural gap

`ignite_p1.ignite()` delegates the per-tick loop to
`smoke_paper_loop.run_smoke()`. `smoke_paper_loop` was authored as a Day 5/6
**smoke** harness — i.e. it explicitly bypasses Layer 0/1, hardcodes
`FOCUS = (BTC, ETH, EURUSD, GOLD)`, hardcodes `regime="bull_trend"`,
fabricates the `MarketView` (`volume_z=3.5, rsi_14=22.0, adx_14=30.0,
momentum_20bar=0.10, ...`), and starts the orchestrator at
`start_gate=GATE_SIGNAL_VALIDATOR` (G3) — **skipping G1 (universe scanner)
and G2 (strategy signal gen as a gate)**, and never invoking G8 (reflector).
G6/G7 are then driven directly with `unrealized_pnl_r=0.2` and no
`widen_proposal`, which deterministically maps to HOLD on both gates per
spec. `_close_oldest_trade` then forces `pnl_r=1.0, won=True` so the cell
matrix and learners see a synthetic 100% win stream.

Net: the production-shaped payloads are real (G3/G4/G5 invoke
`build_*_payload` with the actual `RawSignal.strength` flowing through
`continuous_scalar`), but the **inputs to G6/G7/G8 and the close path are
test fixtures**, not market truth. Layer 0 + Layer 1 + Layer 6 wire-up
never happened — confirmed by the empty tables.

## Per-layer findings (ranked by severity)

### P0 — block any meaningful behavioral data

1. **L0/L1 not wired in `ignite_p1`** — `_layer0_warm` only counts existing
   rows, never refreshes. `discovering-universe` skill exists but no
   producer is scheduled. Result: `FOCUS` is the only path; the entire
   "270-320 → 100-150 → 12-48" funnel is dead code at runtime. Fix path:
   schedule `polaris.core.universe.discovery.refresh()` every 5 min (OKX) /
   10 min (Capital) inside ignite + write to `universe` + recompute
   `watchlist_focus`. Spec already specified this in
   `vault/30_components/layer-0-universe-discovery.md`.

2. **L2 G1/G2/G8 not wired** — orchestrator supports them
   (`GATE_PREREQ_STATES` covers all 8) but caller starts at G3. Fix path:
   start at `GATE_UNIVERSE_SCANNER` so G1 (Haiku focus narrow) + G2
   (`signaling-strategies` skill orchestration as a gate-emit) execute
   under the orchestrator, and add an explicit `_run_g8_reflector` after
   `_close_oldest_trade` so each closed trade produces an `ai_lessons` row
   + cell delta + learner feedback through the spec'd path (not the manual
   path that exists today).

3. **L5 synthetic 100% wins corrupt learner state** — `pnl_r=1.0` is a
   stand-in for "we don't yet have real fills wired into the close path."
   With 1413 wins and 0 losses, every `wins_eff/n_eff = 1.0` → mult
   ratchets to its individual ceiling (`session_mult / regime_mult` clip
   1.4, `max_hold` clip 20.0 — observed). The learner is doing what the
   spec says given its inputs, but the inputs are fiction. Fix path: route
   real OKX fills + simulated mark-to-market PnL into `ClosedTrade.pnl_r`
   instead of the literal `1.0`. Until then, learner deltas are noise.

4. **L4 cell matrix degenerate (`pool=3` permanently)** — only 3 cells
   exist because the smoke loop only ever closes trades for
   `(okx, BTC-USDT, bull_trend) × {volume_burst, tsmom, spot_donchian}`.
   Quartile activation requires a meaningful pool spread; with `pool=3`,
   `routing.resolve_cell_routing_mult` correctly returns `mult=1.0` (not a
   bug — defensive). Real diversity (more symbols, more regimes, real
   wins+losses) unblocks quartile routing.

### P1 — wired but degenerate

5. **L2 G3/G4 stub Haiku → 100% PASS / 100% PROCEED** —
   `model_used=haiku` is a label, not a real call (latency_ms=0). The
   `_haiku_client.py` stub returns affirmative for everything. KILL ratio
   is 0. Fix path: wire the real Anthropic client (skill `claude-api`
   already documents the pattern) or at minimum a probabilistic stub so
   regression tests can observe a non-zero KILL ratio.

6. **L2 G6 always HOLD; G7 always HOLD** — caller-controlled
   `unrealized_pnl_r=0.2` falls cleanly into the HOLD branch (`< -1.0R` =
   EXIT_NOW; `> +0.7R` = ADJUST_EXIT widen window). Spec is intact;
   inputs are wrong. Fix path: drive G6 from real position
   mark-to-market (PnL_R from `fills` + last_price) once real fills are
   fed in, and G7 from a `widen_proposal` produced by the strategy when
   `unrealized_pnl_r > 0.7R`.

7. **L6 live recalc never fires** — `dirty trigger`, `regime_flip`,
   `strategy_swap`, `conviction stacking` all have code + tests but the
   smoke loop never calls them. P0 spec scoped them as "logging only" so
   strictly speaking this matches `vault/30_components/layer-6-...md`,
   but means there is **zero observable behavior** for the layer at
   present.

8. **Capital strategies emit zero in production** — `FOCUS` includes
   `EURUSD` + `GOLD`, but Capital bars use `_stub_bars()` (line 145)
   because Capital auth is heavy. `fx_breakout_basket`, `xau_indices_trend`,
   `session_breakout` therefore see deterministic synthetic bars; whether
   they emit depends on the stub series matching their thresholds (likely
   never). Fix path: wire the real `CapitalAdapter.fetch_bars` path with
   the existing CST session.

### P2 — natural cold phase, re-measure later

9. **`continuous_scalar` working as designed** — log shows 0.85, 0.88,
   1.00 depending on which strategy emitted (vol_z 3.5 → strength 0.7 →
   cs 0.85 for volume_burst; tsmom strength 1.0 → cs 1.00; donchian
   strength 0.75 → cs 0.875). This is the **only** sizing input that is
   actually responsive to signal strength right now.

10. **L7 isolation looks healthy** — 0 halts, 0 faults, 0 cross-poll
    incidents over 18 min. But absence-of-signal ≠ proof; recommend a
    fault-injection test (raise inside one strategy task) once real
    behavior is wired.

## Effect verification — what the data proves

- `final_pct ∈ {0.0170, 0.0175, 0.0200}` for 100% of sized intents.
  Without learner mults / tier_amp / cell mult contributing anything other
  than 1.0×, the sizer is effectively a constant-multiplier function of
  the strategy strength — it has **no autonomous adaptation surface
  active** today.
- Cell matrix `score` drifts upward (0.130 → 0.183 over 18 min) only
  because every trade is a synthetic win; this is not learning, it is
  arithmetic.
- 477 closed trades · $431 PnL — also synthetic (0.5% drift assumption in
  `_close_oldest_trade`).

## Recommended fix priority

1. Wire L0 producer + start orchestrator at G1, populate `universe` +
   `watchlist_focus` (P0 — unblocks every downstream layer's diversity).
2. Wire G8 reflector after every closed trade (P0 — unblocks `ai_lessons`,
   gives learner a real spec-compliant feedback path).
3. Route real fills into `_close_oldest_trade.pnl_r` instead of `1.0`
   (P0 — unblocks honest learner + cell signal).
4. Wire real Haiku in G3/G4 (P1 — unblocks KILL telemetry).
5. Drive G6/G7 from real position state (P1 — unblocks adaptive exit).
6. Wire L6 dirty-tick into the smoke close path (P1).
7. Wire `CapitalAdapter.fetch_bars` for FX/commodity strategies (P1).

None of these require killing the running loop — fixes can land as a
follow-up `smoke_paper_loop` revision (or a new `production_loop`) and the
24h ignition can run to completion to catch any infra regressions in the
current path.

## Vault hooks

- `functional_review` (new tag) — link from layer specs after fixes
  land.
- `ignite_p1_smoke_gap` (new lesson tag) — capture the
  smoke-vs-production caller divergence so future sessions don't mistake
  smoke output for production behavior.
