"""Multi-resolution backtest — 모든 viable strategies × 모든 timeframes.

사용:
    python3 -m scripts.backtest_multi_resolution

Grid:
  Strategies: HYPO-003 SMA(50,200), HYPO-004 Donchian(40,15),
              HYPO-008 VolumeBurst, HYPO-020 VB+Donchian confluence,
              HYPO-032 TSMOM (daily momentum)
  Timeframes: 1m, 5m, 15m, 1H, 4H, 1D
  Tickers: BTC-USDT, ETH-USDT, SOL-USDT, DOGE-USDT (4 liquid)

Total: 5 strategies × 6 timeframes × 4 tickers = 120 backtest runs
Fee: 0.0014 round-trip (INSIGHT-032 OKX paper Lv1 taker 0.07%×2)
Walk-forward: 3-fold (1/3 IS + 2/3 OOS fold; viable = consistent across folds)
Viable filter: Sharpe >= 0.3 AND expectancy > 0 AND n_trades >= 5

Output: viable matrix table + JSON to data/backtest/multi_resolution_<ts>.json

Notes:
  - 1m data: OKX history-candles limited to ~1000 candles (~16h) — low sample
  - 1D data: up to 2.7yr available from OKX
  - TSMOM requires >= 30 daily candles (1d/7d/30d return look-back)
  - min timeframes per strategy enforced (see STRATEGY_TF_GATES below)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from src.backtest.engine import backtest
from src.data.okx_history import fetch_history_paged
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.sma_crossover import SMACrossover
from src.strategies.tsmom import TSMOM
from src.strategies.volume_burst import VolumeBurst

# ── Grid config ───────────────────────────────────────────────────────────────

TICKERS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"]
TIMEFRAMES = ["1m", "5m", "15m", "1H", "4H", "1D"]
FEE_ROUND_TRIP = 0.0014   # INSIGHT-032 OKX paper Lv1 taker 0.07%×2

# Candle counts per TF — target max data available from OKX
CANDLES_PER_TF: dict[str, int] = {
    "1m": 1000,    # ~16h (OKX 1m retention ~1-2 days)
    "5m": 2000,    # ~6.9 days
    "15m": 2000,   # ~20.8 days
    "1H": 4000,    # ~166 days
    "4H": 2000,    # ~333 days
    "1D": 1000,    # ~2.7 years
}

# Walk-forward: 3-fold cross-validation
N_FOLDS = 3

# Viability gates
MIN_TRADES = 5
MIN_SHARPE = 0.3
MIN_EXPECTANCY = 0.0

# Minimum timeframe constraint per strategy (skip combinations below minimum)
# Keys: strategy hypo_id, values: set of forbidden timeframes
STRATEGY_TF_GATES: dict[str, set[str]] = {
    "HYPO-003-SMA": {"1m", "5m"},      # SMA(50,200) needs 200+ bars: 15m+ preferred
    "HYPO-004-DONCH": {"1m"},          # Donchian(40,15) needs 40+ bars: 5m+ ok
    "HYPO-008-VB": set(),              # VolumeBurst works on all TFs
    "HYPO-020-VB-DONCH": {"1m"},       # confluence: 5m+ ok
    "HYPO-032-TSMOM": {"1m", "5m", "15m", "1H", "4H"},  # TSMOM daily only
}


# ── Strategy factory ──────────────────────────────────────────────────────────

def build_strategies() -> list[dict]:
    """Build strategy instances for the grid."""
    return [
        {
            "hypo_id": "HYPO-003-SMA",
            "desc": "SMA Crossover (50, 200) — slow trend following (Moskowitz-style)",
            "strategy": SMACrossover(fast=50, slow=200),
        },
        {
            "hypo_id": "HYPO-004-DONCH",
            "desc": "Donchian Breakout (40, 15) — turtle trading channel",
            "strategy": DonchianBreakout(entry_period=40, exit_period=15),
        },
        {
            "hypo_id": "HYPO-008-VB",
            "desc": "Volume Burst (20) — vol spike continuation",
            "strategy": VolumeBurst(vol_period=20),
        },
        {
            "hypo_id": "HYPO-020-VB-DONCH",
            "desc": "VolumeBurst(20) AND DonchianBreakout(20,10) — vol + channel confluence",
            "strategy": ConfluenceSignal(
                sub_strategies=[
                    VolumeBurst(vol_period=20),
                    DonchianBreakout(entry_period=20, exit_period=10),
                ],
                require_all=True,
            ),
        },
        {
            "hypo_id": "HYPO-032-TSMOM",
            "desc": "Time-Series Momentum (1d/7d/30d) — Moskowitz et al. 2012 JFE",
            "strategy": TSMOM(),
        },
    ]


# ── Walk-forward helpers ──────────────────────────────────────────────────────

def _k_fold_splits(candles: list, n_folds: int) -> list[tuple[list, list]]:
    """3-fold walk-forward: rolling window splits.

    Each fold: IS = first (fold_size × k) candles, OOS = next fold_size.
    Returns list of (is_candles, oos_candles) tuples.
    """
    n = len(candles)
    fold_size = n // (n_folds + 1)
    if fold_size < 10:
        # Not enough candles for multi-fold — single split
        split = max(1, int(n * 0.8))
        return [(candles[:split], candles[split:])]

    splits = []
    for k in range(1, n_folds + 1):
        is_end = fold_size * k
        oos_end = min(is_end + fold_size, n)
        is_candles = candles[:is_end]
        oos_candles = candles[is_end:oos_end]
        if is_candles and oos_candles:
            splits.append((is_candles, oos_candles))
    return splits


# ── Grid runner ───────────────────────────────────────────────────────────────

def run_grid() -> list[dict]:
    """Run full multi-resolution grid. Returns result dicts."""
    strategies = build_strategies()
    results = []
    total = len(TICKERS) * len(TIMEFRAMES) * len(strategies)
    run_idx = 0

    print(f"\n=== Polaris Phase 2N Multi-Resolution Backtest ===")
    print(f"Grid: {len(TICKERS)} tickers × {len(TIMEFRAMES)} TF × {len(strategies)} strategies = {total} runs")
    print(f"Fee: {FEE_ROUND_TRIP*100:.2f}% RT | Walk-forward: {N_FOLDS}-fold\n")

    for ticker in TICKERS:
        for tf in TIMEFRAMES:
            n_candles = CANDLES_PER_TF[tf]
            print(f"  Fetching {ticker} {tf} ({n_candles} candles)...", end="", flush=True)
            try:
                candles = fetch_history_paged(inst_id=ticker, bar=tf, n_candles=n_candles)
            except Exception as exc:
                print(f" FAILED: {exc}")
                for strat in strategies:
                    run_idx += 1
                    results.append({
                        "hypo_id": strat["hypo_id"],
                        "ticker": ticker,
                        "tf": tf,
                        "error": str(exc),
                    })
                continue
            print(f" got {len(candles)} candles")

            for strat in strategies:
                run_idx += 1
                hid = strat["hypo_id"]
                strategy = strat["strategy"]

                # TF gate check
                if tf in STRATEGY_TF_GATES.get(hid, set()):
                    results.append({
                        "hypo_id": hid,
                        "desc": strat["desc"],
                        "ticker": ticker,
                        "tf": tf,
                        "n_candles": len(candles),
                        "error": f"tf_gate: {hid} not valid for {tf}",
                    })
                    continue

                # Min window check
                if len(candles) < strategy.min_window + 10:
                    results.append({
                        "hypo_id": hid,
                        "desc": strat["desc"],
                        "ticker": ticker,
                        "tf": tf,
                        "n_candles": len(candles),
                        "error": f"insufficient candles {len(candles)} < {strategy.min_window}",
                    })
                    continue

                try:
                    folds = _k_fold_splits(candles, N_FOLDS)
                    fold_results = []
                    for fold_i, (is_c, oos_c) in enumerate(folds):
                        if len(is_c) < strategy.min_window:
                            continue
                        is_r = backtest(is_c, strategy, ticker, tf, FEE_ROUND_TRIP)
                        oos_r = (
                            backtest(oos_c, strategy, ticker, tf, FEE_ROUND_TRIP)
                            if len(oos_c) >= strategy.min_window
                            else None
                        )
                        fold_results.append({
                            "fold": fold_i + 1,
                            "is_n": is_r.n_trades,
                            "is_sharpe": round(is_r.sharpe, 4),
                            "is_expectancy": round(is_r.expectancy, 6),
                            "is_mdd": round(is_r.max_drawdown, 4),
                            "oos_n": oos_r.n_trades if oos_r else 0,
                            "oos_sharpe": round(oos_r.sharpe, 4) if oos_r else 0.0,
                            "oos_expectancy": round(oos_r.expectancy, 6) if oos_r else 0.0,
                        })

                    if not fold_results:
                        results.append({
                            "hypo_id": hid,
                            "ticker": ticker,
                            "tf": tf,
                            "error": "no valid folds",
                        })
                        continue

                    # Aggregate across folds
                    avg_is_sharpe = sum(f["is_sharpe"] for f in fold_results) / len(fold_results)
                    avg_is_expectancy = sum(f["is_expectancy"] for f in fold_results) / len(fold_results)
                    total_is_n = sum(f["is_n"] for f in fold_results)
                    avg_oos_sharpe = sum(f["oos_sharpe"] for f in fold_results if f["oos_n"] > 0) / max(1, sum(1 for f in fold_results if f["oos_n"] > 0))
                    avg_oos_expectancy = sum(f["oos_expectancy"] for f in fold_results if f["oos_n"] > 0) / max(1, sum(1 for f in fold_results if f["oos_n"] > 0))
                    total_oos_n = sum(f["oos_n"] for f in fold_results)

                    is_viable = (
                        avg_is_sharpe >= MIN_SHARPE
                        and avg_is_expectancy > MIN_EXPECTANCY
                        and total_is_n >= MIN_TRADES
                    )
                    oos_viable = (
                        is_viable
                        and total_oos_n > 0
                        and avg_oos_expectancy > MIN_EXPECTANCY
                    )

                    results.append({
                        "hypo_id": hid,
                        "desc": strat["desc"],
                        "ticker": ticker,
                        "tf": tf,
                        "n_candles": len(candles),
                        "n_folds": len(fold_results),
                        "avg_is_sharpe": round(avg_is_sharpe, 4),
                        "avg_is_expectancy": round(avg_is_expectancy, 6),
                        "total_is_n": total_is_n,
                        "avg_oos_sharpe": round(avg_oos_sharpe, 4),
                        "avg_oos_expectancy": round(avg_oos_expectancy, 6),
                        "total_oos_n": total_oos_n,
                        "is_viable": is_viable,
                        "oos_viable": oos_viable,
                        "folds": fold_results,
                    })

                except Exception as exc:
                    results.append({
                        "hypo_id": hid,
                        "ticker": ticker,
                        "tf": tf,
                        "error": str(exc),
                    })

    return results


# ── Output ────────────────────────────────────────────────────────────────────

def print_results(results: list[dict]) -> list[dict]:
    """Print grid results and return viable list."""
    valid = [r for r in results if "error" not in r]
    viable = [r for r in valid if r.get("oos_viable")]
    is_only = [r for r in valid if r.get("is_viable") and not r.get("oos_viable")]

    print(f"\n{'='*80}")
    print(f"ALL RESULTS ({len(valid)} valid runs, {len(results) - len(valid)} errors)")
    print(f"{'='*80}")
    header = f"{'HYPO':<22} {'Ticker':<10} {'TF':<5} {'IS_n':>5} {'IS_exp':>8} {'IS_Sh':>6} {'OOS_n':>6} {'OOS_exp':>9} {'FLAG'}"
    print(header)
    print("-" * 90)
    for r in sorted(valid, key=lambda x: -x.get("avg_is_sharpe", 0)):
        flag = " *** OOS-VIABLE" if r.get("oos_viable") else (" * IS-viable" if r.get("is_viable") else "")
        print(
            f"{r['hypo_id']:<22} {r['ticker']:<10} {r['tf']:<5} "
            f"{r.get('total_is_n', 0):>5} {r.get('avg_is_expectancy', 0):>+8.4f} "
            f"{r.get('avg_is_sharpe', 0):>6.2f} "
            f"{r.get('total_oos_n', 0):>6} {r.get('avg_oos_expectancy', 0):>+9.4f}{flag}"
        )

    print(f"\n{'='*80}")
    print(f"VIABLE (IS Sharpe>={MIN_SHARPE} + EV>0 + n>={MIN_TRADES} + OOS EV>0): {len(viable)}/{len(results)} runs")
    print(f"{'='*80}")
    if viable:
        for r in sorted(viable, key=lambda x: -x.get("avg_oos_expectancy", 0)):
            print(
                f"  {r['hypo_id']} {r['ticker']} {r['tf']}: "
                f"IS exp={r['avg_is_expectancy']:+.4f} Sh={r['avg_is_sharpe']:.2f} "
                f"| OOS exp={r['avg_oos_expectancy']:+.4f} n={r['total_oos_n']}"
            )
    else:
        print("  (none — IS-only viable below)")
        for r in sorted(is_only, key=lambda x: -x.get("avg_is_expectancy", 0))[:10]:
            print(
                f"    {r['hypo_id']} {r['ticker']} {r['tf']}: "
                f"IS exp={r['avg_is_expectancy']:+.4f} Sh={r['avg_is_sharpe']:.2f} "
                f"| OOS n={r.get('total_oos_n', 0)}"
            )

    return viable


def main() -> None:
    start = time.time()
    results = run_grid()

    Path("data/backtest").mkdir(parents=True, exist_ok=True)
    out_path = f"data/backtest/multi_resolution_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results saved → {out_path}")

    viable = print_results(results)

    elapsed = time.time() - start
    print(f"\nTotal time: {elapsed:.1f}s | {len(results)} runs")

    if viable:
        print(f"\n[ACTION] {len(viable)} viable (strategy, tf, ticker) cells found.")
        print("  Review for realtime deployment: 5m/15m cells (tick latency feasible)")
        print("  Register via 60_alpha/ HYPO notes after OOS confirmation.")
    else:
        print("\n[INSIGHT] No OOS-viable cells found:")
        print("  - 1D timeframe typically yields most stable signal (less noise)")
        print("  - Consider longer IS windows (fetch more history)")
        print("  - HYPO-032 TSMOM (1D) academic baseline — verify data volume")


if __name__ == "__main__":
    main()
