---
type: ops
status: active
date_created: 2026-05-07
tags: [digest, day9, p0, f1, f2, g6, g7, g8, live-recalc, gpt-wire, per-gate-ai]
related: [[ADR-004]], [[layer-2-per-gate-pipeline]], [[layer-6-live-recalc]], [[2026-05-07_p1_full_audit]]
---

# Day 9 F1+F2 — G6/G7/G8 GPT Decision Wire + Live Recalc Loop

## Why

Pre-fix audit `[[2026-05-07_p1_full_audit]]` (DB live evidence, PID 26451 2h13m):
- G6 HOLD 9,682/9,682 (100%) `model_used="python"` — AI 미작동
- G7 HOLD 9,682/9,682 (100%) `model_used="python"` — AI 미작동
- G8 phase=P0 default → ai_lessons 726 = template only
- G6/G7 fired ONLY at entry, never re-invoked while position open
- Close path = FIFO `_close_oldest_trade`, unlinked from G6 EXIT_NOW

= Jin vision §2 §7 (per-gate AI supervisory + 자가 진화 + 자가 correcting) 정면 위반.

## What changed

### F1 — G6/G7/G8 GPT Decision Wire
- `polaris/core/pipeline/agents/position_monitor.py` — added GPT P1 branch (Sonnet/gpt-5.5).
  - Hard rails (loss stop + Q8 swap) fire BEFORE GPT call (Python fast-path).
  - GPT decides among `HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY` for the open band.
  - DEMO/PAPER unlock prompt + Aggressive bias preserved.
  - Fail-open: parse error → Python rules (never KILL).
  - `model_used="gpt_p1"` honest label.
- `polaris/core/pipeline/agents/adaptive_exit.py` — added GPT P1 branch.
  - GPT decides `HOLD / WIDEN / TIGHTEN / EXIT_NOW`.
  - **TIGHTEN reversed to HOLD** when proposed stop is closer than default ATR floor (Q9 conservative trap avoidance, Jin aggressive bias mandate).
  - **WIDEN only accepted** when proposed_stop is FARTHER than current.
  - Fall through to deterministic Q9 widening rail on fallback.
- `polaris/core/pipeline/agents/post_trade_reflector.py` — already had GPT P1 branch; default phase flipped to "P1" in production_paper_loop.
- `polaris/core/pipeline/gate_orchestrator.py` — phase-aware G6/G7 dispatch.
- `polaris/scripts/_production_pipeline.py` — phase forwarded to entry-time G6/G7 + orchestrator.
- `polaris/scripts/production_paper_loop.py` — default phase=P1 + `--phase` CLI arg.

### F2 — Live Recalc Loop (G6/G7 per-tick GPT)
- `polaris/scripts/_production_recalc.py` (NEW, 341 LOC) — `recalc_active_positions()`.
  - Per-tick sweep over every active position (positions JOIN fills entry → bars last_price).
  - Build market_view + monitor_payload → call G6 with GPT client.
  - `EXIT_NOW` → `close_specific_position(position_id)` (NOT FIFO oldest).
  - `SWAP_STRATEGY` → Layer 6 SSOT `evaluate_strategy_swap`.
  - `ADJUST_EXIT` → call G7 with GPT client → persist gate_event.
  - Fault-isolated per position.

### F2.b — Close path AI-driven (specific contribution_id)
- `polaris/scripts/_production_close.py` — `close_specific_position(position_id)` matches by exact ID.
- Shared body `_close_trade_with_real_pnl(trade, trade_idx)`; `close_oldest_with_real_pnl` delegates.
- `state.open_trades` mutated AFTER DB commit (R2 P1 ordering preserved).
- persist_fill uses `contribution_id=trade.position_id` (Day 8 P0 contract).

## Tests (41 new, 579 total pass)

- `tests/test_g6_position_monitor_gpt.py` (15 tests)
- `tests/test_g7_adaptive_exit_gpt.py` (11 tests)
- `tests/test_g8_reflector_phase_p1.py` (5 tests)
- `tests/test_live_recalc_loop.py` (10 tests)

Property-based:
- G6 decision always ∈ {HOLD, ADJUST_EXIT, EXIT_NOW, SWAP_STRATEGY} for any pnl_r ∈ [-5, +5]
- G6 compute_uPnL_R always finite

## Validation

- 41 new tests pass (15 G6 + 11 G7 + 5 G8 + 10 live recalc).
- Full pytest: **579 passed, 1 skipped** (was 492 baseline; +41 F1+F2 + ~46 from sibling F10/F11).
- mypy --strict polaris/: 105 source files clean.
- ruff: clean.
- 60s smoke (PID self-launched 21:54): 9 ticks · 53,280 bars persisted · 1 regime flip detected (YFI chop→bear_trend) · 0 faults · live_recalc summary line wired.

## Codex review

Codex round 1+2 (gpt-5.4) returned **0 findings against F1+F2**. The 2 findings surfaced (P2 pyproject pytest-asyncio missing dep / P3 Capital session.is_expired falsy timestamp) are pre-existing issues in modules untouched by this PR. APPROVE.

## Aggressive bias preservation

- 0 defensive throttles introduced.
- G7 TIGHTEN-to-HOLD only when stop CLOSER than floor (boundary preserves winner extension).
- G6 default = HOLD only on explicit GPT response or hard rail; ambiguous → Python rules (no premature KILL).
- DEMO unlock prompt in G6/G7/G8 prevents real-money safety bias.
- Hard loss rail (`pnl_r <= -max_loss_r`) fires BEFORE GPT call (deterministic).

## File budgets

| file | LOC |
|------|-----|
| position_monitor.py | 297 |
| adaptive_exit.py | 403 |
| _production_close.py | 410 |
| _production_recalc.py | 341 |
| _production_pipeline.py | 449 |

All ≤ 500.

## Follow-ups

- F11 supervise_strategies SSOT migration (already merged by sibling task).
- F10 strategy timeframe-aware bar feed (already merged).
- F12 dashboard equity hardcode 79000 SSOT 교정 (separate task).
- G6 SWAP_STRATEGY needs candidate discovery — currently degrades to HOLD when GPT requests SWAP without an explicit Q8-eligible candidate. P1 follow-up: surface cell-matrix top-strategy candidate inside the recalc payload.
- 24h smoke under P1 — measure `model_used="gpt_p1"` ratio (target ≥ 95% on G6/G7/G8 with no early loss-rail fires).

## Sources

- Spec: `vault/30_components/layer-2-per-gate-pipeline.md` (Q3 G6/G7 P1 + Q9 floor-only widening)
- Spec: `vault/30_components/layer-6-live-recalc.md` (Q1 5s cadence + dirty triggers)
- Decision: `vault/10_decisions/ADR-004-per-gate-ai-pipeline.md` (§Phase P0/P1)
- Audit: [[2026-05-07_p1_full_audit]]
- Mandate: Jin 2026-05-07 21:30 — DEMO unlock + Aggressive bias preserved
