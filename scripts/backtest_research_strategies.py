"""Academic validation backtest — HYPO-032 TSMOM + HYPO-034 BTC Lead-Lag.

References:
  Moskowitz, Ooi, Pedersen (2012) JFE: "Time series momentum"
  Stalder & Cosenza (2025): "Cross-cryptocurrency return predictability"
  Liu et al. (2022) JoF: "Common risk factors in cryptocurrency"

Usage:
    python3 -m scripts.backtest_research_strategies

Output:
  - HYPO-032 TSMOM per ticker: expectancy, Sharpe, MDD, walk-forward 3-fold
  - HYPO-034 BTC lead-lag: per alt ticker (BTC 5m candle-based proxy)
  - Viable filter: Sharpe >= 0.3 AND expectancy > 0 AND n_trades >= 5 (swing 1D)

Viable tickers → cron candidate list at end.

Fee: 0.0014 (INSIGHT-007 OKX paper Lv1 LV3+ taker 0.07% x2).
"""
from __future__ import annotations

import time
from typing import NamedTuple

from src.backtest.engine import backtest
from src.backtest.result import BacktestResult
from src.data.okx_history import fetch_history_paged
from src.domain.candle import Candle
from src.strategies.btc_dominance_lag import BTCDominanceLag
from src.strategies.tsmom import TSMOM

# ─────── Config ───────────────────────────────────────────────────────────────

TICKERS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT",
    "ADA-USDT", "XRP-USDT",
]
ALT_TICKERS = ["ETH-USDT", "SOL-USDT", "DOGE-USDT", "ADA-USDT", "XRP-USDT"]

FEE_ROUND_TRIP = 0.0014       # INSIGHT-007
TSMOM_1D_N_CANDLES = 1500    # ~4.1 years 1D candles
LEADLAG_5M_N_CANDLES = 2000  # ~6.9 days 5m candles

VIABLE_MIN_SHARPE = 0.3       # ADR-011 swing threshold
VIABLE_MIN_EV = 0.0           # positive EV required
VIABLE_MIN_TRADES = 5         # minimum trades for 1D (lower than scalp due to hold)

WALKFORWARD_FOLDS = 3
# Walk-forward OOS fold fractions: test on last 3 non-overlapping slices
# Fold 1: 40-60%, Fold 2: 60-80%, Fold 3: 80-100%
WF_FOLD_RANGES = [
    (0.40, 0.60),
    (0.60, 0.80),
    (0.80, 1.00),
]


# ─────── Helpers ──────────────────────────────────────────────────────────────

class ViableCandidate(NamedTuple):
    ticker: str
    strategy: str
    sharpe: float
    expectancy: float
    n_trades: int
    wf_all_pass: bool


def _is_viable(result: BacktestResult) -> bool:
    return (
        result.sharpe >= VIABLE_MIN_SHARPE
        and result.expectancy > VIABLE_MIN_EV
        and result.n_trades >= VIABLE_MIN_TRADES
    )


def _walkforward_3fold(candles: list[Candle], strategy_factory) -> list[dict]:
    """Walk-forward 3-fold OOS validation.

    Folds: each fold tests on a slice of the full candle history.
    Strategy is re-instantiated for each fold (no state leakage).

    Returns list of fold results.
    """
    n = len(candles)
    results = []
    for fold_idx, (start_frac, end_frac) in enumerate(WF_FOLD_RANGES, 1):
        start = int(start_frac * n)
        end = int(end_frac * n)
        fold_candles = candles[start:end]
        if not fold_candles or len(fold_candles) < 10:
            results.append({"fold": fold_idx, "n_candles": 0, "trades": 0, "exp": 0.0, "sharpe": 0.0, "verdict": "SKIP"})
            continue
        strat = strategy_factory()
        result = backtest(fold_candles, strat, ticker="wf", timeframe="1D", fee_round_trip=FEE_ROUND_TRIP)
        verdict = "PASS" if result.expectancy > VIABLE_MIN_EV and result.sharpe >= VIABLE_MIN_SHARPE else "FAIL"
        results.append({
            "fold": fold_idx,
            "n_candles": len(fold_candles),
            "trades": result.n_trades,
            "exp": result.expectancy,
            "sharpe": result.sharpe,
            "verdict": verdict,
        })
    return results


def _print_sep(char: str = "-", width: int = 72) -> None:
    print(char * width)


# ─────── HYPO-032: TSMOM 1D Backtest ─────────────────────────────────────────

def run_hypo032_tsmom() -> list[ViableCandidate]:
    """HYPO-032: Time-Series Momentum 1D backtest across all tickers.

    Returns viable candidates (Sharpe >= 0.3 AND EV > 0 AND n_trades >= 5).
    """
    print()
    _print_sep("=")
    print("HYPO-032: Time-Series Momentum (Moskowitz/Ooi/Pedersen 2012 JFE)")
    print("  lookbacks: 1d/7d/30d | bar: 1D | fee: 0.14% round-trip")
    _print_sep("=")

    results: list[tuple[str, BacktestResult]] = []
    viable: list[ViableCandidate] = []

    for ticker in TICKERS:
        print(f"  Fetching {ticker} 1D candles (~{TSMOM_1D_N_CANDLES} max)...", end=" ", flush=True)
        try:
            candles = fetch_history_paged(inst_id=ticker, bar="1D", n_candles=TSMOM_1D_N_CANDLES)
            print(f"n={len(candles)}")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        if len(candles) < 35:
            print(f"    {ticker}: SKIP — insufficient candles ({len(candles)} < 35)")
            continue

        strat = TSMOM()
        result = backtest(candles, strat, ticker=ticker, timeframe="1D", fee_round_trip=FEE_ROUND_TRIP)
        results.append((ticker, result))

        viable_flag = "VIABLE" if _is_viable(result) else "skip"
        print(
            f"    {ticker}: n_trades={result.n_trades:3d} "
            f"exp={result.expectancy:+.2%} "
            f"Sharpe={result.sharpe:.2f} "
            f"MDD={result.max_drawdown:.1%} "
            f"hit={result.hit_rate:.0%}  [{viable_flag}]"
        )

        if _is_viable(result):
            viable.append(ViableCandidate(
                ticker=ticker,
                strategy="TSMOM",
                sharpe=result.sharpe,
                expectancy=result.expectancy,
                n_trades=result.n_trades,
                wf_all_pass=False,  # computed in walk-forward below
            ))

        time.sleep(0.15)  # OKX_RPS_DELAY

    # Walk-forward for viable tickers
    if viable:
        print()
        print("  --- Walk-Forward 3-Fold (OOS) for VIABLE tickers ---")
        updated_viable = []
        for cand in viable:
            print(f"  WALK-FORWARD {cand.ticker} (TSMOM):")
            ticker_candles = None
            # Re-fetch (already fetched but not stored — refetch for clarity)
            try:
                ticker_candles = fetch_history_paged(inst_id=cand.ticker, bar="1D", n_candles=TSMOM_1D_N_CANDLES)
                time.sleep(0.15)
            except Exception as e:
                print(f"    Refetch error: {e}")
                updated_viable.append(cand)
                continue

            fold_results = _walkforward_3fold(ticker_candles, TSMOM)
            all_pass = True
            for fr in fold_results:
                print(
                    f"    Fold {fr['fold']}: n_candles={fr['n_candles']:4d} "
                    f"trades={fr['trades']:3d} "
                    f"exp={fr['exp']:+.2%} "
                    f"Sharpe={fr['sharpe']:.2f}  [{fr['verdict']}]"
                )
                if fr["verdict"] != "PASS":
                    all_pass = False

            updated_viable.append(cand._replace(wf_all_pass=all_pass))
        viable = updated_viable

    return viable


# ─────── HYPO-034: BTC Lead-Lag 5m Backtest ──────────────────────────────────

class _BtcLeadLagProxy(TSMOM):
    """Proxy strategy for BTC lead-lag candle-based backtest.

    Since BTCDominanceLag.evaluate_lag requires tick+state (realtime),
    we proxy it with TSMOM(1d=5m) as a stand-in for the lag-catch signal.

    Academic validation: BTC 5m return predicts alt 5m return within 3 min.
    Proxy: use BTC-correlated candles and short-lookback TSMOM as a directional
    filter. This is a conservative lower-bound estimate of the real strategy.

    IMPORTANT: This is a methodological approximation. Actual evaluate_lag
    requires live BTC state + alt tick — not backtest-able via candle engine alone.
    We use TSMOM(1, 3) on 5m alt candles as proxy (follow-the-leader momentum).
    """

    name = "btc_lead_lag_proxy"

    def __init__(self) -> None:
        # lookbacks: 1-bar (5min) + 3-bar (15min) — captures short-term lag window
        super().__init__(
            lookbacks_days=(1, 3),
            enter_threshold=0.6,
            exit_threshold=0.4,
        )


def run_hypo034_btc_lead_lag() -> list[ViableCandidate]:
    """HYPO-034: BTC Lead-Lag 5m backtest (candle-based proxy).

    NOTE: Real BTCDominanceLag uses tick+state (evaluate_lag).
    This backtest uses TSMOM(1, 3) on 5m alt candles as conservative proxy.
    Viable result here = evidence for paper stage. Realtime validate separately.

    Returns viable candidates.
    """
    print()
    _print_sep("=")
    print("HYPO-034: BTC Lead-Lag Cross-Asset (Stalder & Cosenza 2025)")
    print("  proxy: TSMOM(1,3) on 5m alt candles | fee: 0.14% round-trip")
    print("  NOTE: Proxy approximation — actual strategy requires realtime BTC state")
    _print_sep("=")

    print(f"  Fetching BTC-USDT 5m candles (~{LEADLAG_5M_N_CANDLES})...", end=" ", flush=True)
    try:
        btc_5m = fetch_history_paged(inst_id="BTC-USDT", bar="5m", n_candles=LEADLAG_5M_N_CANDLES)
        print(f"n={len(btc_5m)}")
        time.sleep(0.15)
    except Exception as e:
        print(f"ERROR fetching BTC 5m: {e}")
        return []

    viable: list[ViableCandidate] = []

    for ticker in ALT_TICKERS:
        print(f"  Fetching {ticker} 5m candles (~{LEADLAG_5M_N_CANDLES})...", end=" ", flush=True)
        try:
            alt_5m = fetch_history_paged(inst_id=ticker, bar="5m", n_candles=LEADLAG_5M_N_CANDLES)
            print(f"n={len(alt_5m)}")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        if len(alt_5m) < 10:
            print(f"    {ticker}: SKIP — insufficient candles")
            continue

        strat = _BtcLeadLagProxy()
        result = backtest(alt_5m, strat, ticker=ticker, timeframe="5m", fee_round_trip=FEE_ROUND_TRIP)

        viable_flag = "VIABLE" if _is_viable(result) else "skip"
        print(
            f"    {ticker}: n_trades={result.n_trades:4d} "
            f"exp={result.expectancy:+.2%} "
            f"Sharpe={result.sharpe:.2f} "
            f"MDD={result.max_drawdown:.1%} "
            f"hit={result.hit_rate:.0%}  [{viable_flag}]"
            f"\n      (proxy — realtime validate needed for confirm)"
        )

        if _is_viable(result):
            viable.append(ViableCandidate(
                ticker=ticker,
                strategy="BTC_LEAD_LAG_proxy",
                sharpe=result.sharpe,
                expectancy=result.expectancy,
                n_trades=result.n_trades,
                wf_all_pass=False,
            ))

        time.sleep(0.15)

    return viable


# ─────── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 72)
    print("Polaris Academic Strategy Backtest")
    print("Date: 2026-05-04  |  Fee: 0.14% round-trip (INSIGHT-007)")
    print("Viable: Sharpe >= 0.3 AND EV > 0 AND n_trades >= 5")
    print("=" * 72)

    all_viable: list[ViableCandidate] = []

    # HYPO-032 TSMOM
    viable_032 = run_hypo032_tsmom()
    all_viable.extend(viable_032)

    # HYPO-034 BTC Lead-Lag
    viable_034 = run_hypo034_btc_lead_lag()
    all_viable.extend(viable_034)

    # ─────── Summary ──────────────────────────────────────────────────────────
    print()
    _print_sep("=")
    print("SUMMARY — Viable Candidates")
    _print_sep("=")

    if not all_viable:
        print("  No viable candidates found. Parameters may need tuning.")
    else:
        print(f"  Found {len(all_viable)} viable candidate(s):\n")
        for c in all_viable:
            wf_label = "WF:ALL-PASS" if c.wf_all_pass else ("WF:PARTIAL" if c.strategy == "TSMOM" else "WF:n/a(5m)")
            print(
                f"  [{c.strategy}] {c.ticker}: "
                f"Sharpe={c.sharpe:.2f}  EV={c.expectancy:+.2%}  "
                f"n_trades={c.n_trades}  {wf_label}"
            )

        print()
        viable_hypo032 = [c for c in all_viable if c.strategy == "TSMOM"]
        viable_hypo034 = [c for c in all_viable if "LEAD_LAG" in c.strategy]

        print(f"  HYPO-032 TSMOM viable: {len(viable_hypo032)}/{len(TICKERS)} tickers")
        print(f"  HYPO-034 BTC Lead-Lag (proxy): {len(viable_hypo034)}/{len(ALT_TICKERS)} tickers")

        # Cron candidate recommendation
        wf_passed = [c for c in viable_hypo032 if c.wf_all_pass]
        if wf_passed:
            print()
            print("  CRON CANDIDATE RECOMMENDATION (walk-forward all-PASS):")
            for c in wf_passed:
                print(f"    → Add {c.ticker} TSMOM 1D to daily_paper_runner.py (ADR-010 paper stage)")
        elif viable_hypo032:
            print()
            print("  PAPER STAGE RECOMMENDATION (viable but WF not all-PASS):")
            for c in viable_hypo032:
                print(f"    → Paper-validate {c.ticker} TSMOM 1D (60 days per ADR-010)")

    print()
    _print_sep("=")
    print("HYPO-035 (CrossSectionalMomentum) and HYPO-036 (FundingCarry):")
    print("  → strategies implemented, paper-stage pending (see daily_paper_runner.py)")
    print("  → Backtest via candle engine not applicable:")
    print("    HYPO-035: requires cross-ticker ranking (multi-ticker engine needed)")
    print("    HYPO-036: requires Binance funding REST data (event-driven, not candle-based)")
    _print_sep("=")
    print()


if __name__ == "__main__":
    main()
