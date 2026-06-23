---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, strategy, exit, regime, data, enhancements, forward-roadmap]
---

# Forward Research Agenda (2026-06-22) — INDEX

FIX program (M measurement-honest / S stabilize / R clean-reset / D 4 params) is **done**. Measurement is now HONEST. This is the forward RESEARCH + IMPROVEMENT roadmap, grounded in trustworthy metrics. Source: [[system_design_audit_2026-06-22]] + `.claude/loop_state.md` + [[trading_params_audit_2026-06-22]] / [[execution_layer_p3_2026-06-22]] / [[research_agent_mesh_2026-06-22]] / [[regime_layered_synthesis_2026-05-31]] / [[p3_self_evolve_2026-06-01]].

Mandates held throughout: DEMO/PAPER · AGGRESSIVE / flow_not_block (loss-defense = **precise exit**, never throttle/size-cut/block) · in-loop AI-FREE · surgical-strike (precise entry/exit, only profit, reasoned trades, alt-data = SIGNAL). 0 rejection keywords.

## Sections (Jin's framing)
- **(A) 전략 타당성 / Strategy validity** → [[research_agenda_2026-06-22_A_strategy]]
- **(B) 모니터·엑싯 타당성 / Monitor + exit** → [[research_agenda_2026-06-22_B_exit]]
- **(C) 레짐 + 전략 선택 / Regime + selection** → [[research_agenda_2026-06-22_C_regime]]
- **(D) 데이터·튜닝 / Data + tuning** → [[research_agenda_2026-06-22_D_data]]
- **(E) 추가·확장 / Enhancements + vision** → [[research_agenda_2026-06-22_E_enhance]]

## COVERAGE (Jin's exact asks all mapped)
- 전략 타당성 → **(A)** ✔ (12 items, all 14 strategies incl. 3 registry-orphan tick strategies)
- 모니터·엑싯 타당성 → **(B)** ✔ (9 items, G6/G7 + precise-exit FSM + venue-resting stop)
- 레짐 + 전략 선택 → **(C)** ✔ (8 items, regime label + selection-vs-tilt + cell EV)
- 기타 추가 → **(D)** data/tuning ✔ + **(E)** enhancements/vision ✔
- GAPS found (none of Jin's asks dropped; these are NEW surfaced gaps):
  1. **14≠11 strategy scope** — 3 tick strategies (burst_rider/flow_pressure/micro_reversion) are registry-orphans yet DOMINANT post-reset; whole agenda treats all 14 (A2).
  2. **Selection mechanism conflict** — bar pipeline TILTS by regime, tick engine HARD-SELECTS; never reconciled (C2).
  3. **Replay harness is the cross-cutting unblocker** — replay_runs=0, OOS gating dead; every shadow→promote rail stalls without it (E, P2 but structural).
  4. **OKX tradable∩signal thinness** (_NOW #4) — only item with no debate/plan; upstream-gates all OKX flow (A4 + E).
  5. **Empirical-N gate** — R reset wiped data; every edge/EV/calibration item is research-NEEDED pending warmup (posterior n≥20 / cell n_eff≥5). First gate of the whole agenda.

## EVERYTHING IS GATED ON WARMUP
Post-reset (2026-06-22 07:58, PID 30223) the DB is near-empty. Edge/EV/calibration questions are **currently unanswerable** until the fixed-code bot fills the NIG posterior + cell matrix on honest R. **Gate 0 = let it run.**

## RECOMMENDED SEQUENCE
0. **Gate 0 — accumulate** clean post-reset data (posterior + cell warmup). Everything below waits on this.
1. **P0 measurement-hygiene that needs no warmup**: verify OKX flat-bar filter shipped (D), confirm D1/D2/D3/D4 actually applied live next restart, instrument regime flip-cadence + stop-fill KPIs.
2. **P0 loss-precision build**: OKX venue-resting conditional stop (B/E) — the single largest unaddressed exit hole.
3. **P0 edge readout** once warm: per-strategy posterior incl. tick strategies (A), regime label-accuracy + cell-EV map (C).
4. **P1 calibration on clean data**: exit FSM thresholds + per-strategy exit width (B), entry-slippage (A), session/expectancy learner verify (D), regime selection-vs-tilt decision (C).
5. **P1 edge substrate**: alt-data → MarketView strategy features (D/E), OKX majors universe redesign (A/E).
6. **P2 shadow→promote rails** (need replay harness first): research-mesh P1/P2 collectors, AI-conductor, liquidity-graded T4, swap/conviction organs (E).
7. **P3 ideas** gated on all above: self-evolve generator (only if KILL-spike passes), strategy-discussion chat, add/retire strategy set.
