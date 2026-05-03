# /vault-insight — 가이드된 INSIGHT 작성 + entity backlink 의무 적용

> 패턴 발견 시 INSIGHT-NNN 작성 표준화. Jin 또는 advisor 가 trigger.

## Workflow

1. Read `vault/05_process/meta/insight_lifecycle_policy.md` section 1 (status 정의)
2. Sequential ID 결정: `INSIGHT-{NNN}-{topic-kebab}-{YYYY-MM-DD}.md` (NNN = 다음 번호)
3. Frontmatter 의무 yaml: entity_type, entity_id, date, status, sources, relations, tags
4. 본문 표준 7-section: 발견 / Data evidence / Root cause / Action items P0-P3 / Related entities (의무) / Cross-references / Update history
5. Entity backlink 의무: 본문 안 모든 관련 entity 를 wikilink 로 작성
6. Lifecycle: status=active 로 시작, ADR 연결 시 measurement → verified or obsolete

## Trigger 시점 (필수)

- 같은 패턴 3+ 발현 (ITEM-145 BZ 9-fail 처럼)
- 신규 silent module 발견
- T13 wire 같은 critical event 검증 누적
- audit 결과 종합 (advisor 7개 dispatch 후 등)
- 우연 발견 (log rotation evidence loss 처럼)

## SKIP 시점

- 1회성 관찰 (3 reproduction 못 미쳤을 때) → digest tick log 만
- routine status (정상 운영) → digest 만

## Vault mandatory

- Read 의무: insight_lifecycle_policy, 직전 INSIGHT 1
- Write 의무: 본 skill 자체가 write — INSIGHT 신설 + `_NOW.md` Recent Decisions 갱신 + lessons.md (rule 도출 시)
- Entity wikilink 의무: 5+ entity 명시

## Output template

호출 후:

✅ INSIGHT-NNN 작성 완료
- Path / Status: active
- Sources, Entity backlinks, Action items 카운트
- Cross-link 자동 누적 (다음 vault_crosslink 실행 시)

## Related

- `vault/05_process/meta/insight_lifecycle_policy.md` — INSIGHT lifecycle SSOT
- `vault/05_process/meta/vault_mandatory_protocol.md` — write 의무 location matrix
- `vault/05_process/meta/vault_md_standard.md` — INSIGHT 무제한 length OK
- `vault-tick` — monitoring tick 안 신규 패턴 시 자동 trigger
- 예시: INSIGHT-001-bz-9fail-pattern, INSIGHT-002-monitoring-9tick, INSIGHT-003-canonical-drift-audit

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
