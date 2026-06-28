# Weekend Maker OOS — HONEST RE-RUN (adversarial-verified, 2026-06-28)

DEMO/PAPER. flow_not_block. Supersedes faked parts of
`weekend_maker_oos_backtest_2026-06-28.md`. EVERY number = script output (no
hand-typed). Scripts: `tools/research/{_weekend_baseline_honest.py (new),
_weekend_maker_oos.py, _weekend_exit_attribution.py}`. Data: `okx_candles_cache.sqlite`
29syms 1H 2026-02-05..06-27 (~20wk) + `research_funding.sqlite` 20syms 96d. Fees OKX
maker 8 / taker 10 / slip 15 bps.

## SOLID — signal edge re-confirmed (unchanged)
`_weekend_maker_oos.py:224 pure_forward` — flush H6 raw +0.385/med +0.307/win57.1%,
OOS +0.810. funding H24 raw +1.00/med +0.883/win60.5%, OOS +2.40. Verbatim live gens.

## (1) RANDOM-WEEKEND BASELINE — NOW in code (was fiction before)
`_weekend_baseline_honest.py:97 baseline_edge` — 20 random weekend draws/signal,
same sym/horizon, regime-matched window:
- flush H6: signal +0.385 / baseline **−0.0423** / EDGE **+0.427**; OOS edge +0.717
- funding H24: signal +1.00 / baseline **+0.148** / EDGE **+0.852**; OOS edge +1.689
Prior "−0.052/+0.085" never computed; edge real, but funding OOS +1.69 regime-
amplified → ceiling.

## (2) WIN-RATE (pnl>0) vs HIT-TARGET — prior report's load-bearing LIE
`_weekend_baseline_honest.py:235` + `_weekend_maker_oos.py:145 summ`:
- flush bracket FULL: mean **−0.666**/med **−0.385**/**WIN(pnl>0)=5.4%**/hit_target=73.6%.
  OOS: mean −0.663/med −0.377/WIN=2.9%.
- funding bracket FULL: mean −0.667/med −0.553/WIN=43.4%/hit_target=51.2%.
Prior "med +0.06 / win 64%" = **hit_target_rate 0.736 mis-read**; real pnl>0 win =
5.4% (flush) → "median-positive FIX-not-KILL" premise FALSE for flush as deployed.
Reproduces `_weekend_exit_attribution.py` −0.666/−0.385/5.4%. Root: fee-in-R @median
ATR%(0.795%)=**0.579R** (`_weekend_exit_attribution.py:96`) — one maker RT eats >½ R.

## (3) EXIT ALTERNATIVES OOS — rail fixed −1.0R, only R-unit ATR mult widens
`_weekend_baseline_honest.py:259 exit_alternatives` (net R, OOS=late 40%):
| exit | flush OOS mean/med/win | funding OOS mean/med/win |
|---|---|---|
| A 1.0×ATR (deployed) | −0.663 / −0.377 / 2.9% | −0.499 / −0.315 / 45% |
| B pure-horizon hold | +0.164 / +0.251 / 55.5% | +1.799 / +1.547 / 74.2% |
| C 2.0×ATR R-unit | −0.254 / −0.029 / 43.5% | −0.043 / +0.543 / 61.8% |
| D 3.0×ATR R-unit | −0.105 / +0.065 / 62.4% | +0.222 / +0.722 / 70.4% |
Widening R-unit (fee shrinks in R, stop wider) flips median positive; only funding-D
and pure-horizon reach positive MEAN OOS. flush mean stays slightly neg (left tail)
even at 3×. CPCV: deployed bracket 0/6 blocks pos BOTH (robust loss).

## (4) MAKER EXECUTION — root cause split in two (grep-grounded)
- `maker_fill_shadow`=0 rows in `data/polaris_live.sqlite` because the persisting
  `log_maker_fill()` (`maker_fill_shadow.py:72`, the only INSERT) is called ONLY
  from `tests/test_maker_fill_shadow.py` — **never wired into production**. Live
  entry (`_okx_post_only.py:237,251`) calls `_log_maker_fill_basis()` = INFO-log
  only, no persist. PERSIST-NOT-WIRED, not taker execution.
- Live DID maker-fill: runtime log 15 shadow lines, basis 0.00bps (11/15) /
  clean_fill (15/15), 4423 post_only POSTs. Entries rested at the touch.
- Prior "live=taker 15-49bps" MISREADS `fills.slippage_bps` (`fill_normalizer.py:143`
  = |fill−decision_px|/decision_px = resting-window drift, NOT taker tier). Entry
  slip 2-27bps = drift. Live HARVESTED maker basis≈0; demo-flat-70bps masks $ edge;
  shadow empty only because persist unwired (FIX-EXEC = wire log_maker_fill).

## VERDICT (all numbers from scripts above)
- flush: **FIX-EXIT (R-unit widen) + FIX-EXEC (wire persist)**, NOT prior "64% win".
  Signal edge real (+0.43 over baseline) but as-deployed loses every block (WIN
  5.4%); only 3×ATR R-unit gets OOS median +0.065, mean still −0.105. SHADOW-FIRST.
- funding: **FIX-EXIT**, edge real but OPTIMISTIC (OOS +1.69 regime-amplified;
  conservative ≈+0.2-0.4). 3×ATR R-unit OOS mean +0.222/med +0.722/win 70% — only
  bracket positive in mean. Sample THIN (96d) → durability INCONCLUSIVE; shadow-first.
- Neither KILL. Caveat: single 4.7mo market, funding 96d, late up-drift inflates OOS.
