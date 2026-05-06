---
entity_type: paper_log
entity_id: paper_log_trump_usdt_volume_burst
auto: true
last_modified: 2026-05-04
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: volume_burst
ticker: TRUMP-USDT
---

# Paper Log — TRUMP-USDT volume_burst

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-04T01:03:18 | BALANCE | cash=$5000.00 equity=$5000.00 realized_pnl=$+0.00 open=0 closed=0 |
| 2026-05-05T17:31:16 | OPEN | id=TRUMP-USDT-1777966275414 dir=1 entry=2.34 size=$132 |
| 2026-05-05T18:19:24 | CLOSE | id=TRUMP-USDT-1777966275414 entry=2.34 exit=2.46 net=+4.88% net_usd=+6.42 |
| 2026-05-05T18:19:24 | EXIT_REASON | tp_hit:+0.0508 |
| 2026-05-05T18:20:25 | OPEN | id=TRUMP-USDT-1777969224007 dir=1 entry=2.45 size=$132 |
| 2026-05-05T18:38:05 | CLOSE | id=TRUMP-USDT-1777969224007 entry=2.45 exit=2.40 net=-2.24% net_usd=-2.96 |
| 2026-05-05T18:38:05 | EXIT_REASON | sl_hit:-0.0200 |
| 2026-05-05T18:39:06 | OPEN | id=TRUMP-USDT-1777970346191 dir=1 entry=2.40 size=$132 |
| 2026-05-05T19:31:12 | CLOSE | id=TRUMP-USDT-1777970346191 entry=2.40 exit=2.36 net=-1.91% net_usd=-2.51 |
| 2026-05-05T19:31:12 | EXIT_REASON | signal_exit |
