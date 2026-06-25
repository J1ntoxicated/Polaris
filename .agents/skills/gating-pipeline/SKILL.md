---
name: gating-pipeline
description: Use to orchestrate the 8-gate pipeline (G1 Universe Scanner -> G2 Strategy Signal -> G3 Signal Validator -> G4 Pre-Entry Watcher -> G5 Entry Sizer -> G6 Position Monitor -> G7 Adaptive Exit -> G8 Post-Trade Reflector). G3/G4 = OpenAI GPT gates with a deterministic shadow running in parallel; all other gates = deterministic Python. Drives the signal -> validated -> watched -> sized -> active -> monitored -> exited -> reflected lifecycle.
---

# gating-pipeline (P0 skill)

## When to use
- Main paper loop tick (bar 진입 경로 + live recalc per-position 패스)
- Active position monitor / exit cycle
- Trade close 후 reflector 트리거

## Entry points (code = SSOT)
- Orchestrator: `polaris/core/pipeline/gate_orchestrator.py`
- Gate agents: `polaris/core/pipeline/agents/` (universe_scanner … post_trade_reflector)
- Production wiring: `polaris/scripts/_production_run_signal.py` (진입) +
  `polaris/scripts/_production_recalc.py` (per-position G6→G7 패스)

## Gate 구현 상태 (2026-06-11 코드 검증)
| Gate | 구현 | 비고 |
|---|---|---|
| G1 universe-scanner | deterministic scored vol-ranker | GPT 제거 (ai_conductor P1). 항상 PASS — focus selector |
| G2 strategy-signal | Python pass-through | raw_signal 승격만 |
| G3 signal-validator | GPT + deterministic shadow | PASS/KILL/MODIFY · fail-closed |
| G4 pre-entry-watcher | GPT + deterministic shadow | PROCEED/KILL · python fast-path skip |
| G5 entry-sizer | deterministic Python | `polaris/core/sizing` `compute_size` (T4) |
| G6 position-monitor | deterministic Python | GPT 제거 (ai_conductor P3) — client 파라미터 inert |
| G7 adaptive-exit | deterministic Q9 widening rails | G6 ADJUST_EXIT(winner-widen) 윈도 + phase P1 에서만 GPT 분기 잔존 |
| G8 post-trade-reflector | deterministic Python template | GPT 제거 (ai_conductor P2) — `ai_lessons` DB 가 SSOT |

## SSOT 포인터 (수치/모델명 하드코딩 금지 — 여기서 읽는다)
- GPT 모델명·타임아웃: `polaris/core/pipeline/agents/_gpt_client.py`
  (`GPT_P0_MODEL` / `GPT_P1_MODEL`, env `POLARIS_GPT_P1_MODEL`)
- Shadow 규칙: `agents/_shadow_rules.py` (병행 로깅, 파이프라인 불간섭) ·
  분석은 `agents/shadow_acceptance.py` (read-only)
- 게이트별 fail-mode·rail 상세: 각 agent 모듈 docstring

## Outputs
- gate event log (`gate_event_log.py` → SQLite)
- shadow log (`gate_shadow_events` 테이블)

## Failure handling
- G3/G4 GPT timeout / parse error → fail-closed KILL
- G6/G7 (포지션 보호 게이트) → fail-open HOLD

## Cross-ref
- [[ADR-004]] per-gate pipeline · `vault/30_components/layer-2-per-gate-pipeline.md`
- skills: signaling-strategies (G2 입력) · sizing-positions (G5) · running-paper-loop (구동)
