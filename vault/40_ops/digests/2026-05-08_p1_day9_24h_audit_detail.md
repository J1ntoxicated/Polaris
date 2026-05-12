---
type: runtime
status: active
date_created: 2026-05-08
tags: [digest, p1, day9, audit-detail]
related: [[2026-05-08_p1_day9_24h_full_audit]]
---

# Day 9 24h Audit — Detail

## G6 / G7 / G8 (Day 9 strict)
- **G6**: ADJUST_EXIT gpt 18,918 / HOLD gpt 8,085 / EXIT_NOW python 51,708 / HOLD python 1,914
- **G7**: HOLD gpt 13,031 / WIDEN gpt 7,802 / HOLD python 1,914
- **G8**: REFLECTED gpt_p1 1,917

## Cell pool TOP / BOTTOM (n_eff > 5)
- **TOP**: ENJ/tsmom/bull (0.491·n=136), AAVE/rsi_bb/bull (0.402), HYPE/tsmom/bull (0.40), BNB/tsmom/bull (0.395), YFI/tsmom/chop (0.389)
- **BOTTOM**: DOT/tsmom/chop (-0.741·n=53), LTC (-0.614), ETC (-0.611), UNI (-0.495), AAVE/tsmom/bull (-0.475)

## Day 9 funnel
G1 PASS 7,115 → G2 PASS 7,115 → G3 (KILL 5,214 / PASS 1,374 / MODIFY 527) → G4 (PROCEED 1,582 / KILL 320) → G5 SIZED 1,582 (22% conv)

## Regime live
chop=78, crisis=5, bear_trend=5, bull_trend=3 (4 regimes ✓)

## Strategy fills Day 9
tsmom OKX 2,952 (+$192), spot_donchian 124 (-$60), volume_burst 76 (-$23), **rsi_bb / fx_breakout / xau / session = 0** ⚠

## Cumulative (43h DB)
- Fills: 15,720 (OKX 15,554 / Capital 166)
- AllocatorFence: 5,616 reservations
- Cells: 201 / 37 tickers / 6 strategies / 4 regimes

## Capital silent-drop forensics (P0)
1. G2 emit `symbol=US100` → run_id created
2. G3 PASS → G4 PROCEED → G5 SIZED (gate_event row)
3. T4 sizing: `notional=47400 USD lev=30 cold=True`
4. fence.reserve → reservation row confirmed ✓
5. simulate_open_fill venue=capital → normalize_capital_confirm
6. **fills row NEVER appears for ts>1778190207 venue=capital**
7. **0 fault_events / 0 ERROR log between gate=5 and next event**

Prime suspect: `_smoke_fills.py:128 normalize_capital_confirm` returns Fill without persist, OR `persist_fill` swallows for `instrument_id=US100` (no `capital:` prefix vs OKX `BTC-USDT`). Fix: stderr trace + `assert fill is not None`.
