# Crypto / Macro Intel — collection (split from INSTRUCTIONS.md)

DEMO/PAPER (virtual funds). Same law: this feed only ADDS candidates / RAISES
rank and injects session CONTEXT — never a block/skip/reject (flow_not_block).
Crypto → OKX venue; macro/commodity → Capital CFD context + `macro_events[]`.

## Weekend note
OKX SPOT is 24/7 → the crypto axis (unlock / listing / etf_flow / news) is the
ONLY axis that stays valid on Sat/Sun. Capital CFD + the macro calendar are
weekday-session bound, so on weekends run the crypto axis alone and skip macro.

## Axes → collect + judge (verified sources only, all no-login / free)
1. Token unlock — CryptoRank https://cryptorank.io/token-unlock (WebFetch direct):
   token · date · amount · %MCap. Tag only unlocks within 7d with a large %MCap.
   → `thesis_tag: token_unlock`.
2. Listings / delistings — OKX announcements API (no-auth JSON, code 0):
   `.../api/v5/support/announcements?annType=announcements-new-listings` and
   `...=announcements-delistings`. Diff titles vs prior day; symbol-match =
   catalyst uplift. (Read on www host; bot trades us.okx.com — unrelated.)
   → `thesis_tag: listing`.
3. BTC/ETH ETF daily flow — Bitbo https://bitbo.io/treasuries/etf-flows/ (static
   HTML table, prior-day final). ETH via one WebSearch snippet (Farside/SoSoValue).
   Net-flow sign + size = context. → `thesis_tag: etf_flow`.
4. Crypto news catalyst — Cointelegraph https://cointelegraph.com/rss + CoinDesk
   https://www.coindesk.com/arc/outboundfeeds/rss/ (standard RSS 2.0). Scan
   headlines for OKX-universe symbols. → `thesis_tag: crypto_catalyst`.
5. Econ calendar (CPI/FOMC/NFP/ECB/BOE) — ForexFactory weekly JSON
   https://nfs.faireconomy.media/ff_calendar_thisweek.json (no-auth array;
   date ISO8601 -04:00 ET → convert to UTC). High-impact today/tomorrow →
   CFD session context. → top-level `macro_events[]`, `thesis_tag: macro_event_window`.
6. OPEC / commodity — same FF JSON carries Crude Oil Inventories · Natural Gas
   Storage · OPEC meetings (0 extra fetch). EIA weekly schedule
   https://www.eia.gov/petroleum/supply/weekly/schedule.php (static, holiday
   shifts). → `macro_events[]`, `thesis_tag: commodity_event`.
7. Index rebalance — FTSE Russell/LSEG press releases (WebFetch direct) + a
   quarterly WebSearch for S&P/Nasdaq. Quarterly cadence (3/6/9/12, effective
   3rd-Friday close) → zero daily cost. → `macro_events[]` / `commodity_event`.

## Daily +10 min routine (append to the equity routine; all free / no-login)
1. FF weekly JSON — 1 fetch → today+tomorrow High-impact UTC-converted as CFD
   session context; oil events come in the same file. [2 min]
2. OKX announcements — 2 fetch (listings + delistings) → new-title diff vs prior
   day; symbol match → catalyst uplift. [2 min]
3. Bitbo — 1 fetch → prior-day BTC ETF net-flow sign/size; ETH via 1 WebSearch. [2 min]
4. CryptoRank unlock — 1 fetch → tag only 7-day large-%MCap unlocks to watchlist. [2 min]
5. Cointelegraph RSS — 1 fetch → scan headlines for OKX-universe symbols. [2 min]
- Quarterly (first week of 3/6/9/12): FTSE / S&P rebalance WebSearch.
- DO NOT fetch Farside · CoinGlass · spglobal · coinmarketcal directly
  (403 / JS-only) — use the verified paths above only.
