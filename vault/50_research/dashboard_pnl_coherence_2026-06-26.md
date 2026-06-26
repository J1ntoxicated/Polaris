---
type: digest
status: active
date_created: 2026-06-26
tags: [dashboard, pnl, display-only, demo-paper, fee, quote-ccy]
---

# Dashboard PnL coherence — #49 round-trip fee + #50 quote→USD + open-net uPnL

DEMO/PAPER, display-only fix (per-fill $ truth / sizing / gating / exit / venue
untouched). Single commit on `fix/dashboard-pnl-coherence` (base `b9fa0f0`).
Root of "내 계산이 안 맞아": the TRADES tab and the realised headline disagreed,
and open positions showed gross-only uPnL ("오픈은 프로핏인데 청산하면 로스").

## #49 — per-position net missed the entry-leg fee
`snapshot_sections.py` `net_usd = pnl − real_fee` subtracted only the CLOSE-leg
fee. The headline `_daily_realised_pnl` (snapshot_q_equity.py) nets
`SUM(fee_usd)` over BOTH legs (entry+close). So Σ(per-position net) overstated
by the entry fee. Fix: add `entry_real_fee` (entry notional, USD) →
`net_usd = pnl − close_fee − entry_fee`; FEE$ column (gross−net) auto-reconciles.

## #50 — open-position uPnL/notional were in quote-ccy, not USD
`snapshot_q_positions.py` `upnl = (last−entry)·qty·sign`,
`size_usd = entry·|qty|` used prices in the price-QUOTE ccy (J225=JPY,
EU50/DE40/IT40/NL25=EUR, HK50=HKD) with no conversion → polluted
equity/DD/Sharpe (snapshot sums upnl_usd/size_usd). The SAME un-converted entry
notional also inflated #49's entry fee ~150× for JPY (entry_px·qty as USD →
J225 phantom ~$114 entry fee). Fix: new `_quote_usd_rate` in
`snapshot_q_common.py` — display mirror of the trading path `_QUOTE_RATE_EPICS`
(`_production_capital_sizing.py`), reads latest `capital:<PAIR>` 1m bar close,
USD-equiv→1.0, missing/zero/unknown→1.0 graceful. Applied to upnl/size_usd and
to the recomputed entry notional in #49. `delta_pct` stays a ratio; price cells
keep the quote-ccy label (#11).

## #N — open-position uPnL NET (Jin 2026-06-26, bundled)
Live open uPnL showed only gross ("오픈은 프로핏인데 청산하면 로스"); 125/191
positive closes (65%) flipped net-negative once fees bit. Add
`PositionRow.upnl_net_usd = upnl_usd − [real_fee_usd(venue, entry_notional) +
real_fee_usd(venue, current_notional)]` (both USD via the #50 rate). New
`uPnLnet$` open-positions column (board_tabs.js), coloured by NET sign.

## Live verify (data/polaris_live.sqlite)
- Σ per-position net **$1,937.50** vs headline **$1,938.93** (residual ~$1.4 =
  recompute-vs-stored fee on stale-reconstruct closes); was off ~**$998** pre-fix.
- J225 entry fee now **$0.71** (== its close fee), not ~$114; close fee ==
  stored `fee_usd` exactly. TRADES J225: GROSS$0.44/FEE$0.29/NET$0.16/+0.09%.
- Open `uPnLnet$`: US30 gross $0.00 → net **−$0.40 red** (gross-flat → net loss).

## Files (single commit)
`snapshot_q_common.py` (`_quote_usd_rate`+`_QUOTE_RATE_PAIRS`) ·
`snapshot_q_positions.py` (rate-convert upnl/size + `upnl_net_usd`) ·
`snapshot_models.py` (`PositionRow.upnl_net_usd`) · `snapshot_sections.py`
(quote_ccy up + entry_real_fee USD + net_usd) · `snapshot_queries.py` (re-export)
· `board_tabs.js` (`uPnLnet$` column) · `test_dashboard_pnl_coherence.py` (6 new)
+ alignment net assertion. PRE-EXISTING POS colgroup off-by-one left untouched
(surgical) → separate task.

Verify: mypy --strict + ruff clean, node --check OK, 548 dashboard/fee/equity
tests pass. 2 fresh-agent adversarial reviews = APPROVE (invert table byte-exact
mirror, close fee untouched, ratios invariant, net flip genuine, 0 keyword hits).
