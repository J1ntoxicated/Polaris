---
type: research
status: recorded
date_created: 2026-06-27
tags: [research, backfilled-frontmatter]
---

# Donchian breakout × indices_global — generalization test (REJECT)

> 2026-06-27 · Phase2 fan-out gate (#78) · DEMO/PAPER · real yfinance index bars

## Question
Does the Donchian-30 + mom-20 breakout archetype (xau_indices_trend, brief-claimed
+97bps/day, "BEST FIT for trending equity indices") generalize across the
indices_global class, or is it overfit to a few instruments?

## Method
Replicated xau_indices_trend.py EXACTLY (close>donchian_high_30 & mom_20>0 long,
symmetric short, no look-ahead window bars[-31:-1]). Chandelier ATR(14)×3 let-run
exit (the validated gold exit). 16 equity-index epics → yfinance ^GSPC/^NDX/^DJI/
^RUT/^N225/^FTSE/^GDAXI/^FCHI/^STOXX50E/^HSI/^AXJO/FTSEMIB.MI/^AEX/^IBEX/^SSMI +
FNGS(NYFANG proxy). Capital CFD spread round-trip cost (0.75-8bps/epic) + 1bps
slippage/side. OOS = last 40% chronological.

## Result — OVERFIT / REJECT
- **OOS net-positive: 3/16 instruments (19%)** — US500, HK50 (both ~+0.1% mean,
  marginal), NYFANG (proxy ETN, only 12 OOS trades = noise). Pooled OOS:
  n=485, total **-363%**, mean/trade **-0.749%**, win 32%, Sharpe **-3.74**.
- **Entry archetype has NO edge** (the load-bearing finding): pure forward return
  after a breakout signal (gross, no cost, no exit) is **negative at every
  horizon** — pooled fwd5/10/20/40 = -0.08/-0.18/-0.48/-0.63%. A no-edge entry
  cannot be rescued by any exit.
- **Every exit variant pooled-negative**: chandelier ATR{2,3,4,6}, fixed
  hold{5,10,20}, Donchian-20 exit — all negative, ≤5/16 instruments positive.
- **Robust across decades & params**: negative fwd-edge holds in BOTH 2004-2014
  AND 2014-2024, across Donchian {20,30,55}, with/without momentum filter. Not a
  window/parameter/filter artifact.

## Interpretation
Equity indices **mean-revert at the breakout horizon** — new 30/55-day highs/lows
fade, the OPPOSITE of gold/commodities where the archetype was validated. The
brief's "+97bps/day VALIDATED" was an in-sample/live-fluke artifact; it does not
reproduce on real index bars. The archetype's true home is gold (single-symbol
robustness, per gold_trend_chandelier_1d's own "do NOT generalize" note) — the
same caution applies here: trend-breakout does NOT port from commodities to
equity indices.

## Decision
- **REJECT** the indices breakout fan-out. Do NOT extend SUPPORTED_SYMBOLS to the
  14 equity-index epics on a breakout/momentum-continuation thesis.
- xau_indices_trend's index legs (US500/US100/DE40/UK100/EU50/US30) are
  edge-negative on this thesis — candidate for KILL or thesis-flip (a reversion/
  fade archetype is what the data supports for indices, NOT continuation).
- Fan-out gate worked as designed: blocked a 14-instrument overfit deployment
  (g, autopsy-recurrence prevention).

## Files
- Harness: /tmp/donchian_indices_bt.py · /tmp/donchian_diag.py · /tmp/donchian_robust.py
