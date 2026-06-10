---
type: digest
status: active
date_created: 2026-05-07
tags: [polaris, p1-sprint, logging, codex-review, 4-axis, security]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[layer-2-per-gate-pipeline]], `gate_orchestrator`, `setup_polaris_logging`, [[aggressive-bias]]
---

# Polaris P1 verbose-logging patch — 4-axis review (외부 codex)

## Summary

Sub-agent (a9a508d6) added 60 logger sites in 18 files + new `polaris/logging_config.py` (88 LOC) + 7 tests, then re-launched the 24h paper loop without external review. This review enforced the `feedback_code_review_codex_external` mandate (작성 ≠ 리뷰) post-hoc.

| Axis | Verdict | Findings |
|---|---|---|
| 1 — Spec + Security | **PASS (after fixes)** | KILL level discipline + `defensive` source comment fixed; rotation deferred to operator (acceptable for demo) |
| 2 — Dead code | **PASS (after fix)** | `smoke_paper_loop.main()` now wires `setup_polaris_logging` |
| 3 — Hardcode | **PASS** | DEFAULT_FORMAT/DATEFMT/LOG_FILE Final constants; tag prefixes acceptable Python convention |
| 4 — AI usage | **PASS** | G8 P0=python is correct (my prompt was wrong); fallback labels accurate; no Haiku payload leaked |

**Security verdict (codex):** `NO SECRETS LEAKED`
- OKX api_key/secret/passphrase/OKX-ACCESS-SIGN — never in any logger call
- Capital X-CAP-API-KEY/CST/X-SECURITY-TOKEN/identifier/password — never logged
- ANTHROPIC_API_KEY — never logged
- venue adapters log only `clOrdId`, `ordId`, `dealId`, `dealRef`, `inst`, `sz`, `px`, `side`, `code`, `msg[:160]`, `account_id`, `deadline_ts`
- live runtime log file (789 KB after 12 min) grep-clean for all secret keywords

## Live verification (PID 57257)

- ISO-UTC ms format works: `2026-05-06T22:29:39.607Z [INFO] polaris.core.pipeline.gate_orchestrator:206 [G3 okx/BTC-USDT] decision=PASS model=haiku ...`
- T4 sizing log emits all 9 components (base/cont/tier/cell/list/proposed/cap/final/binding/notional/lev/kelly/cold) — Phase 1 sizing observability ✓
- Cell matrix log shows quartile + multiplier ✓
- Learner update shows n_eff/wins_eff transition ✓
- Projected log volume: ~95 MB/24h (acceptable for demo; rotation deferred to operator)

## Fixes applied (no process restart)

1. **`polaris/core/pipeline/gate_orchestrator.py:205-232`** — KILL decision now logs at `ERROR` level (not INFO), errored handler at WARNING, success at INFO. `[orchestrator] pipeline KILL` line elevated to `logger.error`. Aligns with spec: ERROR = block/abort.
2. **`polaris/scripts/smoke_paper_loop.py:760-790`** — `main()` now calls `setup_polaris_logging(level, log_file)` with `--verbose` and `--log-file` CLI args (defaults to `DEFAULT_LOG_FILE`). Standalone `python3 -m polaris.scripts.smoke_paper_loop` runs no longer silently lose all 30+ INFO/DEBUG logger calls.
3. **`polaris/core/isolation/circuit_breaker.py:271`** — source comment "(defensive)" → "schema invariant guards the writer". Aggressive Bias charter compliance (no defensive language anywhere in source).

## Verification

- `pytest tests/`: **415 passed** (no regressions; +0 new tests)
- `ruff check`: clean (auto-organized smoke_paper_loop imports)
- `mypy --strict`: clean for all 3 edited files

## Open items (deferred, not blocking)

- Log file rotation: 95 MB/day projected. Acceptable for demo; operator handles via `logrotate` or manual truncate. logging_config docstring already documents this contract.
- Live PID 57257 still uses old AST (Python doesn't hot-reload). Fixes apply on next restart. No restart required during this review per Jin's directive (do not interrupt 24h loop).

## Iteration count

1 codex round (effort=high, model=gpt-5.3-codex). Verdict transitioned REJECT → APPROVE after the 3 fixes above.

## Final verdict

**SHIP** — 24h loop continues uninterrupted. Hot-fixes queued; will take effect on next restart. Security verified clean.
