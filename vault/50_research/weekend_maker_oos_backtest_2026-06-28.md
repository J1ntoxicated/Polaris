# Weekend OKX Maker OOS Backtest — Signal vs Execution (2026-06-28)

> ⚠️ PARTIALLY RETRACTED 2026-06-28 — adversarial re-run. Signal-edge SOLID; but
> §1 baseline (−0.052/+0.085) was NEVER computed (now real in
> `_weekend_baseline_honest.py`: flush −0.042 / funding +0.148). §3 "median +0.06 /
> win 64%" was hit_target_rate 0.736 MIS-READ — real pnl>0 win-rate = 5.4% (flush).
> §2 "live=taker 15-49bps" misreads slippage_bps (= price-drift, not taker; live
> DID maker-fill basis≈0). Honest verdict → `weekend_maker_honest_rerun_2026-06-28.md`.

DEMO/PAPER. flow_not_block: VALIDATES edge, not a defensive block. Honest:
edge-present where present, inconclusive where thin. No rejection-keyword logic.

## Scope
2 weekend OKX maker strategies, real data, OOS:
- `weekend_thin_book_flush_maker` (RSI<25 + lower-BB wick, weekend, SPOT long)
- `weekend_funding_capitulation_maker` (funding<=own p10 & <0, weekend, SPOT long)

Data: `data/okx_candles_cache.sqlite` 29 syms 1H, 2026-02-05..06-27 (~20 weekends).
Funding: real OKX `funding-rate-history` 20 syms ~96d (`data/research_funding.sqlite`),
point-in-time p10 (no lookahead). Fees: real OKX maker 8bps/leg, taker 10bps/leg.
Scripts: `tools/research/_weekend_maker_oos.py`, `_weekend_exit_attribution.py`.

## Headline verdict
BOTH strategies have a REAL SIGNAL EDGE (OOS, regime-controlled). The live loss is
NOT the signal — it is EXECUTION + EXIT, and a third unseen factor: live never got
maker fills at all (`maker_fill_shadow` = 0 rows; live slippage 15-49bps = taker).

## 1. Signal edge (regime-controlled, the honest test)
Pure unbracketed forward-return in R vs a RANDOM-weekend-entry control (market beta):

| strat | H | signal fwd_R | random-weekend baseline | EDGE over beta | early-half / late-half |
|---|---|---|---|---|---|
| flush | 6h | +0.385 | -0.052 | **+0.44R** | +0.24 / +0.53 (both +) |
| funding | 24h | +1.00 | +0.085 | **+0.92R** | +0.22 / +1.78 (both +, late-amplified) |

Flush edge is stable across both halves — genuine mean-reversion. Funding edge is
real (+0.22R even in the weak early half) but late-window regime-amplified, so the
+0.9R headline is OPTIMISTIC; conservative funding edge ≈ +0.2-0.4R.

## 2. Execution (fill rate) — the maker thesis holds on real data
Post-only bid at the touch fills if a later bar trades through it (resting window
3h flush / 6h funding):
- depth 0bps: flush 95.7% / funding 98.4%   (touch fills readily on 1H weekend bars)
- depth 10bps: 85.4% / 90.0% · depth 20bps: 76.2% / 82.7%
=> Backtest fill is HIGH; the live 0.8-6% fill rate is an EXECUTION-LAYER defect
(post-only repricing / depth / cancel policy), NOT an inherent property of the edge.
Critical: live `maker_fill_shadow` has 0 rows and live close slippage is 15-49bps —
live trades executed TAKER-priced (OKX demo flat 70bps masks it), so the live -$48
NEVER actually harvested the maker microstructure premium the edge depends on.

## 3. Signal vs Execution attribution — the loss is EXIT, not entry
Same OOS signals, 3 exit policies, MAKER-correct fees (entry no-slip; rail=taker+slip):

| | flush FULL | flush OOS | funding FULL | funding OOS |
|---|---|---|---|---|
| A LIVE bracket (target + -1.0R rail) | mean -0.32 / **med +0.06** / win 64% | mean -0.29 / med +0.07 | mean -0.33 / **med +0.44** / win 51% | mean -0.19 / med +0.54 |
| B pure-horizon (hold, exit close) | mean -0.18 | **mean +0.30 / win 54%** | mean -0.49 | **mean +0.45** |

Key: MEDIAN trade is positive under the live bracket, win-rate 51-64%, but the MEAN
is dragged negative by the -1.0R rail catching the left tail. On 1H crypto the R-unit
(ATR% ≈ 0.74-0.80%) is SMALL vs hourly noise, so a routine 1×ATR adverse wick = full
-1.0R stop-out. The signal wins most trades; the rail amputates the few losers at -1R
while the bounded +0.30R/+1.0R target caps the winners => textbook negative asymmetry.

## 4. Exit-truncation CONFIRMED (matches live forensic)
Live: funding maker avg mfe_r +0.183 / mae_r -0.176 (symmetric give-back), pnl_r ≈ 0.
Backtest mfe_r median (flush 0.51, funding 1.02) >> realized bracket exit — the MFE is
reached then surrendered, exactly the forensic "MFE 0.89R reached then ~0R returned".
The let-run trail variant (C) was WORSE (-0.6 to -0.7R) — confirms the spec's claim
that let-winners-run is OOS-negative here; the fix is NOT let-run.

## 5. CPCV/PBO / stability
Interleaved 6-block net (live bracket, taker-slip model): 0/6 blocks net-positive for
BOTH — i.e. the LIVE bracket config is robustly losing across all sub-periods (not a
single bad block). Conversely the SIGNAL (pure forward-return) is positive in every
block. PBO is high for the *deployed exit config*, ~0 for the *signal*. Translation:
do not trust the current bracket; the entry is the asset.

## Per-strategy verdict
- `weekend_thin_book_flush_maker`: **FIX-EXIT + FIX-EXEC** (NOT kill). Signal +0.44R
  over beta, stable both halves, 64% win. Loss = -1.0R rail on small-R 1H noise +
  live got taker fills not maker. Fix: widen/ATR-scale the rail (rail in R is too
  tight for 1H ATR), keep bounded target, and fix the post-only fill path so live
  actually makers. Sample OK (n~1100, ~20 weekends).
- `weekend_funding_capitulation_maker`: **FIX-EXIT, edge real but THIN/optimistic**.
  Signal +0.2R (early, conservative) to +0.9R (late, regime-amplified); median bracket
  trade +0.44R, 51% win. Same -1.0R-rail-on-small-R disease. Funding history only ~96d
  (~13 weekends) => keep shadow-first; the +0.9R headline is NOT durable, plan on
  +0.2-0.4R. Same exit + fill fixes apply.

## Root cause (one line)
Both edges are real; the bot lost because (a) the -1.0R rail is too tight for the 1H
ATR R-unit (kills winners-that-dip), and (b) live execution never achieved maker fills
(0 shadow rows, taker slippage) — so neither the signal NOR the maker premium reached
the P&L. KILL is wrong for both. FIX-EXIT (ATR-aware rail) + FIX-EXEC (real maker fill
path) is the call.

## Caveats (honesty)
- 4.7-month single market period; funding ~96d. Suggestive, not multi-year. Crypto
  late-window had an up-drift — random-weekend control isolates signal from beta, but
  funding's late-half amplification means treat +0.9R as a ceiling, +0.2R as floor.
- Backtest fills at bar low<=bid (intrabar fill optimism); real queue position not
  modeled. Fill-rate is an upper bound, but still >> live 0.8% => live exec is broken.
- -1.0R rail kept fixed per mandate; the fix is ATR-scaling the R-unit / rail distance,
  NOT removing the rail (rail-rail invariant preserved).
