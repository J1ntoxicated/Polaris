---
entity_type: paper_log
entity_id: paper_log_ordi_usdt_trade_flow
auto: true
last_modified: 2026-05-04
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: trade_flow
ticker: ORDI-USDT
---

# Paper Log — ORDI-USDT trade_flow

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-04T07:38:16 | OPEN | id=ORDI-USDT-1777844296368 dir=1 entry=5.28 size=$200 |
| 2026-05-04T07:42:10 | CLOSE | id=ORDI-USDT-1777844296368 entry=5.28 exit=5.26 net=-0.50% net_usd=-1.00 |
| 2026-05-04T07:42:10 | EXIT_REASON | sl_hit:-0.0036 |
| 2026-05-04T07:42:10 | OPEN | id=ORDI-USDT-1777844530468 dir=1 entry=5.26 size=$200 |
| 2026-05-04T07:44:06 | CLOSE | id=ORDI-USDT-1777844530468 entry=5.26 exit=5.25 net=-0.33% net_usd=-0.66 |
| 2026-05-04T07:44:06 | EXIT_REASON | signal_exit |
| 2026-05-04T07:48:02 | OPEN | id=ORDI-USDT-1777844882129 dir=1 entry=5.26 size=$200 |
