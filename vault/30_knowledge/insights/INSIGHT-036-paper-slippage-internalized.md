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

## Phase 7 (2026-05-05) — Liquidity-aware sizing (Layer 3)

Spread filter blocked wide-spread entries, but tight-spread thin pairs still
slipped on size walks (BLUR 10.2bps observed with $300 size). Phase 7 caps
size to **10% of top-5 ask depth**.

`compute_liquidity_cap("buy", book, max_book_fraction=0.10)` (P6 pure, 6 tests).
Sizing: `min(sizing, hard_cap, cash, liq_cap)`.

## Codex round-1 (2026-05-05) — 3 critical fixes accepted

1. **liq_cap=0 silent bypass** → SKIP entry with `[LIQ-SKIP]` log (book missing
   = unknown liquidity, not safe).
2. **Spread filter bypass for invalid quotes** → `compute_spread_bps` `inf`
   triggers SKIP unconditionally (caller no longer guards on `bid>0 and ask>0`).
3. **Exit notional drift** — entry `size_usd` is stale once price moved.
   Fix: `exit_notional = (size_usd / entry_price) × tick_price` so exit
   slippage matches realized base-qty notional.

Codex round-2: NONE (all 3 fixes verified clean — entry_price>0 enforced,
_liq_skip is final gate, inf path division-safe).

## Cumulative Result (Phase 6 + 7 + Codex round-1)

- Live readiness: **22 → 35 → 41/100** (MARGINAL threshold reached)
- Paper PnL: **−$59 → −$30 → +$11.82** (negative-to-positive flip)
- Live PnL est: **−$77 → −$37 → +$2.14** (first positive estimate)
- Overall EV: **−0.42% → −0.084% → +0.024%** per trade
- Slippage drag: $109 → $6.96 → $9.68 (drag stable, sample grew 154→201)
- Tests: 806 + 6 new (Phase 7) = 812 passing
