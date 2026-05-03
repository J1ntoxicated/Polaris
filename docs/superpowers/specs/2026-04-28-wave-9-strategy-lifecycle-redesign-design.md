# Wave 9 — Strategy Lifecycle Redesign (Disabled Status 폐기)

**Date**: 2026-04-28 11:35
**Vault refs**: [[INSIGHT-025]] (block paradigm pattern) [[feedback_no_block_filter_architecture]]

---

## 1. Context

Jin mandate (2026-04-28 11:30):
> "왜 자꾸 막는거니. 라이프사이클 좀."

Block paradigm 0 mandate 완전 일관 = strategy status enum 의 `disabled` 자체 폐기. 

Current state (after 153 reset):
- 201 active / 5 retired (Jin permanent) / 1 inactive
- 0 disabled (자율 reset 완료)

But schema/code 에 `disabled` enum value 잔존 = 정합 위반 (재발 가능).

---

## 2. Decision

**`disabled` status 자체 폐기** (block paradigm 0 완전 일관).

Surviving statuses:
- `active` — default, 모든 evolver mutations + Tournament 평가 후 default
- `retired` — Jin permanent (MSG-P0-4-G11-KILL pattern), code-level 명시
- `inactive` — 일시적 (별개 의미, 검토 필요)

폐기:
- `disabled` enum value 제거
- 모든 `s["status"] = "disabled"` set site (Wave 6 Phase 3 + Wave 5 이미 폐기) 검증
- `disabled_at` column 그대로 유지 (historical metadata)

---

## 3. Implementation

### Step 1 — Code grep + 모든 'disabled' 처리 path 검토

```bash
grep -rn '"disabled"\|status.*disabled\|status="disabled"' invasion/ --include='*.py'
```

각 site:
- Set sites (이미 Wave 6 Phase 3 폐기): verify
- Read/check sites: `if status == 'disabled'` → `if status == 'retired'` 또는 폐기

### Step 2 — `_pipeline_scan.py:53` `is_disabled` check

기존:
```python
return s.get("status", "active") == "disabled"
```

신규:
```python
# WAVE-9 DEPRECATED: disabled 폐기. retired 만 block paradigm 정합 (Jin permanent).
return s.get("status", "active") == "retired"
```

### Step 3 — Other status check sites

`evolution/_reviewers/strategy.py:69` — `WHERE s.status='disabled'` query (alert 영역) → `retired` 또는 query 자체 폐기.

### Step 4 — Schema validation (선택)

`strategies.status` CHECK constraint 추가:
```sql
ALTER TABLE strategies ADD CHECK (status IN ('active', 'retired', 'inactive'));
```

또는 단순 enum 폐기 — runtime 그대로 두고 코드만 정합.

### Step 5 — Test

- AST + import smoke
- 기존 disabled status query 사이트 모두 retired 으로 정합 변경
- 새 strategy mutation = status='active' default 검증

---

## 4. North Star alignment

- ✅ Block paradigm 0 완전 일관 (disabled enum 자체 폐기)
- ✅ Amplify-only mandate
- ✅ `feedback_no_block_filter_architecture` 100%
- ✅ Retired 만 (Jin permanent) 명시 retirement
- ✅ Strategy lifecycle 단순화 (active vs retired)

---

## 5. Risk

1. **legacy `status='disabled'` query** 모두 처리 — dev-coder grep 으로 식별
2. **`inactive` status 의미** — adopted 영역? broker_sync? 검토
3. **Historical disabled_at metadata** — 그대로 유지 (트래킹 용)
4. **Schema CHECK constraint** — 추가 시 기존 data migration 필요 (이미 153 active reset 완료, disabled 0 행)

---

## 6. References

- Vault: [[INSIGHT-025]] [[2026-04-28-wave-6-tournament-cell-mult-redesign-design]]
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_no_quick_patch_ever]]
- Code: `invasion/strategy/engine.py`, `invasion/trade/_pipeline_scan.py`, `invasion/evolution/_reviewers/strategy.py`
