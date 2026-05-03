---
entity_type: counter
entity_id: perp_counter
auto: true
last_modified: 2026-05-03
expires: never
editable: false
back_links: ["[[ADR-009]]", "[[INSIGHT-013]]"]
mode: meta
reviewed_by: none
tags: [meta, counter, polaris, mode/meta]
---

# PERP Counter (ADR-009)

SPOT-only 유지 + 5+ 연속 SPOT 가설 fast-fail 시 PERP 검토 ADR 강제 트리거.

| # | HYPOTHESIS | timestamp | reason |
|---|---|---|---|
| 1 | HYPO-001 (RSI mean reversion BTC) | 2026-05-03 | All parameters expectancy < fee (INSIGHT-013) |

**Current: 1/5**
