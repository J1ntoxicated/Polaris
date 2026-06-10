---
type: lesson
status: active
date_created: 2026-05-30
tags: [lesson, architecture, gates, streams, unified-vs-split, invariant-core]
related: [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[ADR-006-cell-matrix|ADR-006]], [[ADR-007-learner-network|ADR-007]], [[2026-05-30_ai_validity_audit]], [[2026-05-29_venue_asset_class_differentiation]]
---

# Gate architecture: unified vs per-stream (DEMO/PAPER, read-only analysis)

**판정**: NEITHER pure-unified NOR full-split. Correct = unified gate SKELETON
(lifecycle + invariant core) + per-stream ROUTING (StreamConfig, already built)
+ per-stream DECISION MODULES at the 3 content-bearing gates (regime / G3 / G6 / G7)
and universe ranking. Strategy pattern, NOT pipeline fork.

## Key structural finds (code-verified)
- `core/streams/config.py` StreamConfig SSOT ALREADY separates per-stream
  routing/isolation from invariants. Docstring: "Stream supplies leverage/caps/
  dispatch, NEVER a multiplier; T4 chain, headroom_min(), 0.09 ceiling untouched."
- `headroom_min()` = ONE shared min(); per-stream isolation from the cap VALUES
  fed in (track_remaining/venue_daily/per_symbol 50/35/equity), not a forked fn.
- Cell matrix keyed exchange×strategy×ticker×regime; parent2=strategy×regime.
- **Strategy rosters fully DISJOINT** across A/B/C (verified). So learner rows +
  cells are ALREADY de-facto stream-partitioned via the strategy key — even
  parent2 (no exchange) doesn't cross-pool (rsi_bb_pullback ≠ equity_rsi_bb_pullback).
- Regime: `classify_regime` keyed venue×underlying_group_id (already per-group);
  `altdata/fuser.py` already routes evidence per-group (crypto F&G/funding, FX
  VIX/HY). The "price-only regime misfit" is INCOMPLETE WIRING (P1 stub), not a
  wrong KEY. Keying is already per-stream-correct.

## Dilution quantification
Cross-stream pooled samples ≈ 0 today (disjoint rosters) → split's DILUTION
cost ≈ 0. The real cost of full-split = 3× engine copies → MECHANISM divergence
+ N surfaces where the 9-stack ban can be independently violated. Strictly worse.

## Irreducible shared core (must NOT fork)
T4 compute_size + 9-stack clip + headroom_min()+0.09 · learner ENGINE (rows
already partitioned) · cell ENGINE (score=avg_pnl×√n/70, EWMA, warmup parent
fallback) · exit FSM math · regime 2-close confirm gate · L7 isolation/breaker.

## Split the DATA, share the MECHANISM
Per-stream: regime evidence, gate prompts/thresholds, universe sub-pools, caps,
sessions, rosters. Shared: invariant-bearing arithmetic.
