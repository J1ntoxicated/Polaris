---
name: discovering-universe
description: Use to refresh the dynamic ticker universe (Layer 0) — fetches OKX SPOT instruments via /api/v5/market/tickers and Capital CFD market navigation tree, applies liquidity filters (24h vol > $30M learner-tunable, state=live, USDT-quote for OKX), and persists the active watchlist to SQLite. Run on startup, every 5min (OKX) and 10min (Capital), and on listing-change events.
---

# discovering-universe (P0 skill)

## When to use
- Startup (full refresh)
- Periodic (5min OKX / 10min Capital)
- Listing change event (new instrument)
- Manual Jin trigger (debug)

## Inputs
- venue: `okx` | `capital` | `both`
- mode: `full` | `delta` (default `delta`)

## Process

### OKX SPOT
1. `GET https://us.okx.com/api/v5/market/tickers?instType=SPOT` (with `x-simulated-trading: 1`)
2. Filter: `vol_24h > $30M` AND `state == "live"` AND symbol ends with `-USDT`
3. Result: ~100-150 active

### Capital CFD
1. `GET https://demo-api-capital.backend-capital.com/api/v1/marketnavigation` (with session token)
2. 5 카테고리 walk: forex (~70) + indices (~30) + commodity (~20) + crypto CFD (~50) ≈ 170
3. P2 candidate: shares ~5000 (P0 skip)

### Persist
- `universe(venue, symbol, vol_24h, last_updated, active)` table upsert
- Inactive 마킹 (last_updated > 1h)

## Outputs
- Active universe count + delta (added/removed)
- Vault update: `40_ops/universe_<date>.md` (digest if material change)

## Filter (learner-tunable)
- `vol_24h_min`: default $30M, learner #1 session_mult adjustable
- `tier_threshold`: high/mid/low boundary

## Failure handling
- OKX rate limit (20 req / 2s) → backoff 2s
- Capital session expire → re-auth + retry
- Venue down → use cached watchlist (TTL 1h)

## Cross-ref
- [[ADR-003]] Layer 0
- [[active-autonomous-vision]] §1
- agent: universe-scanner (gate 1, focus 30-ticker selection)
