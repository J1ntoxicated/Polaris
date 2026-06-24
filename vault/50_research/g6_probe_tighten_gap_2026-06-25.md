---
type: research
status: open-design
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [research, g6, position-monitor, probe, tighten, flow_not_block, open-design]
---

# G6 DEGRADED — monitor ignores probe TIGHTEN (gap locked, fix deferred)

DEMO/PAPER · aggressive · flow_not_block (precise EXIT TIMING, NOT block/size-cut).
9-stack untouched · GPT=0. systematic-debugging: gap confirmed code+DB, characterization
test locked; FIX is a trading-behaviour change → deferred to design, NOT speculatively
shipped on the live monitor. 백링크 [[g7_tick_trail_atr_scale_2026-06-25]] ·
[[g1_universe_gate_audit_2026-06-23]].

## CONFIRMED GAP (producer→gap→consumer, file:line)
- PRODUCER `_production_probe_attach.observe_probes` (:130-206): builds populated
  ProbeContext, persists `probe_decisions` row (HOLD/WIDEN/TIGHTEN/HARVEST) to the
  SEPARATE `data/probes.sqlite` sidecar (state.probe_conn). OBSERVE-ONLY, returns None.
- GAP `payload_builder.build_monitor_payload` (:380-401): emits only position /
  unrealized_pnl_r / max_loss_r / swap_candidate. recalc bolts on market_view +
  recent_ticks (`_production_recalc.py:470-480`). NO probe action key.
- CONSUMER `agents/position_monitor.py:_python_decision` (:74-108): HOLD for any
  −1R<pnl_r≤+0.7R w/o swap, regardless of a concurrent TIGHTEN. Only live readers of
  probe_decisions are OFFLINE (/debate digest, dashboard). Audit's file pointer was
  off (it cited build_monitor_payload at recalc:460-480 = the call site; real builder
  is payload_builder.py:380); gap itself confirmed.

## DB EVIDENCE (data/probes.sqlite)
HOLD 22,266 / TIGHTEN 7,359 / HARVEST 5,465 / WIDEN 227. TIGHTEN rows = adverse
composite_lean −0.34..−0.54 on OPEN positions at pnl_r −0.01..−0.06 (exit_state=open).
~60% of closed HOLD decisions realized adverse R = "HOLD violated after the fact".
**probe_decisions.trail_mult is NULL for every action** — the engine emits the ACTION,
not a concrete tighter trail; a fix must SYNTHESIZE the tighten width (a trading param).

## WHY DEFERRED (not a mechanical bug like G7)
Real design decisions, each a trading-behaviour/architecture choice (mandate → care):
1. G7 `adaptive_exit_gate` is structurally WIDEN-ONLY (`_production_recalc.py:621`
   "only ever looser in the winner's favour") — ADJUST_EXIT cannot tighten today;
   reusing it for a tighten changes its contract.
2. No ready trail_mult → must synthesize a tighten magnitude (param).
3. HOLD→EXIT_NOW would CLOSE adverse-but-unstopped positions (recoverable) — tension
   with aggressive/flow_not_block; the prompt itself requires precise-exit, not a block.
4. Net-new cooldown / `probe_last_consulted_ts` state machinery.
5. ORDERING: `run_precise_exit` (G7 FSM trail) runs at recalc:438 BEFORE G6 at :450 in
   the SAME pass → a G6-set tighten directive lands NEXT pass (must be designed for).

## LOCKED + NEXT
- TEST `tests/test_g6_probe_tighten_gap.py`: 2 pass (HOLD baseline + gap: G6 ignores a
  payload `probe_action=TIGHTEN`), 1 xfail strict (FIXSPEC: HOLD must escalate to a
  tighter-exit directive once built — flips loud when fix lands).
- FIX SHAPE (recommended, flow_not_block): recalc reads latest open probe action from
  state.probe_conn (fail-open) → thread `probe_action` onto monitor_payload → G6 HOLD +
  TIGHTEN → feed a TIGHTER trail to G7 (exit TIMING), cooldown-gated. NOT HOLD→EXIT_NOW,
  NOT size-cut; −1.0R rail + entry side untouched.
- DECISION OWED (Jin-surface / /debate): tighten magnitude, cooldown, gate ownership,
  EXIT_NOW-vs-tighter-trail. Then build→fresh-Claude review→supervised deploy.

mandate_ok=true · 거부키워드 0 · G7 sibling shipped (82c31b4).
