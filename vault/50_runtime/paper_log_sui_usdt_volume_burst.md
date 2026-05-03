---
entity_type: paper_log
entity_id: paper_log_sui_usdt_volume_burst
auto: true
last_modified: 2026-05-04
expires: never
editable: false
back_links: ["[[60_alpha/_README]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: none
tags: [meta, paper, alpha, polaris, mode/alpha]
strategy: volume_burst
ticker: SUI-USDT
---

# Paper Log — SUI-USDT volume_burst

Append-only. ADR-010 paper 운영 기록.

## Events

| timestamp | event | detail |
|---|---|---|
| 2026-05-04T00:55:38 | OPEN | id=SUI-USDT-1777816800000 dir=1 entry=0.93 size=$250 |
| 2026-05-04T00:55:38 | BALANCE | cash=$4750.00 equity=$5000.00 realized_pnl=$+0.00 open=1 closed=0 |
| 2026-05-04T00:56:09 | BALANCE | cash=$4750.00 equity=$4999.17 realized_pnl=$+0.00 open=1 closed=0 |
| 2026-05-04T00:59:57 | CLOSE | id=SUI-USDT-1777816800000 entry=0.93 exit=0.93 net=-0.54% net_usd=-1.34 |
| 2026-05-04T00:59:57 | EXIT_REASON | sl_hit:-0.0040<=-0.0035 |
| 2026-05-04T00:59:57 | OPEN | id=SUI-USDT-1777816800000 dir=1 entry=0.93 size=$250 |
| 2026-05-04T00:59:57 | BALANCE | cash=$4748.72 equity=$4998.66 realized_pnl=$-1.34 open=1 closed=1 |
