"""Polaris dashboard v1 — snapshot dataclasses (DB row → typed view models).

Pure value objects consumed by ``snapshot.collect_snapshot`` (the query layer)
and ``render.py`` (the ANSI grid renderer). Split out of ``snapshot.py`` to keep
each module ≤500 LOC; all names are re-exported from ``snapshot`` so existing
import paths (``from polaris.scripts.dashboard.snapshot import PositionRow``)
keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from polaris.core.sizing.constants import demo_starting_equity_total

# Module-level constant for backwards-compatible imports + tests; resolved once
# at import time and reflects the *current* env. Tests that patch env should
# call ``demo_starting_equity_total()`` directly instead.
STARTING_CAPITAL: Final[float] = demo_starting_equity_total()


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
