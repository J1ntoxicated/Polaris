# Collection method — equity axes (split from INSTRUCTIONS.md)

DEMO/PAPER. How to collect + judge the equity candidate axes. Additive rank-
uplift only (flow_not_block). Scoring/hygiene stay in `INSTRUCTIONS.md`.

## Daily routine (US pre-market — Sydney evening, AEST). ~25-30 min, ~8-10 WebFetch
- T-0: StockAnalysis premarket (https://stockanalysis.com/markets/premarket/) → top gap-ups → queue.
- T-5: Finviz earnings — screener.ashx?v=111&f=earningsdate_todayafter then ...=tomorrow → catalyst calendar.
- T-10: Finviz groups (groups.ashx?g=sector&v=140) → note top 2-3 sectors by Week/Quarter RS → sector uplift.
- T-13: Finviz 52wk-high (screener.ashx?v=111&s=ta_newhigh&o=-volume, 1-2 pages) → high×high-volume crossover.
- T-18: FINRA daily short-vol (cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt) → short-vol ratio → squeeze uplift. Biweekly HighShortInterest.com cross-check.
- T-23: Finviz news (news.ashx) + Google News RSS (news.google.com/rss/search?q=TICKER+when:1d&hl=en-US&gl=US&ceid=US:en) → tag catalyst headline per candidate.
- T-28: Integrate — rank = axis-weighted score, write `data/intel/alpaca_seed.json` per CONTRACT.md.

## Evidence axes (research top-5) → how to collect + judge
1. PEAD / earnings surprise (STRONGEST): standardized surprise (SUE), + direction. 52wk-high proximity × positive surprise = amplified drift (George-Hwang-Li). Use earnings calendar + surprise; do NOT use raw beat/miss headline alone.
2. Analyst revisions/upgrades: revision BREADTH (many raising) > single upgrade. New-buy → 6mo drift. From Finviz news / Google News.
3. Sector/industry relative strength: breakout inside a strong industry persists (Moskowitz-Grinblatt). From Finviz groups.
4. Volume confirmation: abnormal high volume on the breakout (Gervais et al). From ta_newhigh o=-volume; compare vs average.
5. Insider opportunistic (cluster) buy — RARE bonus only: non-routine buys → +82bp/mo (Cohen-Malloy-Pomorski). Routine/10b5-1 = signal 0. Absence ≠ negative.
Short-interest: LOW SI = small + uplift; HIGH SI is ONLY a squeeze bonus WHEN a live catalyst exists — never a standalone thesis.
