---
type: research
status: recorded
date_created: 2026-06-27
tags: [research, backfilled-frontmatter]
---

# Burst-Momentum Backtest — FEE-FATAL (fade=single-regime artifact)

DEMO/PAPER · OKX SPOT · real 1H bars · real fees · long-only · flow_not_block (extract, not block)
Script: `data/burst_momentum_bt.py` · 2026-06-27

## Candidate asked
Big burst (return z >= θ vs trailing vol) → FOLLOW the burst (momentum continuation),
maker entry + let-run exit. NOT the KILLed fast-reversion scalp.

## Data
OKX 1H bars: 157 syms, 12.8d span (only window with intraday history; 15m=3d, 1m=0.7d).
Universe = USDT-quote, ≥200 bars, real spread<25bps (quote_ticks live), 66 syms.
Cost: REAL maker 8bps/leg, REAL taker 10, DEMO flat 70 (venue actually bills). +spread +slip.

## Result — MOMENTUM (the asked candidate) = FEE-FATAL
Gross fwd return after a 1H burst is **flat-to-negative** at every z/hold:
- z≥3 h1 gross -14.7 / h2 +6.1 / h4 -35.4 ; z≥4 h1 -37.1 / h2 +18.5 / h4 +6.0 (bps)
- maker-net negative everywhere (best +0.48bps noise); demo-net -135 to -191bps.
- Tail z≥5/6 looked +48~+57bps maker-net BUT n=9-19, t<1, IS/OOS sign flips → noise.
**Momentum does NOT continue after a 1H crypto burst. Follow-direction is fee-fatal.**
(confirms prompt caveat: re-check → fee-fatal → that's the answer.)

## FADE (opposite: buy-dip after big DOWN burst) — strong-looking but SINGLE-REGIME
z≥4 h1: maker-net +124bps win70% t6.0 n105; z≥5: +162bps win80% t7.2.
🚨 But 88/110 fires were ONE 06-24/25 market-wide crash:
- PRE-crash (only "normal" window, n=22): net **+0.2bps t=0.02** = ZERO edge.
- CRASH 24-25 (n=88): +175bps t8.1. POST-crash: 0 events.
- DEMO 70bps: all-sample +4bps t0.22; even crash-only +39bps t1.81 (not sig) = demo-fatal.
- Top-liquid (spread<8) z≥5 survives demo (+112bps win88%) but n=16, all crash.
→ The fade "edge" is a single capitulation-bounce, not a repeatable pattern. Trades are
correlated bets on one selloff (64 syms but ~2 days), effective n ≈ a few crash days.

## Verdict
- **Asked candidate (burst-momentum continuation): FEE_FATAL.** No config nets positive.
- Fade/capitulation-bounce: real economics (forced-liq overshoot) but UNPROVEN as standing
  edge — needs many independent crash episodes + maker_fill_shadow live to confirm. On demo
  70bps it's marginal-to-fatal. = the existing weekend_funding_capitulation_maker axis (#80),
  not a new momentum strat.
- Jin's "215 flowing, 0 caught": market IS alive (burst_z 24), but the move is mean-reverting
  noise at 1H, and the asked momentum-follow loses gross before fees. 0 trades ≠ missed fast edge.

Links: [[project_validated_edge_is_slow_trend_not_scalp]] · task#57 · [[weekend_funding_capitulation_maker]]
