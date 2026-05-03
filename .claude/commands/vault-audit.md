# /vault-audit — Vault currency + 정합 전수조사 (periodic)

> 주기 (weekly/monthly) 또는 large change 후 invoke. INSIGHT-003 audit script 자동화.

## Workflow

### 1. Path validity (canonical_files.md)
- `.claude/docs/canonical_files.md` 안 모든 backtick 경로 `Path.exists()` 검증
- Broken path → `invasion/` prefix 누락 의심 시 자동 fix 제안
- "현재 없음" 표시된 planned entries 는 skip

### 2. Advisor / Skill 정합
- `.claude/agents/*.md` 20 file 각각 vault wikilink count
- `.claude/commands/*.md` 8 file vault wikilink count
- Orphan (referrer 0) → resolution 권고

### 3. Deprecated reference 잔재 scan
- `shared/`, `exchange/ig/`, "3-세션" (active context 만, archive 제외)
- 발견 → archive 이동 또는 historical context marker 권고

### 4. Vault entity coverage
- DB ground truth (sqlite) vs vault entity file count
  - strategies: `SELECT DISTINCT strategy_id FROM trades` vs `vault/02_live/strategies/*.md`
  - tickers: `SELECT DISTINCT ticker FROM trades` vs `vault/02_live/tickers/*.md`
  - cells: `SELECT COUNT(*) FROM strategy_cell_matrix` vs `vault/02_live/cells/by_cell/*.md`
- Gap 발견 → `tools/db_views_export.py` 또는 supplementary 재실행

### 5. Wikilink graph integrity
- vault 안 모든 `[[X]]` wikilink → entity X 존재 여부
- Broken wikilink 발견 → INSIGHT 또는 entity 신설 권고

### 6. Frontmatter standard 준수
- 모든 vault entity (component/cell/ticker/strategy/insight/adr) 의 `entity_type`, `entity_id` 필수 field 존재 검증
- `vault_md_standard` length 권장 위반 (예: atomic >150줄) 감지

### 7. Crosslink 갱신
- `python3 -m tools.vault_crosslink` 실행
- backlink 누적 확인

## Output

```markdown
# Vault Audit — {YYYY-MM-DD HH:MM}

## Path validity
- canonical_files.md: {N}/{T} valid ({M} broken auto-fixable, {P} intentional planned)

## Advisor/Skill currency
- 20 advisor: {N} linked, {O} orphan
- 8 skill: {N} linked

## Deprecated cleanup
- Remaining: {list of files with active deprecated refs}

## Vault coverage gap
- Strategies: vault {V} / DB {D} → gap {G}
- Tickers: vault {V} / DB {D} → gap {G}
- Cells: vault {V} / DB {D} → gap {G}

## Wikilink integrity
- Total wikilinks: {N}
- Broken targets: {B}

## Frontmatter compliance
- Missing entity_type: {N}
- Missing entity_id: {N}
- Length violation (atomic >150): {N}

## Action items
- P0 (immediate): {...}
- P1 (Jin sanction): {...}
```

## Trigger

- Jin manual: `/vault-audit` (periodic — weekly 권장)
- Large vault change 후 (예: 본 세션 처럼 30_components 신설 등)
- INSIGHT 누적 50+ 시 (lifecycle policy review)
- Quarterly cleanup

## Vault mandatory

- Read 의무: 본 skill 이 audit 자체이므로 모든 vault scan
- Write 의무: audit 결과 → `vault/04_ops/audit/{YYYY-MM-DD}-vault-audit.md` (또는 INSIGHT 승격 시 INSIGHT-NNN)

## Related

- [[INSIGHT-003-canonical-drift-audit-2026-04-26]] — 첫 audit 결과
- [[vault_mandatory_protocol]] — vault 사용 의무
- [[insight_lifecycle_policy]] — audit 결과 → INSIGHT 승격 기준
- [[graph_groups]] — Obsidian visualization

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
