---
entity_type: paper_log
entity_id: paper_log_bnb_usdt_grid_bot
auto: true
last_modified: 2026-05-05
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: grid_bot
ticker: BNB-USDT
---

# Paper Log — BNB-USDT grid_bot

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-05T10:00:04 | OPEN | id=BNB-USDT-1777939184913 dir=1 entry=622.70 size=$300 |
| 2026-05-05T12:39:22 | CLOSE | id=BNB-USDT-1777939184913 entry=622.70 exit=626.50 net=+0.41% net_usd=+1.23 |
| 2026-05-05T12:39:22 | EXIT_REASON | tp_hit:+0.0061 |
| 2026-05-05T12:42:50 | OPEN | id=BNB-USDT-1777948970167 dir=1 entry=625.80 size=$300 |
| 2026-05-05T16:42:54 | CLOSE | id=BNB-USDT-1777948970167 entry=625.80 exit=626.50 net=-0.09% net_usd=-0.26 |
| 2026-05-05T16:42:54 | EXIT_REASON | max_hold:14403s |
