---
type: ADR
adr_id: ADR-011
aliases: [ADR-011]
status: active
date_created: 2026-06-22
tags: [adr, ai-free, gates, cutover, deterministic, gpt-shadow]
related: [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-008-7-strategies-signal-generator-role|ADR-008]], [[system_design_audit_2026-06-22]]
reviewed_by: builder≠reviewer (W3 adversarial audit) + Jin
supersedes_part_of: [[ADR-004-per-gate-ai-pipeline|ADR-004]]
---

# ADR-011 — W3 AI-Free In-Loop Cutover

## Decision
In-loop trading decisions are **deterministic Python primary**. **No Anthropic/Claude in runtime; in-loop GPT calls = 0.** GPT runs **shadow + sentinel only** (parallel observe-only, never decides a live trade). Supersedes the in-loop LLM-gate part of [[ADR-004-per-gate-ai-pipeline|ADR-004]].

## What changed (W3 cutover, live)
- **G3 Signal Validator / G4 Pre-Entry Watcher / G7 Adaptive Exit** — were Anthropic Haiku/Sonnet gates in ADR-004; now **deterministic Python primary**.
- The prior deterministic *shadow* that ran in parallel is now the *primary* decision path.
- **GPT (OpenAI)** = shadow agreement + sentinel/live-audit sidecar (observe-only). **Anthropic = blocked for runtime** (Claude Code dev-tooling only).
- In-loop GPT calls = **0** (verified at cutover). All other gates were already deterministic Python.

## Rationale
- Latency + cost + nondeterminism of in-loop LLM unfit for tick/bar cadence.
- AI-free core = reproducible, auditable, replay-stable measurement (precondition for the M→S→D→R program in `loop_state.md`).
- alt-data / regime / evidence stay as **signal & shadow** inputs — never block/throttle (flow_not_block preserved).

## Guardrails
- DEMO/PAPER only · aggressive bias preserved (no size-cut / block / throttle).
- Shadow/sentinel divergence = telemetry only; never gates a live trade.
- Provider routing: Anthropic runtime = forbidden; GPT = shadow/sentinel; Gemini = /debate cross-check.

## Sources
- W3 cutover commits (deterministic G3/G4/G7 primary; in-loop GPT=0).
- [[system_design_audit_2026-06-22]] (AI-free core = sound, wired as designed).
- [[ADR-004-per-gate-ai-pipeline|ADR-004]] (in-loop LLM-gate portion now superseded).
