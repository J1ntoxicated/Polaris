"""Polaris dashboard v1 — snapshot collection (DB → DashboardSnapshot).

Pure read-only query layer. Renderer (`render.py`) consumes this dataclass and
produces the 220×55 ANSI grid. All queries best-effort: missing tables / empty
data return zero-defaults so the dashboard never crashes a paper loop.

Sources (data/polaris.sqlite):
- ``fills`` (ADR-003) — realised PnL, fill stream, USD notional
- ``positions`` — open positions
- ``cell_matrix_p0`` — Layer 4 routing scores
- ``learner_state`` — Layer 5 P0 mults (key column is ``key``, NOT ``key_dims``)
- ``gate_events`` — Layer 2 funnel (last 1h)
- ``regime_state`` — per-group regime
- ``bars`` — last close per instrument
- ``risk_events`` / ``strategy_fault_events`` — alert log
- ``watchlist_focus`` / ``universe`` — focus count

Starting capital: pulled from ``polaris.core.sizing.constants`` (Day 9 F12
SSOT fix) — total demo equity ≈ USD $130K (OKX SPOT $79K + Capital CFD $51K).
Per-venue values are exposed on the snapshot for the renderer's split label.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from polaris.core.sizing.constants import (
    demo_starting_equity_capital,
    demo_starting_equity_okx,
    demo_starting_equity_total,
)

DEFAULT_DB_PATH: Final[Path] = Path("data/polaris.sqlite")


def _starting_capital() -> float:
    """Resolved live each call so env-overrides (POLARIS_DEMO_STARTING_EQUITY_*) win."""
    return demo_starting_equity_total()


# Module-level constant for backwards-compatible imports + tests; resolved once
# at import time and reflects the *current* env. Tests that patch env should
# call ``demo_starting_equity_total()`` directly instead.
STARTING_CAPITAL: Final[float] = demo_starting_equity_total()
DEFAULT_R_USD: Final[float] = 10.0          # 1 R = $10 (display heuristic)
EQUITY_BUCKET_SEC: Final[int] = 5 * 60       # 5-min bucket, 288 buckets/24h
EQUITY_LOOKBACK_SEC: Final[int] = 24 * 3600
GATE_FUNNEL_LOOKBACK_SEC: Final[int] = 3600
GPT_LOOKBACK_SEC: Final[int] = 3600
LEARNER_DELTA_LOOKBACK_SEC: Final[int] = 3600

# GPT pricing (USD per 1K tokens) — gpt-5-mini & gpt-5.5 ballpark for projection.
GPT_PRICE_PER_1K: Final[Mapping[str, float]] = {
    "gpt-5-mini": 0.000150,
    "gpt-5.5": 0.005,
    "gpt": 0.000150,           # default mini for unlabelled rows
}
GPT_TOKENS_PER_CALL: Final[int] = 1500       # heuristic — average prompt+completion


@dataclass(slots=True)
class PositionRow:
    venue: str
    symbol: str
    strategy_id: str
    side: str
    qty: float
    entry_price: float
    last_price: float
    delta_pct: float
    upnl_usd: float
    size_usd: float
    held_sec: float
    cell_mult: float
    # row_count > 1 = drift / scale-in indicator. The renderer surfaces this
    # as a "[×N]" badge so duplicate-logical-key rows do not silently fill
    # the active-positions slot the way they did pre-2026-05-10.
    row_count: int = 1


@dataclass(slots=True)
class StrategyStat:
    strategy_id: str
    open_n: int
    closed_n: int
    wr_pct: float
    avg_r: float
    pf: float
    pnl_usd: float
    notional_usd: float


@dataclass(slots=True)
class GateRow:
    gate_id: int
    label: str
    pass_n: int
    kill_n: int
    other_n: int
    total: int
    pass_rate: float


@dataclass(slots=True)
class CellRow:
    exchange: str
    strategy: str
    ticker: str
    regime: str
    n_eff: float
    score: float
    mult: float


@dataclass(slots=True)
class RegimeBar:
    regime: str
    count: int


@dataclass(slots=True)
class ClosedTrade:
    ts_close: int
    venue: str
    symbol: str
    strategy_id: str
    side_close: str
    entry_price: float
    exit_price: float
    pnl_usd: float
    r_units: float
    held_sec: float
    exit_reason: str


@dataclass(slots=True)
class LearnerSlot:
    learner_id: str
    key: str
    value: float
    delta_1h: float
    n_eff: float


@dataclass(slots=True)
class GptStat:
    model: str
    calls_per_h: int
    cost_per_h_usd: float
    cost_24h_proj_usd: float


@dataclass(slots=True)
class AlertRow:
    ts: int
    level: str
    module: str
    msg: str


@dataclass(slots=True)
class DashboardSnapshot:
    ts_now: int = 0
    starting_capital: float = STARTING_CAPITAL
    # Day 9 F12 fix — venue-split starting equity (OKX SPOT + Capital CFD).
    starting_capital_okx: float = 0.0
    starting_capital_capital: float = 0.0
    equity_now: float = STARTING_CAPITAL
    # Codex P0 fix: ``cash_now`` was mislabelled — it's actually deployed
    # notional across open positions, not free cash / margin headroom (we
    # have no venue balance probe in the snapshot path). We expose it as
    # ``exposed_usd`` and let the renderer label it ``EXPOSED$``.
    exposed_usd: float = 0.0
    upnl_total: float = 0.0
    daily_pnl_usd: float = 0.0
    daily_trades: int = 0
    drawdown_pct: float = 0.0
    peak_equity: float = STARTING_CAPITAL
    sharpe_24h: float = 0.0
    open_positions_n: int = 0
    active_cells_n: int = 0
    universe_focus_n: int = 0
    universe_last_refresh: str = "n/a"
    equity_curve: list[float] = field(default_factory=list)   # 24h, oldest→newest
    equity_curve_ts: list[int] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    strategy_stats: list[StrategyStat] = field(default_factory=list)
    gate_funnel: list[GateRow] = field(default_factory=list)
    cell_top: list[CellRow] = field(default_factory=list)
    cell_bottom: list[CellRow] = field(default_factory=list)
    regime_bars: list[RegimeBar] = field(default_factory=list)
    recent_trades: list[ClosedTrade] = field(default_factory=list)
    learners: list[LearnerSlot] = field(default_factory=list)
    gpt_stats: list[GptStat] = field(default_factory=list)
    alerts: list[AlertRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _safe_query(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _now_s() -> int:
    return int(time.time())


def _strategy_label(s: str | None) -> str:
    return (s or "?")[:18]


def _symbol_from_inst(inst: str | None) -> str:
    if not inst:
        return "?"
    return inst.split(":", 1)[-1] if ":" in inst else inst


# ---------------------------------------------------------------------------
# Section: equity / pnl / drawdown / sharpe
# ---------------------------------------------------------------------------


def _build_equity_curve(
    conn: sqlite3.Connection, *, now_s: int, starting_capital: float
) -> tuple[list[int], list[float], float]:
    """Build 24h equity curve buckets from realised fills.

    equity_t = starting_capital + sum(pnl_usd for closed fills with ts<=t)

    Returns (bucket_ts list, equity list, total_realised).
    """
    lookback_ms = (now_s - EQUITY_LOOKBACK_SEC) * 1000
    rows = _safe_query(
        conn,
        """SELECT ts_ms, pnl_usd FROM fills
           WHERE is_close = 1 AND ts_ms >= ?
           ORDER BY ts_ms ASC""",
        (lookback_ms,),
    )

    # Realised PnL outside the 24h window (carry-in starting point).
    pre_rows = _safe_query(
        conn,
        "SELECT COALESCE(SUM(pnl_usd), 0.0) FROM fills WHERE is_close = 1 AND ts_ms < ?",
        (lookback_ms,),
    )
    base = starting_capital + (float(pre_rows[0][0]) if pre_rows else 0.0)

    n_buckets = EQUITY_LOOKBACK_SEC // EQUITY_BUCKET_SEC
    bucket_ts = [
        now_s - EQUITY_LOOKBACK_SEC + (i + 1) * EQUITY_BUCKET_SEC
        for i in range(n_buckets)
    ]
    equity = [base] * n_buckets

    if not rows:
        total_realised = base - starting_capital
        return bucket_ts, equity, total_realised

    cum = base
    fill_iter = iter(rows)
    next_fill = next(fill_iter, None)
    for i, t in enumerate(bucket_ts):
        boundary_ms = t * 1000
        while next_fill is not None and int(next_fill[0]) <= boundary_ms:
            cum += float(next_fill[1] or 0.0)
            next_fill = next(fill_iter, None)
        equity[i] = cum

    total_realised = cum - starting_capital
    return bucket_ts, equity, total_realised


def _drawdown_and_sharpe(
    equity: list[float], *, starting_capital: float
) -> tuple[float, float, float]:
    """Compute (drawdown_pct, peak_equity, sharpe_24h) from equity series."""
    if not equity:
        return 0.0, starting_capital, 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    # Sharpe = mean(returns) / std(returns) * sqrt(N)
    if len(equity) < 2:
        return max_dd, peak, 0.0
    rets = []
    for i in range(1, len(equity)):
        prev = equity[i - 1] or 1e-9
        rets.append((equity[i] - prev) / prev)
    if len(rets) < 2:
        return max_dd, peak, 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = var**0.5 or 1e-9
    sharpe = (mean / std) * (len(rets) ** 0.5)
    return max_dd, peak, sharpe


def _daily_realised_pnl(
    conn: sqlite3.Connection, *, now_s: int
) -> tuple[float, int]:
    """Realised PnL & closed-trade count over last 24h."""
    lookback_ms = (now_s - EQUITY_LOOKBACK_SEC) * 1000
    rows = _safe_query(
        conn,
        """SELECT COALESCE(SUM(pnl_usd), 0.0), COUNT(*) FROM fills
           WHERE is_close = 1 AND ts_ms >= ?""",
        (lookback_ms,),
    )
    if not rows:
        return 0.0, 0
    return float(rows[0][0] or 0.0), int(rows[0][1] or 0)


# ---------------------------------------------------------------------------
# Section: positions
# ---------------------------------------------------------------------------


def _last_prices(conn: sqlite3.Connection) -> dict[str, float]:
    rows = _safe_query(
        conn,
        """SELECT instrument_id, close FROM bars
           WHERE (instrument_id, ts) IN
             (SELECT instrument_id, MAX(ts) FROM bars GROUP BY instrument_id)""",
    )
    return {str(r[0]): float(r[1] or 0.0) for r in rows}


def _entry_price_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str, str], float]:
    """Latest open-fill price per (venue, instrument_id, strategy_id)."""
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, fill_price FROM fills
           WHERE is_close = 0
           ORDER BY ts_ms DESC""",
    )
    out: dict[tuple[str, str, str], float] = {}
    for venue, inst, strat, px in rows:
        key = (str(venue), str(inst), str(strat))
        if key not in out:
            out[key] = float(px or 0.0)
    return out


def _cell_mult_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str, str, str], float]:
    """Compute per-cell mult from cell_matrix_p0 score quartile.

    top quartile (score percentile ≥ 75) → 1.5
    bottom quartile (≤ 25) → 0.5
    middle → 1.0
    Cells with n_eff < 20 → 1.0 (warmup, pool default).
    """
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, n_eff, score
           FROM cell_matrix_p0
           ORDER BY score DESC""",
    )
    if not rows:
        return {}
    eligible = [(r[0], r[1], r[2], r[3], float(r[5] or 0.0))
                for r in rows if float(r[4] or 0.0) >= 20.0]
    if not eligible:
        return {(str(r[0]), str(r[1]), str(r[2]), str(r[3])): 1.0 for r in rows}
    eligible.sort(key=lambda x: x[4], reverse=True)
    n = len(eligible)
    top_cut = max(1, n // 4)
    bot_cut = max(1, n // 4)
    out: dict[tuple[str, str, str, str], float] = {}
    for i, (ex, strat, tic, reg, _score) in enumerate(eligible):
        if i < top_cut:
            mult = 1.5
        elif i >= n - bot_cut:
            mult = 0.5
        else:
            mult = 1.0
        out[(str(ex), str(strat), str(tic), str(reg))] = mult
    # Default for ineligible
    for r in rows:
        key = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        if key not in out:
            out[key] = 1.0
    return out


def _read_positions(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    last_prices: dict[str, float],
    entry_lookup: dict[tuple[str, str, str], float],
    cell_mult: dict[tuple[str, str, str, str], float],
    regime_lookup: dict[tuple[str, str], str],
) -> list[PositionRow]:
    # Logical-key dedup (B-P0-1, 2026-05-10):
    #   GROUP BY (venue, symbol, strategy_id, side) so legacy duplicate rows
    #   from the lifecycle drift incident collapse to one ``PositionRow``.
    #   ``row_count`` surfaces the duplication so the renderer can tag drift
    #   without hiding the live position. ``held_sec`` uses MIN(opened_ts) so
    #   drift collapses to the oldest entry's age. ``qty`` is summed so the
    #   reported notional matches what scale-in / drift accumulated.
    #
    #   This is the temporary GROUP BY before A-PR5 introduces the
    #   ``v_open_positions`` view; the view will preserve the same logical
    #   key shape so this reader switches with a one-line SQL change.
    # Column order documented inline so the unpacking below stays legible.
    # NOTE: ``SUM(qty)`` is inflated for legacy drift rows (each accidental
    # duplicate added its own qty); collapses back to a single physical row
    # post A-PR2/A-PR4 so the inflation disappears with migration.
    rows = _safe_query(
        conn,
        """SELECT venue,                                  -- r[0]
                  symbol,                                 -- r[1]
                  MAX(underlying_group_id) AS group_id,   -- r[2]
                  strategy_id,                            -- r[3]
                  MAX(active_strategy_id) AS active_sid,  -- r[4]
                  side,                                   -- r[5]
                  SUM(qty) AS qty,                        -- r[6]
                  MIN(opened_ts) AS opened_ts,            -- r[7]
                  COUNT(*) AS row_count                   -- r[8]
           FROM positions
           WHERE status NOT IN ('closed', 'cancelled')
           GROUP BY venue, symbol, strategy_id, side
           ORDER BY MIN(opened_ts) DESC""",
    )
    out: list[PositionRow] = []
    for r in rows:
        venue = str(r[0])
        symbol = str(r[1])
        group_id = str(r[2] or "")
        strat = str(r[4] or r[3])
        side = str(r[5])
        qty = float(r[6] or 0.0)
        opened = int(r[7] or 0)
        row_count = int(r[8] or 1)
        inst = f"{venue}:{symbol}"
        entry = entry_lookup.get((venue, inst, strat))
        if entry is None or entry <= 0.0:
            entry = last_prices.get(inst, 0.0) or 0.0
        last = last_prices.get(inst, entry)
        sign = 1.0 if side.lower() in {"long", "buy"} else -1.0
        delta_pct = ((last - entry) / entry * 100.0) if entry > 0 else 0.0
        upnl = (last - entry) * qty * sign
        size_usd = entry * abs(qty)
        regime = regime_lookup.get((venue, group_id), "chop")
        mult = cell_mult.get((venue, strat, symbol, regime), 1.0)
        held_sec = max(0.0, float(now_s - opened))
        out.append(
            PositionRow(
                venue=venue,
                symbol=symbol,
                strategy_id=strat,
                side=side,
                qty=qty,
                entry_price=entry,
                last_price=last,
                delta_pct=delta_pct * sign,
                upnl_usd=upnl,
                size_usd=size_usd,
                held_sec=held_sec,
                cell_mult=mult,
                row_count=row_count,
            )
        )
    out.sort(key=lambda p: p.upnl_usd, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: per-strategy stats
# ---------------------------------------------------------------------------


def _strategy_stats(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    positions: list[PositionRow],
) -> list[StrategyStat]:
    lookback_ms = (now_s - EQUITY_LOOKBACK_SEC) * 1000
    rows = _safe_query(
        conn,
        """SELECT strategy_id,
                  COUNT(*) AS closed_n,
                  SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
                  COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END), 0.0) AS gross_win,
                  COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN -pnl_usd ELSE 0 END), 0.0) AS gross_loss,
                  COALESCE(SUM(pnl_usd), 0.0) AS pnl_total
           FROM fills
           WHERE is_close = 1 AND ts_ms >= ?
           GROUP BY strategy_id""",
        (lookback_ms,),
    )
    by_strat: dict[str, StrategyStat] = {}
    for r in rows:
        sid = str(r[0])
        closed_n = int(r[1] or 0)
        wins = int(r[2] or 0)
        gross_win = float(r[3] or 0.0)
        gross_loss = float(r[4] or 0.0)
        pnl_total = float(r[5] or 0.0)
        wr = (wins / closed_n * 100.0) if closed_n else 0.0
        avg_r = (pnl_total / closed_n / DEFAULT_R_USD) if closed_n else 0.0
        pf = (gross_win / gross_loss) if gross_loss > 0 else (gross_win and 9.99 or 0.0)
        by_strat[sid] = StrategyStat(
            strategy_id=sid,
            open_n=0,
            closed_n=closed_n,
            wr_pct=wr,
            avg_r=avg_r,
            pf=pf,
            pnl_usd=pnl_total,
            notional_usd=0.0,
        )

    # Layer in open-position counts + notional
    for p in positions:
        s = by_strat.get(p.strategy_id) or StrategyStat(
            strategy_id=p.strategy_id, open_n=0, closed_n=0, wr_pct=0.0,
            avg_r=0.0, pf=0.0, pnl_usd=0.0, notional_usd=0.0,
        )
        s.open_n += 1
        s.notional_usd += p.size_usd
        by_strat[p.strategy_id] = s

    out = sorted(by_strat.values(), key=lambda s: s.pnl_usd, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: gate funnel
# ---------------------------------------------------------------------------


_GATE_LABELS: Final[dict[int, str]] = {
    1: "Universe",
    2: "Strategy",
    3: "Validator",
    4: "PreEntry",
    5: "Sizer",
    6: "Monitor",
    7: "Exit",
    8: "Reflector",
}


def _gate_funnel(conn: sqlite3.Connection, *, now_s: int) -> list[GateRow]:
    rows = _safe_query(
        conn,
        """SELECT gate_id, decision, COUNT(*) FROM gate_events
           WHERE created_ts >= ?
           GROUP BY gate_id, decision
           ORDER BY gate_id""",
        (now_s - GATE_FUNNEL_LOOKBACK_SEC,),
    )
    by_gate: dict[int, dict[str, int]] = {}
    for r in rows:
        gid = int(r[0])
        dec = str(r[1] or "?").upper()
        n = int(r[2] or 0)
        by_gate.setdefault(gid, {})[dec] = n

    out: list[GateRow] = []
    for gid in range(1, 9):
        d = by_gate.get(gid, {})
        pass_n = d.get("PASS", 0) + d.get("PROCEED", 0) + d.get("HOLD", 0) + d.get(
            "SIZED", 0
        ) + d.get("REFLECTED", 0)
        kill_n = d.get("KILL", 0) + d.get("EXIT", 0) + d.get("REJECT", 0)
        total = sum(d.values())
        other = max(0, total - pass_n - kill_n)
        rate = (pass_n / total * 100.0) if total else 0.0
        out.append(
            GateRow(
                gate_id=gid,
                label=_GATE_LABELS.get(gid, f"G{gid}"),
                pass_n=pass_n,
                kill_n=kill_n,
                other_n=other,
                total=total,
                pass_rate=rate,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: cell matrix top/bottom
# ---------------------------------------------------------------------------


def _cell_top_bottom(
    conn: sqlite3.Connection,
    *,
    cell_mult: dict[tuple[str, str, str, str], float],
    n: int = 5,
) -> tuple[list[CellRow], list[CellRow], int]:
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, n_eff, score
           FROM cell_matrix_p0
           WHERE n_eff >= 20
           ORDER BY score DESC""",
    )
    if not rows:
        return [], [], 0
    eligible_n = len(rows)
    top_rows = rows[:n]
    bot_rows = list(reversed(rows[-n:]))
    if eligible_n < n * 2:
        # Avoid duplicate row showing in both top & bottom for tiny pools
        seen = {(r[0], r[1], r[2], r[3]) for r in top_rows}
        bot_rows = [r for r in bot_rows if (r[0], r[1], r[2], r[3]) not in seen]

    def _build(r: tuple[Any, ...]) -> CellRow:
        key = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        return CellRow(
            exchange=str(r[0]),
            strategy=str(r[1]),
            ticker=str(r[2]),
            regime=str(r[3]),
            n_eff=float(r[4] or 0.0),
            score=float(r[5] or 0.0),
            mult=cell_mult.get(key, 1.0),
        )

    return [_build(r) for r in top_rows], [_build(r) for r in bot_rows], eligible_n


# ---------------------------------------------------------------------------
# Section: regime heatmap
# ---------------------------------------------------------------------------


def _regime_bars(
    conn: sqlite3.Connection,
) -> tuple[list[RegimeBar], dict[tuple[str, str], str]]:
    rows = _safe_query(
        conn,
        """SELECT venue, underlying_group_id, regime FROM regime_state""",
    )
    counts: dict[str, int] = {}
    lookup: dict[tuple[str, str], str] = {}
    for v, g, regime in rows:
        regime_s = str(regime or "chop")
        counts[regime_s] = counts.get(regime_s, 0) + 1
        lookup[(str(v), str(g))] = regime_s
    ordered = ["chop", "bull_trend", "bear_trend", "crisis"]
    bars = [RegimeBar(regime=r, count=counts.get(r, 0)) for r in ordered]
    return bars, lookup


# ---------------------------------------------------------------------------
# Section: recent closed trades (trade-level pairing)
# ---------------------------------------------------------------------------


def _recent_closed_trades(
    conn: sqlite3.Connection, *, n: int = 10
) -> list[ClosedTrade]:
    """Pair close fills with their entry fills, exact when possible.

    Codex round 2 P0 fix: the production pipeline now persists every fill
    with a ``contribution_id`` that equals the originating ``position_id``
    (see ``_production_pipeline.persist_fill`` + ``_production_close``).
    We pair via that exact ID first, falling back to FIFO per
    ``(venue, instrument_id, strategy_id)`` only when ``contribution_id`` is
    NULL (legacy / smoke rows). This eliminates cross-pairing when two open
    positions share the same ``(venue, inst, strat)`` bucket.
    """
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty, size_usd, is_close,
                  contribution_id
           FROM fills
           ORDER BY ts_ms ASC""",
    )
    # Index by exact contribution_id → first open fill (entry)
    open_by_contrib: dict[str, tuple[float, int]] = {}
    open_fifo: dict[tuple[str, str, str], list[tuple[float, int]]] = {}
    closed_trades: list[ClosedTrade] = []
    for r in rows:
        venue = str(r[0])
        inst = str(r[1])
        strat = str(r[2])
        side = str(r[3])
        fill_price = float(r[4] or 0.0)
        pnl = float(r[5] or 0.0)
        ts_ms = int(r[6] or 0)
        qty = float(r[7] or 0.0)
        is_close = bool(r[9])
        contrib = r[10]
        contrib_str = str(contrib) if contrib else None
        bucket = (venue, inst, strat)
        if not is_close:
            if contrib_str and contrib_str not in open_by_contrib:
                open_by_contrib[contrib_str] = (fill_price, ts_ms)
            open_fifo.setdefault(bucket, []).append((fill_price, ts_ms))
            continue
        # CLOSE leg pairing rules (codex round 3 P0 tightening):
        #  • contribution_id present + exact open found → pair exact
        #  • contribution_id present + open MISSING     → reconstruct from PnL
        #    (do NOT FIFO-pair, that would re-introduce the cross-pair bug)
        #  • contribution_id NULL (legacy / smoke rows) → FIFO bucket fallback
        entry_px: float | None = None
        open_ts_ms: int | None = None
        if contrib_str:
            if contrib_str in open_by_contrib:
                entry_px, open_ts_ms = open_by_contrib.pop(contrib_str)
                # Drain matching FIFO entry so legacy fallbacks don't reuse it.
                fifo = open_fifo.get(bucket)
                if fifo:
                    for i, (px, ts) in enumerate(fifo):
                        if px == entry_px and ts == open_ts_ms:
                            fifo.pop(i)
                            break
            # else: deliberately do NOT FIFO-pair — fall through to reconstruct.
        else:
            fifo = open_fifo.get(bucket) or []
            if fifo:
                entry_px, open_ts_ms = fifo.pop(0)
        if entry_px is None or open_ts_ms is None:
            # Stale close: reconstruct entry from PnL
            if qty > 0 and fill_price > 0:
                sign = 1.0 if side.lower() == "sell" else -1.0
                entry_px = fill_price - (pnl / qty) * sign
            else:
                entry_px = fill_price
            held_sec = 0.0
        else:
            held_sec = max(0.0, (ts_ms - open_ts_ms) / 1000.0)
        if pnl > 0:
            reason = "TP"
        elif pnl < 0:
            reason = "SL" if held_sec < 600 else "TIME"
        else:
            reason = "FLAT"
        closed_trades.append(
            ClosedTrade(
                ts_close=ts_ms // 1000,
                venue=venue,
                symbol=_symbol_from_inst(inst),
                strategy_id=strat,
                side_close=side,
                entry_price=entry_px,
                exit_price=fill_price,
                pnl_usd=pnl,
                r_units=pnl / DEFAULT_R_USD,
                held_sec=held_sec,
                exit_reason=reason,
            )
        )
    closed_trades.sort(key=lambda t: t.ts_close, reverse=True)
    return closed_trades[:n]


# ---------------------------------------------------------------------------
# Section: learner state (3 P0)
# ---------------------------------------------------------------------------


_LEARNER_FEATURED: Final[tuple[str, ...]] = ("session_mult", "regime_mult", "max_hold")


def _learner_slots(conn: sqlite3.Connection, *, now_s: int) -> list[LearnerSlot]:
    cutoff = now_s - LEARNER_DELTA_LOOKBACK_SEC
    out: list[LearnerSlot] = []
    for lid in _LEARNER_FEATURED:
        # Pick the row with highest n_eff (most-informed key)
        rows = _safe_query(
            conn,
            """SELECT key, value, n_eff, updated_at FROM learner_state
               WHERE learner_id = ? ORDER BY n_eff DESC LIMIT 1""",
            (lid,),
        )
        if not rows:
            out.append(LearnerSlot(learner_id=lid, key="-", value=1.0, delta_1h=0.0, n_eff=0.0))
            continue
        key = str(rows[0][0])
        value = float(rows[0][1] or 1.0)
        n_eff = float(rows[0][2] or 0.0)
        # Look up snapshot ≥ 1h ago for delta
        snap = _safe_query(
            conn,
            """SELECT value FROM learner_snapshot
               WHERE learner_id = ? AND key = ? AND snapshot_ts <= ?
               ORDER BY snapshot_ts DESC LIMIT 1""",
            (lid, key, cutoff),
        )
        prev = float(snap[0][0]) if snap else value
        out.append(
            LearnerSlot(
                learner_id=lid, key=key, value=value, delta_1h=value - prev, n_eff=n_eff,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: GPT cost
# ---------------------------------------------------------------------------


def _gpt_stats(conn: sqlite3.Connection, *, now_s: int) -> list[GptStat]:
    rows = _safe_query(
        conn,
        """SELECT model_used, COUNT(*) FROM gate_events
           WHERE created_ts >= ? AND model_used IS NOT NULL
           GROUP BY model_used""",
        (now_s - GPT_LOOKBACK_SEC,),
    )
    out: list[GptStat] = []
    for model, n in rows:
        if not model:
            continue
        m = str(model)
        if m == "python":
            continue
        price = GPT_PRICE_PER_1K.get(m, GPT_PRICE_PER_1K["gpt"])
        cost_per_h = int(n) * GPT_TOKENS_PER_CALL / 1000.0 * price
        out.append(
            GptStat(
                model=m,
                calls_per_h=int(n),
                cost_per_h_usd=cost_per_h,
                cost_24h_proj_usd=cost_per_h * 24.0,
            )
        )
    out.sort(key=lambda g: g.calls_per_h, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: alert log
# ---------------------------------------------------------------------------


def _alerts(conn: sqlite3.Connection, *, n: int = 3) -> list[AlertRow]:
    out: list[AlertRow] = []
    risk = _safe_query(
        conn,
        """SELECT created_ts, event_type, strategy_id, payload_json FROM risk_events
           ORDER BY created_ts DESC LIMIT ?""",
        (n,),
    )
    for r in risk:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="WARN",
                module=str(r[2] or "risk"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    fault = _safe_query(
        conn,
        """SELECT event_ts, fault_type, strategy_id, detail_json FROM strategy_fault_events
           ORDER BY event_ts DESC LIMIT ?""",
        (n,),
    )
    for r in fault:
        out.append(
            AlertRow(
                ts=int(r[0] or 0),
                level="ERROR",
                module=str(r[2] or "strat"),
                msg=f"{r[1]}: {str(r[3])[:60]}",
            )
        )
    out.sort(key=lambda a: a.ts, reverse=True)
    return out[:n]


# ---------------------------------------------------------------------------
# Section: universe
# ---------------------------------------------------------------------------


def _universe(conn: sqlite3.Connection) -> tuple[int, str]:
    # Current cycle's focus count (NOT all-time accumulated rows)
    rows = _safe_query(
        conn,
        "SELECT COUNT(*), cycle_ts FROM watchlist_focus "
        "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus) "
        "GROUP BY cycle_ts",
    )
    if rows and rows[0][0]:
        cnt, last_ts = rows[0]
        ts_str = (
            time.strftime("%H:%M:%S", time.localtime(int(last_ts))) if last_ts else "n/a"
        )
        return int(cnt), ts_str
    rows = _safe_query(conn, "SELECT COUNT(*) FROM universe WHERE is_active = 1")
    cnt = int(rows[0][0]) if rows else 0
    return cnt, "n/a"


# ---------------------------------------------------------------------------
# Top-level collector
# ---------------------------------------------------------------------------


def collect_snapshot(db_path: Path = DEFAULT_DB_PATH) -> DashboardSnapshot:
    """Single-pass DB read → DashboardSnapshot. Returns zero-snapshot on missing DB."""
    starting_capital = _starting_capital()
    starting_okx = demo_starting_equity_okx()
    starting_capital_cap = demo_starting_equity_capital()
    if not db_path.exists():
        return DashboardSnapshot(
            ts_now=_now_s(),
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            equity_now=starting_capital,
            peak_equity=starting_capital,
        )

    now_s = _now_s()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = None
    try:
        bucket_ts, equity, _total_realised = _build_equity_curve(
            conn, now_s=now_s, starting_capital=starting_capital,
        )
        equity_now = equity[-1] if equity else starting_capital
        dd_pct, peak, sharpe = _drawdown_and_sharpe(
            equity, starting_capital=starting_capital,
        )
        daily_pnl, daily_n = _daily_realised_pnl(conn, now_s=now_s)
        last_prices = _last_prices(conn)
        entry_lookup = _entry_price_lookup(conn)
        cell_mult = _cell_mult_lookup(conn)
        regime_bars, regime_lookup = _regime_bars(conn)
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=last_prices,
            entry_lookup=entry_lookup,
            cell_mult=cell_mult,
            regime_lookup=regime_lookup,
        )
        upnl_total = sum(p.upnl_usd for p in positions)
        notional_open = sum(p.size_usd for p in positions)
        # P0 fix (codex): expose the truthful "deployed notional" instead of a
        # mislabelled CASH proxy. Real free-cash / margin-headroom requires a
        # venue balance probe that lives outside the snapshot path.
        exposed_usd = notional_open
        equity_with_upnl = equity_now + upnl_total
        # Re-compute DD/Peak with uPnL-included current point
        equity_full = (
            equity + [equity_with_upnl] if equity else [equity_with_upnl]
        )
        dd_pct, peak, sharpe = _drawdown_and_sharpe(
            equity_full, starting_capital=starting_capital,
        )
        strategy_stats = _strategy_stats(conn, now_s=now_s, positions=positions)
        gate_funnel = _gate_funnel(conn, now_s=now_s)
        cell_top, cell_bot, eligible_n = _cell_top_bottom(conn, cell_mult=cell_mult, n=5)
        recent_trades = _recent_closed_trades(conn, n=10)
        learners = _learner_slots(conn, now_s=now_s)
        gpt_stats = _gpt_stats(conn, now_s=now_s)
        alerts = _alerts(conn, n=3)
        focus_n, focus_ts = _universe(conn)
        return DashboardSnapshot(
            ts_now=now_s,
            starting_capital=starting_capital,
            starting_capital_okx=starting_okx,
            starting_capital_capital=starting_capital_cap,
            equity_now=equity_with_upnl,
            exposed_usd=exposed_usd,
            upnl_total=upnl_total,
            daily_pnl_usd=daily_pnl,
            daily_trades=daily_n,
            drawdown_pct=dd_pct,
            peak_equity=peak,
            sharpe_24h=sharpe,
            open_positions_n=len(positions),
            active_cells_n=eligible_n,
            universe_focus_n=focus_n,
            universe_last_refresh=focus_ts,
            equity_curve=equity_full,
            equity_curve_ts=bucket_ts + [now_s],
            positions=positions,
            strategy_stats=strategy_stats,
            gate_funnel=gate_funnel,
            cell_top=cell_top,
            cell_bottom=cell_bot,
            regime_bars=regime_bars,
            recent_trades=recent_trades,
            learners=learners,
            gpt_stats=gpt_stats,
            alerts=alerts,
        )
    finally:
        conn.close()
