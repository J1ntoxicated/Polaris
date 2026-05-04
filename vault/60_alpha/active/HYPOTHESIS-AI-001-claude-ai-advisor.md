---
entity_type: hypothesis
entity_id: HYPO-AI-001
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-034]]", "[[ADR-012]]"]
mode: alpha
reviewed_by: codex
maturity: paper_active
tags: [type/hypothesis, status/active, scope/alpha, polaris, ai, phase3]
---

# HYPO-AI-001 — Claude AI Advisor Realtime Analysis

## Hypothesis

**AI-driven entry** — Claude Haiku per-tick market analysis, aggregating multi-source signals (tick + book + flow + multi-TF + funding + regime) → confident ENTER_LONG at ≥ 0.65 confidence exceeds 0.14% round-trip fee.

Jin mandate (2026-05-04): '원래 의도는 AI 개입 실시간 분석으로 거래'. 모태 advisor 핵심 패턴 인수.

## Method (Realtime tick-driven, Phase 3)

- WebSocket OKX SPOT (existing)
- Tickers: BTC-USDT, ETH-USDT, SOL-USDT
- Model: claude-haiku-4-5-20251001
- Entry: confidence ≥ 0.65 → ENTER_LONG $200
- Exit profile: liquidation (TP 1.5%, SL 0.7%, max 30min)
- Rate limit: 1 API call per ticker per 60s
- Cache: same market_state hash → 5min (no duplicate call)
- Code: src/strategies/ai_advisor.py

## Market State Sources

| Source | Fields |
|--------|--------|
| OKX tickers WS | last, change_24h, taker_buy_ratio, book_imbalance |
| OKX trades WS | VPIN (50-trade window) |
| Candle cache 1H | RSI approximation (14-period) |
| BTC 1D cache | trend_1d, trend_4h (proxy), regime |
| Binance Futures REST | funding_8h (60s poll, shared with HYPO-027) |

## Cost Profile

- $0.0006/call × 180 calls/h = ~$0.11/h
- Profitable if ≥ 1 $1.5+ net winner per day

## Status

- PAPER realtime active (com.polaris.paper.realtime)
- Phase 3 launch: 2026-05-04
- Deprecation gate: n=10 / -$5 cumulative (fast_fail)
- Promotion gate: 30 trades + WR > 50% + avg_net > fee

## Validation Criteria

| Gate | Threshold |
|------|-----------|
| Fast fail (trades) | n=10 |
| Fast fail (cumulative loss) | -$5 |
| Promotion min trades | 30 |
| Promotion min WR | 50% |
| Promotion min avg_net | > 0.14% (fee cover) |
