"""Polaris dashboard v0 — terminal sample row (220×55, ANSI color, 5 s refresh).

Renders six panels:
- Universe (Layer 0) — focus count + last refresh
- Active positions — per-venue table
- Cell routing distribution — top×1.5 / mid×1.0 / bottom×0.5 counts
- Learner state snapshot — 3 P0 learners (mult * sample size)
- Recent fills — last 10 rows. Header depends on the source table:
    * ``fills`` (ADR-003 unified schema)  → venue / symbol / side / size_usd / fee_usd / slip_bps / src
    * ``orders`` (P0 fallback when ``fills`` empty) → venue / symbol / side / qty_base / status / src
- Daily PnL — running USD aggregate

P0 = read-only DB snapshot; the loop is deliberately stateless so the dashboard
does not race the paper loop. P1 = WebSocket push channel.

Run: ``python3 -m polaris.scripts.dashboard_v0`` (Ctrl-C to exit).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from polaris.core.pipeline.payload_builder import CELL_POOL_MIN_N_EFF

DB_PATH: Final[Path] = Path("data/polaris.sqlite")
TARGET_WIDTH: Final[int] = 220
DEFAULT_REFRESH_SEC: Final[float] = 5.0

# Polaris ANSI palette (reduced 8-color baseline so it survives any terminal).
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
FG_GREEN = "\x1b[32m"
FG_RED = "\x1b[31m"
FG_YELLOW = "\x1b[33m"
FG_CYAN = "\x1b[36m"
FG_MAGENTA = "\x1b[35m"
FG_WHITE = "\x1b[97m"
CLEAR = "\x1b[2J\x1b[H"


@dataclass(slots=True)
class DashboardSnapshot:
    universe_focus_count: int
    universe_last_refresh: str
    positions: list[dict[str, Any]]
    cell_dist: dict[str, int]
    learner_state: list[dict[str, Any]]
    recent_fills: list[dict[str, Any]]
    daily_pnl_usd: float


# ---------------------------------------------------------------------------
# DB readers (best-effort — table missing → empty)
# ---------------------------------------------------------------------------


def _safe_query(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    try:
        cur = conn.execute(sql, params)
        return list(cur.fetchall())
    except sqlite3.Error:
        return []


def _read_universe(conn: sqlite3.Connection) -> tuple[int, str]:
    rows = _safe_query(
        conn,
        "SELECT COUNT(*), MAX(last_seen_ts) FROM watchlist_focus",
    )
    if rows and rows[0][0]:
        cnt, last_ts = rows[0]
        ts_str = (
            time.strftime("%H:%M:%S", time.localtime(last_ts)) if last_ts else "n/a"
        )
        return int(cnt), ts_str
    # Fallback to universe table.
    rows = _safe_query(conn, "SELECT COUNT(*) FROM universe")
    cnt = int(rows[0][0]) if rows else 0
    return cnt, "n/a"


def _read_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _safe_query(
        conn,
        """SELECT venue, symbol, strategy_id, side, qty, status, opened_ts
           FROM positions
           WHERE status NOT IN ('closed','cancelled')
           ORDER BY opened_ts DESC
           LIMIT 12""",
    )
    return [
        {
            "venue": r[0],
            "symbol": r[1],
            "strategy_id": r[2],
            "side": r[3],
            "qty": r[4],
            "status": r[5],
            "opened_ts": r[6],
        }
        for r in rows
    ]


def _read_cell_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    """Count cells per quartile band based on ``score`` percentile in
    ``cell_matrix_p0``. P0 = simple bucket split."""
    rows = _safe_query(
        conn,
        "SELECT score FROM cell_matrix_p0 WHERE n_eff >= ? ORDER BY score DESC",
        (CELL_POOL_MIN_N_EFF,),
    )
    if not rows:
        return {"top": 0, "mid": 0, "bottom": 0}
    n = len(rows)
    top = max(1, n // 4)
    bottom = max(1, n // 4)
    return {"top": top, "mid": n - top - bottom, "bottom": bottom}


def _read_learner_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _safe_query(
        conn,
        """SELECT learner_id, key_dims, mult, n_eff
           FROM learner_state
           ORDER BY updated_ts DESC
           LIMIT 6""",
    )
    return [
        {"learner": r[0], "key": r[1], "mult": r[2], "n": r[3]} for r in rows
    ]


def _read_recent_fills(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read the last 10 unified ``Fill`` rows.

    Prefers the ADR-003 ``fills`` table when it exists (Day 6 deliverable).
    Falls back to ``orders`` so the panel still renders during P0 smoke,
    surfacing the unified Fill columns (``size_usd``, ``fee_usd``,
    ``slippage_bps``) wherever possible.
    """
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, size_usd, fee_usd,
                  slippage_bps, ts_ms, fill_price, pnl_usd, is_close
           FROM fills
           ORDER BY ts_ms DESC
           LIMIT 10""",
    )
    if rows:
        out: list[dict[str, Any]] = []
        for r in rows:
            inst = r[1] if isinstance(r[1], str) else ""
            symbol = inst.split(":", 1)[-1] if ":" in inst else inst
            out.append(
                {
                    "venue": r[0],
                    "symbol": symbol,
                    "strategy_id": r[2],
                    "side": r[3],
                    "size_usd": float(r[4] or 0.0),
                    "fee_usd": float(r[5] or 0.0),
                    "slippage_bps": float(r[6] or 0.0),
                    "ts": int(r[7] or 0) // 1000,
                    "fill_price": float(r[8] or 0.0),
                    "pnl_usd": float(r[9] or 0.0),
                    "is_close": bool(r[10]),
                    "source": "fills",
                    "status": "close" if bool(r[10]) else "open",
                }
            )
        return out
    # Fallback to orders (status / order_key only — the orders DDL has no
    # qty / side columns; codex Day 6 P1 fix). Until a Fill row lands the
    # panel can only show pending order metadata.
    rows = _safe_query(
        conn,
        """SELECT venue, symbol, strategy_id, status, updated_ts, order_key
           FROM orders
           ORDER BY updated_ts DESC
           LIMIT 10""",
    )
    return [
        {
            "venue": r[0],
            "symbol": r[1],
            "strategy_id": r[2],
            "side": "?",
            "size_usd": 0.0,
            "qty_base": 0.0,
            "fee_usd": 0.0,
            "slippage_bps": 0.0,
            "fill_price": 0.0,
            "pnl_usd": 0.0,
            "is_close": False,
            "ts": r[4],
            "source": "orders",
            "status": r[3],
            "order_key": r[5],
        }
        for r in rows
    ]


def _read_daily_pnl(conn: sqlite3.Connection) -> float:
    """Best-effort PnL: sum of ``pnl_r_sum_eff`` across active cells × 1.0 (USD per R unit)."""
    rows = _safe_query(conn, "SELECT SUM(pnl_r_sum_eff) FROM cell_matrix_p0")
    if not rows or rows[0][0] is None:
        return 0.0
    return float(rows[0][0])


def collect_snapshot(db_path: Path) -> DashboardSnapshot:
    if not db_path.exists():
        return DashboardSnapshot(
            universe_focus_count=0,
            universe_last_refresh="n/a",
            positions=[],
            cell_dist={"top": 0, "mid": 0, "bottom": 0},
            learner_state=[],
            recent_fills=[],
            daily_pnl_usd=0.0,
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cnt, ts = _read_universe(conn)
        return DashboardSnapshot(
            universe_focus_count=cnt,
            universe_last_refresh=ts,
            positions=_read_positions(conn),
            cell_dist=_read_cell_distribution(conn),
            learner_state=_read_learner_state(conn),
            recent_fills=_read_recent_fills(conn),
            daily_pnl_usd=_read_daily_pnl(conn),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _box_title(title: str, width: int) -> str:
    pad = max(0, width - len(title) - 4)
    return f"{BOLD}{FG_CYAN}┌─ {title} {'─' * pad}┐{RESET}"


def _box_line(text: str, width: int) -> str:
    inner = width - 2
    truncated = text[:inner].ljust(inner)
    return f"{FG_CYAN}│{RESET}{truncated}{FG_CYAN}│{RESET}"


def _box_bottom(width: int) -> str:
    return f"{FG_CYAN}└{'─' * (width - 2)}┘{RESET}"


def _render_universe(snap: DashboardSnapshot, width: int) -> list[str]:
    lines = [
        _box_title("Universe (Layer 0)", width),
        _box_line(
            f"  focus_count = {FG_YELLOW}{snap.universe_focus_count:>3}{RESET}   "
            f"last_refresh = {FG_YELLOW}{snap.universe_last_refresh}{RESET}",
            width,
        ),
        _box_bottom(width),
    ]
    return lines


def _render_positions(snap: DashboardSnapshot, width: int) -> list[str]:
    lines = [_box_title("Active positions (per-venue)", width)]
    if not snap.positions:
        lines.append(_box_line(f"  {DIM}(no open positions){RESET}", width))
    else:
        header = f"  {'VENUE':<8}{'SYMBOL':<14}{'STRAT':<22}{'SIDE':<6}{'QTY':<10}{'STATUS':<10}"
        lines.append(_box_line(f"{BOLD}{header}{RESET}", width))
        for p in snap.positions:
            lines.append(
                _box_line(
                    f"  {p['venue']:<8}{p['symbol']:<14}{p['strategy_id'][:20]:<22}"
                    f"{p['side']:<6}{p['qty']:<10.4f}{p['status']:<10}",
                    width,
                )
            )
    lines.append(_box_bottom(width))
    return lines


def _render_cell_dist(snap: DashboardSnapshot, width: int) -> list[str]:
    cd = snap.cell_dist
    bar_top = FG_GREEN + "█" * cd["top"] + RESET
    bar_mid = FG_YELLOW + "█" * cd["mid"] + RESET
    bar_bot = FG_RED + "█" * cd["bottom"] + RESET
    lines = [
        _box_title("Cell routing distribution", width),
        _box_line(f"  top×1.5    {cd['top']:>3} {bar_top[: width - 30]}", width),
        _box_line(f"  mid×1.0    {cd['mid']:>3} {bar_mid[: width - 30]}", width),
        _box_line(f"  bottom×0.5 {cd['bottom']:>3} {bar_bot[: width - 30]}", width),
        _box_bottom(width),
    ]
    return lines


def _render_learners(snap: DashboardSnapshot, width: int) -> list[str]:
    lines = [_box_title("Learner state (Layer 5 P0)", width)]
    if not snap.learner_state:
        lines.append(_box_line(f"  {DIM}(no learner state yet){RESET}", width))
    else:
        for ls in snap.learner_state:
            mult = ls["mult"] or 1.0
            color = FG_GREEN if mult > 1.0 else (FG_RED if mult < 1.0 else FG_WHITE)
            lines.append(
                _box_line(
                    f"  {ls['learner']:<14} {ls['key'][:40]:<42} mult={color}{mult:>5.3f}{RESET} "
                    f"n={ls['n']:>4}",
                    width,
                )
            )
    lines.append(_box_bottom(width))
    return lines


def _render_recent_fills(snap: DashboardSnapshot, width: int) -> list[str]:
    lines = [_box_title("Recent fills (last 10)", width)]
    if not snap.recent_fills:
        lines.append(_box_line(f"  {DIM}(no fills yet){RESET}", width))
    else:
        source = snap.recent_fills[0].get("source", "fills")
        # Header columns differ between sources because ``orders`` lacks
        # USD notional, fee and slippage. We expose ``QTY_BASE`` instead so
        # the dashboard never misrepresents base ccy / contracts as USD.
        if source == "fills":
            header = (
                f"  {'VENUE':<8}{'SYMBOL':<14}{'STRAT':<22}{'SIDE':<6}"
                f"{'SIZE_USD':<12}{'FEE_USD':<10}{'SLIP_BPS':<10}{'SRC':<8}"
            )
        else:
            header = (
                f"  {'VENUE':<8}{'SYMBOL':<14}{'STRAT':<22}{'SIDE':<6}"
                f"{'QTY_BASE':<12}{'STATUS':<10}{'SRC':<8}"
            )
        lines.append(_box_line(f"{BOLD}{header}{RESET}", width))
        for f in snap.recent_fills:
            if source == "fills":
                row = (
                    f"  {f['venue']:<8}{f['symbol']:<14}{f['strategy_id'][:20]:<22}"
                    f"{f['side']:<6}{f['size_usd']:<12.4f}{f['fee_usd']:<10.4f}"
                    f"{f['slippage_bps']:<10.2f}{source:<8}"
                )
            else:
                row = (
                    f"  {f['venue']:<8}{f['symbol']:<14}{f['strategy_id'][:20]:<22}"
                    f"{f['side']:<6}{f['qty_base']:<12.4f}"
                    f"{str(f.get('status', '-'))[:8]:<10}{source:<8}"
                )
            lines.append(_box_line(row, width))
    lines.append(_box_bottom(width))
    return lines


def _render_pnl(snap: DashboardSnapshot, width: int) -> list[str]:
    color = FG_GREEN if snap.daily_pnl_usd >= 0 else FG_RED
    return [
        _box_title("Daily PnL (cell-matrix R-sum, P0 proxy)", width),
        _box_line(
            f"  total_pnl_r = {color}{snap.daily_pnl_usd:>+10.4f}{RESET} R-units",
            width,
        ),
        _box_bottom(width),
    ]


def render(
    snap: DashboardSnapshot,
    *,
    width: int = TARGET_WIDTH,
    refresh_sec: float = DEFAULT_REFRESH_SEC,
) -> str:
    blocks: list[list[str]] = [
        [
            f"{BOLD}{FG_MAGENTA}POLARIS DASHBOARD v0{RESET}   "
            f"{DIM}{time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}   "
            f"{FG_CYAN}refresh = {refresh_sec:g} s{RESET}"
        ],
        _render_universe(snap, width),
        _render_positions(snap, width),
        _render_cell_dist(snap, width),
        _render_learners(snap, width),
        _render_recent_fills(snap, width),
        _render_pnl(snap, width),
    ]
    return "\n".join("\n".join(block) for block in blocks)


# ---------------------------------------------------------------------------
# CLI loop
# ---------------------------------------------------------------------------


def run_loop(
    *,
    db_path: Path = DB_PATH,
    refresh_sec: float = DEFAULT_REFRESH_SEC,
    once: bool = False,
    width: int = TARGET_WIDTH,
) -> int:
    try:
        while True:
            snap = collect_snapshot(db_path)
            sys.stdout.write(
                CLEAR + render(snap, width=width, refresh_sec=refresh_sec) + "\n"
            )
            sys.stdout.flush()
            if once:
                return 0
            time.sleep(refresh_sec)
    except KeyboardInterrupt:
        sys.stdout.write(f"\n{DIM}dashboard exit on Ctrl-C{RESET}\n")
        return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard_v0")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--refresh", type=float, default=DEFAULT_REFRESH_SEC)
    parser.add_argument("--once", action="store_true", default=False)
    parser.add_argument("--width", type=int, default=TARGET_WIDTH)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run_loop(
        db_path=args.db,
        refresh_sec=args.refresh,
        once=args.once,
        width=args.width,
    )


if __name__ == "__main__":
    sys.exit(main())
