# Cowork Session — Alpaca Watchlist Intel

DEMO/PAPER trading bot (virtual funds). This feed only ADDS candidates and
RAISES rank — never a block/skip/reject filter (flow_not_block).

## Mission
Surface US-equity momentum / 52-week-high breakout candidates that have a REAL
catalyst, so the Polaris bot's Alpaca watchlist gets seeded ahead of the RTH
open. Output a scored candidate list per `CONTRACT.md`. INTENDED consumption (ingest
side NOT yet wired — see CONTRACT "Ingest status"): the bot unions your symbols
into its active/focus set (additive, seat-alongside) so the deterministic
`equity_52wk_high_breakout` strategy evaluates them once each symbol has ≥253
daily bars. Until that reader lands, this session produces the feed only.

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

## Scoring rubric (0-1, see CONTRACT thesis_tag)
Base = PEAD(SUE, ×52wk-high-proximity interaction) 0.35 + revision breadth 0.20
+ sector RS 0.20 + relative volume 0.15 + short-squeeze-with-catalyst 0.10.
Insider opportunistic buy = +0.05 bonus (cap total at 1.0). Weights follow the
research strength ranking (PEAD strongest, insider rarest). Score is a
rank-uplift term, never a size or a gate.

## Hygiene (mandatory)
- Verify ticker EXISTS: confirm on Finviz quote.ashx?t=SYMBOL before emitting.
- Every candidate needs ≥1 evidence URL (source of the catalyst). No URL → drop.
- No guessing / no fabricated tickers, prices, or dates. Only what a source shows.
- Set `expiry_ts` (e.g. end of the US trading day). Expiry = bot ignores it (fail-safe).
- Forbidden words anywhere in output — keep this list at zero hits:
  12주 · 90d gate · monthly review · regrets/ · posture standard · regulatory cap
  · professional risk · real-money safety · fractional Kelly is too aggressive in
  practice · 표본 부족 risk.
- Output MUST validate against `CONTRACT.md`. When in doubt, emit fewer, higher-confidence candidates.

## Crypto / macro axes → `INSTRUCTIONS_CRYPTO_MACRO.md`
OKX (crypto, 24/7) + Capital CFD (macro/commodity) collection — per-axis method,
verified free/no-login sources, and the daily +10 min routine — lives in the
split file `INSTRUCTIONS_CRYPTO_MACRO.md`. Weekends: crypto axis only (OKX 24/7);
macro/CFD axes are weekday-session bound. Those candidates use `venue` okx /
capital and the new `thesis_tag` + `macro_events[]` fields in `CONTRACT.md`.
