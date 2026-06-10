---
type: digest
status: active
phase: P1
date_created: 2026-05-07
tags: [digest, p1, migration, haiku, gpt, openai, llm, layer-2]
related: [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[layer-2-per-gate-pipeline]], [[_NOW]]
reviewed_by: claude (self-audit + smoke acceptance + codex external review queued)
---

# P1 Haiku → GPT Migration — 2026-05-07

## Mandate (Jin 2026-05-07)

- **Anthropic API = Claude Code (Opus 4.7) 개발용만**, code 내부에서 사용 X.
- **Code 내부 LLM = OpenAI GPT** (gpt-5-mini for P0 G1/G3/G4, gpt-5.5 for P1 G6/G7/G8).
- **Codex CLI review = 그대로 codex** (gpt-5.4 / gpt-5.5 model split).
- Anthropic credit 부족 → claude-haiku-4-5-20251001 HTTP 400 → G3 fail-CLOSED → 1h ignite_p1 0 fills.

## Code surface (LOC + count)

| File | Status | LOC |
|---|---|---|
| `polaris/core/pipeline/agents/_gpt_client.py` | NEW | 326 |
| `polaris/core/pipeline/agents/_haiku_client.py` | DELETED | -236 |
| `polaris/scripts/_smoke_gpt_stub.py` | NEW | 94 |
| `polaris/scripts/_smoke_haiku_stub.py` | DELETED | -95 |
| `polaris/core/pipeline/agents/{universe_scanner,signal_validator,pre_entry_watcher,post_trade_reflector}.py` | imports + labels | ~30 lines diff |
| `polaris/core/pipeline/gate_orchestrator.py` | model dispatch | ~10 lines diff |
| `polaris/core/pipeline/gate_state.py` | model_used doc comment | 1 line |
| `polaris/scripts/{production_paper_loop,smoke_paper_loop,smoke_production_paper_loop,smoke_day3,_production_pipeline}.py` | factory wiring | ~30 lines diff |
| `tests/test_gpt_client.py` | NEW | 320 |
| `tests/test_layer2_haiku_gates.py` → `test_layer2_gpt_gates.py` | RENAME + symbol update | ~10 lines diff |
| `tests/test_layer2_pipeline.py` | mock class rename + assertion update | ~15 lines diff |
| `tests/{test_day6,test_day7,test_paper_loop_smoke,test_pipeline_full_g4_g7,test_production_paper_loop}.py` | bulk rename | ~10 lines diff |

**Net**: -331 lines (haiku files) + +740 lines (gpt files + tests) = +409 net. Tests added: 18 new (test_gpt_client.py).

## Wire format

- **Endpoint**: `https://api.openai.com/v1/chat/completions` via `httpx.AsyncClient` (no openai SDK dep).
- **Token budget**: gpt-5.x uses `max_completion_tokens`, gpt-4.x uses `max_tokens` — auto-dispatched by `_is_gpt5(model)`.
- **`reasoning_effort='minimal'`** applied to gpt-5.x calls — required to prevent reasoning tokens from consuming the entire budget. Without this fix: G3 returned `text=''` → schema violation → KILL. After fix: G3 returns valid JSON in <2s.
- **Anthropic-shape shim**: `GPTClient.messages.create(...)` adapts to OpenAI POST internally so existing call sites unchanged.

## Smoke acceptance (real OpenAI hit, 30s tick=5)

```
G3:  23 PASS / 2 MODIFY / 4 KILL → 86% PASS rate (was 0% with broken Haiku)
G4:   8 PROCEED / 17 KILL → 32% PROCEED
fills_okx: 9 (8 open + 1 close) — REAL round-trip
g1/g2/g8: 29 / 29 / 1
sized_count: 8
fault_events: 0
pass_count: 7/7 ✓
```

Latency observed: gpt-5-mini ~1.4-2.0s typical, p99 ~5s; gpt-5.5 ~1.0-1.2s.

## Test results

- `pytest tests/` → **461 passed, 1 skipped** (live API gated by `POLARIS_GPT_LIVE=1`).
- `ruff check polaris/ tests/` → **All checks passed**.
- `mypy --strict polaris/core/pipeline/agents/_gpt_client.py polaris/core/pipeline/agents/{universe_scanner,signal_validator,pre_entry_watcher,post_trade_reflector}.py polaris/scripts/_smoke_gpt_stub.py` → **Success: no issues found in 6 source files**.
- Live OpenAI hit verified: `pytest tests/test_gpt_client.py::test_gpt_complete_real_api -xvs` → PASSED in 1.18s.

## Aggressive-bias preservation

- G1 (Universe Scanner) fail-OPEN to top-by-vol — unchanged.
- G3 (Validator) fail-CLOSED to KILL on schema violation / network error — unchanged.
- G4 (Pre-Entry Watcher) fail-CLOSED + fast-path PROCEED — unchanged.
- G8 (Post-Trade Reflector) fail-OPEN, drop lesson on low confidence — unchanged.
- Confidence floor (0.70), soft-mode dampening (25%, <100 trades), Δ clamp (P0=±0.03 / P1=±0.10) — unchanged.

## Security

- `OPENAI_API_KEY` read once in `GPTClient.__init__`, stored on instance, used only in the `Authorization: Bearer ...` HTTP header.
- `__repr__` returns `GPTClient(model='gpt-5-mini')` — no api_key. Test asserts.
- Error messages: `repr(exc)` from httpx — does NOT contain the api_key (httpx redacts headers from exception strings; verified empirically on 402 path).

## Codex external review (2026-05-07, gpt-5.4)

**Verdict R1**: REJECT_WITH_FIXES, no P0 blockers, 4 P1 fixes:

1. **`reasoning_effort` override hook** — `_GPTMessages.create()` accepts it but `call_gpt()` did not forward → hook was nominal not real. **FIXED**: `call_gpt(reasoning_effort=...)` now forwards through.
2. **G8 hardcoded model name** — `post_trade_reflector.py` derived label from literal `"gpt-5.5"` / `"gpt-5-5"` strings. **FIXED**: predicate now compares against `GPT_P1_MODEL` / `GPT_P0_MODEL` constants (matches base id + dated variants like `gpt-5.5-2026-04-23`).
3. **Error sanitization placeholder** — `repr(exc)` persisted raw. **FIXED**: `_sanitize_error_text()` redacts any `sk-...` or `Bearer ...` substring before persistence.
4. **P1 G8 dispatch bypass** — `_production_close.py` hardcoded `client=None` for G8 → paper harness could never exercise GPT P1 lesson branch even with `phase=="P1"`. **FIXED**: `close_oldest_with_real_pnl(gpt_client=, phase=)` threads through; `run_production_paper_loop(phase=)` exposes the flip; G8 forwards `GPT_P1_MODEL` + client when P1.

P2 cosmetic debt (deferred): `haiku` / `haiku_client` kwarg names still litter `production_paper_loop.py` / `_production_pipeline.py` / `gate_orchestrator.py`. Functional but mislabels the runtime model.

5 new tests added (`test_sanitize_error_text_*`, `test_call_gpt_forwards_reasoning_effort_kwarg`, `test_call_gpt_default_does_not_set_reasoning_effort_kwarg`, `test_call_gpt_error_path_sanitizes_api_key`).

## Restart status

- Old loop PID 87687 stopped pre-migration (Anthropic credit fail, 0 fills/h).
- 24h re-launch: post-codex-fixes (P1 1-4 all addressed).

## Forward links

- [[ADR-004-per-gate-ai-pipeline|ADR-004]] §Phase: P0 LLM tier = OpenAI GPT (was Anthropic Haiku).
- `feedback_anthropic_dev_only_openai_runtime` — new memory rule.
- Ignite_p1 24h watchdog: pending APPROVE.
