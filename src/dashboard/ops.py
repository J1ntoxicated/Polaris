"""Polaris Ops Dashboard — terminal real-time view (Phase 25).

Layout (single window, terminal width adaptive):
  HEADER         4 rows  (title + broker + portfolio + daily target + velocity)
  POSITION GROUPS 14 rows (per-ticker contributions, HOT/WARM/COLD/LOSING state)
  PM ACTIVITY     9 rows  (cycle counts + top opportunities)
  REALTIME HYPOs 12 rows  (per-strategy KPI from SQL ledger)
  LIVE LOG       remainder (ALL events, colored, latest at bottom)
  FOOTER          1 row   (uptime + connection + tick)

Polaris terminal aesthetic — JetBrains-Mono, ANSI 256-color pastels,
unicode box drawing, ★ glyphs. ZERO emoji / SVG / gradients.

User mandate: "ALL logs output 원칙" — only filter clearly internal noise.

Run: python3 -m src.dashboard.ops
"""
from __future__ import annotations

import os
import sys
import time

from src.dashboard.ansi import (
    P_GRN, P_RED, P_DIM, POLARIS_BLUE, c, pad, hline,
)
from src.dashboard.sections.header import render as render_header
from src.dashboard.sections.positions_dense import render as render_positions
from src.dashboard.sections.pm_activity import render as render_pm_activity
from src.dashboard.sections.realtime_hypos import render as render_realtime_hypos
from src.dashboard.sections.live_log import render as render_live_log


REFRESH_INTERVAL_S = 1.0


def _get_W() -> int:
    try:
        w, _ = os.get_terminal_size()
        return max(140, w)
    except Exception:
        return 200


def _get_H() -> int:
    try:
        _, h = os.get_terminal_size()
        return max(40, h - 1)
    except Exception:
        return 60


def _render_footer(W: int, tick: int) -> str:
    """Footer — single row. Uptime, refresh tick, hint."""
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]
    body = (
        f"  {c(spinner, POLARIS_BLUE)} "
        f"{c('Polaris Ops', P_DIM)} {c('★', POLARIS_BLUE)} "
        f"{c('refresh 1s', P_DIM)} {c('·', P_DIM)} "
        f"{c('Ctrl-C exit', P_DIM)} {c('·', P_DIM)} "
        f"{c(f'tick {tick}', P_DIM)}"
    )
    return pad(body, W)


def render_full(tick: int = 0) -> str:
    """Layout — log compressed to 3 rows (heartbeat only).
    Bulk = positions / PM / realtime HYPOs.
    """
    W = _get_W()
    H = _get_H()

    HEADER_H = 4
    LOG_H = 4   # 1 hline + 3 rows
    FOOTER_H = 1

    # Positions absorbs available space (bulk content)
    fixed = HEADER_H + LOG_H + FOOTER_H + 9 + 12  # PM + Realtime
    pos_h = max(8, H - fixed)

    out: list[str] = []
    out.extend(render_header(W, tick))                       # 4
    out.extend(render_positions(W, n=pos_h))                  # var (bulk)
    out.extend(render_pm_activity(W, n=9))                    # 9
    out.extend(render_realtime_hypos(W, n=12))                # 12
    out.extend(render_live_log(W, n=LOG_H))                   # 4 (1 hline + 3 rows)
    out.append(_render_footer(W, tick))                       # 1
    return "\n".join(out)


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _watched_files() -> dict[str, float]:
    """All files dashboard depends on — re-exec when any changes."""
    import glob
    paths = (
        glob.glob(os.path.join(_PROJECT_ROOT, "src/dashboard/*.py"))
        + glob.glob(os.path.join(_PROJECT_ROOT, "src/dashboard/sections/*.py"))
        + glob.glob(os.path.join(_PROJECT_ROOT, "src/risk/*.py"))
    )
    out = {}
    for p in paths:
        try:
            out[p] = os.path.getmtime(p)
        except OSError:
            pass
    return out


def main() -> None:
    """Continuous refresh loop with auto-reload on code change.

    Ctrl-C exits cleanly. Edit any dashboard / risk module → next tick
    re-execs self with new code (no manual restart needed).
    """
    initial_mtimes = _watched_files()
    last_reload_check = 0
    tick = 0
    # Hide cursor
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            # Auto-reload check every 2 ticks (~2s) — cheap mtime stat
            now = time.time()
            if now - last_reload_check > 2.0:
                current = _watched_files()
                changed = [
                    f for f in current
                    if current[f] > initial_mtimes.get(f, 0)
                ]
                if changed:
                    sys.stdout.write("\033[H\033[2J")
                    sys.stdout.write(
                        f"\n  ★ RELOAD — code changed: {os.path.basename(changed[0])}\n\n"
                    )
                    sys.stdout.write("\033[?25h")  # show cursor
                    sys.stdout.flush()
                    time.sleep(0.3)
                    os.execv(
                        sys.executable,
                        [sys.executable, "-m", "src.dashboard.ops"],
                    )
                last_reload_check = now

            # Move cursor home + clear screen
            sys.stdout.write("\033[H\033[2J")
            sys.stdout.write(render_full(tick))
            sys.stdout.write("\n")
            sys.stdout.flush()
            tick += 1
            time.sleep(REFRESH_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        # Restore cursor
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
