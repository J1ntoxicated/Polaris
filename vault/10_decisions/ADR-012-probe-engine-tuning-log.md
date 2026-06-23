---
type: ADR
adr_id: ADR-012
aliases: [ADR-012]
status: active
date_created: 2026-06-23
tags: [adr, probe, exit-engine, tuning-log, surgical-strike, g6, observe-only]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-011-ai-free-cutover|ADR-011]], [[north-star]]
reviewed_by: 4-angle design workflow (builder≠reviewer) + Jin vision
---

# ADR-012 — Per-Gate Probe → Engine → Tuning-Log Architecture

## Decision
Surface real-time judgement as a reusable **PROBE → ENGINE → TUNING-LOG** triple, attached to **G6 first**, generalizable to other gates. Realizes the surgical-strike north star: alt-data/technical/session = **evidence SIGNAL**, loss-defense = **precise exit-timing**, never throttle/block/size-cut. Deterministic (AI-free core preserved, [[ADR-011-ai-free-cutover|ADR-011]]).

## Architecture (3 pure pieces)
- **PROBE** — `evaluate(ctx: ProbeContext) -> ProbeReading | None` (None = ABSTAIN; mirrors `fuse_evidence`). A probe only *describes the world*: `ProbeReading(probe_id, kind, lean[-1,+1] signed position-relative, confidence[0,1], evidence)`. Never acts/closes/sizes/blocks. `ProbeContext` = ZERO-fetch view over data `_evaluate_position` already holds + the TTL `AltDataCache`. Catalog: profit-taking(giveback — top lever), loss-defense, technical(atr_slope/vol_z), session-hours(per-epic opening_hours), volume, multi-res-bars, news/consensus(fuse_evidence), regime/crisis, COT.
- **ENGINE** (sole actor) — `compose(readings) -> EngineDecision`: confidence-weighted lean → quantize {hold,widen,tighten,harvest} → fills ONLY existing knobs (`trail_mult` loosen-only, `mfe_protect` ratchet-only, `profit_target_r`, G7 `widen_atr_mult` via Q9 floor-rail). PARAMETER OVERLAY on the #26 FSM — never a new close path.
- **TUNING-LOG** — `data/probes.sqlite` SIDECAR (no live-DB WAL contention): `probe_readings` + `probe_decisions` (+ giveback/outcome backfilled at close) → `v_probe_outcomes` → offline `/debate` calibration, never auto-applied.

## First slice (observe-only, byte-identical)
Slice 1 = engine returns `action=HOLD`, ALL knobs `None` → nothing threads into `run_precise_exit` (provably no behavior change; mirrors W1 sentinel + G3/G4 shadow). Attach ~3 lines in `_evaluate_position` after `pnl_r`. Run for days → MEASURE would-be tighten vs realized giveback (MFE +0.278R → realized −0.947R) BEFORE any knob moves. Slice 2 = `POLARIS_PROBE_ENGINE=trail_only` after shadow-compare + /debate.

## Guardrails (invariant)
- flow_not_block: worst loss-side action = harvest EARLIER; never block entry / cut size / loosen the −1R floor. 9-stack untouched (engine touches ZERO sizing). Hard rails (−1R G6, protected-BEP, loser-timeout, Q9, hard-MAX) BYPASS the engine. Rejection keywords = 0.

## /debate flags (calibration — not silent hardcode)
lean→knob quantization thresholds + knob deltas (giveback_margin_r, lock_tighten_delta_r, widen_trail_max_mult) · cross-probe weights · confidence/HOLD floors · **policy: may alt-data adverse-lean tighten an open WINNER, or is alt-data strictly entry-signal/regime-evidence?** · crisis force-harvest · observe→act promotion gate (measured giveback reduction).

## Sources
- Design workflow `wqw4qbm3c` (4-angle: probe-interface/engine/tuning-log/integration → synthesis). [[north-star]] surgical-strike thesis. Generalizes `fuse_evidence`→`detect_regime_flip` evidence/actor split.
