# INVASION TRADING SYSTEM — CLAUDE CODE RULES

## Project Overview
- `invasion/spot/` package — Run: `python3 -m invasion.spot --headless`  (SPOT-only since 2026-05-01, ADR-007 Phase α applied; legacy `invasion/` main bot 영구 정지 commit `f3fd23a8`)
- AI: ADR-006 단계 1 적용 — `ai_active_modes=['strategy_evolution']` 만 (advisor 폐기)
- Exchanges (active in SPOT): OKX SPOT (paper Lv1, fee 0.7%/side — INSIGHT-032), Alpaca stock (paper, weekend 휴장)
- Session: **통합 Harness 단일 세션** (Opus 4.7) + 20 advisor/executor agent pool (2026-04-19 통합 → T13 Phase 4 5 신규 추가 04-24). 구 3-세션 (Dev/Ops/Harness) 및 IPC 파일 폐기 → `.claude/archive/3-session-deprecated/` + `tasks/archive/2026-04-20_*`
- Regime: Dynamic per-group + Crisis Escalation. ALL regimes ATTACK (no defensive)
- Config: ParamRegistry 4-Tier (FROZEN/CONFIG/DYNAMIC/COMPUTED) — global default fallback
- **Decision SSOT: `strategy_cell_matrix` 8-dim** (exchange × group × session × regime × strategy × direction × ticker × liquidity_tier) — 100% 메트릭스화 plan `.claude/plans/cell-matrix-100pct-pivot.md` 진행 중 (Phase 1 sizing → 5 Elo per cell)
- DB: SQLite WAL (`data/invasion.sqlite`)
- Core philosophy: **Aggressive Contrarian — crisis = opportunity, max bet on fear**
- Pilot framing (~04-24): 100% 메트릭스화 완료까지 trading 손실 = architectural validation 데이터. milestone 후 fresh DB → production data.
- Dashboard: 2-window (operations.py LEFT + intel.py RIGHT, upper LG monitors)
- Strategy Evolution: auto-evolving via Elo tournament + genetic mutations
- Clean data epoch: 1775839507 (2026-04-11 02:45 AEST)

## 참조 문서 (지침서)
- **Session Run Book**: [.claude/loop.md](.claude/loop.md) — 자율 개선 루프 + 세션 부팅 인덱스
- **북극성**: [.claude/docs/north_star.md](.claude/docs/north_star.md)
- **코딩 규약**: [.claude/docs/coding_conventions.md](.claude/docs/coding_conventions.md) — pre/post-flight, naming, bot ops, param mgmt, governance
- **Canonical File Map**: [.claude/docs/canonical_files.md](.claude/docs/canonical_files.md)
- **설명서 (구조)**: `docs/ARCHITECTURE.md`
- **Data Governance**: `docs/GOVERNANCE.md`
- **Agents/Skills**: `.claude/agents/`, `.claude/commands/`

## 🔴 VAULT MANDATORY (Jin 2026-04-26 mandate; Karpathy LLM Wiki 2026-04-27)
**모든 instruction / advisor / harness / skill 은 vault 사용 의무화**. 3-layer (Karpathy spec, https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): Raw Sources `data/` + Wiki `vault/` + Schema `CLAUDE.md` & `vault_mandatory_protocol`. 3 ops: **Ingest** / **Query** / **Lint** (see [vault/05_process/meta/karpathy_workflow.md](vault/05_process/meta/karpathy_workflow.md)).
- **SSOT**: [vault/05_process/meta/vault_mandatory_protocol.md](vault/05_process/meta/vault_mandatory_protocol.md) + [vault/05_process/meta/karpathy_workflow.md](vault/05_process/meta/karpathy_workflow.md)
- **세션 진입 의무 read**: `vault/_NOW.md` (Tier 0) + `vault/INDEX.md` (catalog) — 특별 file `vault/log.md` (chronological)
- **작업 종료 의무 write** (1+): INSIGHT / ADR / digest / lessons / harness_items / `_NOW.md` 갱신 + `vault/log.md` chronological line
- **SQL 쿼리**: `vault/05_process/meta/sqlite_mcp_query_cookbook.md` 또는 `mcp__sqlite__read_query` (raw bash sqlite3 ad-hoc 금지)
- **Entity 링크 의무**: `[[ZEC]]` `[[BZ]]` `[[risk_off]]` `[[TIME]]` 명시 — 자동 backlink 누적
- **Lint** (주 1회 또는 50+ commit 후): `python3 tools/vault_lint.py --karpathy --report` (orphan / stale / contradictions)
- **위반 = 휘발성 의존 = 작업 무효**. 매 작업 종료 self-check "vault 에 무엇 write 했나?"

## Session Start (Required)
1. Read `vault/_NOW.md` — live state, recent decisions, active issues (**MANDATORY first**)
2. Read `tasks/lessons.md` — past mistake patterns (vault: `[[lessons]]`)
3. Read memory files (MEMORY.md index)
4. Read `docs/ARCHITECTURE.md` — system layer structure (vault: `[[ARCHITECTURE]]`)
5. Read relevant agent file if task matches
6. Plan before code — report plan first, then execute
7. **End-of-session**: write at least 1 to vault (digest/insight/ADR/lesson)

## Absolute Rules
- SPOT pivot 후 `invasion/spot/runtime.py` 가 entry. Legacy `invasion/main.py` 등 18 subdir (393 files) = dead code, archive 대기 (Jin 결정 영역).
- `try/except pass` (error swallowing) FORBIDDEN — at minimum use log_event
- Before deleting: `grep -rn "MODULE_NAME" invasion/ --include="*.py" | grep import`
- Legacy dirs deleted (shared/, core/, exchange/ig/)

## Decision Authority
| Decision | Owner |
|----------|-------|
| Architecture changes | Jin approval required |
| Duplicate file deletion | Jin approval required |
| Parameter changes | Autonomous after validation |
| Bug fixes / Refactoring | Claude Code autonomous |

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
