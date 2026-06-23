---
title: Data Foundation + Flow Audit
date: 2026-06-23
type: research
tags: [layer-0, foundation, flow, probes, ingestion]
status: audit
links: ["[[g1_universe_gate_audit_2026-06-23]]", "[[gate_bus_probe_wiring_audit_2026-06-23]]", "[[ADR-003-8-layer-architecture]]"]
---

# Data Foundation + Flow Audit (READ-ONLY)

## Foundation verdict
Upstream ingestion is SOUND + FRESH. The base is not badly COLLECTED;
it is badly DELIVERED downstream and not yet JUDGED. Jin's instinct is
directionally right but mis-located: the root is throttling + a missing
judgment layer, not weak data collection.

## Live evidence (data/polaris_live.sqlite, ts=seconds)
- WS fresh: okx + capital quote_ticks streaming, age ~18s, still flowing.
- OKX tape REAL: last_trade_size>0 on 15463/15463 ticks → OFI computable.
- Capital sizes hardcoded 0 (canonical.py:178-182) → 0 sized ticks.
- Focus latest cycle: okx=4 / capital=22 / alpaca=4.
- OKX streamed distinct (5m) = 3 → near-frozen socket set.
- Gate events model_used = `python` ×63316 → in-loop GPT = 0.

## SOUND
- WS + REST bar + altdata (COT/funding/F&G/macro) all live, fused as
  tilt-only (flow_not_block).
- Pipeline ORDER valid: signal→alloc→watch→entry→toss; no double-trade;
  T4 reused → 9-stack ban intact.
- Per-venue grouped z-score ALREADY built (watchlist.py:69-117,
  `_grouped_z_score` per venue/asset_class) — plan's BUILD#1 is DONE.
- AI-free seam clean: ambiguity flagged, consumer deferred, gpt stays 0.

## WEAK / MISSING
- [flow] OKX active universe = 12/189; 177 rejected by depth<$25k /
  atr<2.0% floors (active_reason verified). This (not pooled-z) thins
  the OKX tick engine → few candidates win focus.
- [probe] Probes observe-only + G6-only (roles.py:14-15); G1-G5 = reserved
  seats. applied affects nothing → judgment layer effectively unbuilt.
- [probe] Tick-path ProbeContext degenerate (peak/trough=None, no
  recent_ticks) → blind on the live-majority tick-opened positions.
- [probe] session_hours DEAD: config.py:208 frozenset({forex,index,
  commodity}) + probe_attach.py:98 `next(iter(...))` → FX graded on
  commodity calendar, 0 readings (latent correctness bug).
- [ai] Ambiguity seam fed but unplugged (consumer deferred) → ambiguous
  cases default to deterministic HOLD; Q5 layer absent by design.
- [mapping] Capital microstructure dead (size=0) → OFI/aggr_flow=0;
  burst_rider + flow_pressure cannot fire on Capital (venue limit).
- [ingestion] WS focus churn not propagated to stable socket → frozen
  streamed set; incremental subscribe would close it.

## Is the foundation the root?
PARTIALLY + mis-located. Collection is healthy; the '사단' is downstream
delivery (OKX active floor + frozen socket) + an unbuilt judgment layer.

## Build path (flow_not_block-safe; RAISE flow or ADD judgment)
1. Populate tick-path ProbeContext (peak/trough/recent_ticks).
2. Fix session_hours asset_class resolution + lead window.
3. Periodic graceful / incremental WS re-subscribe.
4. Build ENTRY-side probe seam (G1-G4 judge) — Jin-surface, large.
5. Re-tune OKX depth/ATR floors — Jin-surface (trading-param /debate).
6. Wire/analyze AI arbiter on ambiguous flag — Jin-surface.
