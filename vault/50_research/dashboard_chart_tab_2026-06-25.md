---
type: research
status: active
date_created: 2026-06-25
tags: [dashboard, visualizer, chart, technicals, display-only, demo]
related: [[feedback_dashboard_english_only]], [[feedback_dashboard_bloomberg_dense_no_cards]], [[_NOW]]
---

# Dashboard Chart Tab — per-ticker candles + all technicals (Jin request, 2026-06-25)

DEMO/PAPER, **display-only · READ-ONLY** (zero behavior change; trading/sizing untouched).
New "Chart" tab on the :8770 web dashboard: pick a ticker → candlestick + volume +
every computed technical as a graph. Built TDD, fresh-Claude adversarial review
(APPROVE-WITH-NITS, nits actioned), verified live in-browser against the running bot DB.

## What landed
- **Backend (new, read-only)** `tools/visualizer/chart_data.py`: pure indicator **SERIES**
  math (sma/ema/rsi-Wilder/macd 12·26·9/bollinger 20·2/atr-Wilder/stochastic/rolling-vwap/
  adx-Wilder/momentum) + `build_ticker_chart(conn, venue, symbol, resolution, limit)` +
  `list_chart_symbols(conn)`. Reads the `bars` table via `read_recent_bars`.
  - **Why a new module, not `build_real_market_view`**: that returns ONE scalar per
    indicator (the latest gate reading); a chart needs the full per-bar line. The series
    conventions mirror `_production_indicators.py` so the canvas agrees with the bot.
  - MACD/Stochastic/true-VWAP were **absent** from the trading indicators → computed here.
- **Server** `server.py`: `_ticker_chart(path)` + `/api/ticker_chart` route (`?list=symbols`
  for the selector; `?venue=&symbol=&resolution=` for one ticker). Fresh `mode=ro` conn,
  closed in `finally`; graceful empty payload on missing-DB/unknown-symbol/unknown-resolution
  (never raises → board poll safe). Route wrapped like its `/api/*` siblings.
- **Frontend** `static/chart.js` + vendored `static/vendor/lightweight-charts.standalone.production.js`:
  self-injects `#pane-chart` and registers its renderer (so the only shared-file edits are
  +1 TABS line in `board.js` and +2 `<script>` lines in `index.html` — minimal conflict
  surface vs. the concurrent dashboard builder). Candles + volume + EMA20/EMA50/BB/VWAP
  overlays on the price pane; RSI/MACD/ADX sub-panes. Venue-grouped symbol selector +
  venue-aware resolution selector (1m/5m/15m/1h/1d → native `1m/5m/15m/1H/1D`). English UI.

## Decisions / corrections
- **Lib license**: task said "lightweight-charts MIT" — v4.2.3 is actually **Apache-2.0**
  (permissive, fine for a private demo dashboard). Pinned + vendored (no CDN → offline-robust,
  matches the dashboard's all-local pattern). v4 API (`addCandlestickSeries` etc.).
- **#12 technical-store hook**: `build_ticker_chart` docstring marks the seam — swap the
  on-demand `_indicators(...)` for a store read keyed by `(venue,symbol,native_interval)`
  when it exists; on-demand stays the fallback. Now = on-demand (as scoped).
- Resolutions are venue-dependent (okx 1m/15m/1h · capital 1m/5m/1h · alpaca 1m/1d); the
  selector only offers intervals that actually have stored bars.

## Verification
18 tests (incl. explicit MACD/ADX index-alignment), `mypy --strict` clean on the new module,
`ruff` clean, `node --check` on JS. Live browser (worktree server :8772 vs untouched live
:8770): Chart tab switches, 256 symbols, candles+overlays+3 sub-panes render for AAPL/BTC,
symbol+resolution switching re-renders, **zero console errors**. server.py's 4 `no-any-return`
mypy errors are **pre-existing** (not from this change) — left untouched (surgical).

## Open / follow-up
- Chart draws stored history with no dead-feed freshness guard (intentional for a chart vs.
  the trading path); could surface newest-bar age in the status line later (not required).
- When yahoo bars land they appear automatically (same `bars` table). When the #12 technical
  store lands, point the reader at it via the documented hook.
