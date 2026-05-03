"""Paper cron — daily multi-HYPO multi-ticker runner (shell P6).

Daily cycle: 모든 active HYPOTHESIS의 모든 ticker 1 cycle 실행.

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
from src.strategies.sma_crossover import SMACrossover

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ─────── Active HYPOTHESES ───────
# Polaris 운영 가설 — vault/60_alpha/active/ 정합.
# 새 HYPO 추가 시 이 list 갱신 (또는 vault scanner 자동화 추후).
ACTIVE_HYPOS = [
    {
        "hypo_id": "HYPO-003",
        "strategy": SMACrossover,
        "strategy_params": {"fast": 50, "slow": 200},
        "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "bar": "1D",
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    },
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
