# CLAUDE.md — Polaris v2

Behavioral guidelines + Polaris project context. **항상 숙지.**

## 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**
- State assumptions explicitly. If uncertain, ask.
- Multiple interpretations → present them. Don't pick silently.
- Simpler approach exists → say so. Push back when warranted.

## 2. Simplicity First
**Minimum code. Nothing speculative.**
- No features beyond asked. No abstractions for single-use code.
- No "flexibility" not requested. No error handling for impossible scenarios.
- 200 lines could be 50 → rewrite.
- "Senior engineer says overcomplicated?" Yes → simplify.

## 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**
- Don't "improve" adjacent code/comments/formatting. Don't refactor what isn't broken.
- Match existing style. Notice unrelated dead code → mention, don't delete.
- Remove orphans YOUR changes made unused. Pre-existing dead code stays unless asked.
- Test: every changed line traces directly to user request.

## 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- Multi-step tasks: state plan with `verify` checkpoints per step.
- Strong success criteria → loop independently. Weak → constant clarification.

(Source: `~/Downloads/CLAUDE.md` — 원본 read-only)

---

# Polaris v2 — Project Context

## Project
- Repo `/Users/jinyoon/Projects/Polaris` · DEMO/PAPER paper-trading bot · Reset 2026-05-06 (tag `pre-reset-2026-05-06`)
- Tracks: OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) + Capital CFD demo

## Absolute mandates (non-negotiable)
<absolute-mandates>
- **DEMO/PAPER ONLY** — 가상 자금. real-money 보수 논거 (regulatory / professional risk / capital protection / fund mandate / fractional Kelly is too aggressive in practice) **무효**
- **Aggressive bias preserved** — defensive throttle 거부. `aggressive_always_profit` / `no_defensive_param_dampen` / `no_block_filter_architecture` / `flow_not_block`
- **Anthropic = Claude Code (Opus) 개발용만** / Code 내부 LLM = **OpenAI GPT** (gpt-5-mini P0 / gpt-5.5 P1)
- **9-stack collapse 영구 봉쇄** — sizing chain ≤1 mult 누적 X. T4: 1 continuous scalar (0.75-1.5×) BEFORE clip + tier amplifier (1.5/2/3×) + cell mult (1.5/0.5/1.0) + hard MAX headroom min()
<rejection-keywords sweep="모든 vault digest / log line / agent prompt → 0건 의무">
12주 · 90d gate · monthly review · regrets/ · posture standard · regulatory cap · professional risk · real-money safety · fractional Kelly is too aggressive in practice · 표본 부족 risk
</rejection-keywords>
</absolute-mandates>

## 8-Layer Architecture ([[ADR-003]])
L0 Dynamic Universe · L1 Canonical+Baseline · L2 Per-Gate AI (G1-G8) · L3 Sizing+Risk · L4 Cell Matrix · L5 Learner Network · L6 Live Recalc · L7 Strategy Isolation

## Reference (Session start mandate)
- `vault/_NOW.md` (Tier 0, mandatory) · `vault/INDEX.md` · `vault/log.md` (1-line append, NO interpretation)
- `vault/00_charter/` · `vault/10_decisions/ADR-001~008` · `vault/30_components/layer-0..7-*.md`
- `~/.claude/projects/-Users-jinyoon-Projects-Polaris/memory/MEMORY.md` (영속 원칙 index)

## Workflow (영속 cycle)
1. **Session start** — read `_NOW.md` + MEMORY.md
2. **비-자명 결정 = 슈퍼 브레인 4 합주** — vault read + sequential-thinking + codex debate + vault update
3. **모든 신규 코드 = codex 외부 review** (작성 ≠ 리뷰)
4. **모든 sub-agent prompt 의무**: DEMO 명시 + Aggressive bias + 거부 키워드 sweep + Vault append
5. **큰 wave = 5-axis review**: technical / 4-axis policy / cumulative coherence / functional / live audit
6. **Vault 지속 리뷰** (주 1회 또는 50+ commit) — Karpathy 3-ops + backlink + lifecycle
7. **Session end** — material change 시 vault append (digest/insight/ADR/lesson) + log 1-line

## Handoff & Agent 모델 (context pollution 방지 + brain contribution)
Canonical spec → `vault/30_components/harness-collab-protocol.md`. 메인 = orchestrator + synthesizer, raw read/search dump 회피.

<agent-definition>
Agent = 자율 실행 주체: 독립 context + 전체 toolkit 소환 (sub-agent spawn / advisor codex / skill / vault r·w / sequential-thinking / parallel 협업).
tool call · skill · advisor 단독 호출 ≠ agent — agent는 위 권한을 행사하는 실행 주체.
</agent-definition>

<handoff-triggers>
5+ 파일 read·codebase-wide search → Explore·general-purpose · 큰 wave 검수 → 5-axis 병렬(technical/4-axis policy/coherence/functional/live) · 다단계 설계 → Plan, 리팩토링 → code-simplifier · 신규 코드·거동 변경 → code-implementer→codex-debate-partner · 거부키워드 sweep hit·9-stack/sizing 변경·vault write 충돌 → 전담 agent · 오염 신호(Read 5+/grep 100+ line/반복 search) → 전환 · 단일 known target → 직접 처리
</handoff-triggers>

<builder-not-reviewer>
코드·spec·rule 작성 주체 self-review 금지 (confirmation bias). 신규 작성 → codex 외부 review **의무** (codex 불가 시에만 별도 Claude reviewer fallback). Workflow #3 강화.
</builder-not-reviewer>

<sub-agent-prompt-header>
DEMO/PAPER 명시 + Aggressive bias + 거부 키워드 sweep + length cap + vault r·w 권한 (brain contribution).
</sub-agent-prompt-header>

## Tech stack
- Python 3.13+, `httpx` async, `sqlite3` stdlib, `mypy --strict`, `ruff` clean
- TDD: 실패 → 코드 → pass. `pytest` + `hypothesis` (property for `core/` pure)
- File ≤500 LOC (split if longer) · Markdown ≤60 lines (`md_max_60_lines_split`)

## Anti-pattern (재발 방지 체크)
- v1 9-stack collapse · cold_start=$0 → entry 0 · OKX 401 = `www.okx.com` (실제 `us.okx.com`)
- Codex R1 ROLLBACK = demo context 누락 → real-money 보수 권고
- 정적 ticker hardcode → Layer 0 dynamic universe · 정적 strategy 4-method → signal generator only + AI gate
- Single-axis review only → cumulative + functional 누락 위험
- Smoke harness fixture mode = production 위장 → `production_paper_loop.py` 별도

## Quick reference
- **24h paper loop**: `python3 -m polaris.scripts.ignite_p1 --paper --duration 86400 --tick 5 --full-pipeline --real-roundtrip --db data/polaris.sqlite -vv --log-file data/paper/polaris_runtime.log`
- **Dashboard**: WEB — `./scripts/start_dashboard.sh` launches `tools.visualizer.server` (detached) + opens browser at http://localhost:8770 (left=Neural Cloud sphere, right=analysis board, fed by `/api/snapshot`). Terminal `dashboard_v2` retired 2026-05-29; tty-cleanup removed ([[feedback_never_kill_claude_session]]). Stop: `./scripts/stop_dashboard.sh`.
- **Stop**: `kill -SIGTERM <PID>` · **Tests**: `python3 -m pytest tests/ -q` · **Vault lint**: `python3 tools/vault_lint.py --karpathy --report`
