# /vault-tick — 15m monitoring tick + vault Step 0/7 통합

> Jin invokable 또는 /loop 안 자동 호출. 기존 6-step monitoring 에 **Step 0 (vault read) + Step 7 (vault write)** 추가.

## Workflow (8-step)

### Step 0 (Read) — vault context
- `vault/_NOW.md` Recent Decisions tail 3
- 최근 INSIGHT (`vault/03_knowledge/insights/INSIGHT-*.md` 최근 1)
- 직전 tick (`vault/04_ops/digests/daily-{today}.md` 마지막 section)

### Step 1-6 (기존 monitoring tick)

1. `data/invasion.log` tail 500 + channel emit rate
2. Silent module 감지 (CELL_LEARN/CUSUM/IPS_FEEDBACK/DIRECTION_MOD/CELL_EXIT_OVERRIDE/PHASE0_HELPER/POOL_ALPHA/EMA_APPLY)
3. 부족 로그 list + dev-coder spec 후보
4. 중복/noise refine 후보 (rate >100/min 또는 반복 패턴)
5. ERROR/WARN 상위 5
6. `tasks/harness_items.md` ITEM 추가

### Step 7 (Write) — vault digest + state update
- `vault/04_ops/digests/daily-{today}.md` tick log append (HH:MM section)
- 신규 패턴 발견 시 INSIGHT-NNN 작성
- Bot state 변경 시 `_NOW.md` Recent Decisions 갱신
- Entity backlink 의무 (관련 ticker/strategy/regime/exit)

## SQL 쿼리 표준 (Step 1-6 안)

❌ 금지: raw `sqlite3 data/invasion.sqlite "SELECT ..."` ad-hoc
✅ 의무: cookbook reference (`vault/05_process/meta/sqlite_mcp_query_cookbook.md` Q1-Q10) 또는 `mcp__sqlite__read_query`

## Output template

```markdown
# Tick — {HH:MM AEST}

## Step 0 (vault read)
- Recent decision: {_NOW tail 1줄}
- Last tick: {digest 마지막 section 제목}

## Step 1-6 (data scan)
| Section | Result |
| ... | ... |

## Step 7 (vault write)
- digest append: ✓ daily-{today} {HH:MM} section
- INSIGHT trigger: {Y/N}, {topic if Y}
- _NOW update: {Y/N}, {decision if Y}
- Entity backlinks: [[ticker]] [[strategy]] ...
```

## Trigger

- /loop 자동 invoke (15-20m interval)
- Jin 수동 `/vault-tick`
- 신규 패턴 의심 시 (alert.fired)

## Related

- [[vault_mandatory_protocol]] section 4 (tick / loop 통합)
- [[loop]] (skill 자체)
- [[vault-status]] (read-only 종합)
- [[insight_lifecycle_policy]] (INSIGHT 작성 시점 기준)

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
