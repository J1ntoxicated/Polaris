"""HYPO-020 3-fold walk-forward validation (INSIGHT-016 + HYPO-003 기준 통일).

ADR-010 Promotion Gate 요건 중 하나: walk-forward 3-fold all TEST EV positive.

Walk-forward scheme (expanding window):
  Fold 1: TRAIN 0-40%,  TEST 40-60%
  Fold 2: TRAIN 0-60%,  TEST 60-80%
  Fold 3: TRAIN 0-80%,  TEST 80-100%

Robust 판정 기준 (INSIGHT-016):
  - 각 fold TEST: expectancy > 0 (EV 양수)
  - 각 fold TEST: Sharpe >= 0.3 (ADR-011 swing threshold)
  - 모든 3 fold 통과 → ROBUST

Usage:
    .venv/bin/python scripts/walkforward_validate.py
    .venv/bin/python scripts/walkforward_validate.py --ticker DOGE-USDT --max-pages 50
    .venv/bin/python scripts/walkforward_validate.py --debug
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Project root → sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import backtest
from src.data.okx_history import fetch_history_paged
from src.domain.candle import Candle
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.volume_burst import VolumeBurst

FEE_ROUND_TRIP = 0.0014  # OKX paper Lv3 (INSIGHT-007 + INSIGHT-025 latent bug fix)
SHARPE_THRESHOLD = 0.3    # ADR-011 swing threshold
EV_THRESHOLD = 0.0        # EV > 0 required


def _make_strategy() -> ConfluenceSignal:
    """HYPO-020: ConfluenceSignal(VB + Donchian 40/15, require_all=True)."""
    return ConfluenceSignal(
        sub_strategies=[VolumeBurst(), DonchianBreakout(40, 15)],
        require_all=True,
        target_size_usd=200.0,
    )


def run_walkforward(
    ticker: str = "DOGE-USDT",
    bar: str = "1D",
    max_pages: int = 50,
    debug: bool = False,
) -> dict:
    """Run 3-fold walk-forward validation for HYPO-020.

    Returns:
        dict with fold results + overall robust verdict.
    """
    print(f"Fetching {ticker} {bar} candles (max_pages={max_pages})...")
    candles: list[Candle] = fetch_history_paged(
        inst_id=ticker,
        bar=bar,
        n_candles=max_pages * 100,
    )
    n = len(candles)
    print(f"Fetched {n} candles ({candles[0].timestamp_ms} → {candles[-1].timestamp_ms})")

    if n < 100:
        print(f"ERROR: insufficient candles ({n} < 100). Walk-forward requires >= 100.")
        return {"robust": False, "error": f"only {n} candles"}

    strategy_name = _make_strategy().name

    # 3-fold expanding window
    folds = [
        (0, int(0.4 * n), int(0.4 * n), int(0.6 * n)),
        (0, int(0.6 * n), int(0.6 * n), int(0.8 * n)),
        (0, int(0.8 * n), int(0.8 * n), n),
    ]

    print()
    print(f"=== HYPO-020 3-Fold Walk-Forward ({ticker} {bar}, n={n}) ===")
    print(f"Strategy: {strategy_name}")
    print(f"Fee: {FEE_ROUND_TRIP} ({FEE_ROUND_TRIP*100:.2f}% round-trip)")
    print(f"Threshold: EV > {EV_THRESHOLD:.0%} AND Sharpe >= {SHARPE_THRESHOLD}")
    print()

    fold_results = []
    all_pass = True

    for i, (tr_start, tr_end, te_start, te_end) in enumerate(folds, 1):
        train = candles[tr_start:tr_end]
        test = candles[te_start:te_end]

        train_result = backtest(
            candles=train,
            strategy=_make_strategy(),
            ticker=ticker,
            timeframe=bar,
            fee_round_trip=FEE_ROUND_TRIP,
        )
        test_result = backtest(
            candles=test,
            strategy=_make_strategy(),
            ticker=ticker,
            timeframe=bar,
            fee_round_trip=FEE_ROUND_TRIP,
        )

        ev_pass = test_result.expectancy > EV_THRESHOLD
        sharpe_pass = test_result.sharpe >= SHARPE_THRESHOLD
        fold_pass = ev_pass and sharpe_pass
        if not fold_pass:
            all_pass = False

        verdict = "PASS" if fold_pass else "FAIL"

        print(
            f"Fold {i}: TRAIN[{tr_start}:{tr_end}] n={train_result.n_trades:3d} "
            f"exp={train_result.expectancy:+.4f} sharpe={train_result.sharpe:.3f}"
            f"  |  TEST[{te_start}:{te_end}] n={test_result.n_trades:3d} "
            f"exp={test_result.expectancy:+.4f} sharpe={test_result.sharpe:.3f} "
            f"ev={'OK' if ev_pass else 'FAIL'} sharpe={'OK' if sharpe_pass else 'FAIL'} "
            f"[{verdict}]"
        )

        if debug:
            print(
                f"  TRAIN: hit_rate={train_result.hit_rate:.3f} "
                f"max_dd={train_result.max_drawdown:.4f}"
            )
            print(
                f"  TEST:  hit_rate={test_result.hit_rate:.3f} "
                f"max_dd={test_result.max_drawdown:.4f}"
            )

        fold_results.append({
            "fold": i,
            "train_candles": len(train),
            "test_candles": len(test),
            "train": train_result.summary(),
            "test": test_result.summary(),
            "ev_pass": ev_pass,
            "sharpe_pass": sharpe_pass,
            "fold_pass": fold_pass,
        })

    print()
    overall_verdict = "ROBUST" if all_pass else "NOT ROBUST"
    print(f"=== Walk-Forward Verdict: {overall_verdict} ===")
    if all_pass:
        print("All 3 fold TEST: EV > 0 + Sharpe >= 0.3 — HYPO-020 walk-forward PASSED.")
        print("Next step: 60-day paper tracking completion → Promotion Gate review.")
    else:
        failed = [f["fold"] for f in fold_results if not f["fold_pass"]]
        print(f"Fold(s) {failed} failed. HYPO-020 needs parameter review.")
        print("Do NOT promote to cron ACTIVE_HYPOS.")

    return {
        "ticker": ticker,
        "bar": bar,
        "n_candles": n,
        "strategy": strategy_name,
        "fee_round_trip": FEE_ROUND_TRIP,
        "folds": fold_results,
        "robust": all_pass,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="HYPO-020 3-fold walk-forward validation (INSIGHT-016)"
    )
    ap.add_argument("--ticker", default="DOGE-USDT", help="OKX instrument id")
    ap.add_argument("--bar", default="1D", help="candle timeframe")
    ap.add_argument(
        "--max-pages", type=int, default=50,
        help="max fetch pages (100 candles/page, default 50 = 5000 candles)"
    )
    ap.add_argument("--debug", action="store_true", help="print hit_rate + max_dd per fold")
    args = ap.parse_args()

    result = run_walkforward(
        ticker=args.ticker,
        bar=args.bar,
        max_pages=args.max_pages,
        debug=args.debug,
    )
    sys.exit(0 if result.get("robust") else 1)


if __name__ == "__main__":
    main()
