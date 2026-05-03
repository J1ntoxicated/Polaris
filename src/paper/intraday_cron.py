"""Polaris intraday cron — 15min cycle, universe top 30, low fee assumption.

매 15분 실행:
- HYPO-007 (RSI 15m intraday) × universe top 30 ticker
- HYPO-008 (Volume burst 1h) × universe top 20 ticker (lower for resource)
- Live OKX LV3 fee 가정: round-trip 0.0014 (taker 0.07% × 2)

Setup:
    cp scripts/com.polaris.paper.intraday.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.polaris.paper.intraday.plist
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
import traceback

from src.data.okx_universe import fetch_top_volume_tickers
from src.paper.runner import run_cycle
from src.strategies.rsi_15m_intraday import RSI15mIntraday
from src.strategies.volume_burst import VolumeBurst

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Live fee 가정 (OKX LV3+ taker 0.07% × 2)
LIVE_FEE_ROUND_TRIP = 0.0014

INTRADAY_HYPOS = [
    {
        "hypo_id": "HYPO-007",
        "strategy_cls": RSI15mIntraday,
        "strategy_params": {},
        "bar": "15m",
        "n_universe": 30,
        "starting_usd": 5000.0,
        "max_position_pct": 0.04,  # 4% per trade
    },
    {
        "hypo_id": "HYPO-008",
        "strategy_cls": VolumeBurst,
        "strategy_params": {},
        "bar": "1H",
        "n_universe": 20,  # 1h fewer ticker (resource)
        "starting_usd": 5000.0,
        "max_position_pct": 0.05,
    },
]


def main() -> int:
    today = _dt.datetime.now().isoformat(timespec="seconds")
    logger.info(f"=== Polaris Intraday Cron — {today} ===")

    # Universe top 30 fetch (cached 15min)
    try:
        universe = fetch_top_volume_tickers(n=30)
        logger.info(f"Universe ({len(universe)}): {', '.join(universe[:10])}...")
    except Exception as e:
        logger.error(f"Universe fetch failed: {e}")
        return 1

    summaries = []
    errors = []
    trades_opened = 0

    for hypo in INTRADAY_HYPOS:
        n = hypo["n_universe"]
        tickers = universe[:n]
        for ticker in tickers:
            try:
                strategy = hypo["strategy_cls"](**hypo["strategy_params"])
                summary = run_cycle(
                    ticker=ticker, strategy=strategy, bar=hypo["bar"],
                    starting_usd=hypo["starting_usd"],
                    max_position_pct=hypo["max_position_pct"],
                    fee_round_trip=LIVE_FEE_ROUND_TRIP,  # ← 핵심: live fee
                )
                summary["hypo_id"] = hypo["hypo_id"]
                summaries.append(summary)
                # log only non-HOLD or position change
                if summary.get("signal") != "hold" or summary.get("n_open_post", 0) > 0:
                    logger.info(
                        f"{hypo['hypo_id']} {ticker} {hypo['bar']}: "
                        f"signal={summary.get('signal')} "
                        f"open={summary.get('n_open_post')} closed={summary.get('n_closed')} "
                        f"equity=${summary.get('equity_usd', 0):.2f}"
                    )
                if summary.get("n_open_post", 0) > summary.get("n_open_pre", 0):
                    trades_opened += 1
            except Exception as e:
                err = f"{hypo['hypo_id']} {ticker}: {type(e).__name__}: {e}"
                errors.append(err)
                logger.error(err)

    logger.info(f"=== Done. Trades opened this cycle: {trades_opened}, errors: {len(errors)} ===")

    if errors:
        for e in errors[:10]:
            print(f"  ERR: {e}")

    return 1 if errors and trades_opened == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
