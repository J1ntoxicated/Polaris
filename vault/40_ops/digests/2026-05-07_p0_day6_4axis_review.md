---
type: digest
status: active
date_created: 2026-05-07
tags: [polaris, p0-sprint, day6, codex-review, 4-axis]
related: [[ADR-003]], [[ADR-004]], [[ADR-007]], [[layer-2-per-gate-pipeline]]
---

# Polaris P0 Day 6 — 4-axis review (codex external)

## Summary

7-round codex review of P0 Day 6 + Day-3-era debt. Final = **APPROVE** (R7).

| Round | Verdict | Failed axes | Fixes |
|---|---|---|---|
| R1 | REJECT_WITH_FIXES | 1, 3 | ignite_p1 contract doc + 3 hardcodes (CELL_POOL_MIN_N_EFF, PNL_R_USD_DENOM) |
| R2 | REJECT_WITH_FIXES | 1, 4 | G8 P0 spec violation (Haiku used) → orchestrator phase guard |
| R3 | REJECT_WITH_FIXES | 2 | G8 P1 should be Sonnet not Haiku → SONNET_MODEL routing |
| R4 | REJECT_WITH_FIXES | 3 | 3 reflector hardcodes → LESSON_MAX_TOKENS / LESSON_PROMPT_TRADE_TRUNCATE_CHARS / DEFAULT_TIMEOUT_SEC |
| R5 | REJECT_WITH_FIXES | 1, 2 | P0 Python template + Δ clamp + 2 more hardcodes (LESSON_SOFT_MODE_TRADE_COUNT, LESSON_RECENT_TRADES_MAX, LESSON_DELTA_CLAMP_P0) |
| R6 | REJECT_WITH_FIXES | 1, 2 | P1 Δ rail expansion + watcher hardcode preempt (3 gates) |
| **R7** | **APPROVE** | (none) | — |

## Final state

- pytest: **408 pass** (+5 phase/template regression tests)
- mypy --strict: 91 src files clean
- ruff: all checks pass
- smoke (6 s 2-tick): 9 full_pipeline_runs · 9 sized (100%) · 12 fills_persisted · gate_pass_counts={6: 9, 7: 9}

## Axis breakdown (final)

### Axis 1 — Plan/ADR/Phase 0 spec 정합 PASS
- ignite_p1 contract: bootstrap + delegation (smoke owns L0/L2/L6/L7) — doc ↔ 구현 정렬
- G8 P0 = Python template (deterministic lesson + clamped Δ); G8 P1 = Sonnet (model_used="sonnet")
- Δ clamp rail: P0 ±0.03, P1 ±0.10 (ADR-007 §learner_delta)
- 거부 키워드 sweep: 12주 / 90d gate / regulatory / professional risk / monthly review / regrets / posture standard → **0건**

### Axis 2 — Dead code 0건 PASS
- 16 new + 7 modified: 모든 함수/심볼 호출 site 보유
- payload_builder 5 builders + helpers → smoke_paper_loop + tests/test_pipeline_full_g4_g7.py
- fills_persist persist_fill / read_recent_fills / make_fill_id → 모두 호출
- ignite_p1 mode flags (paper / dry-run / full-pipeline / real-roundtrip) → 모두 wire

### Axis 3 — Hardcode 0건 PASS
신규 모듈 상수 SSOT 11개:
- `CELL_POOL_MIN_N_EFF` (5.0), `PNL_R_USD_DENOM` (50.0) — payload_builder + dashboard 공유
- `MIN_OKX_NOTIONAL_USD` (10.0), `MIN_CAPITAL_LOT` (1.0) — round-trip
- `SONNET_MODEL`, `HAIKU_MODEL`, `DEFAULT_TIMEOUT_SEC` — _haiku_client
- `LESSON_*` 7개 (CONFIDENCE_FLOOR / SOFT_MODE_TRADE_COUNT / RECENT_TRADES_MAX / DELTA_CLAMP_P0 / DELTA_CLAMP_P1 / MAX_TOKENS / PROMPT_TRADE_TRUNCATE_CHARS / SOFT_SCALAR_MAX) — post_trade_reflector
- `WATCHER_MAX_TOKENS`, `VALIDATOR_MAX_TOKENS`, `VALIDATOR_RECENT_TRADES_MAX`, `SCANNER_MAX_TOKENS`, `SCANNER_PROMPT_UNIVERSE_CAP` — gate-local

### Axis 4 — AI 적절 사용 PASS
- payload_builder / fills_persist / round-trip / ignite_p1 = Python only
- LLM call sites = G1 (Haiku) / G3 (Haiku) / G4 (Haiku) / G8 (P0 Python template, P1 Sonnet)
- _smoke_haiku_stub gate-aware decision routing OK
- `GateOrchestrator(phase="P0")` (default) suppresses Haiku client for G8 — **architectural** P0 invariant

## Day 6 산출 (변경 없음, R1-R6 fix 추가)

### Modified
- `polaris/storage/schema.py` — DDL_FILLS + 3 indexes
- `polaris/scripts/smoke_paper_loop.py` — full_pipeline=True orchestration
- `polaris/scripts/dashboard_v0.py` — recent_fills reads `fills` first, orders fallback (CELL_POOL_MIN_N_EFF parameterized)
- `polaris/core/data/ingest.py` — Day 1 P2 fix (materialize generator + recompute @ batch max_ts)
- `polaris/core/pipeline/__init__.py` — export 5 builders

### New
- `polaris/core/pipeline/payload_builder.py` — 5 builders + helpers + 2 SSOT constants
- `polaris/core/data/fills_persist.py` — Fill persistence
- `polaris/scripts/_smoke_real_roundtrip.py` — OKX + Capital demo round-trip
- `polaris/scripts/ignite_p1.py` — P1.0 ignition entry point (R1 contract doc fix)
- `polaris/scripts/smoke_day6_full_pipeline.py` — full-pipeline smoke runner

### R1-R6 Day-3 era touched (debt cleanup surfaced by 4-axis lens)
- `polaris/core/pipeline/gate_orchestrator.py` — `phase: str = "P0"` arg, P0 G8 client suppression, P1 G8 Sonnet routing
- `polaris/core/pipeline/agents/__init__.py` — docstring G8 mapping update
- `polaris/core/pipeline/agents/_haiku_client.py` — SONNET_MODEL constant + DEFAULT_TIMEOUT_SEC export
- `polaris/core/pipeline/agents/post_trade_reflector.py` — Python template P0 path (write lesson + clamp Δ), 7 SSOT constants, P0/P1 Δ rail
- `polaris/core/pipeline/agents/pre_entry_watcher.py` — WATCHER_MAX_TOKENS + LESSON_RECENT_TRADES_MAX
- `polaris/core/pipeline/agents/signal_validator.py` — VALIDATOR_MAX_TOKENS + VALIDATOR_RECENT_TRADES_MAX
- `polaris/core/pipeline/agents/universe_scanner.py` — SCANNER_MAX_TOKENS + SCANNER_PROMPT_UNIVERSE_CAP

## Test additions (5 new regression tests)

- `tests/test_layer2_pipeline.py`:
  - `test_g8_p0_phase_forces_python_even_with_haiku_client`
  - `test_g8_p1_phase_forwards_sonnet_model`
  - `test_orchestrator_rejects_invalid_phase`
  - `test_g8_p0_python_template_writes_lesson_row`
  - `test_g8_p0_python_template_clamps_delta_to_rail`

## Verdict

**APPROVE @ iteration 7** — Day 6 P0 + collateral Day 3 spec debt cleared. Ready for Day 7 (P1.0 24 h watchdog).

## Sources
- codex R1: `/tmp/p0_day6_4axis_codex_r1.md`
- codex R2-R7: stdout (session ids in raw chat log)
