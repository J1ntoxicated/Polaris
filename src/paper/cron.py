"""Paper cron — daily multi-HYPO multi-ticker runner (shell P6).

Daily cycle: PROMOTED (Promotion Gate passed) HYPOTHESIS 실행.
ADR-010: BACKTEST → PAPER → Promotion Gate → cron.

This module = production-promoted hypos ONLY.
Paper-stage hypos → src/paper/daily_paper_runner.py.

Usage:
    python -m src.paper.cron                       # all active hypos
    python -m src.paper.cron --dry-run             # 결과만 print, state 저장 X (TODO)

Cron setup (사용자 manual):
    0 1 * * * cd /Users/jinyoon/Projects/Polaris && .venv/bin/python -m src.paper.cron >> data/paper/cron.log 2>&1

또는 launchd (macOS):
    ~/Library/LaunchAgents/com.polaris.paper.daily.plist
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
import traceback

from src.paper.runner import run_cycle
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.sma_crossover import SMACrossover

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ─────── Active HYPOTHESES (Promotion Gate PASSED) ───────
# ADR-010 lifecycle: BACKTEST → PAPER → Promotion Gate → HERE (cron).
# Only hypotheses that completed paper validation enter this list.
# Paper-stage hypotheses → src/paper/daily_paper_runner.py (DAILY_PAPER_HYPOS).
#
# Round 15 (Jin 판단 2026-05-04): 1d trend portfolio 강화.
#   - HYPO-003-SMA50-200: 8 ticker (BTC/ETH/SOL/DOGE/ADA/XRP/ORDI/SUI)
#   - HYPO-004-DONCH-40-15: Donchian 40/15 (BTC+ETH — conservative long-period)
#   - HYPO-004-DONCH-20-10: Donchian 20/10 (BTC+ETH+SOL — medium-period swing)
# Codex Round 15 ADR-010 fix (2026-05-04): HYPO-020 removed from here.
#   HYPO-020 paper stage not completed. Moved to daily_paper_runner.py.
#   See src/paper/daily_paper_runner.py DAILY_PAPER_HYPOS for HYPO-020 status.
# 새 HYPO 추가 시 이 list 갱신 (또는 vault scanner 자동화 추후).
ACTIVE_HYPOS = [
    # HYPO-003 SMA crossover 1D — 8 ticker basket (Round 15 확대)
    # backtest validated: SMA 50/200 1D SPOT long-only, fee 0.0014, Sharpe viable
    {
        "hypo_id": "HYPO-003-SMA50-200",
        "strategy": SMACrossover,
        "strategy_params": {"fast": 50, "slow": 200},
        "tickers": [
            "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT",
            "ADA-USDT", "XRP-USDT", "ORDI-USDT", "SUI-USDT",
        ],
        "bar": "1D",
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        "max_position_pct": 0.02,
    },
    # HYPO-004 Donchian breakout variant 1 — conservative long-period (Round 15)
    # entry=40 (2-month high), exit=15 (3-week low) — BTC+ETH only (high liquidity)
    {
        "hypo_id": "HYPO-004-DONCH-40-15",
        "strategy": DonchianBreakout,
        "strategy_params": {"entry_period": 40, "exit_period": 15},
        "tickers": ["BTC-USDT", "ETH-USDT"],
        "bar": "1D",
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        "max_position_pct": 0.02,
    },
    # HYPO-004 Donchian breakout variant 2 — medium-period swing (Round 15)
    # entry=20 (1-month high), exit=10 (2-week low) — BTC+ETH+SOL (medium basket)
    {
        "hypo_id": "HYPO-004-DONCH-20-10",
        "strategy": DonchianBreakout,
        "strategy_params": {"entry_period": 20, "exit_period": 10},
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "bar": "1D",
        "starting_usd": 50000.0,  # Phase 2Q: 10x capital
        "max_position_pct": 0.02,
    },
    # HYPO-020 NOTE: moved to daily_paper_runner.py DAILY_PAPER_HYPOS (ADR-010 fix).
    # Paper validation in progress since 2026-05-04. Promotion Gate pending.
]


def main() -> int:
    """Run all active hypos × tickers. Returns exit code (0 = all OK)."""
    today = _dt.date.today().isoformat()
    logger.info(f"=== Polaris Paper Cron — {today} ===")
    errors = []
    summaries = []
    for hypo in ACTIVE_HYPOS:
        strategy_cls = hypo["strategy"]
        strategy_params = hypo["strategy_params"]
        for ticker in hypo["tickers"]:
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
                summaries.append(summary)
                logger.info(
                    f"{hypo['hypo_id']} {ticker} {hypo['bar']}: "
                    f"signal={summary.get('signal')} "
                    f"open={summary.get('n_open_post')} closed={summary.get('n_closed')} "
                    f"equity=${summary.get('equity_usd', 0):.2f} "
                    f"pnl=${summary.get('realized_pnl_usd', 0):+.2f}"
                )
            except Exception as e:
                err = f"{hypo['hypo_id']} {ticker}: {type(e).__name__}: {e}"
                errors.append(err)
                logger.error(err)
                traceback.print_exc()

    print()
    print(f"=== Summary {today} ===")
    print(json.dumps(summaries, indent=2, default=str))
    if errors:
        print(f"\n⚠️ {len(errors)} errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
