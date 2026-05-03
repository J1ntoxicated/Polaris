---
entity_type: index
entity_id: alpha_index
auto: true
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[60_alpha/_README]]", "[[INDEX]]"]
mode: alpha
reviewed_by: codex
tags: [meta, alpha, index, polaris]
---

# Alpha Index — 가설 status별

> 옵시디언 Dataview 플러그인이 자동 갱신. 수동 편집 시 dataview rerun 필요.

## Active (진행 중)

```dataview
TABLE
  file.frontmatter.last_modified AS "Last Modified",
  file.frontmatter.expires AS "Expires",
  file.frontmatter.reviewed_by AS "Reviewed By"
FROM "60_alpha/active"
WHERE entity_type = "hypothesis"
SORT file.frontmatter.last_modified DESC
```

## Graduated (ADR 승격)

```dataview
TABLE
  file.frontmatter.last_modified AS "Last Modified",
  file.frontmatter.ack_at AS "ADR Ack At"
FROM "60_alpha/graduated"
WHERE entity_type = "hypothesis"
SORT file.frontmatter.last_modified DESC
```

## Archived (실패/폐기)

```dataview
TABLE
  file.frontmatter.last_modified AS "Archived At",
  file.frontmatter.expires AS "Expired"
FROM "60_alpha/archived"
WHERE entity_type = "hypothesis"
SORT file.frontmatter.last_modified DESC
```

## Promotion Gate Pending

```dataview
TABLE
  file.frontmatter.last_modified AS "Paper Done At",
  file.frontmatter.expires AS "Expires"
FROM "60_alpha/active"
WHERE contains(file.tags, "promotion-pending")
```

## Status 통계

```dataview
TABLE WITHOUT ID
  status AS "Status",
  count AS "Count"
FROM "60_alpha"
WHERE entity_type = "hypothesis"
GROUP BY file.frontmatter.status AS status
```

## 카운트 (수동 갱신 또는 dataview)

| Status | Count |
|---|---|
| Active | 0 |
| Graduated | 0 |
| Archived | 2 |
| **Total** | 2 |

> Phase 2에서 첫 HYPOTHESIS-001 추가 예정.
