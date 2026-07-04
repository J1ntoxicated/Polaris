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

## Collection method → `INSTRUCTIONS_COLLECT.md`
Daily pre-market routine (T-0..T-28, ~25-30 min) + the research top-5 evidence
axes (PEAD / revisions / sector RS / volume / insider) — how to collect + judge
each — live in the split file. Read it before a run.

## Scoring rubric (0-1, see CONTRACT thesis_tag)
`score_catalyst` = PEAD(SUE, ×52wk-high-proximity interaction) 0.35 + revision
breadth 0.20 + sector RS 0.20 + relative volume 0.15 + short-squeeze-with-
catalyst 0.10. Insider opportunistic buy = +0.05 bonus (cap total at 1.0).
Weights follow research strength (PEAD strongest, insider rarest).

Then apply a PROXIMITY decay to get the emitted `score`:
`score = score_catalyst × clamp(close/(0.99×high_52wk), 0, 1)²`.
A candidate at/through the trigger keeps its full catalyst score; one far below
`0.99×high_52wk` (our entry line) has its RANK naturally decayed — it still ships
in the feed (never dropped for distance). AVAV taught this: catalyst 0.56 but
-54% outside the trigger, so its rank must not sit near a fireable name. Score is
a rank-uplift term, never a size or a gate — decay lowers rank, it never blocks.

## Geometry + freshness self-report (per CONTRACT schema)
- `close` + `high_52wk` from the same quote used to verify the ticker (Finviz /
  StockAnalysis). Emit `dist_to_trigger_pct = 100*(close/(0.99*high_52wk)-1)`.
- `catalyst_age_days` = today − `catalyst_ts` date; `drift_consumed = true` when
  the initial pop has already faded (price pulled back after a touch / window is
  late). BB (10d elapsed, faded after the touch) = `drift_consumed:true`; AVAV
  (4d, early in the window) = `false`. This is a hint field, not a filter.

## Hygiene (mandatory)
- Verify ticker EXISTS on Finviz quote.ashx?t=SYMBOL; read `close`+`high_52wk` there.
- ≥2 INDEPENDENT live evidence URLs (prefer SEC/BLS/gov/wire). Every link must
  resolve NOW — no 404/403 (BB's Benzinga 404, ETH single-source were rejected).
  <2 live sources → drop the candidate.
- No guessing / no fabricated tickers, prices, dates. Only what a source shows.
- Set `expiry_ts` (end of US trading day). Expiry = bot ignores it (fail-safe).
- Forbidden words anywhere — keep at zero hits: 12주 · 90d gate · monthly review
  · regrets/ · posture standard · regulatory cap · professional risk · real-money
  safety · fractional Kelly is too aggressive in practice · 표본 부족 risk.
- Validate against `CONTRACT.md`. When in doubt, emit fewer, higher-confidence.

## Crypto / macro axes → `INSTRUCTIONS_CRYPTO_MACRO.md`
OKX crypto (24/7) + Capital CFD macro collection, sources, +10 min routine live
there. Weekends = crypto axis only. Uses `venue` okx/capital + crypto `thesis_tag`
+ `macro_events[]` (`CONTRACT_CRYPTO_MACRO.md`).
