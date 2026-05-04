---
entity_type: paper_log
entity_id: paper_log_ordi_usdt_breakout_momentum
auto: true
last_modified: 2026-05-04
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: breakout_momentum
ticker: ORDI-USDT
---

# Paper Log — ORDI-USDT breakout_momentum

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-04T06:48:53 | BALANCE | cash=$5000.00 equity=$5000.00 realized_pnl=$+0.00 open=0 closed=0 |
| 2026-05-04T11:47:42 | OPEN | id=ORDI-USDT-1777859262163 dir=1 entry=5.51 size=$200 |
| 2026-05-04T11:49:04 | CLOSE | id=ORDI-USDT-1777859262163 entry=5.51 exit=5.49 net=-0.54% net_usd=-1.08 |
| 2026-05-04T11:49:04 | EXIT_REASON | sl_hit:-0.0040 |
| 2026-05-04T11:50:05 | OPEN | id=ORDI-USDT-1777859405409 dir=1 entry=5.46 size=$200 |
| 2026-05-04T11:50:30 | CLOSE | id=ORDI-USDT-1777859405409 entry=5.46 exit=5.44 net=-0.51% net_usd=-1.01 |
| 2026-05-04T11:50:30 | EXIT_REASON | sl_hit:-0.0037 |
| 2026-05-04T11:51:30 | OPEN | id=ORDI-USDT-1777859490811 dir=1 entry=5.42 size=$200 |
| 2026-05-04T11:53:04 | CLOSE | id=ORDI-USDT-1777859490811 entry=5.42 exit=5.46 net=+0.47% net_usd=+0.94 |
| 2026-05-04T11:53:04 | EXIT_REASON | tp_hit:+0.0061 |
