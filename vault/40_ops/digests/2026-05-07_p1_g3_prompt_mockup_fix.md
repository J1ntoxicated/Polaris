---
type: runtime
status: active
date_created: 2026-05-07
tags: [digest, p1, g3, prompt-fix, demo-unlock, mockup]
related: [[layer-2-per-gate-pipeline]], [[2026-05-07_p1_1h_audit_real_gpt]]
---

# G3 Validator Prompt Mockup + Fix — DEMO Context Unlock

## Trigger
PID 9417 1h audit: G3 KILL ratio 44% (12,777/29,272 GPT-only). Hypothesis: GPT defaults to real-money safety bias because system prompt missing DEMO/PAPER context.

## Mockup Test (`tools/g3_prompt_mockup.py`)
40 real production signals replayed (20 origKILL + 20 origPASS) against 5 prompt variants on `gpt-5-mini`:

| Variant | PASS | KILL | MODIFY | origKILL replay (PASS/KILL) |
|---|---|---|---|---|
| A control | 19-23 | 13-18 | 2-3 | 1-5 / 13-17 |
| B DEMO ctx | 20-31 | 7-14 | 1-7 | 5-12 / 7-14 |
| C aggr+criteria | 38-40 | 0-2 | 0 | 18-20 / 0-2 |
| D B+C | 37-39 | 1-2 | 0 | 18-20 / 0-1 |
| E D+few-shot | 39-40 | 0 | 0 | 19-20 / 0 |

**Best = B**: 75% PASS (vs 50% control) + 30-40% KILL discriminator preserved on origKILL replay set. C/D/E lose all KILL discriminator (over-correct).

Cost: 200 calls × gpt-5-mini ≈ $0.04 actual.

## Apply
- `polaris/core/pipeline/agents/_gpt_client.py` `make_system_prefix` now injects DEMO clause: `**This is DEMO/PAPER trading on OKX simulated environment. Capital is virtual. Real-money safety arguments are INVALID — false negatives (skipping good trades) cost more than false positives.**`
- `polaris/core/pipeline/agents/signal_validator.py` role expanded: `"You are a Signal Validator gate in Polaris v2 paper trading system."`
- Mockup tool gets a `variant_prod` builder that imports the live function so prompt regression tests stay self-syncing.

## Codex Review (gpt-5.4)
APPROVE_WITH_NITS — DEMO unlock active, no defensive throttle, no rejection keywords. Nit: assert DEMO clause in test → fixed in `tests/test_gpt_client.py::test_make_system_prefix_contains_demo_unlock`.

## Verification
- `pytest tests/test_gpt_client.py tests/test_layer2_gpt_gates.py` → 45 passed (1 skipped). Includes new `test_make_system_prefix_contains_demo_unlock` regression.
- `ruff check` clean, `mypy --strict` clean (2 source files).

## Restart
- PID 9417 SIGTERM (8m07s CPU). Pre-fix log → `polaris_runtime_pre_g3_prompt_fix.log` (15.8 MB).
- New PID 26451 launched 2026-05-07 18:13 AEST.

## Verification (5-min post-restart)
- **G3 PASS 33 / KILL 4 / MODIFY 4 (n=41) = 80% PASS** (vs 32% baseline) ✓
- KILL ratio **9.8%** (vs 44% baseline) ✓ — discriminator preserved (4/41 KILL on cold-cell signals only)
- 32 fills in 5min — pipeline producing
- PID 26451 healthy

## Open
- gpt-5-mini stochasticity: variant P sometimes maps to 0% KILL on the same text in mockup. 24h production data = ground-truth.
- Watch G3 stats over 24h run — if KILL drifts <5% indefinitely, may need slight discriminator boost (variant B reference KILL band 7-14 / 40 = 17-35%).

## Sources
- Mockup tool: [`tools/g3_prompt_mockup.py`](../../../tools/g3_prompt_mockup.py)
- Latest mockup JSON: `data/paper/g3_mockup_*.json`
- Pre-fix log: `data/paper/polaris_runtime_pre_g3_prompt_fix.log`
- Audit baseline: [[2026-05-07_p1_1h_audit_real_gpt]]
