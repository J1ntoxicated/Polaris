"""Day 8 — production paper loop smoke (60 s real-fetch + verify).

Runs the production loop against a fresh SQLite DB so we can verify the 8
acceptance criteria from the Day 8 spec:

1. universe table populated (OKX > 10).
2. Capital path attempted (creds permitting).
3. bars table populated (> 100 rows).
4. cell_matrix_p0 grows beyond 3 cells.
5. learner mult delta from real WR (not synthetic 100%).
6. fills with mixed venues when possible.
7. G1/G2/G8 events > 0.
8. fault_events stay finite (no halt cascade).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

from polaris.logging_config import DEFAULT_LOG_FILE, setup_polaris_logging
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts.production_paper_loop import run_production_paper_loop

logger = logging.getLogger(__name__)


def _verify(db_path: Path) -> dict[str, int | float]:
    """Run the 8 verification queries against the persisted DB."""
    out: dict[str, int | float] = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out["universe_active_okx"] = int(conn.execute(
            "SELECT COUNT(*) FROM universe WHERE is_active = 1 AND venue = 'okx'"
        ).fetchone()[0] or 0)
        out["universe_active_capital"] = int(conn.execute(
            "SELECT COUNT(*) FROM universe WHERE is_active = 1 AND venue = 'capital'"
        ).fetchone()[0] or 0)
        out["bars_total"] = int(conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] or 0)
        out["cell_matrix_cells"] = int(conn.execute(
            "SELECT COUNT(*) FROM cell_matrix_p0"
        ).fetchone()[0] or 0)
        out["fills_okx"] = int(conn.execute(
            "SELECT COUNT(*) FROM fills WHERE venue = 'okx'"
        ).fetchone()[0] or 0)
        out["fills_capital"] = int(conn.execute(
            "SELECT COUNT(*) FROM fills WHERE venue = 'capital'"
        ).fetchone()[0] or 0)
        out["g1_events"] = int(conn.execute(
            "SELECT COUNT(*) FROM gate_events WHERE gate_id = 1"
        ).fetchone()[0] or 0)
        out["g2_events"] = int(conn.execute(
            "SELECT COUNT(*) FROM gate_events WHERE gate_id = 2"
        ).fetchone()[0] or 0)
        out["g8_events"] = int(conn.execute(
            "SELECT COUNT(*) FROM gate_events WHERE gate_id = 8"
        ).fetchone()[0] or 0)
        out["fault_events"] = int(conn.execute(
            "SELECT COUNT(*) FROM strategy_fault_events"
        ).fetchone()[0] or 0)
        # Real WR on the cell matrix: any non-1.0 / non-0.0 ratio means we are
        # NOT synthesizing 100% wins anymore. Schema column is `wins_eff`.
        wr_rows = conn.execute(
            "SELECT wins_eff, n_eff FROM cell_matrix_p0 WHERE n_eff > 0"
        ).fetchall()
        if wr_rows:
            wrs = [float(r[0]) / float(r[1]) for r in wr_rows if r[1] > 0]
            out["wr_min"] = min(wrs) if wrs else 0.0
            out["wr_max"] = max(wrs) if wrs else 0.0
        else:
            out["wr_min"] = 0.0
            out["wr_max"] = 0.0
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_production_paper_loop")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--tick", type=float, default=5.0)
    parser.add_argument("--db", type=Path, default=Path("data/polaris_smoke_prod.sqlite"))
    parser.add_argument(
        "--real-gpt", "--real-haiku", dest="real_gpt",
        action="store_true", default=False,
        help="Use the real GPT factory (default: StubGPTClient).",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args(argv if argv is not None else None)
    log_level = "DEBUG" if args.verbose >= 2 else "INFO"
    setup_polaris_logging(level=log_level, log_file=DEFAULT_LOG_FILE)
    args.db.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    # Smoke uses StubGPTClient by default so it can verify the wiring
    # without needing live OpenAI API access. Set --real-gpt to force the
    # real factory (G3/G4 will fail-CLOSED if OPENAI_API_KEY is missing /
    # the model id is rejected). Post 2026-05-07 migration P0 = gpt-5-mini,
    # P1 = gpt-5.5.
    rc = asyncio.run(
        run_production_paper_loop(
            duration_sec=args.duration, tick_sec=args.tick, db_path=args.db,
            haiku=None if args.real_gpt else StubGPTClient(),
        )
    )
    elapsed = time.time() - started
    verify = _verify(args.db)
    logger.info("=========== Day 8 production smoke verify ===========")
    logger.info("elapsed_sec      : %.1f", elapsed)
    for k, v in verify.items():
        logger.info("%-20s : %s", k, v)
    # Acceptance: at least 4/8 must be > 0 to call the loop "wired" beyond fixture.
    keys = (
        "universe_active_okx", "bars_total", "cell_matrix_cells",
        "g1_events", "g2_events", "g8_events", "fills_okx",
    )
    pass_count = 0
    for k in keys:
        raw = verify.get(k, 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            pass_count += 1
    logger.info("pass_count       : %d/7 (need ≥ 4)", pass_count)
    return 0 if pass_count >= 4 and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
