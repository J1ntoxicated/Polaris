---
type: research
status: built-reviewed
date_created: 2026-06-25
date_updated: 2026-06-25
tags: [g6, g7, probe, exit, tighten, flow-not-block]
---

# G6 probe-TIGHTEN consumer — FIX LANDED (flag-gated)

Builds on [[g6_probe_tighten_gap_2026-06-25]] (gap locked) + [[g7_tick_trail_atr_scale_2026-06-25]]
(G7 width anchor). /debate-converged design (GPT a68dfca2 + Gemini a55e65bd): probe
TIGHTEN → G6 tighten_intent → G7 tighter-trail. NO EXIT_NOW (recoverable position block
= flow_not_block 위배). ratchet loosening 금지 유지. builder≠reviewer (fresh-Claude
APPROVE-WITH-NITS, 0 blocker).

## SHIPPED (1st increment, TDD)
- **G7 tighten rail** `adaptive_exit.py`: `can_tighten_exit` (pure, ratchet-safe — long
  proposed>current / short proposed<current, else reject) + `_python_tighten` (ADJUST_EXIT
  tighter stop | HOLD) + dispatch `tighten_proposal` branch (consumed BEFORE widen; absent
  → widen path byte-identical). widen-only 함수/거동 100% 보존.
- **G6 consumer** `position_monitor.py`: adverse HOLD band + `probe_action=="TIGHTEN"` →
  ADJUST_EXIT(`tighten_intent`). stop_hit(−1.0R)/swap/widen_window 우선순위 보존, 최하위
  분기. flag `POLARIS_G6_PROBE_TIGHTEN` (default OFF → byte-identical).
- **Bridge** `probes/tighten_intent.py` (new): `latest_probe_tighten` (probes.sqlite
  사이드카 freshest OPEN row, fail-open) + `synth_tighten_stop` (anchor peak/trough −
  trail_mult·ATR; probe trail_mult NULL → ATR-N% fallback = `EXIT_HARVEST_TRAIL_MULT`;
  not-tighter → None).
- **Wiring** `_production_recalc.py`: probe_action 주입(flag-무관, G6가 flag로 게이트) +
  tighten_intent → synth → G7 `tighten_proposal` + `tightening_applied` → stop UPDATE
  (다음 tick `_trailing_stop` ratchet이 max/min으로 loosening 봉쇄 — 3중 방어).

## INVARIANTS
trading core (`exit_engine.py` evaluate_exit/_trailing_stop) UNTOUCHED · −1.0R rail / size
/ entry / 9-stack 무관 · EXIT_NOW 미발화 · ratchet monotone. mypy --strict 0 / ruff clean
/ 31 신규+수정 테스트 pass. 거부키워드 0.

## DEFERRED (flag/next increment)
- tick-path probe→trail 직접 공급 (현재 tick은 strategy 기반 trail_mult; tighten consumer
  는 bar-recalc G6/G7 경로만). ExitEngine.compose에 trail_mult 합성(=ADR-012 Slice 2,
  /debate-gated) 미착수 — observe-only 유지, 이 fix는 사이드카 READ만.
- debounce: per-position override 카운터/last-override ts = positions-schema 추가 deferred
  (`TIGHTEN_DEBOUNCE_DEFERRED_*` placeholder; synth+ratchet monotone이라 thrash 무해).

## SUPERVISED DEPLOY NOTE
`POLARIS_G6_PROBE_TIGHTEN=1` 한 포지션-track부터. 모니터: `atr_trail_stop` adverse-R ↓ ·
give-back ↓ · `recalc_tighten_applied` 카운터 · 닫힌 HOLD adverse% (gap의 60% baseline 대비).
flow_not_block: 타이트닝=정밀 엑싯, −1.0R/size/entry 불변.

mandate_ok=true · 거부키워드 0
