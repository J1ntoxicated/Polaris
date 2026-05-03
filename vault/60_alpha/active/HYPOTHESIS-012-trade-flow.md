---
entity_type: hypothesis
entity_id: HYPO-012
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-018]]", "[[ADR-012]]"]
mode: alpha
reviewed_by: codex
maturity: verified
tags: [type/hypothesis, status/active, scope/alpha, polaris]
---

# HYPO-012 — taker buy ratio > 0.6 (last 100 trades)

## Hypothesis
**trade flow** — taker buy ratio > 0.6 (last 100 trades) 가 OKX SPOT live fee 0.14% (round-trip) 통과 + paper 30+ trades 후 expectancy > 0.

## Method (Realtime tick-driven, ADR-012)
- WebSocket OKX SPOT
- Tickers: 8 majors
- Fee: 0.0014 (LV3 가정)
- Code: src/strategies/trade_flow.py
- TP/SL: +0.6% / -0.35% (intraday default, runner enforces)

## Status
- BACKTEST viable (positive expectancy filter applied per-ticker)
- PAPER realtime active (com.polaris.paper.realtime)
- 30+ trades 도달 시 Promotion Gate 평가

## Risk
- 단타 본질: hit rate < 50% (큰 winner + many small loss)
- INSIGHT-007 fee 함정 (live LV3 0.14% 가정 — paper Lv1 0.7% X)
- Live 라이브 전환 시 LV3 fee 실제 검증 필수

## Related
- ADR-010 (Backtest + Paper)
- ADR-012 (Realtime shift)
- INSIGHT-007 (fee 함정)
- INSIGHT-018 (tick-driven 발견)
- src/strategies/trade_flow.py
