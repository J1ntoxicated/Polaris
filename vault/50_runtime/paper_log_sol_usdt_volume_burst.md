---
entity_type: paper_log
entity_id: paper_log_sol_usdt_volume_burst
auto: true
last_modified: 2026-05-04
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: volume_burst
ticker: SOL-USDT
---

# Paper Log — SOL-USDT volume_burst

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-04T00:55:35 | BALANCE | cash=$5000.00 equity=$5000.00 realized_pnl=$+0.00 open=0 closed=0 |
| 2026-05-04T07:14:10 | OPEN | id=SOL-USDT-1777842850010 dir=1 entry=84.43 size=$250 |
| 2026-05-04T07:29:31 | CLOSE | id=SOL-USDT-1777842850010 entry=84.43 exit=84.33 net=-0.26% net_usd=-0.65 |
| 2026-05-04T07:29:31 | EXIT_REASON | signal_exit |
| 2026-05-04T07:34:16 | OPEN | id=SOL-USDT-1777844050668 dir=1 entry=84.37 size=$250 |
| 2026-05-04T07:41:01 | CLOSE | id=SOL-USDT-1777844050668 entry=84.37 exit=84.29 net=-0.23% net_usd=-0.59 |
| 2026-05-04T07:41:01 | EXIT_REASON | signal_exit |
