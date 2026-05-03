# Polaris Wave 3B — INSIGHT-015 Phase 2 + INSIGHT-008 Audit

**Date**: 2026-04-28 00:15
**Vault refs**: [[INSIGHT-015]] [[INSIGHT-008]] [[2026-04-27-polaris-structural-overhaul-design]]

---

## 1. Context

Wave 2A + Wave 2B/3A 완료 후 마지막 영역. `feedback_overhaul_over_incremental` mandate — 한 번에 재설계.

**INSIGHT-015 Phase 2**: events.jsonl 194MB → sqlite events table SSOT 전환 (dual storage 폐기)
**INSIGHT-008 audit**: 8 silent modules body grep — 6 modules `0 emit sites` 발견 (dead code 또는 historical channel mismatch), vault status resolve

---

## 2. Batch 1 — INSIGHT-015 Phase 2 (events.jsonl → sqlite SSOT)

### Current state
- `bus.py:51`: `__init__(self, log_path="data/events.jsonl")` — 모든 event jsonl write
- `dashboard/data.py:160`: events.jsonl reader (TTL 300/1800)
- `data/events.jsonl`: **194MB** sustained (Phase 1 deploy 후에도 누적)
- DB: `events` 테이블 부재 (`ai_event_audits`/`candidate_events`/`trade_events` 만 존재)

### Fix — Single SSOT migration

**Step 1 — Schema 신설** (`store_core.py` 또는 schema 정의):
```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT,  -- JSON serialized
    created_at REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
```

**Step 2 — bus.py write 변경**:
- `publish()` / `publish_async()` 에서 jsonl append 제거
- `store._enqueue` (write queue) 또는 `store._lock` 사용해 sqlite events insert
- log_path parameter deprecated — 호환성 위해 유지하되 ignore

**Step 3 — dashboard read 변경**:
- `dashboard/data.py:160` events.jsonl tuple → sqlite events query 패턴
- TTL 그대로 (300/1800)

**Step 4 — 기존 events.jsonl archive**:
- `data/archive/events_2026-04-28-0015.jsonl` 으로 mv (194MB 보존)
- Bot 시작 시 events.jsonl 부재 OK (sqlite SSOT)

**Verify**:
1) AST + import smoke
2) bot restart 후 첫 event publish → sqlite events table row count > 0
3) dashboard query 시 sqlite events row return
4) jsonl 신규 write 0 (`ls -la data/events.jsonl` size 변화 X 또는 부재)

**Commit**: `feat(insight-015 phase-2): events.jsonl → sqlite SSOT consolidation`

**Risk control**:
- dashboard fail 시 visualizer 영향 (paper account, isolated)
- rollback path: log_path parameter 유지 + sqlite events 무시 시 jsonl fallback 가능 (단 design 의도 X)

---

## 3. Batch 2 — INSIGHT-008 Silent Module Audit (vault only)

### Audit 결과 (2026-04-28 grep)

| Module | Body grep emit sites | Status |
|---|---|---|
| CUSUM | 0 | dead code 또는 historical |
| IPS_FEEDBACK | 0 | dead code 또는 historical |
| CELL_EXIT_OVERRIDE | 0 | dead code 또는 historical |
| PHASE0_HELPER | 0 | dead code 또는 historical |
| POOL_ALPHA | 0 | dead code 또는 historical |
| EMA_APPLY | 0 | dead code 또는 historical |
| SKIP_DEMOTED | DEMOTE 폐기로 N/A | superseded |
| SKIP_DEMOTED_SPARSE | DEMOTE 폐기로 N/A | superseded |

### 결론
- 8 modules 중 2개 (SKIP_DEMOTED*) DEMOTE 폐기 superseded
- 6개 (CUSUM/IPS_FEEDBACK/CELL_EXIT_OVERRIDE/PHASE0_HELPER/POOL_ALPHA/EMA_APPLY) 코드 자체 0 emit
- INSIGHT-008 root cause 정확: monitoring channel grep FP — 모듈 자체가 emit 0 이면 silent 가 아니라 dead/historical
- INSIGHT-008 status: open → resolved (audit 결론)

### 후속 작업 (별도 spec, optional)
- 6 modules 의 historical 코드 cleanup (만약 진짜 dead code 면) — 별도 brainstorming
- 또는 의도된 silent (조건부 emit) 인지 verify — code review 필요

**Vault write**: INSIGHT-008 status update + audit findings 기록

---

## 4. North Star alignment

- ✅ Block 0 (전부 architectural fix / audit / observability)
- ✅ `feedback_overhaul_over_incremental` — single dispatch jsonl → sqlite (dual-write 우회 X)
- ✅ `feedback_audit_fstring_prefix_scan` — INSIGHT-008 channel grep FP root cause 정합
- ✅ `feedback_no_quick_patch_ever` — Phase 2 = full migration, partial dual-write X

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- bot restart 후 first-cycle observe

### 1-2 cycle (30m-1h)
- INSIGHT-015: jsonl 신규 write 0, sqlite events row 정상 누적, dashboard 정상
- INSIGHT-008: vault audit 기록 + status update

---

## 6. References

- Vault: [[INSIGHT-015]] [[INSIGHT-008]] [[INSIGHT-002]] [[2026-04-27-polaris-structural-overhaul-design]] [[2026-04-27-polaris-wave-2b-3a-design]]
- Memory: [[feedback_overhaul_over_incremental]] [[feedback_audit_fstring_prefix_scan]] [[feedback_no_quick_patch_ever]]
- Code: `invasion/bus.py`, `invasion/data/store_core.py`, `invasion/dashboard/data.py`
