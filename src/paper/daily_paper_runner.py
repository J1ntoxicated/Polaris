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

from src.paper.runner import run_cycle
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.donchian_breakout import DonchianBreakout
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
]


def main(dry_run: bool = False) -> int:
    """Run all paper-stage hypos x tickers. Returns exit code (0 = all OK)."""
    today = _dt.date.today().isoformat()
    logger.info(f"=== Polaris Daily Paper Runner (ADR-010 paper stage) — {today} ===")
    if dry_run:
        logger.info("DRY-RUN mode: results printed, state NOT saved")

    errors = []
    summaries = []
    for hypo in DAILY_PAPER_HYPOS:
        strategy_cls = hypo["strategy"]
        strategy_params = hypo["strategy_params"]
        paper_since = hypo.get("paper_since", "unknown")
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
