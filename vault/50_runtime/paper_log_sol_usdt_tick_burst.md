---
entity_type: paper_log
entity_id: paper_log_sol_usdt_tick_burst
auto: true
last_modified: 2026-05-05
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: tick_burst
ticker: SOL-USDT
---

# Paper Log — SOL-USDT tick_burst

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-05T01:52:36 | OPEN | id=SOL-USDT-1777909956011 dir=1 entry=84.44 size=$300 |
| 2026-05-05T01:59:45 | CLOSE | id=SOL-USDT-1777909956011 entry=84.44 exit=84.47 net=-0.10% net_usd=-0.31 |
| 2026-05-05T01:59:45 | EXIT_REASON | signal_exit |
| 2026-05-05T14:26:03 | OPEN | id=SOL-USDT-1777955162665 dir=1 entry=84.89 size=$300 |
| 2026-05-05T14:28:26 | CLOSE | id=SOL-USDT-1777955162665 entry=84.89 exit=84.68 net=-0.45% net_usd=-1.34 |
| 2026-05-05T14:28:26 | EXIT_REASON | signal_exit |
| 2026-05-06T08:52:10 | OPEN | id=SOL-USDT-1778021530329 dir=1 entry=86.62 size=$300 |
| 2026-05-06T09:28:25 | CLOSE | id=SOL-USDT-1778021530329 entry=86.62 exit=86.31 net=-0.56% net_usd=-1.67 |
| 2026-05-06T09:28:25 | EXIT_REASON | sl_hit:-0.0036 |
