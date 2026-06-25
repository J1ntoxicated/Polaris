---
name: executing-orders
description: Use to submit, modify, and cancel orders via the OKX SPOT demo (us.okx.com + x-simulated-trading 1) and Capital CFD demo (demo-api-capital.backend-capital.com) venue adapters. Handles idempotent order keys, constraint translation (cash vs margin/leverage/liquidation), order state normalization, and fill reception. P0 = REST polling; P1 = WebSocket fill stream.
---

# executing-orders (P0 skill)

## When to use
- Sized order from gate 5 (entry-sizer)
- Adjust/exit order from gate 7 (adaptive-exit)
- Manual cancel (risk-officer halt)

## Inputs
- order_intent: {venue, symbol, side, notional, entry_type, slippage_tier}
- idempotency_key: `strategy_id + symbol + timeframe + signal_ts`

## Process

1. policy_engine.check(mode, action="place_order", target=venue+symbol)
2. Idempotency check (dedup table query)
3. Constraint translate (cash spot OKX / margin lev Capital)
4. Submit via venue adapter:
   - OKX: `POST /api/v5/trade/order` with `x-simulated-trading: 1`
   - Capital: `POST /api/v1/positions` with session token
5. Order state normalize: {pending, partial, filled, cancelled, rejected}
6. Persist to `orders` table (venue column unified)
7. Emit event: `order_placed`

## Outputs
- order_id (venue-native + internal)
- order state stream

## Constraints

### OKX SPOT
- Long-only, no leverage
- Fee: 0.1% maker/taker (demo)
- Min order: per instrument (`minSz` from instruments endpoint)
- Tick size: per instrument

### Capital CFD
- Long/short, leverage native
- Lev ceilings: forex 30× / indices 20× / gold 20× / commodity 10×
- Fee: spread (built-in)

## Failure handling
- Venue 5xx → retry 3× exponential backoff
- 401 (OKX) → endpoint check (`us.okx.com` not `www.okx.com`) — see `feedback_okx_region_endpoint`
- Session expire (Capital) → re-auth + retry
- Rate limit (OKX 20 req/2s) → queue + backoff
- Reject → persist + post-trade reflector signal

## P0 vs P1
- P0: REST polling for fills (1s interval)
- P1: WebSocket fill stream (low-latency)

## Cross-ref
- [[ADR-003]] Per-Venue Adapters
- agent: risk-officer (policy_engine gate)
- skill `governing-risk` (hard cap enforcement parallel)
