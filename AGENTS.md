# INVASION TRADING SYSTEM — Codex RULES

## Project Overview
- `invasion/` package — Run: `python3 -m invasion --headless`
- AI: Gemini (primary) + Codex API (critical only)
- Exchanges: OKX (crypto) + Capital.com (forex/indices/commodity) + Alpaca (US stocks/ETF) + Binance (data only)
- Regime: Dynamic per-group + Crisis Escalation. ALL regimes ATTACK (no defensive)
- Config: ParamRegistry SSOT — 4-Tier: FROZEN / CONFIG / DYNAMIC / COMPUTED
- DB: SQLite WAL (`data/invasion.sqlite`)
- Core philosophy: **Aggressive Contrarian — crisis = opportunity, max bet on fear**
- Dashboard: 2-window (operations.py LEFT + intel.py RIGHT, upper LG monitors)
- Strategy Evolution: auto-evolving via Elo tournament + genetic mutations
- Clean data epoch: 1775839507 (2026-04-11 02:45 AEST)

## 참조 문서 (지침서)
- **Session Run Book**: [.codex/loop.md](.codex/loop.md) — Codex bootstrap + shared harness map
- **북극성**: [.codex/docs/north_star.md](.codex/docs/north_star.md)
- **코딩 규약**: [.codex/docs/coding_conventions.md](.codex/docs/coding_conventions.md) — pre/post-flight, validation, Codex workflow
- **Canonical File Map**: [.codex/docs/canonical_files.md](.codex/docs/canonical_files.md)
- **Shared Harness Rules**: [docs/harness/SHARED_RULES.md](docs/harness/SHARED_RULES.md)
- **Cross-Harness Protocol**: [docs/harness/CROSS_HARNESS_PROTOCOL.md](docs/harness/CROSS_HARNESS_PROTOCOL.md)
- **App-Native Collaboration**: [docs/harness/APP_NATIVE_COLLAB.md](docs/harness/APP_NATIVE_COLLAB.md)
- **설명서 (구조)**: `docs/ARCHITECTURE.md`
- **Data Governance**: `docs/GOVERNANCE.md`
- **Agents/Skills**: `.codex/agents/`, `.codex/commands/`

## Session Start (Required)
1. Read `tasks/lessons.md` — past mistake patterns
2. Read `.codex/MEMORY.md` — shared memory index
3. Read `docs/ARCHITECTURE.md` — system layer structure
4. Read relevant agent file if task matches
5. Plan before code — report plan first, then execute

## Cross-Harness Artifacts (Jin 2026-04-16 22:27 전환)
- **Codex 호출**: Harness 가 `codex:codex-rescue` / Agent / Skill 로 **inline 직접 호출** (파일 IPC 삭제)
- Dev/Ops 의 Codex 요청: `dev_to_harness.md [REVIEW-REQUEST-CODEX]` / `ops_to_harness.md [CODEX-QUERY]` → Harness 중재
- Codex 결과 중계: `harness_to_dev.md [CODEX-RESULT]` / `harness_to_ops.md [CODEX-RESULT]`
- ~~`tasks/claude_to_codex.md`~~ / ~~`tasks/codex_to_claude.md`~~ / ~~`tasks/harness_debate.md`~~ — 삭제됨 (deprecated 2026-04-16)
- IPC 채널 4개만: `dev_to_harness`, `harness_to_dev`, `ops_to_harness`, `harness_to_ops`, 직접 `dev_to_ops`/`ops_to_dev`

## Absolute Rules
- `main.py`: minimize direct changes — new features go in separate modules
- `try/except pass` (error swallowing) FORBIDDEN — at minimum use log_event
- Before deleting: `grep -rn "MODULE_NAME" invasion/ --include="*.py" | grep import`
- Legacy dirs deleted (shared/, core/, exchange/ig/)

## Decision Authority
| Decision | Owner |
|----------|-------|
| Architecture changes | Jin approval required |
| Duplicate file deletion | Jin approval required |
| Parameter changes | Autonomous after validation |
| Bug fixes / Refactoring | Codex autonomous |

## Communication
- Jin과 한국어
- Code and docs in English
- No Japanese

## 영속 원칙 (메모리 참조)
- `feedback_aggressive_always_profit` — 공격적 상시 수익 북극성
- `feedback_loss_profit_asymmetry` — 비대칭 유리 (대칭 = 위험)
- `feedback_root_cause_evidence_based` — 게싱 금지, 증거 기반
- `feedback_md_max_60_lines_split` — 60줄 + 분리 + 상호 참조
- `feedback_harness_design_principles` — Anthropic harness 원칙
