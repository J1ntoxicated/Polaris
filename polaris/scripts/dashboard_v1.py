"""Polaris dashboard v1 — professional trading terminal (220×55, 10 panels).

Replaces v0 single-row dump with the trader-grade layout demanded by Jin
2026-05-07: equity / cash / DD / Sharpe top-bar, 24h equity sparkline, live
positions with uPnL/Δ%/last/HELD/cell mult, per-strategy WR/PF/PnL$/notional,
gate funnel pass-rate, cell matrix top/bottom by score, regime heatmap,
trade-level recent-closed (with paired open→close PnL), 3-learner state with
1h delta arrow, GPT calls/h + projected $/h, and alert log tail.

Read-only against ``data/polaris.sqlite``. Pure render functions live in
``polaris.scripts.dashboard.render``; DB queries live in
``polaris.scripts.dashboard.snapshot``.

Run:  ``python3 -m polaris.scripts.dashboard_v1`` (Ctrl-C to exit)
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from polaris.scripts.dashboard.ansi_palette import (
    DIM,
    HIDE_CURSOR,
    HOME,
    NEUTRAL,
    RESET,
    SHOW_CURSOR,
    color,
)
from polaris.scripts.dashboard.render import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    render_dashboard,
)
from polaris.scripts.dashboard.snapshot import (
    DEFAULT_DB_PATH,
    DashboardSnapshot,
    collect_snapshot,
)

DEFAULT_REFRESH_SEC: Final[float] = 5.0


def _emit_frame(rows: list[str]) -> None:
    """Write one full 220×55 frame, cursor-home (no flicker)."""
    sys.stdout.write(HOME + "\n".join(rows) + "\n")
    sys.stdout.flush()


def run_loop(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    refresh_sec: float = DEFAULT_REFRESH_SEC,
    once: bool = False,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
) -> int:
    """Main render loop (5s default). Returns 0 on Ctrl-C."""
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()
    try:
        while True:
            t0 = time.time()
            snap = collect_snapshot(db_path)
            rows = render_dashboard(snap, width=width, height=height)
            _emit_frame(rows)
            elapsed = time.time() - t0
            if once:
                return 0
            sleep_for = max(0.0, refresh_sec - elapsed)
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.write(f"\n{color('dashboard exit on Ctrl-C', NEUTRAL + DIM)}\n")
        sys.stdout.flush()
        return 0
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET)
        sys.stdout.flush()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard_v1")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--refresh", type=float, default=DEFAULT_REFRESH_SEC)
    parser.add_argument("--once", action="store_true", default=False)
    parser.add_argument("--width", type=int, default=TARGET_WIDTH)
    parser.add_argument("--height", type=int, default=TARGET_HEIGHT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_loop(
        db_path=args.db,
        refresh_sec=args.refresh,
        once=args.once,
        width=args.width,
        height=args.height,
    )


# Backwards re-exports for tests / external callers
__all__ = [
    "DashboardSnapshot",
    "collect_snapshot",
    "main",
    "render_dashboard",
    "run_loop",
]


if __name__ == "__main__":
    sys.exit(main())
