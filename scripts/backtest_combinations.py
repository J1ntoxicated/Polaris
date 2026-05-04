"""Combination grid backtest — viable alpha 조합 발굴 (Phase 2h).

사용:
    python3 -m scripts.backtest_combinations

Grid:
- 6 tickers × 3 timeframes × N combination strategies
- fee = 0.0014 (INSIGHT-007 OKX paper Lv1 LV3+ taker 0.07% × 2)
- Viable filter: Sharpe >= 0.3 AND expectancy > 0 AND n_trades >= 10
- Walk-forward: last 20% candles as OOS validation

HYPO candidates:
- HYPO-018: VolumeBurst(20) + SMACrossover(10,30) — vol burst with trend filter
- HYPO-019: SMACrossover(10,30) + DonchianBreakout(20,10) — dual breakout
- HYPO-020: VolumeBurst(20) + DonchianBreakout(20,10) — vol burst + channel breakout
- HYPO-021: SMACrossover(50,200) + DonchianBreakout(40,15) — slow trend + turtle
- HYPO-022: 3-way: VB + SMA(10,30) + Donchian(20,10) N-of-M(2)
- HYPO-023: VB(10) + SMA(10,30) faster vol_period=10
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
from src.strategies.volume_burst import VolumeBurst

# ---------------------------------------------------------------------------
# Grid config
# ---------------------------------------------------------------------------

TICKERS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "ADA-USDT", "ORDI-USDT"]
TIMEFRAMES = ["1H", "4H", "1D"]
FEE_ROUND_TRIP = 0.0014   # INSIGHT-007

# Candle counts per TF (target ~2 years of data where available)
CANDLES_PER_TF: dict[str, int] = {
    "1H": 4000,   # ~166 days (OKX 1H history ~1-2 years)
    "4H": 2000,   # ~333 days
    "1D": 1000,   # ~2.7 years
}

MIN_TRADES = 10      # too few → noise
MIN_SHARPE = 0.3     # swing-viable threshold
MIN_EXPECTANCY = 0.0 # positive EV required

OOS_SPLIT = 0.20  # last 20% = out-of-sample

TARGET_SIZE = 200.0  # USD per combo entry


def build_combos() -> list[dict]:
    """Build combination strategy definitions."""
    return [
        {
            "hypo": "HYPO-018-VB-SMA",
            "desc": "VolumeBurst(20) AND SMACrossover(10,30) — vol burst + trend filter",
            "strategy": ConfluenceSignal(
                sub_strategies=[VolumeBurst(vol_period=20), SMACrossover(fast=10, slow=30)],
                require_all=True,
                target_size_usd=TARGET_SIZE,
            ),
        },
        {
            "hypo": "HYPO-019-SMA-DONCH",
            "desc": "SMACrossover(10,30) AND DonchianBreakout(20,10) — dual breakout",
            "strategy": ConfluenceSignal(
                sub_strategies=[SMACrossover(fast=10, slow=30), DonchianBreakout(entry_period=20, exit_period=10)],
                require_all=True,
                target_size_usd=TARGET_SIZE,
            ),
        },
        {
            "hypo": "HYPO-020-VB-DONCH",
            "desc": "VolumeBurst(20) AND DonchianBreakout(20,10) — vol burst + channel",
            "strategy": ConfluenceSignal(
                sub_strategies=[VolumeBurst(vol_period=20), DonchianBreakout(entry_period=20, exit_period=10)],
                require_all=True,
                target_size_usd=TARGET_SIZE,
            ),
        },
        {
            "hypo": "HYPO-021-SMA200-DONCH40",
            "desc": "SMACrossover(50,200) AND DonchianBreakout(40,15) — slow trend + turtle",
            "strategy": ConfluenceSignal(
                sub_strategies=[SMACrossover(fast=50, slow=200), DonchianBreakout(entry_period=40, exit_period=15)],
                require_all=True,
                target_size_usd=TARGET_SIZE,
            ),
        },
        {
            "hypo": "HYPO-022-3WAY-NofM",
            "desc": "VB(20) + SMA(10,30) + Donch(20,10) — 2-of-3 N-of-M",
            "strategy": ConfluenceSignal(
                sub_strategies=[
                    VolumeBurst(vol_period=20),
                    SMACrossover(fast=10, slow=30),
                    DonchianBreakout(entry_period=20, exit_period=10),
                ],
                require_all=False,
                min_confluence=2,
                target_size_usd=TARGET_SIZE,
            ),
        },
        {
            "hypo": "HYPO-023-VB10-SMA",
            "desc": "VolumeBurst(10, faster) AND SMACrossover(10,30) — faster vol",
            "strategy": ConfluenceSignal(
                sub_strategies=[VolumeBurst(vol_period=10), SMACrossover(fast=10, slow=30)],
                require_all=True,
                target_size_usd=TARGET_SIZE,
            ),
        },
    ]


def _walk_forward_split(candles: list, oos_split: float) -> tuple[list, list]:
    """Split candles into IS (in-sample) and OOS (out-of-sample)."""
    n_oos = max(1, int(len(candles) * oos_split))
    return candles[:-n_oos], candles[-n_oos:]


def run_grid() -> list[dict]:
    """Run full grid. Returns list of result dicts."""
    combos = build_combos()
    results = []
    total = len(TICKERS) * len(TIMEFRAMES) * len(combos)
    run_idx = 0

    print(f"\n=== Polaris Phase 2h Combination Backtest Grid ===")
    print(f"Grid: {len(TICKERS)} tickers × {len(TIMEFRAMES)} TF × {len(combos)} combos = {total} runs")
    print(f"Fee: {FEE_ROUND_TRIP*100:.2f}% round-trip | OOS: {int(OOS_SPLIT*100)}%\n")

    for ticker in TICKERS:
        for tf in TIMEFRAMES:
            n_candles = CANDLES_PER_TF[tf]
            print(f"  Fetching {ticker} {tf} ({n_candles} candles)...", end="", flush=True)
            try:
                candles = fetch_history_paged(inst_id=ticker, bar=tf, n_candles=n_candles)
            except Exception as exc:
                print(f" FAILED: {exc}")
                for combo in combos:
                    run_idx += 1
                    results.append({
                        "hypo": combo["hypo"],
                        "ticker": ticker,
                        "tf": tf,
                        "error": str(exc),
                    })
                continue
            print(f" got {len(candles)} candles")

            is_candles, oos_candles = _walk_forward_split(candles, OOS_SPLIT)

            for combo in combos:
                run_idx += 1
                hypo = combo["hypo"]
                strategy = combo["strategy"]

                # Check candle count vs min_window
                if len(is_candles) < strategy.min_window + 5:
                    results.append({
                        "hypo": hypo,
                        "ticker": ticker,
                        "tf": tf,
                        "n_candles_is": len(is_candles),
                        "n_candles_oos": len(oos_candles),
                        "min_window": strategy.min_window,
                        "error": f"insufficient IS candles {len(is_candles)} < {strategy.min_window}",
                    })
                    continue

                try:
                    # IS backtest
                    is_result = backtest(is_candles, strategy, ticker, tf, FEE_ROUND_TRIP)
                    # OOS backtest (validation)
                    oos_result = backtest(oos_candles, strategy, ticker, tf, FEE_ROUND_TRIP)

                    row = {
                        "hypo": hypo,
                        "desc": combo["desc"],
                        "ticker": ticker,
                        "tf": tf,
                        "n_candles_is": len(is_candles),
                        "n_candles_oos": len(oos_candles),
                        "min_window": strategy.min_window,
                        # IS metrics
                        "is_n_trades": is_result.n_trades,
                        "is_hit_rate": round(is_result.hit_rate, 4),
                        "is_expectancy": round(is_result.expectancy, 6),
                        "is_sharpe": round(is_result.sharpe, 4),
                        "is_max_drawdown": round(is_result.max_drawdown, 4),
                        # OOS metrics
                        "oos_n_trades": oos_result.n_trades,
                        "oos_hit_rate": round(oos_result.hit_rate, 4),
                        "oos_expectancy": round(oos_result.expectancy, 6),
                        "oos_sharpe": round(oos_result.sharpe, 4),
                        "oos_max_drawdown": round(oos_result.max_drawdown, 4),
                    }
                    results.append(row)
                except Exception as exc:
                    results.append({
                        "hypo": hypo,
                        "ticker": ticker,
                        "tf": tf,
                        "error": str(exc),
                    })

    return results


def print_results(results: list[dict]) -> list[dict]:
    """Print grid results and return viable list."""
    valid = [r for r in results if "error" not in r and r.get("is_n_trades", 0) > 0]
    viable = [
        r for r in valid
        if r["is_sharpe"] >= MIN_SHARPE
        and r["is_expectancy"] > MIN_EXPECTANCY
        and r["is_n_trades"] >= MIN_TRADES
    ]
    # Also check OOS positive expectancy for viable
    viable_oos = [
        r for r in viable
        if r["oos_n_trades"] > 0 and r["oos_expectancy"] > 0
    ]

    print(f"\n{'='*70}")
    print(f"ALL RESULTS ({len(valid)} valid runs)")
    print(f"{'='*70}")
    print(f"{'HYPO':<28} {'Ticker':<10} {'TF':<5} {'IS_n':>5} {'IS_exp':>8} {'IS_Sha':>7} {'OOS_n':>6} {'OOS_exp':>9}")
    print("-" * 90)
    for r in sorted(valid, key=lambda x: -x["is_sharpe"]):
        flag = " *** VIABLE" if r in viable_oos else (" * IS-viable" if r in viable else "")
        print(
            f"{r['hypo']:<28} {r['ticker']:<10} {r['tf']:<5} "
            f"{r['is_n_trades']:>5} {r['is_expectancy']:>+8.4f} {r['is_sharpe']:>7.2f} "
            f"{r['oos_n_trades']:>6} {r['oos_expectancy']:>+9.4f}{flag}"
        )

    print(f"\n{'='*70}")
    print(f"VIABLE (IS Sharpe>={MIN_SHARPE} + EV>0 + n>={MIN_TRADES} + OOS EV>0): {len(viable_oos)}/{len(results)} runs")
    print(f"{'='*70}")
    if viable_oos:
        for r in sorted(viable_oos, key=lambda x: -x["oos_expectancy"]):
            print(
                f"  {r['hypo']} {r['ticker']} {r['tf']}: "
                f"IS exp={r['is_expectancy']:+.4f} Sh={r['is_sharpe']:.2f} MDD={r['is_max_drawdown']:.1%} "
                f"| OOS exp={r['oos_expectancy']:+.4f} n={r['oos_n_trades']}"
            )
    else:
        print("  (none — all filter out. IS-only viable:")
        for r in sorted(viable, key=lambda x: -x["is_expectancy"])[:5]:
            print(
                f"    {r['hypo']} {r['ticker']} {r['tf']}: "
                f"IS exp={r['is_expectancy']:+.4f} Sh={r['is_sharpe']:.2f} "
                f"| OOS exp={r['oos_expectancy']:+.4f} n={r['oos_n_trades']} trades)"
            )

    return viable_oos


def main() -> None:
    start = time.time()
    results = run_grid()

    # Save full results
    Path("data/backtest").mkdir(parents=True, exist_ok=True)
    out_path = f"data/backtest/combinations_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results saved → {out_path}")

    viable = print_results(results)

    elapsed = time.time() - start
    print(f"\nTotal time: {elapsed:.1f}s | {len(results)} runs")

    if not viable:
        print("\n[INSIGHT] 모든 조합 filter out — 개별 대안:")
        print("  - timeframe 늘리기 (1D only)")
        print("  - exit 로직 추가 (TP/SL band)")
        print("  - 3rd filter (BTC dominance, funding rate)")
    else:
        print(f"\n[ACTION] {len(viable)} viable strategy found → 60_alpha/active 등록 검토 (HYPO-018+ 후보)")


if __name__ == "__main__":
    main()
