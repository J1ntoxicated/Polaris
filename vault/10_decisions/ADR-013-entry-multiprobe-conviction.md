---
type: ADR
adr_id: ADR-013
aliases: [ADR-013]
status: active
date_created: 2026-06-23
tags: [adr, probe, entry, conviction, signal, multi-signal-arbitration, g1, g2]
related: [[ADR-012-probe-engine-tuning-log|ADR-012]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-008-7-strategies-signal-generator-role|ADR-008]], [[north-star]]
reviewed_by: 4-angle design workflow (builder≠reviewer) + Jin (G1/G2 = foundation)
---

# ADR-013 — Entry Multi-Probe Ensemble + Conviction (the symmetric twin of ADR-012)

## Decision
Make the ENTRY/signal side (G1/G2) a multi-PROBE ensemble — the symmetric twin of [[ADR-012-probe-engine-tuning-log|ADR-012]]'s monitoring/exit probes. **Same probe abstraction, same tuning log; the ONLY difference is output type + consumer.** Jin (2026-06-23): G1/G2 (opportunity discovery) is THE foundation — downstream is moot without good entries.

## Architecture (reuses ADR-012 verbatim where shapes fit)
- **EntryProbeContext** — candidate-relative zero-fetch view over the RawSignal + MarketView + regime + `AltDataCache.get_for_group` + cell warm-n. Built at the G2→G3 seam from data the pipeline already holds (zero new fetch).
- **EntryProbe.evaluate(ctx) → ProbeReading | None** — REUSE `ProbeReading` (lean[-1,+1] candidate-relative: +1 corroborates THIS entry, −1 contradicts, None abstains). **A negative lean is NOT a veto** — it just produces no raise. Catalog: technical · volume · multi-res-bars · news/consensus(`fuse_evidence`) · regime/crisis · COT · microstructure · the 11 strategies (each RawSignal = a pre-baked probe).
- **ConvictionEngine.compose(readings) → ConvictionReading** (RAISE-ONLY twin of ExitEngine): same `_composite()` → `conviction_mult = 1.0 + max(0, composite_lean)×(CONV_MAX−1)` ∈ [1.0, CONV_MAX]. The type has NO veto/suppress field — flow_not_block holds by construction.
- **Consumer seam**: `signal_strength_effective = strength × conviction_mult` BEFORE `SignalIntent`, then the EXISTING `continuous_scalar` [0.75,1.50] clamp binds. Adds ZERO new T4 multiplier (raises the INPUT to the one scalar that already exists) — 9-stack untouched.
- **Tuning log**: REUSE `data/probes.sqlite` with a `gate_id` discriminator (entry vs G6); `v_entry_conviction_outcomes` joins decision→realized trade R for offline /debate calibration.

## Multi-signal arbitration (Jin: BLEND)
When multiple signals fire, rank by **learned expectancy (strategy×cell×regime posterior) × current-evidence conviction** (this ensemble). Conviction = the "evidence" half; the cell-matrix/learner posterior = the "what historically paid" half. Select/allocate by the blend.

## First slice (observe-only, byte-identical)
Slice 1: `conviction_mult=1.0` + applied=False → nothing multiplies strength (mirror ADR-012 Slice 1). Log would-be conviction vs the live admission/strength for days, then Slice 2 (`POLARIS_ENTRY_CONVICTION=on`) after shadow-compare + /debate.

## Guardrails
flow_not_block: conviction can only RAISE strength within the existing [0.75,1.50] clamp; never veto/suppress/block/cut. 9-stack untouched. AI-free deterministic (alt-data enters as evidence/signal only). Rejection keywords = 0.

## /debate flags
`CONV_MAX` ceiling · per-probe/per-kind weights · confidence/composite floors · **may alt-data adverse-lean ever lower conviction below baseline (NO — raise-only) vs strictly entry-signal** · the observe→act promotion gate (measured entry-quality lift).

## Sources
Design workflow `wz18k5f2i` (4-angle → synthesis). Generalizes ADR-012 to the entry side; `fuse_evidence`→`detect_regime_flip` evidence/actor split is the precedent.
