# /vault-status — Vault-only 현 상태 종합

> Bot/log 의존 0. vault read 만으로 5초 종합. Tier 0 + recent INSIGHT/ADR + 작업 큐 + active issue.

## Workflow

1. Read `vault/_NOW.md` (Tier 0) — Recent Decisions tail 5
2. Read `tasks/harness_items.md` tail 3 (active 작업 큐)
3. List `vault/03_knowledge/insights/INSIGHT-*.md` (active patterns)
4. Read latest ADR (`vault/03_knowledge/decisions/ADR-*.md`) — status (applied/verified/superseded)
5. Read today digest (`vault/04_ops/digests/daily-{today}.md`) 마지막 5 tick
6. Output 6-section 한국어 종합

## Output sections

- 🎯 **Bot Live State**: `_NOW` 직전 결정 1-2줄
- 🚨 **Active Issues**: `_NOW` Active Issues
- 📈 **Today Trajectory**: 5 tick (1h Net + T13 fires)
- 🔍 **Active INSIGHTs**: 제목 + status
- 📜 **Active ADRs**: 제목 + status
- 📋 **작업 큐**: ITEM 최근 3
- ⏭️ **Recommended next action**: 우선순위 1개

## Trigger

- Jin: `/vault-status` 수동
- harness-mode session 부팅 시 auto invoke
- 매 monitoring tick Step 0 의 표준화

## Vault mandatory

- Read 의무: `_NOW.md`, latest INSIGHT/ADR, harness_items
- Write 의무: 본 skill read-only (status report only)

## Related

- `vault/05_process/meta/vault_mandatory_protocol.md` section 1 — Tier 0 read 의무
- `harness-mode` (session 부팅 통합)
- `loop` (15m monitoring tick Step 0)
- `vault-tick` (write counterpart)

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
