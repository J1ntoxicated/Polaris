---
type: INSIGHT
id: INSIGHT-036
status: active
date: 2026-05-05
tags: [paper-engine, slippage, fill-model, live-readiness, P6-pure]
back_links: ["[[INSIGHT-032]]", "[[ADR-013]]", "[[ADR-014]]", "[[_NOW]]"]
expires: 2026-08-05
editable: true
reviewed_by: codex
mode: dev
---

# INSIGHT-036 — Paper engine internalizes slippage via realistic fill engine

## Problem

Paper-vs-live PnL divergence root cause: paper engine filled both entry and
exit at `tick_price` (last trade), but live execution is taker-side:
- BUY pays `ask` + walks deeper if size > L1 depth → market impact
- SELL hits `bid` − walks deeper → same on the other side

Live readiness audit (pre-fix): 32/100, slippage drag estimated at $27 over
153 trades, live EV friction delta = +0.08% per trade (LIVE_FRICTION_DELTA).

For thin pairs (PEPE/SHIB/ORDI), structural spread alone exceeds 0.10%,
silently destroying paper-positive EV when ported to live (LESSON-002).

## Fix (Phase 6, 2026-05-05)

`src/paper/slippage_model.py` (P6 pure) — fill price hierarchy:
1. **Walk orderbook** (top-N levels): BUY consumes asks, SELL consumes bids,
   blended VWAP for target USD size.
2. **L1 fallback**: cross to ask/bid if book empty.
3. **Default fallback**: `last × (1 ± 0.05%)` if no quote data.

Plus pre-entry **spread filter**: skip ENTER if `(ask−bid)/mid > 5bps`
(structural slippage from thin liquidity).

Wired into `realtime_runner.py`:
- Entry (line ~1145): `entry_price = compute_fill_price("buy", size, book, last, bid, ask)`
- Exit (line ~1064): `exit_price = compute_fill_price("sell", pos.size_usd, book, last, bid, ask)`
- Spread filter at entry path (line ~1108)

## Result

- `live_readiness_audit.py`: 32 → 35/100 (+3), slippage drag $27 → $6.90 (−75%)
- Live PnL estimate: $−59 → $−37 (+$22)
- Future trades will further compress divergence as new (slippage-internalized)
  trades dominate the sample. LIVE_FRICTION_DELTA reduced from 0.08% → 0.02%
  (residual: latency, partial fills, dark liquidity).

## Tests

`tests/paper/test_slippage_model.py` — 21 tests, all pure:
- `walk_book` 5 cases (top level / multi-level / partial / empty / zero)
- `compute_fill_price` 9 cases (buy/sell book / L1 fallback / default / partial blend)
- `compute_spread_bps` + `should_skip_entry_spread` 7 cases

## Next (Phase 7+)

- **Maker-only mode** for live: post-only limit at bid/ask (saves 4bps round-trip)
- **Liquidity-aware sizing**: cap size to top-5 ask depth × 0.10
- **L2 orderbook persistence** for backtest accuracy (currently only WS-derived top-5)
