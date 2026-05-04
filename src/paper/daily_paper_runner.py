"""Daily paper runner — ADR-010 paper validation stage (shell P6).

ADR-010 lifecycle: BACKTEST → PAPER → Promotion Gate → cron.

This module = PAPER stage. Hypotheses here are NOT production triggers.
They are under paper validation: 60-day real-time tracking before
Promotion Gate review (Sharpe + EV + hit_rate criteria).

Usage:
    python -m src.paper.daily_paper_runner                # all paper hypos
    python -m src.paper.daily_paper_runner --dry-run      # print only, no state save

Cron setup (paper validation — separate from production cron):
    30 1 * * * cd /Users/jinyoon/Projects/Polaris && .venv/bin/python -m src.paper.daily_paper_runner >> data/paper/daily_paper.log 2>&1

Promotion Gate criteria (ADR-011):
    - paper Sharpe >= 0.3 (swing threshold)
    - paper expectancy > 0 (EV positive)
    - n_trades >= 10 (sample adequacy)
    - walk-forward 3-fold all TEST EV positive (INSIGHT-016)
    - 60+ calendar days of paper tracking
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
import traceback

from src.data.binance_funding import fetch_funding_rate, to_binance_futures_symbol
from src.data.okx_history import fetch_history
from src.paper.runner import run_cycle
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.cross_sectional_momentum import CrossSectionalMomentum
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.funding_carry import FundingCarry
from src.strategies.volume_burst import VolumeBurst

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ─────── Paper Validation HYPOTHESES ───────────────────────────────────────────
# These hypotheses passed BACKTEST gate (ADR-011 IS Sharpe threshold) but have
# NOT completed paper validation. They run daily alongside production cron to
# accumulate real-time data for Promotion Gate review.
#
# Promotion Gate (cron entry):
#   - paper Sharpe >= 0.3 AND paper EV > 0 AND n_trades >= 10
#   - walk-forward 3-fold robust (scripts/walkforward_validate.py)
#   - 60+ calendar days paper tracking
#
# Status annotation: paper_since = date paper validation started.
DAILY_PAPER_HYPOS = [
    # HYPO-020: VolumeBurst AND DonchianBreakout confluence (Phase 2h)
    # BACKTEST: DOGE 1D IS n=14, exp +11.81%, Sharpe 0.42 > ADR-011 swing threshold 0.3
    # OOS: n=3, exp +2.78% (consistent positive — INSIGHT-031).
    # ORDI archived: outlier-driven (1 trade +435.4% = inscription boom 2023, INSIGHT-031).
    # ADR-010 compliance (Codex Round 15 fix 2026-05-04):
    #   cron entry was premature — paper stage not completed.
    #   Moved from cron ACTIVE_HYPOS to here for proper paper tracking.
    # paper_since: 2026-05-04
    # HYPO-035: Cross-Sectional Momentum — Jegadeesh & Titman 1993 JoF
    # Academic: 50+ year persistent factor in stocks/futures/crypto (Liu et al. 2022).
    # Crypto adapt: 30d lookback / 7d hold / top 30% → long / SPOT-only (no short).
    # Runner: uses set_rank_pct injection (cross-sectional ranking via evaluate_universe).
    # For paper_runner compatibility, each ticker runs independently with rank_pct pre-set
    # via _run_cs_momentum_cycle (see main() below). SPOT long-only.
    # Promotion criteria: 60d paper, Sharpe >= 0.3, EV > 0, n_trades >= 8.
    # paper_since: 2026-05-04
    {
        "hypo_id": "HYPO-035-CS-MOM",
        "_type": "cross_sectional",  # runner dispatches to _run_cs_momentum_cycle()
        "strategy_cls": CrossSectionalMomentum,
        "strategy_params": {
            "lookback_days": 30,
            "hold_days": 7,
            "top_pct": 0.30,
            "target_size_usd": 200.0,
        },
        "universe": [
            "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT",
            "ADA-USDT", "XRP-USDT", "ORDI-USDT", "SUI-USDT",
        ],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 8,
            "min_calendar_days": 60,
        },
    },
    # HYPO-036: Carry Trade — Funding Rate Extreme (Liu & Yu 2024)
    # Entry: Binance 8h funding <= -0.05% → SPOT long carry opportunity.
    # Exit: funding recovery >= 0% OR 12h max hold.
    # Runner: polling via binance_funding.fetch_funding_rate (REST, not candle-based).
    # For paper_runner compatibility, dispatches to _run_funding_carry_cycle() in main().
    # SPOT long-only.
    # Promotion criteria: 60d paper, Sharpe >= 0.3, EV > 0, n_trades >= 8.
    # paper_since: 2026-05-04
    {
        "hypo_id": "HYPO-036-FUNDING-CARRY",
        "_type": "funding_carry",  # runner dispatches to _run_funding_carry_cycle()
        "strategy_cls": FundingCarry,
        "strategy_params": {
            "entry_threshold": -0.0005,
            "exit_threshold": 0.0,
            "max_hold_hours": 12.0,
            "target_size_usd": 200.0,
        },
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "bar": "1H",  # check funding every 1H (funding cycle 8H, intraday bar for price)
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 8,
            "min_calendar_days": 60,
        },
    },
    {
        "hypo_id": "HYPO-020-VB-DONCH-DOGE",
        "strategy": ConfluenceSignal,
        "strategy_params": {
            "sub_strategies": [VolumeBurst(), DonchianBreakout(40, 15)],
            "require_all": True,
            "target_size_usd": 200.0,
        },
        "tickers": ["DOGE-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # ── Phase 2O (2026-05-04): 7 backtest-viable strategies paper validation ────
    # Multi-resolution backtest grid (171s) identified 10 viable (Sharpe>=0.3, IS+OOS EV>0).
    # ADR-010: all enter PAPER stage (60d tracking) before Promotion Gate → cron.
    #
    # HYPO-004-DONCH-DOGE-1D: IS exp +63%, OOS exp +67%, Sharpe 1.16 — strongest signal.
    # Donchian 40/15 applied to DOGE (already proven for BTC/ETH in cron ACTIVE_HYPOS).
    {
        "hypo_id": "HYPO-004-DONCH-DOGE-1D",
        "strategy": DonchianBreakout,
        "strategy_params": {"entry_period": 40, "exit_period": 15},
        "tickers": ["DOGE-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-020-VB-DONCH-BTC-1D: IS Sharpe 0.57 — VB+Donchian AND confluence for BTC.
    {
        "hypo_id": "HYPO-020-VB-DONCH-BTC-1D",
        "strategy": ConfluenceSignal,
        "strategy_params": {
            "sub_strategies": [VolumeBurst(), DonchianBreakout(40, 15)],
            "require_all": True,
            "target_size_usd": 200.0,
        },
        "tickers": ["BTC-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-020-VB-DONCH-ETH-1D: IS Sharpe 0.45 — VB+Donchian AND confluence for ETH.
    {
        "hypo_id": "HYPO-020-VB-DONCH-ETH-1D",
        "strategy": ConfluenceSignal,
        "strategy_params": {
            "sub_strategies": [VolumeBurst(), DonchianBreakout(40, 15)],
            "require_all": True,
            "target_size_usd": 200.0,
        },
        "tickers": ["ETH-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-008-VB-1D-BTC: VolumeBurst 1D (currently runs 1H realtime — 1D is new entry).
    # IS Sharpe 0.38 — slower signal, captures larger volume expansions.
    {
        "hypo_id": "HYPO-008-VB-1D-BTC",
        "strategy": VolumeBurst,
        "strategy_params": {},
        "tickers": ["BTC-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-008-VB-1D-ETH: IS Sharpe 0.41 — slightly stronger than BTC 1D variant.
    {
        "hypo_id": "HYPO-008-VB-1D-ETH",
        "strategy": VolumeBurst,
        "strategy_params": {},
        "tickers": ["ETH-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-008-VB-1D-DOGE: IS Sharpe 0.36 — weakest of 1D VB trio but OOS EV>0.
    {
        "hypo_id": "HYPO-008-VB-1D-DOGE",
        "strategy": VolumeBurst,
        "strategy_params": {},
        "tickers": ["DOGE-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
    # HYPO-008-VB-4H-ETH: first viable 4H entry. n=53 trades (9x faster than 1D).
    # IS Sharpe 0.33 — runner.py bar="4H" uses OKX 4H candles natively.
    {
        "hypo_id": "HYPO-008-VB-4H-ETH",
        "strategy": VolumeBurst,
        "strategy_params": {},
        "tickers": ["ETH-USDT"],
        "bar": "4H",
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,
        "paper_since": "2026-05-04",
        "promotion_criteria": {
            "min_sharpe": 0.3,
            "min_ev": 0.0,
            "min_trades": 10,
            "min_calendar_days": 60,
        },
    },
]


def _run_cs_momentum_cycle(hypo: dict, dry_run: bool = False) -> tuple[list[dict], list[str]]:
    """HYPO-035 cross-sectional momentum cycle.

    Fetches all universe 1D candles, computes cross-sectional ranks,
    injects rank_pct into strategy, then runs per-ticker paper cycle.

    Returns (summaries, errors).
    """
    summaries = []
    errors = []
    hypo_id = hypo["hypo_id"]
    paper_since = hypo.get("paper_since", "unknown")
    universe = hypo.get("universe", [])
    bar = hypo.get("bar", "1D")

    strat_cls = hypo["strategy_cls"]
    strat_params = hypo["strategy_params"]
    # Build universe-aware strategy instance
    strat = strat_cls(universe=universe, **strat_params)

    # Fetch all universe candles
    universe_windows: dict[str, list] = {}
    for ticker in universe:
        try:
            candles = fetch_history(inst_id=ticker, bar=bar, limit=min(strat.min_window + 10, 100))
            universe_windows[ticker] = candles
        except Exception as e:
            logger.warning(f"{hypo_id} {ticker}: candle fetch failed: {e}")
            universe_windows[ticker] = []

    # Cross-sectional evaluate
    try:
        universe_signals = strat.evaluate_universe(universe_windows)
    except Exception as e:
        errors.append(f"{hypo_id} evaluate_universe: {type(e).__name__}: {e}")
        return summaries, errors

    # Per-ticker paper cycle with injected rank
    for ticker in universe:
        try:
            rank_sig = universe_signals.get(ticker)
            if rank_sig is None:
                continue
            # Build ticker-specific strategy instance with rank injected
            ticker_strat = strat_cls(universe=universe, **strat_params)
            if rank_sig.action.value == "enter_long":
                ticker_strat.set_rank_pct(0.9)  # top — will trigger ENTER_LONG
            elif rank_sig.action.value == "exit":
                ticker_strat.set_rank_pct(0.1)  # bottom — will trigger EXIT
            else:
                ticker_strat.set_rank_pct(0.5)  # mid — HOLD

            if dry_run:
                summaries.append({
                    "hypo_id": hypo_id,
                    "ticker": ticker,
                    "signal": rank_sig.action.value,
                    "reason": rank_sig.reason,
                    "dry_run": True,
                    "paper_since": paper_since,
                })
                continue

            summary = run_cycle(
                ticker=ticker,
                strategy=ticker_strat,
                bar=bar,
                starting_usd=hypo["starting_usd"],
                max_position_pct=hypo["max_position_pct"],
            )
            summary["hypo_id"] = hypo_id
            summary["paper_since"] = paper_since
            summary["cs_rank_signal"] = rank_sig.action.value
            summaries.append(summary)
            logger.info(
                f"[PAPER] {hypo_id} {ticker} {bar}: "
                f"cs_rank={rank_sig.action.value} signal={summary.get('signal')} "
                f"open={summary.get('n_open_post')} equity=${summary.get('equity_usd', 0):.2f}"
            )
        except Exception as e:
            err = f"{hypo_id} {ticker}: {type(e).__name__}: {e}"
            errors.append(err)
            logger.error(err)
            traceback.print_exc()

    return summaries, errors


def _run_funding_carry_cycle(hypo: dict, dry_run: bool = False) -> tuple[list[dict], list[str]]:
    """HYPO-036 funding carry cycle.

    Fetches Binance funding rate, evaluates FundingCarry signal,
    then runs per-ticker paper cycle if signal is ENTER_LONG.

    Returns (summaries, errors).
    """
    summaries = []
    errors = []
    hypo_id = hypo["hypo_id"]
    paper_since = hypo.get("paper_since", "unknown")
    bar = hypo.get("bar", "1H")

    strat_cls = hypo["strategy_cls"]
    strat_params = hypo["strategy_params"]
    strat = strat_cls(**strat_params)

    import time as _time
    ts_ms = int(_time.time() * 1000)

    for ticker in hypo.get("tickers", []):
        try:
            binance_sym = to_binance_futures_symbol(ticker)
            funding_rate = fetch_funding_rate(binance_sym, use_cache=True)

            carry_signal = strat.evaluate_funding(
                funding_8h=funding_rate,
                ts_ms=ts_ms,
                position_age_hours=0.0,
                in_position=False,
            )
            logger.info(
                f"{hypo_id} {ticker}: funding={funding_rate} signal={carry_signal.action.value} "
                f"reason={carry_signal.reason}"
            )

            if dry_run:
                summaries.append({
                    "hypo_id": hypo_id,
                    "ticker": ticker,
                    "funding_rate": funding_rate,
                    "signal": carry_signal.action.value,
                    "reason": carry_signal.reason,
                    "dry_run": True,
                    "paper_since": paper_since,
                })
                continue

            if carry_signal.action.value in ("enter_long", "exit"):
                summary = run_cycle(
                    ticker=ticker,
                    strategy=strat,
                    bar=bar,
                    starting_usd=hypo["starting_usd"],
                    max_position_pct=hypo["max_position_pct"],
                )
                summary["hypo_id"] = hypo_id
                summary["paper_since"] = paper_since
                summary["funding_rate"] = funding_rate
                summaries.append(summary)
                logger.info(
                    f"[PAPER] {hypo_id} {ticker}: "
                    f"funding={funding_rate} runner_signal={summary.get('signal')} "
                    f"equity=${summary.get('equity_usd', 0):.2f}"
                )
            else:
                summaries.append({
                    "hypo_id": hypo_id,
                    "ticker": ticker,
                    "signal": "hold_funding_neutral",
                    "funding_rate": funding_rate,
                    "paper_since": paper_since,
                })

        except Exception as e:
            err = f"{hypo_id} {ticker}: {type(e).__name__}: {e}"
            errors.append(err)
            logger.error(err)
            traceback.print_exc()

    return summaries, errors


def main(dry_run: bool = False) -> int:
    """Run all paper-stage hypos x tickers. Returns exit code (0 = all OK)."""
    today = _dt.date.today().isoformat()
    logger.info(f"=== Polaris Daily Paper Runner (ADR-010 paper stage) — {today} ===")
    if dry_run:
        logger.info("DRY-RUN mode: results printed, state NOT saved")

    errors = []
    summaries = []
    for hypo in DAILY_PAPER_HYPOS:
        hypo_type = hypo.get("_type", "standard")
        paper_since = hypo.get("paper_since", "unknown")

        if hypo_type == "cross_sectional":
            # HYPO-035: cross-sectional universe ranking
            s, e = _run_cs_momentum_cycle(hypo, dry_run=dry_run)
            summaries.extend(s)
            errors.extend(e)
            continue

        if hypo_type == "funding_carry":
            # HYPO-036: funding rate carry
            s, e = _run_funding_carry_cycle(hypo, dry_run=dry_run)
            summaries.extend(s)
            errors.extend(e)
            continue

        # Standard per-ticker cycle
        # Support both "strategy_cls" (new) and "strategy" (legacy HYPO-020 key)
        strategy_cls = hypo.get("strategy_cls") or hypo["strategy"]
        strategy_params = hypo["strategy_params"]
        for ticker in hypo.get("tickers", []):
            try:
                strategy = strategy_cls(**strategy_params)
                summary = run_cycle(
                    ticker=ticker,
                    strategy=strategy,
                    bar=hypo["bar"],
                    starting_usd=hypo["starting_usd"],
                    max_position_pct=hypo["max_position_pct"],
                )
                summary["hypo_id"] = hypo["hypo_id"]
                summary["paper_since"] = paper_since
                summaries.append(summary)
                logger.info(
                    f"[PAPER] {hypo['hypo_id']} {ticker} {hypo['bar']}: "
                    f"signal={summary.get('signal')} "
                    f"open={summary.get('n_open_post')} closed={summary.get('n_closed')} "
                    f"equity=${summary.get('equity_usd', 0):.2f} "
                    f"pnl=${summary.get('realized_pnl_usd', 0):+.2f} "
                    f"(paper_since={paper_since})"
                )
            except Exception as e:
                err = f"{hypo['hypo_id']} {ticker}: {type(e).__name__}: {e}"
                errors.append(err)
                logger.error(err)
                traceback.print_exc()

    print()
    print(f"=== Paper Summary {today} ===")
    print(json.dumps(summaries, indent=2, default=str))
    if errors:
        print(f"\n{len(errors)} paper runner errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry_run))
