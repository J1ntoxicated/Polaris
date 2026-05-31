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
    # E2 expanded-column display (read-only): per-position regime label +
    # exit-FSM state + stop price + MFE/MAE in R. Sourced from the positions
    # row (``exit_state`` / ``stop_price`` / ``mfe_r`` / ``mae_r``) + the
    # regime_state lookup. Graceful zero/empty when columns are NULL. These NEVER
    # feed sizing/gating — pure board columns.
    regime: str = ""
    exit_state: str = "open"
    stop_price: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    upnl_pct: float = 0.0


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
    # E2 expanded-column display (read-only): per-trade regime label, pnl as a %
    # of entry notional, and the demo-vs-real fee split for the TRADES tab.
    # ``fee_usd`` is the stored demo fee (0.7% drain); ``real_fee_usd`` is the
    # same notional re-priced at the REAL OKX schedule. Graceful zero when the
    # source row omits them. NEVER feed sizing/gating — pure board columns.
    regime: str = ""
    pnl_pct: float = 0.0
    fee_usd: float = 0.0
    real_fee_usd: float = 0.0


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
class RotationEvent:
    """One capital-rotation fire — display-only telemetry row.

    Sourced from the ``loop_rotation_events`` table the rotation wire appends to
    (``state.rotations`` lives in process memory the read-only dashboard cannot
    reach). NEVER read by sizing / gating / the rotation evaluator.
    """

    ts: int
    venue: str
    victim_symbol: str
    victim_strategy: str
    winner_symbol: str
    e_new: float
    e_held: float
    margin: float
    cost: float


@dataclass(slots=True)
class RotationTelemetry:
    """Rollup of rotation + session-forced-exit observability for the dashboard.

    ``rotation_count`` / ``session_forced_exit_count`` are session-window totals;
    ``last_rotation`` is the most-recent fire (or ``None`` when none). Graceful
    zero when the telemetry tables are empty or absent (older schema)."""

    rotation_count: int = 0
    session_forced_exit_count: int = 0
    last_rotation: RotationEvent | None = None


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
    # Cost monitoring (display-only — "근거 있는 수익 추적"): track the real
    # deductions per stream so profit is evidence-based. ``fee_usd`` = Σ venue
    # fills.fee_usd; ``slippage_usd`` = Σ derived from fills.slippage_bps;
    # ``ai_cost_usd`` = Σ gate_events tokens × model-price (attributed via the
    # position_id→venue join); ``net_after_cost_usd`` = net_pnl − slippage −
    # ai_cost (net_pnl ALREADY nets fees, so fees are counted exactly once;
    # economic identity = gross_close_pnl − fee − slippage − ai_cost). These
    # NEVER feed sizing/gating — pure read-only telemetry.
    fee_usd: float = 0.0
    slippage_usd: float = 0.0
    ai_cost_usd: float = 0.0
    net_after_cost_usd: float = 0.0
    # OPEN vs CLOSED split (follow-up #12) — additive. ``open_positions_n`` above
    # is the currently-open count; ``closed_n`` is this lane's closed-fill count
    # (== ``daily_trades``, surfaced under a clearer name so the board can show a
    # distinct open-vs-closed view per exchange lane). ``recent_closed`` is the
    # lane's most-recent closed trades (newest first), an empty list when none.
    closed_n: int = 0
    recent_closed: list[ClosedTrade] = field(default_factory=list)


@dataclass(slots=True)
class ConfidenceCell:
    """One (strategy × regime) real-fee-net edge row for the confidence panel.

    ``expected_real_fee_net_r`` = mean per-trade R after the REAL fee schedule;
    ``lcb_real_fee_net_r`` = NIG posterior lower-confidence-bound on that mean
    (a ``+`` ``lcb_sign`` is the edge-confidence signal). Display-only —
    sourced from ``confidence.confidence_summary``; never feeds sizing/gating.
    """

    strategy_id: str
    regime: str
    n: int
    expected_real_fee_net_r: float
    lcb_real_fee_net_r: float
    lcb_sign: str


@dataclass(slots=True)
class ConfidencePanel:
    """Go-live confidence rollup (Component A) — real-fee-net edge evidence.

    The headline overall metrics + per-(strategy×regime) cells Jin watches to
    judge confidence to open real OKX. ``fee_drag_real_r`` vs ``fee_drag_demo_r``
    is the real-vs-demo cost wedge (demo is the 7x penalty). Display-only."""

    n_closed: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    turnover_ratio: float = 0.0
    fee_drag_real_r: float = 0.0
    fee_drag_demo_r: float = 0.0
    cells: list[ConfidenceCell] = field(default_factory=list)


@dataclass(slots=True)
class RegimeStateRow:
    """One per-(venue, asset-group/symbol) regime row for the REGIME tab.

    Sourced read-only from ``regime_state`` (the live regime classifier output):
    ``regime`` + ``confidence`` + the layered evidence (``evidence_json`` decoded
    into L1 macro / L2 asset-class / L3 price-action labels where present) + the
    2-consecutive-state hysteresis (``consecutive_candidate`` /
    ``consecutive_count``). Display-only; never feeds sizing/gating."""

    venue: str
    group_id: str
    regime: str
    confidence: float
    consecutive_candidate: str
    consecutive_count: int
    updated_ts: int
    # Layered evidence labels decoded from evidence_json (best-effort; empty when
    # a layer is absent). l1 = macro, l2 = asset-class, l3 = price-action.
    evidence_l1: str = ""
    evidence_l2: str = ""
    evidence_l3: str = ""


@dataclass(slots=True)
class ExitReasonBar:
    """One exit-reason histogram bucket for the EXIT tab (count by reason)."""

    reason: str
    count: int


@dataclass(slots=True)
class ExitSurface:
    """Exit-engine observability rollup for the EXIT tab — read-only.

    ``fsm_states`` = distribution of the open positions' ``exit_state`` FSM label
    (open / touched / protected / harvest / ...). ``loser_timeout_n`` = count of
    closed trades whose exit_reason is the loser-timeout ('TIME'). ``reasons`` =
    exit-reason histogram over the recent closed trades. ``g6_decisions`` /
    ``g7_decisions`` = the G6 Monitor / G7 Adaptive-Exit gate decision tallies
    (decision → count). Never feeds sizing/gating."""

    fsm_states: dict[str, int] = field(default_factory=dict)
    loser_timeout_n: int = 0
    reasons: list[ExitReasonBar] = field(default_factory=list)
    g6_decisions: dict[str, int] = field(default_factory=dict)
    g7_decisions: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowAgreementRow:
    """Conductor shadow agreement for the AI tab — read-only.

    One row per (gate_id, regime) over ``gate_shadow_events``: how often the
    deterministic technical decision matched the live GPT decision. ``n`` = rows
    with a known GPT decision; ``mismatch_n`` = where technical != GPT;
    ``agree_pct`` = (n − mismatch_n)/n. ``n_no_gpt`` = rows where GPT was absent
    (deterministic-only, excluded from the agreement ratio). Display-only."""

    gate_id: int
    regime: str
    n: int
    mismatch_n: int
    agree_pct: float
    n_no_gpt: int


@dataclass(slots=True)
class EntryAdmissionStat:
    """Entry-admission shadow rollup for the AI tab — read-only.

    Over ``entry_admission_shadow``: per (regime) — total evaluations + how many
    the edge-first rule WOULD suppress (net of the real round-trip fee). Pure
    SHADOW telemetry (behavior 0 — never blocks the pipeline)."""

    regime: str
    n: int
    would_suppress_n: int
    suppress_pct: float


@dataclass(slots=True)
class AiShadowPanel:
    """AI-tab shadow rollup container (conductor agreement + admission shadow)."""

    shadow_agreement: list[ShadowAgreementRow] = field(default_factory=list)
    admission: list[EntryAdmissionStat] = field(default_factory=list)
    admission_total_n: int = 0
    admission_suppress_n: int = 0


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
    # Component A (Jin 2026-05-31) — go-live confidence signal. The SAME gross
    # close pnl as ``equity_curve`` (demo-actual), but fees RECOMPUTED at the
    # REAL OKX schedule (0.10% taker) instead of the stored 0.7% demo fee. This
    # real-fee-net curve is the headline Jin watches for go-live. Additive — the
    # demo-actual ``equity_curve`` / ``equity_now`` fields keep their meaning.
    equity_curve_real_fee_net: list[float] = field(default_factory=list)
    equity_now_real_fee_net: float = STARTING_CAPITAL
    real_fee_total: float = 0.0   # Σ real-schedule fees over the session
    demo_fee_total: float = 0.0   # Σ stored demo fees (the 0.7% drain)
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
    # Rotation + session-forced-exit telemetry (follow-up #12) — display-only,
    # additive. Read from the loop_rotation_events / loop_session_exit_events
    # tables; graceful zero when none. dataclasses.asdict serializes
    # ``last_rotation`` (a nested RotationEvent or None) for the web snapshot.
    rotation_count: int = 0
    session_forced_exit_count: int = 0
    last_rotation: RotationEvent | None = None
    # Component A (Jin 2026-05-31) — go-live confidence panel: real-fee-net
    # per-(strategy×regime) edge + overall win-rate / profit-factor / turnover /
    # real-vs-demo fee drag. Display-only; dataclasses.asdict serializes it for
    # the web board. Graceful zero-panel when there are no closed trades.
    confidence: ConfidencePanel = field(default_factory=ConfidencePanel)
    # E2 IA-rebuild tabs (Jin 2026-05-31) — read-only display additions. Each is
    # surfaced by a dedicated full-width tab on the web board. All additive;
    # dataclasses.asdict serializes them for the snapshot. NEVER feed
    # sizing/gating/exit/strategy/loop — pure board columns.
    #  REGIME tab — per-(venue, asset-group) live regime + confidence + evidence.
    regime_states: list[RegimeStateRow] = field(default_factory=list)
    #  EXIT tab — exit-engine FSM distribution + reason histogram + G6/G7 counts.
    exit_surface: ExitSurface = field(default_factory=ExitSurface)
    #  AI tab — conductor shadow agreement + entry-admission would-suppress stats.
    ai_shadow: AiShadowPanel = field(default_factory=AiShadowPanel)
