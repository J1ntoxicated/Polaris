---
type: research
status: decided
date_created: 2026-06-23
tags: [research, hardening, structural, measurement, probes, self-evolution, mandate]
---

# Structural Hardening — Polaris v2 (2026-06-23) [DECIDED]

> 6-lens adversarial audit (8 structural axes) → 24 confirmed weaknesses → 18 ranked, dedup'd hardening items. 4 /debate rounds (GPT+Gemini) folded in. Hardening = STRUCTURAL robustness (gap/overlap/dead-path/weak-link, measurement honesty, mandate integrity, loop closure, aggressive-flow alignment) — NOT defensive add-ons. Backlink: [[structural_roadmap_2026-06-22]] · [[ADR-003-8-layer-architecture]] · [[ADR-011-ai-free-cutover]] · [[ADR-012-probe-engine-tuning-log]].

## 6-lens confirmed weaknesses (code-verified)
- **Measurement honesty**: equity-curve + daily/PF/WR/$ surfaces sum `fills.is_close=1 pnl_usd` with NO `positions.status` JOIN (snapshot_queries L187/L265/L372) — reconciled close-fills pollute the headline $ even though the R column already excludes them (open-position query L541 `status NOT IN ('closed','cancelled','reconciled')`). 274 phantom close-fills, ~−$1072 net leak. Two-ruler R (excursion-R vs stream-R) unlabeled at consumer seams.
- **Probe composition / self-evolution**: `mark_source="bar"` hardcoded (_production_probe_attach.py L190) → tick exit pass unprobed (6923 bar / 0 tick rows). The P&L-driving adaptive-thesis tick half emits zero probe data, so the calibration/loop reader reads flat — self-evolution loop is structurally open.
- **Execution dead-path**: `_drop_for_bidirectional` (tick engine L438) fires AFTER short-intent construction (L534), 20,111 generate-then-drop occurrences; long-only-venue shorts (OKX spot, Alpaca equity) are unexecutable so this is dead-path hygiene, not a flow block.
- **Action-contract**: `SWAP` in EngineAction enum but `_quantize` (engine.py L37-45) never emits it (no producer); WIDEN structurally unreachable (max composite_lean 0.210 < `_WIDEN_LEAN` 0.35, probe vocabulary single-side bearish) — the aggressive let-winner-run-wider voice is silent.
- **Role taxonomy**: no `ProbeRole` field; 4 catalog probes all implicitly Position/Exit; 5-role SSOT (Eligibility/Signal/Validate/Position/Exit) un-built.
- **Profitability / unit economics**: flow_pressure per-trade gross edge ~9.5x below round-trip taker fee — arithmetic floor no downstream lever lifts.

## /debate convergence (no single-verdict adopted)
- **flow_pressure fee-floor**: CONVERGE — entry-conviction suppression mandate-forbidden both sides. DIVERGE — GPT exit-shaping + fee-favorable routing vs Gemini relocate/archive. → DEBATE+JIN-SURFACE, exit-shape/routing only.
- **AI-escalation seam**: STRONG CONVERGE — build observe-only `ambiguous` column now (runtime unchanged, GPT calls=0), DEFER the arbiter (async/offline does NOT exempt ADR-011). Gemini added A→B graduation criterion.
- **REMAP grace/deadband/streak**: CONVERGE — data-grounded calibration over backfilled sample, green-never-cut preserved by construction, shadow-first, ENV-override not hardcode. DIVERGE — single static theta (GPT) vs held_seconds-tier lifecycle (Gemini). Job=BUILD, apply=JIN-SURFACE.
- **Probe role/WIDEN**: CONVERGE — role-SSOT-first (type-enforced), make WIDEN reachable via a favorable-only probe (don't delete), shadow-validate before un-seal. DIVERGE — EXIT vs POSITION role for the new probe.

## Hardening plan
- **BUILD (autonomous, mandate-clean — 14 items)**: (1) reconciled `status` JOIN on headline fills surfaces + drift-tile shows actual realized reconciled $ — display-only, pnl_usd truth untouched. (2) thread observe_probes into tick exit pass `mark_source='tick'` — byte-identical fail-open sidecar, nothing into run_precise_exit (upstream of 4 & 16). (3) move long-only short check upstream into `_collect_intents` (keep L534 backstop). (4) offline v_probe_outcomes calibration reader → Vault digest, never auto-applied. (5) loud asset_class fallback + known-prefix validation. (6) two-ruler R unit-label at every seam. (7) bar-vs-tick CUT-rate telemetry (optional 2-bar streak makes CUT fire LESS). (8) ProbeRole SSOT (mypy-strict). (9) compose dict↔schedule adapter. (10) reserve/remove unemittable SWAP. (11) `ambiguous` column observe-only (GPT=0). (12) thread base_mfe_protect through non-HOLD modes. (13) pin liquidity-floor boundary (quality-only inputs). (14) doc probe layer as EXIT-ONLY G6 until entry seam exists.
- **DEBATE (Jin-surface)**: (15) flow_pressure fee-floor — exit-shaping/routing only, entry-suppression FORBIDDEN. (16) REMAP theta calibration job=BUILD, ENV apply=Jin.
- **JIN-SURFACE (behavior/AI)**: (17) WIDEN enablement via favorable-only probe + compose un-seal. (18) AI arbiter over ambiguous ticks (ADR-011 boundary).

## Mandate integrity
mandate_integrity_ok = TRUE. Every BUILD item verified free of throttle/block/size-cut/entry-veto/9-stack: ranks 1/6 display+naming only (dollar truth untouched); 2/4/11/14 observe-only sidecar/offline (GPT=0, nothing threaded into run_precise_exit); 3 removes only unexecutable shorts (no executable trade suppressed); 5 observability + correct-er default; 7's optional bar-streak makes CUT fire LESS (delays to 2-bar confirmation, lets winners flow — opposite of a defensive throttle); 8/9/10/12/13 schema/typing/plumbing/test, zero behavior delta. Every behavior/sizing/execution/AI item held Jin-surface (15/16/17/18), not auto-applied. Aggressive bias preserved and strengthened (WIDEN restoration ADDS an upside voice, never a downside brake); flow_not_block intact; 9-stack untouched; DEMO/PAPER. Rejection-keyword sweep = 0 across all item text.
