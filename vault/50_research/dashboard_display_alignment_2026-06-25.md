---
type: digest
status: built-reviewed
date_created: 2026-06-25
tags: [dashboard, display, snapshot, visualizer, display-only, demo]
---

# Dashboard Display Alignment — 4 fixes (2026-06-25) [BUILT-REVIEWED]

> Jin frustration: the DB is correct but the screen confuses. 4 DISPLAY-ONLY
> fixes to the read-only snapshot/visualizer path — zero trading/sizing/gating
> /exit/order behaviour change. DEMO/PAPER, aggressive bias preserved. Builder
> ≠ reviewer: fresh-Claude adversarial review = APPROVE. Backlink:
> [[structure_hardening_2026-06-23]] · [[ADR-003-8-layer-architecture]].

## The 4 issues → fixes
1. **PnL gross/fee/net separated** ("수익이랑 피랑 따로 적어"). `ClosedTrade`
   gains `net_usd = pnl_usd(gross) − real_fee_usd`. TRADES tab now shows
   GROSS$ / FEE$ / NET$ as 3 columns (FEE = gross − net so the three
   reconcile). Honest: a small gross nets negative once the fee bites (live:
   US100 gross +0.37, fee 0.42 → net −0.05).
2. **Position direction** ("롱인데 셀로 표기"). Recent trades showed
   `side_close` ('sell' = a long EXIT) → longs read as shorts. Added
   `ClosedTrade.position_side` (from `positions.side` via
   contribution_id==position_id); board + mobile label by it. Fallback when the
   position row is absent: sell-close ⇒ long, buy-close ⇒ short.
3. **Entry regime** ("레짐 다 -"). `positions.entry_regime` is populated
   (chop/bull_trend/crisis) but the trade rows never joined it. Added
   `ClosedTrade.entry_regime` + `PositionRow.entry_regime` (positions join;
   `MAX(entry_regime)` over the logical-key GROUP BY, same precedent as
   exit_state/stop).
4. **Non-USD quote ccy** (J225 71,847 = JPY shown raw). New
   `_quote_ccy_for_symbol(venue, symbol, universe_quote)`: OKX trusts
   `universe.quote_ccy`; Capital corrects the "USD" discovery placeholder — FX
   pair → trailing 3 chars, index epic → curated map (J225→JPY, EU50→EUR,
   UK100→GBP, HK50→HKD…), only the supported set (mirrors
   `_QUOTE_RATE_EPICS`) is labelled, else USD. `quote_ccy` rides on both
   `ClosedTrade` + `PositionRow`; frontend `fmtPxCcy` prepends ¥/€/£… to the
   price cells ONLY — size_usd/pnl stay $.

## Display-only invariant (verified)
- Modified dataclasses (`PositionRow`/`ClosedTrade`) are the dashboard's own
  types (distinct from `core.learners.ClosedTrade`); consumed only by render /
  `dataclasses.asdict` serialization. New fields are additive keyword defaults —
  no positional unpack breaks. New SQL runs only in the read-only snapshot path.
- Round-trip-fee nicety prototyped then REVERTED: overloading `real_fee_usd`
  with entry+close sum broke 2 e2 fee tests + EDGE est_cost → out of scope.
  Close-only re-priced fee kept; true round-trip fee = follow-up.

## Verify
- TDD: `tests/test_dashboard_display_alignment.py` (7 tests — gross/fee/net,
  position_side vs side_close, entry_regime on trade + position, quote_ccy FX/
  index/unsupported/OKX branches). Live-DB e2e: 40 trades, net=gross−fee 0
  violations, 0 empty position_side / entry_regime.
- mypy --strict + ruff clean; 3 JS `node --check`; 137 dashboard/snapshot/render
  tests green. Preview MCP: TRADES header = …SIDE…REGIME…GROSS$ FEE$ NET$…;
  row0 SIDE=long (close=sell), REGIME=chop, fmtPxCcy(71847,JPY)=¥71,847.0.
- Files: snapshot_models · snapshot_q_common (`_quote_ccy_for_symbol`) ·
  snapshot_q_positions · snapshot_queries (re-export) · snapshot_sections ·
  board.js (`fmtPxCcy`) · board_tabs.js · mobile.js.
