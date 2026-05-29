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
class EdgeValidationRow:
    """Edge-validation Phase 1 row — Bayesian posterior on cost-adjusted
    expectancy per (strategy × ticker × regime). Display only; not a gate."""

    exchange: str
    strategy: str
    ticker: str
    regime: str
    cost_adj_exp: float  # mu_n (posterior mean of cost-adjusted expectancy R)
    p_pos: float  # P(expectancy>0)
    n_samples: int
    verdict: str  # validated-alpha / anti-edge / unproven
    est_cost: bool  # True when Capital const-bps cost was used (vs real fees)


@dataclass(slots=True)
class GptStat:
    model: str
    calls_per_h: int
    cost_per_h_usd: float
    cost_24h_proj_usd: float
    # Silent-degradation telemetry: per-model success rate over the lookback.
    # ok_pct = success rows / total rows (gate_events.phase == 'success'). A
    # model silently failing (e.g. gpt-5.5 100% errors) shows ok_pct ≈ 0 even
    # while calls_per_h stays high — surfaces the failure the cost card alone
    # would hide.
    ok_pct: float = 100.0
    err_n: int = 0


@dataclass(slots=True)
class AlertRow:
    ts: int
    level: str
    module: str
    msg: str


@dataclass(slots=True)
class StreamSummary:
    """Per-stream (venue lane) rollup for the stage-2 dashboard — read-only.

    One row per registered stream (A_okx_crypto / B_capital_cfd /
    C_alpaca_equity), emitted even when a venue has zero activity so all three
    lanes always render. ``stream_id`` / ``venue`` / ``label`` / ``product_class``
    / ``color`` are sourced from the streams SSOT (``polaris.core.streams.config``
    + a display label/color map keyed on stream_id) — never a second hardcoded
    venue map.

    Reconciliation invariant: ``Σ net_pnl_usd`` == global ``daily_pnl_usd`` and
    ``Σ open_positions_n`` == global ``open_positions_n`` (the dashboard never
    lies). ``upnl_usd`` / ``exposed_usd`` likewise sum to the global totals.

    Per-stream derivables: ``net_pnl_usd`` (session realised, net of fees),
    ``daily_trades`` (closed-fill count), ``open_positions_n``, ``exposed_usd``
    (deployed notional), ``upnl_usd`` (unrealised). ``equity_usd`` /
    ``drawdown_pct`` are best-effort (``starting_capital + net_pnl + upnl`` and a
    naive peak/now DD); ``starting_capital`` is the per-venue split where known
    (okx / capital), alpaca placeholder 0.0.
    """

    stream_id: str
    venue: str
    label: str
    product_class: str
    color: str
    starting_capital: float = 0.0
    equity_usd: float = 0.0
    net_pnl_usd: float = 0.0
    upnl_usd: float = 0.0
    exposed_usd: float = 0.0
    open_positions_n: int = 0
    daily_trades: int = 0
    drawdown_pct: float = 0.0


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
    edge_validation: list[EdgeValidationRow] = field(default_factory=list)
    gpt_stats: list[GptStat] = field(default_factory=list)
    alerts: list[AlertRow] = field(default_factory=list)
    # Stage-2 per-stream (venue lane) rollup — additive. One row per registered
    # stream; sums reconcile to the global totals above. server.py
    # dataclasses.asdict auto-serializes this for the web snapshot.
    streams: list[StreamSummary] = field(default_factory=list)
