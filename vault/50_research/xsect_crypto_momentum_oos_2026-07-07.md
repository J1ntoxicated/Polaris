---
type: research
status: recorded
date_created: 2026-07-07
tags: [research, backfilled-frontmatter]
---

# Candidate #4 — OKX crypto cross-sectional momentum: REJECT (2026-07-07)

DEMO/PAPER research. Aggressive bias preserved, flow_not_block. Honest net.
Backtest-only prototype (no BaseStrategy registered — reject).

## Mechanism tested
Cross-sectional relative-strength: rank OKX 1D liquid USDT majors by ROC(lookback),
long the top quintile equal-weight, rebalance monthly/biweekly, hold to next rebal.
Turnover-based taker fee (35 bps/side) + slippage stress. Slow, low-frequency.

## Data cleaning (load-bearing)
`data/polaris_live.sqlite` okx 1D has **irregular sub-daily bars** (deltas 8h/16h,
not 24h) → dedup to one close per UTC day. Raw universe has **garbage stub prices**
(ALPHA 0.01→100000, HYPE 5e-6→32.98, OKB-ETH/ASTER-USD wrong-quote). A naive ranker
BUYS exactly these spikes → phantom +8,655,900 bps OOS "edge". Restricting to the
curated `OKX_LIQUID_ROSTER` (USDT majors) + dropping any >100% adjacent jump +
95% dense-grid coverage → 48 clean majors, 260-day common grid [2025-10..2026-07].

## OOS result on the CLEAN universe — negative everywhere
| lb | rebal | tf | n | full SR | mean bps | OOS mean bps | vs-benchmark spread bps | spread SR |
|----|-------|----|---|---------|----------|--------------|-------------------------|-----------|
| 60 | 5  | .2 | 39 | -0.367 | -186 | -227 | **-82**  | -0.300 |
| 60 | 10 | .2 | 19 | -0.397 | -320 | -552 | **-79**  | -0.215 |
| 90 | 5  | .2 | 33 | -0.410 | -198 | -127 | **-114** | -0.133 |
| 60 | 5  | .3 | 39 | -0.368 | -176 | -193 | **-73**  | -0.316 |
| 60 | 5  | .1 | 39 | -0.253 | -164 | -240 | **-61**  | -0.131 |

- **Every config negative** full-sample AND OOS, net-of-fee, before slippage.
- **Spread vs equal-weight all-majors baseline is negative in ALL cases** — the
  top-quintile ranker does WORSE than naively holding the whole basket. No
  cross-sectional SELECTION edge over this window (fails gate Tier-1 relative).
- Monthly cadence (n=8): PBO=1.000, deflated_sharpe=0.042 → `admit_strategy`
  reject on both rails.
- Slippage stress irrelevant — already negative at slip+0.

## Verdict: REJECT
No positive OOS net-of-fee edge; does not beat naive beta; fails admit_strategy.
The only positive was a pure DB-artifact (winner-carried by stub prices) — the exact
autopsy pattern the roadmap warns to reject.

Caveat (honest): window is a ~9mo crypto drawdown/chop regime (bench itself −84..−242
bps/period). X-sectional crypto momentum may work on longer multi-year samples, but
on the DATA AVAILABLE it does not survive. Do not register.

Script: `scratchpad/xsect_mom_bt.py` (session-local).
Related: [[project_validated_edge_is_slow_trend_not_scalp]], [[strategy_expansion_roadmap_2026-06-*]]
