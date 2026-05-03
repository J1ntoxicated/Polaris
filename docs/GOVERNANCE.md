# INVASION Data Governance Framework

## Purpose
This framework ensures data integrity, code quality, and change control across the entire
Invasion trading system. The Data Dictionary (`data_dictionary.json`) is the SINGLE SOURCE
OF TRUTH for all modules, functions, variables, formulas, lineage, and quality metrics.

## Document Maturity & Verification Process

### Maturity Levels
| Level | Status | Usage |
|-------|--------|-------|
| **DRAFT** | Created, NOT verified | READ-ONLY reference. Do NOT base code changes on this. |
| **VERIFIED** | External audit passed | Can use as reference for planning. |
| **AUTHORITATIVE** | Multiple audit cycles passed | Can use as basis for code changes. |

**Current level: VERIFIED** (promoted 2026-04-08, verification cycle #4)

### Verification Process (Blueprint)
```
1. EXTERNAL AUDIT (Codex)
   → Codex reads dictionary + actual code
   → Reports: MATCH / MISMATCH / MISSING per item
   
2. REVIEW FINDINGS
   → MISMATCH = two possibilities:
     a) Dictionary wrong → fix dictionary (governance session)
     b) Code wrong → add to todo.md as bug (code session fixes)
   → MISSING = add to dictionary
   
3. DISAGREE RESOLUTION
   → If auditor and steward disagree on expected behavior:
     → Escalate to Governor (Jin)
     → Governor decides: is it a doc error or a code bug?
     → Decision recorded in verification_log.md
   
4. FIX & RE-VERIFY
   → Dictionary fixes: governance session applies
   → Code fixes: code session applies → updates dictionary in same commit
   → Re-run Codex audit on affected modules
   
5. PROMOTION
   → All CRITICAL items resolved
   → Codex re-audit passes with 0 CRITICAL, ≤3 MINOR
   → Governor approves → DRAFT → VERIFIED
   
6. ONGOING
   → Every code change: docs-sync updates dictionary
   → Every 10 commits: steward review
   → Any disagreement: back to step 1
```

### Key Principle
**코드로 dictionary를 검증하지 말 것.** Dictionary가 blueprint.
Dictionary가 맞으면 코드를 고치고, 코드가 맞으면 dictionary를 고친다.
둘 다 틀릴 수도 있으니 항상 **의도(설계)** 기준으로 판단.

## Dictionary Entry Structure — 3 Layers

Each function/formula entry in the dictionary has 3 layers:

### Layer 1: AS-IS (현재 상태)
- pseudo_code, formulas, params, thresholds — 코드에 있는 그대로
- 자동 추출 (AST) + 수동 검증으로 유지
- **변경 시기**: 코드가 변경될 때 같은 커밋에서

### Layer 2: ASSESSMENT (적정성 평가)
- `appropriateness`: GOOD / ACCEPTABLE / QUESTIONABLE / NEEDS_REVIEW
- `evidence`: 근거 (DB 통계, 백테스트, 논문 레퍼런스 등)
- `reviewed_by`: 어떤 전문가/스킬이 평가했는지
- `reviewed_at`: 평가 일시
- **변경 시기**: 전문가 리뷰 후 (steward_agent 실행 결과)

### Layer 3: RECOMMENDATION (개선 권고)
- `recommendation`: 구체적 개선 제안
- `rationale`: 충분한 리서치/디베이트 근거
- `debate_ref`: /debate 결과 링크 (있으면)
- `priority`: P0~P3
- `status`: PROPOSED / DEBATED / APPROVED / IMPLEMENTED
- **변경 시기**: /debate 또는 /alpha-strategist 결과 → Governor 승인 후

### Rules for Layer 2 & 3
1. **임의 추가 금지** — 리서치/디베이트 없이 "내 생각에" 수준의 권고 금지
2. **근거 필수** — 최소 DB 통계 기반 또는 3-AI 디베이트 합의
3. **Governor 승인** — Layer 3 RECOMMENDATION → APPROVED 전환은 Jin만 가능
4. **주기적 재평가** — 100 trades마다 또는 regime 변경 시 Layer 2 재평가

## Configuration Principle: Single Source of Truth
- **ALL values through ParamRegistry or config** — 코드에 매직넘버/하드코딩 금지
- 새 파라미터 추가 시: ParamRegistry에 등록 → preg()으로 읽기 → live_config.json에 반영
- 새 매핑 추가 시: data/*.json 외부 파일 → 코드에서 로드 → API 자동 생성 우선
- 하드코딩 발견 시: 삭제(dead) → 중앙화(이동) → 로직 리뷰(적정성) 3단계
- shared/ 디렉토리: 완전 삭제 완료 (2026-04-07, see agents/REFACTOR_AGENT.md)

## Decision Tree: preg fallback chain (Cell Matrix 100% Pivot)

Plan: [`.claude/plans/cell-matrix-100pct-pivot.md`](../.claude/plans/cell-matrix-100pct-pivot.md)

**결정 우선순위** (모든 trade decision 공통):
```
1. cell_matrix lookup (8-dim: exchange × group × session × regime × strategy × direction × ticker × tier)
   ├─ sample 충분 (n ≥ 학습 threshold) → cell 값 사용
   └─ sample 부족 → 2
2. preg() global default (Tier 3 DYNAMIC, AI Governor hourly)
   ├─ 등록됨 → preg 값 사용
   └─ 미등록 → 3
3. hardcode default (Tier 1 FROZEN — safety invariants only)
```

**hardcode 허용 영역** (FROZEN only):
- `clean_data_epoch` (1775839507), `kill_switch` 임계치, WAL checkpoint 간격
- safety invariants (bounds min/max, schema enum 정의)
- 그 외 모든 숫자 → preg 또는 cell 이관 의무

## Data Lineage: Cell Matrix closed loop
```
cell_matrix lookup → sizing / exit / direction / provider_weight / strategy Elo 결정
  → trade outcome (pnl_pct, exit_type, hold_sec, max_profit_pct)
  → hourly_stats learner (cell 별 aggregate)
  → cell_matrix update (optimal_trail, bep, max_hold, cell_score_long/short)
  → 다음 cycle lookup 반영
```

**lineage 무결성 규칙**: cell 이 학습하는 column 은 decision site 에서 반드시 lookup 경유 (hardcode 우회 금지).

## Core Principles

1. **Dictionary is Gospel** — If it's not in data_dictionary.json, it doesn't exist (governance gap)
2. **No Arbitrary Changes** — All code changes MUST be approved by domain steward first
3. **Lineage Integrity** — Every data transformation must be traceable source→transform→destination
4. **External Audit** — Codex performs independent audit quarterly + on-demand
5. **Continuous Quality** — Automated quality checks run on every code change

## Governance Roles

### Governor (Jin)
- Final authority on architecture and strategy decisions
- Approves steward nominations and cross-domain changes
- Reviews external audit findings

### Governance Manager (Claude Code)
- Maintains data_dictionary.json as SSOT
- Enforces change control process
- Coordinates steward reviews
- Schedules and executes audits
- Triggers Codex external audit when needed

### Domain Stewards
| Domain | Scope | Steward Agent | Files |
|--------|-------|---------------|-------|
| trade_ops | Pipeline, entry, exit, position, portfolio | /strategy-review | invasion/trade/ |
| exchange_infra | OKX, Capital, Alpaca, IBKR, Binance adapters | /health-check | invasion/exchange/ |
| signal_quality | Signal providers, scoring, composite, verdict | /alpha-strategist | invasion/signal/, invasion/signals/ |
| strategy_evolution | Strategy engine, tournament, backtester, evolver | /strategy-review | invasion/strategy/ |
| ai_layer | Orchestrator, controller, live AI, prompts | /debug | invasion/ai/ |
| regime_detection | Regime detectors, crisis escalation | /strategy-review | invasion/market/ |
| data_pipeline | Candle cache, collectors, DB store, technicals | /data-review | invasion/data/, invasion/utils/ |
| risk_ops | Safety, defense, scheduler, tick architecture | /health-check | invasion/ops/, invasion/ticks/ |
| config_params | ParamRegistry, live_config, regime_presets | /param-tune | invasion/config/ |
| dashboard_viz | All dashboard modules, state writer | /dashboard-qa | invasion/dashboard/ |

## Change Control Process

### Before ANY Code Change:
```
1. READ docs/governance/data_dictionary.json
   - Find affected modules, functions, variables
   - Check lineage — what depends on what you're changing?
   - Identify domain steward

2. PLAN the change
   - What modules are affected?
   - Does this change any formula, data type, or lineage?
   - Are there downstream consumers?

3. CONSULT steward (via /strategy-review, /health-check, etc.)
   - Present: what, why, impact analysis
   - Get approval or revision request

4. IMPLEMENT with dictionary update
   - Code change + data_dictionary.json update in SAME commit
   - If adding: new entry in dictionary MUST exist
   - If removing: mark as deprecated, don't just delete
   - If modifying: update formula, params, lineage

5. POST-FLIGHT verify
   - python3 -c "import invasion.main"
   - Lineage still consistent?
   - No orphaned references?

6. CODEX REVIEW (for P0/P1 changes)
   - External audit via /codex:rescue
   - Verify dictionary matches actual code
```

### Forbidden Without Governor Approval:
- Architecture changes (new packages, module restructure)
- Formula changes (pnl, sizing, scoring, exit conditions)
- Data lineage changes (new data flow, removed dependency)
- Steward reassignment

## Data Dictionary Structure

`docs/governance/data_dictionary.json` — master file containing:

```json
{
  "metadata": {
    "version": "1.0.0",
    "last_updated": "2026-04-10T12:00:00+11:00",
    "total_modules": 188,
    "total_classes": "N",
    "total_functions": "N",
    "last_full_audit": "2026-04-08",
    "next_scheduled_audit": "2026-04-15"
  },
  "domains": {
    "trade_ops": {
      "steward": "strategy-review",
      "modules": [...],
      "quality_score": 0-100,
      "open_issues": [...],
      "last_audit": "2026-04-08"
    }
  },
  "modules": [...],
  "lineage": {
    "price": { "source": "...", "transforms": [...], "consumers": [...] },
    "pnl_pct": { ... },
    "signal_score": { ... }
  },
  "formulas": {
    "pnl_pct": "(price - entry) / entry * 100",
    "sizing": "equity * base * tier * regime * score * streak * session * ticker"
  },
  "quality_registry": {
    "known_issues": [...],
    "audit_history": [...]
  }
}
```

## Audit Schedule (Event-Driven)

| Audit Type | Trigger | Executor | Scope |
|------------|---------|----------|-------|
| **Dictionary Sync** | Every commit that touches invasion/ | docs-sync agent | Dictionary matches code |
| **Steward Review** | Every 10 commits | Domain steward skill | Domain-specific quality |
| **Codex External Audit** | P0/P1 change OR architecture change | /codex:rescue | Independent code verification |
| **Formula Verification** | Any formula/calculation change | Domain steward | Math correctness |
| **Lineage Check** | Any data flow change (new input/output) | Governance Manager | Lineage integrity |
| **Health Check** | 15min cron (live bot) | /health-check | Process, positions, errors |
| **Signal Quality** | Every 100 trades | /alpha-strategist | Entry strength vs outcomes |
| **Strategy Fitness** | Every evolution cycle | /strategy-review | Fitness vs real PnL |
| **Full System Audit** | Major release OR 500 trades | All stewards | Everything |

### Commit-Based Triggers
```
commits 1-9:   docs-sync auto (dictionary update only)
commit 10:     steward review (affected domains)
commit 20:     steward review + signal quality check
commit 50:     full domain audit
P0/P1 fix:     Codex external audit (immediate)
architecture:  Codex audit + Governor approval + all stewards
```

## Quality Metrics (per domain)

- **Coverage**: % of functions documented in dictionary
- **Accuracy**: % of dictionary entries matching actual code
- **Lineage Integrity**: % of data flows fully traced
- **Issue Density**: open issues per 1000 LOC
- **Staleness**: days since last audit

## Integration with Development

### CLAUDE.md Rule (MANDATORY):
```
Before modifying ANY code in invasion/:
1. Check docs/governance/data_dictionary.json for affected entries
2. Consult domain steward
3. Update dictionary in same commit as code change
4. If dictionary entry doesn't exist → governance gap → report to Governor
```

### docs-sync Agent:
Automatically triggered after code changes to sync dictionary.

### Codex External Audit:
```
/codex:rescue "Audit docs/governance/data_dictionary.json against actual code.
Verify: (1) all modules listed, (2) functions match, (3) formulas correct,
(4) lineage intact, (5) no orphaned entries. Report discrepancies."
```
