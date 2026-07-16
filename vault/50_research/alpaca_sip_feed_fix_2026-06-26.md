---
type: research
status: recorded
date_created: 2026-06-26
tags: [research, backfilled-frontmatter]
---

# Alpaca realtime fix — SIP feed switch + liquidity filter (2026-06-26)

DEMO/PAPER. flow_not_block preserved. Base e6c81b6.

## Problem
Alpaca WS on free IEX (30-symbol cap). >30 subscribe → "symbol limit exceeded"
→ reconnect churn → sparse ticks (6503/10min vs OKX 60812 steady). Jin holds a
paid Alpaca read entitlement (SIP, unlimited) → churn dissolves on SIP.

## Change
- **Feed switch**: `POLARIS_ALPACA_FEED` (default `sip`, `iex` fallback). SSOT
  resolver `alpaca_feed_token()` in `core/universe/schema.py` (no layer inversion;
  venues→core import). `ws.py` maps token→URL; `adapter.fetch_bars` feed default
  now env-driven (was pinned `iex` for the old delayed-SIP account).
- **Graceful SIP→IEX downgrade**: on the `{"T":"error","msg":"insufficient
  subscription"}` control frame (verified live), `parse_message` flips
  `_active_feed`→iex (one-shot warn, no key value logged); reconnect lands on IEX.
  Connect-failure path deliberately NOT a trigger (same host both feeds → transient).
- **WS budget**: `ws_budget_for_venue('alpaca')` feed-driven — SIP 60 / IEX 30.
  env-raisable `POLARIS_WS_BUDGET_ALPACA`. Client-side IEX 30-cap (`_effective_symbols`)
  = runtime-downgrade safety net. OKX 60 / Capital 40 unchanged.
- **Liquidity filter** (re-applied from divergent acb0ef4): real Alpaca spread
  plumbed from snapshot `latestQuote` bid/ask; Alpaca `max_spread_bps` 0→100.
  Excludes 88%-spread junk (MGN/WHLR/ARQQ). Membership-eligibility test, not a
  block — wide-spread names still WATCHED/scored, only `trade_eligible` deferred.

## SIP smoke (task ④) — KEY FINDING
`.env` has ONLY `ARCHIVE_ALPACA_PAPER_*`; the active `ALPACA_PAPER_API_KEY` /
`ALPACA_PAPER_SECRET` slots are EMPTY. `resolve_alpaca_credentials()` falls back to
the archive paper key, which authenticates but returns **INSUFFICIENT_SUBSCRIPTION
on SIP** (no entitlement) and CONNECTION_LIMIT on IEX (auth OK, live bot holds the
conn). → Jin's paid SIP key is NOT yet in `.env`. Drop it into the active
`ALPACA_PAPER_*` slots and SIP activates automatically (default feed=sip). Until
then the runtime fallback correctly downgrades to IEX on the verified error frame.
Key values never printed (booleans/categories only).

## Verify
TDD (feed env branch / SIP→IEX downgrade / WS budget SIP+IEX / IEX 30-cap /
liquidity eligibility). 132 touched tests pass; mypy --strict + ruff clean; fresh
adversarial review APPROVE (flow_not_block confirmed, 0 key-leak, OKX/Capital
unchanged). 2 pre-existing base failures unrelated (rank_top_n stale 120 vs 1500;
sentinel full-sweep bound).
