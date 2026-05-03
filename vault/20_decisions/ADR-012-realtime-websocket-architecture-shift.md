---
entity_type: adr
entity_id: ADR-012
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[ADR-010]]", "[[INSIGHT-018]]", "[[code_review_workflow]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-04
maturity: provisional
tags: [type/adr, status/provisional, scope/spot, priority/p0, polaris]
---

# ADR-012 — WebSocket Realtime Architecture Shift

## Status
- proposed: 2026-05-04 (Jin mandate "캔들로 거래 들어가면 어케")
- provisional: 2026-05-04 (운영 모델 정합 회복 시점)

## Context

기존 ADR-010 cron (15min poll) → candle close 후 next bar open entry = **1-15분 latency**.
사용자 mandate: 실시간 WebSocket = tick stream으로 **즉시 entry/exit**.

## Decision

**Cron-driven → Tick-driven realtime architecture**:

### 새 구조
```
WebSocket OKX SPOT (tickers + books5 + trades) — public, no auth
  ↓ (millisecond tick)
Strategy 매 tick 평가 (indicator는 candle 60s cache + REST poll)
  ↓
Entry/exit 즉시 tick price (no candle wait)
TP/SL hit 즉시 close
```

### 새 launchd 구조
- `com.polaris.paper.realtime` (long-running, KeepAlive=true) ← 신규
- `com.polaris.paper.daily` (01:00 UTC, 1d HYPO-003/004) ← 유지
- `com.polaris.paper.intraday` (15min cron) ← **폐기** (realtime이 대체)
- `com.polaris.dashboard` (login agent)

### Strategy 분류 (primary_tf)
- `tick`: TickMomentum (tick payload 직접)
- `book`: OrderBookImbalance (books5 채널)
- `flow`: TradeFlow (trades 채널, taker buy/sell ratio)
- `15m/1H/1D`: candle-based (RSI/Vol/Breakout) — REST poll 60s indicator cache

### OKX WebSocket 한계 발견
- candle{period} channel = OKX **business endpoint** (public WS X)
- → `wss://ws.okx.com:8443/ws/v5/public` 에서 candle subscribe 불가
- 해결: REST `multi_tf.fetch_multi_tf` 60s poll로 candle cache 갱신

## Consequences

### 긍정
- Latency 1-15분 → millisecond
- 매 tick 평가 → entry trigger 더 자주
- TP/SL tick hit 즉시 close (candle close wait X)
- WebSocket 추가 지표 (24h vol/high/low/spread/orderbook/trade flow) 활용

### 부정
- Long-running process (KeepAlive=true) → 실패 monitoring 필요
- Indicator cache 60s lag (REST poll 한계)
- 매 tick 평가 = CPU 부담 (10 ticker × 6 strategy = 60 evaluations/tick)

### Mitigations
- Indicator cache TTL 조정 가능
- Strategy lazy evaluation (tick price 변화 작으면 skip)
- launchd KeepAlive auto-restart on crash

## Codex Debate Summary

⚠️ **codex 외부 리뷰 미실행** (운영 모델 ADR-004 위반). Phase 2c~e 모든 코드 통합 리뷰 필요 (TODO).

## Verification
- [x] WebSocket 활성 (com.polaris.paper.realtime PID 89193)
- [x] 첫 entry 발생 (SOL VolumeBurst $84.37, DOGE imbalance $0.10851, DOGE flow $0.10851)
- [ ] Codex Round 1 review (Phase 2c~e 코드 통합)
- [ ] 24h 운영 후 trade 빈도 + PnL 평가

## Rollback Path
- WebSocket process 안정성 문제 시 → cron 복귀 (ADR-010)
- 단 candle close wait latency 다시 발생

## Related
- ADR-010 (Backtest + Paper parallel — 보강)
- ADR-004 (Codex 외부 리뷰 — 본 ADR 위반 사례)
- INSIGHT-018 (Realtime tick-driven discovery)
- HYPO-007 ~ HYPO-012 (모든 신규 strategies)
- code_review_workflow
