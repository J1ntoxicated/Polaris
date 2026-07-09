"""Polaris dashboard v1 — snapshot dataclasses (DB row → typed view models).

Pure value objects consumed by ``snapshot.collect_snapshot`` (the query layer)
and ``render.py`` (the ANSI grid renderer). Split out of ``snapshot.py`` to keep
each module ≤500 LOC; all names are re-exported from ``snapshot`` so existing
import paths (``from polaris.scripts.dashboard.snapshot import PositionRow``)
keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

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
    #
    # Hardening #6 (2026-06-23): ``mfe_atr_r`` / ``mae_atr_r`` are the per-trade
    # -ATR EXCURSION ruler (``_close_excursion_r`` / ``realised_r``), suffixed so
    # the tile never confuses them with the per-stream LEDGER R (``pnl_r`` /
    # ``avg_r`` / ``sum_r`` via ``realised_r_stream``). Two different rulers.
    regime: str = ""
    exit_state: str = "open"
    stop_price: float = 0.0
    # [P0-5] ``None`` (not 0.0) when the row has no ``risk_usd`` R denominator
    # yet (e.g. a freshly reconcile-imported position before the ATR anchor
    # resolves) — the renderer shows 'n/a', never a fabricated 0.0R that reads
    # as a real (flat) excursion measurement.
    mfe_atr_r: float | None = 0.0
    mae_atr_r: float | None = 0.0
    upnl_pct: float = 0.0
    # uPnL NET of the expected round-trip real fee (entry + expected close) —
    # the live mirror of the closed-trade gross/fee/net split, so an open
    # position that is gross-positive but would net-negative once both fee legs
    # bite reads as a loss on the board (Jin 2026-06-26). ``upnl_usd`` is the
    # gross (USD-converted) leg; this is gross − expected_roundtrip_real_fee.
    # Display-only — NEVER feeds sizing/gating/exit.
    upnl_net_usd: float = 0.0
    # Display-alignment additions (read-only). ``entry_regime`` is the IMMUTABLE
    # regime stamped at entry (positions.entry_regime — chop/bull_trend/...), so
    # the board shows the regime the trade was opened in, not just the live one
    # (``regime`` above). ``quote_ccy`` is the true price-quote currency for the
    # ENTRY/CURRENT/STOP cells (J225 is JPY, EU50 is EUR); size_usd / upnl_usd
    # stay USD. Both NEVER feed sizing/gating — pure board columns.
    entry_regime: str = ""
    quote_ccy: str = "USD"
    # Human-readable instrument name (universe.name, display-only): "Apple Inc."
    # for AAPL. "" when unknown → UI falls back to the symbol. NEVER feeds anything.
    name: str = ""
    # Symbol sparkline (Jin 2026-06-25) — the symbol's most-recent N closes
    # (oldest→newest) so the board draws a tiny inline trend graph next to the
    # symbol. Sourced from the read-only bars cache; empty when no bars. NEVER
    # feeds sizing/gating/exit — pure board chrome.
    spark: list[float] = field(default_factory=list)


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
    # STRATEGY ROSTER state (Jin 2026-07-07) — "which edges are active + how".
    # ``strategy_class`` is EARN/PROVE/BENCH from the ``strategy_class`` table
    # (best-effort: an unbootstrapped row defaults to "EARN", the same
    # fail-open default ``resolve_strategy_class`` uses). VIRTUAL mode forces
    # EARN at sizing regardless of this label (every signal becomes a real
    # virtual trade) — this field shows the tracked class, not the sizing
    # override. ``signals_24h`` = distinct signals emitted in the last 24h
    # (``signals`` table). ``last_signal_ts`` = 0 when never signalled.
    # Display-only; never feeds sizing/gating/exit.
    strategy_class: str = "EARN"
    signals_24h: int = 0
    last_signal_ts: int = 0


@dataclass(slots=True)
class TickerStat:
    """P0.3 (2026-06-22): per-ticker cumulative realized R from positions.pnl_r
    (honest — includes drift-close estimates + uncapped catastrophic losses after
    fix #1 / P0.4). Surfaces where the bot bleeds / wins by symbol. Display-only."""

    venue: str
    symbol: str
    n: int
    wr_pct: float
    sum_r: float
    # Symbol sparkline (Jin 2026-06-25) — recent closes (oldest→newest) for the
    # inline mini trend graph on the per-ticker panel. Bars-cache sourced; empty
    # when no bars. Display-only; never feeds sizing/gating/exit.
    spark: list[float] = field(default_factory=list)


@dataclass(slots=True)
class GateEvent:
    """One recent per-gate decision for the live gate-event feed (read-only).

    Sourced from ``gate_events`` (newest first): the gate id + its label, the
    decision (PASS / KILL / MODIFY / HOLD / …), and the best-effort strategy /
    symbol / reason decoded from the row's ``payload_json`` (the table has no
    dedicated strategy/symbol columns — they live nested in the payload, shape
    varies by gate, so extraction is graceful: empty string when absent). NEVER
    feeds sizing/gating — pure display feed."""

    gate_id: int
    label: str
    decision: str
    strategy: str
    symbol: str
    reason: str
    ts: int
    # Per-gate rich detail decoded from payload_json (display-only). The shape is
    # gate-specific and all keys are optional: G5 carries the T4 size line
    # (risk_pct/notional/scalar/tier/cell/leverage), G8 the lesson
    # (lesson_type/confidence), G1 the focus count. Empty dict for gates with no
    # extra detail. NEVER feeds sizing/gating — pure feed chrome.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GateDecisionRow:
    """One per-gate DECISION-summary row (replaces the pass/kill GateRow).

    The bot is flow_not_block by absolute mandate — gates emit
    PASS/SIZED/HOLD/REFLECTED, essentially never KILL — so a pass/kill ratio is
    forever ~100% (zero information) and visually implies a block-filter
    architecture the mandate forbids. This row instead carries each gate's
    CHARACTERISTIC meaningful output for the window: a one-line ``headline`` plus
    a small typed ``metrics`` dict (gate-specific keys, all optional). Decoded
    read-only from ``gate_events.payload_json``; NEVER feeds sizing/gating."""

    gate_id: int
    label: str
    headline: str
    n: int
    metrics: dict[str, Any] = field(default_factory=dict)


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
    # Display-alignment additions (read-only — Jin: "수익이랑 피랑 따로 적어",
    # "롱인데 셀로 표기", "레짐 다 -"). ``net_usd`` is the single-truth net =
    # gross (``pnl_usd``) − real fee (``real_fee_usd``); the board shows
    # gross/fee/net split so a small gross that nets negative reads honestly.
    # ``position_side`` is the POSITION direction (long/short, from
    # positions.side) — distinct from ``side_close`` (the close FILL side, e.g.
    # 'sell' = a long exit) which made longs read as shorts. ``entry_regime`` is
    # the immutable regime at entry (positions.entry_regime). ``quote_ccy`` is
    # the true price-quote currency for the ENTRY/EXIT cells (J225 → JPY); pnl /
    # fee stay USD. NEVER feed sizing/gating — pure board columns.
    net_usd: float = 0.0
    position_side: str = ""
    entry_regime: str = ""
    quote_ccy: str = "USD"
    # Human-readable instrument name (universe.name, display-only): "Apple Inc."
    # for AAPL. "" when unknown → UI falls back to the symbol. NEVER feeds anything.
    name: str = ""
    # Symbol sparkline (Jin 2026-06-25) — recent closes (oldest→newest) for the
    # inline mini trend graph on the recent-trades row. Bars-cache sourced; empty
    # when no bars. Display-only; never feeds sizing/gating/exit.
    spark: list[float] = field(default_factory=list)


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
    # Drift (reconciled) loss is a SEPARATE counter labelled "tracking failures,
    # not trades" — excluded from PF/WR/avg_r/ticker R (Step M, 2026-06-22).
    # Hardening #1 (2026-06-23): ``reconciled_realized_usd`` is the PRIMARY figure
    # = ACTUAL realized Σ fills.pnl_usd over the reconciled positions' close legs.
    # ``reconciled_loss_usd`` is the labeled 'est' SECONDARY (mae_r × risk_usd,
    # recorded at reconcile time) kept for when no close fill exists.
    reconciled_realized_usd: float = 0.0
    reconciled_loss_usd: float = 0.0
    reconciled_loss_n: int = 0
    # Hardening #5 (2026-06-23): count of DISTINCT instruments that fell back to a
    # venue-correct asset_class (NULL universe class or unknown group_id prefix).
    # >0 means a held name aged out of the universe OR an L1 group_id mis-prefixes
    # — observability, never a throttle.
    asset_class_fallback_n: int = 0
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
    # fills.fee_usd; ``slippage_usd`` = Σ derived from fills.slippage_bps — a
    # separate unsigned (model) estimate, INFORMATIONAL only, since actual
    # slippage is already baked into fills.pnl_usd (real fill price); ``ai_cost_usd``
    # = Σ gate_events tokens × model-price (attributed via the position_id→venue
    # join); ``net_after_cost_usd`` = net_pnl − ai_cost (net_pnl ALREADY nets
    # fees + slippage since both are reflected in the real fill price; economic
    # identity = gross_close_pnl − fee − ai_cost). These NEVER feed
    # sizing/gating — pure read-only telemetry.
    fee_usd: float = 0.0
    slippage_usd: float = 0.0
    ai_cost_usd: float = 0.0
    net_after_cost_usd: float = 0.0
    # OPEN vs CLOSED split (follow-up #12) — additive. ``open_positions_n`` above
    # is the currently-open count; ``closed_n`` is this lane's closed-POSITION
    # count (P0-4 ③ — one row per trade; may differ from ``daily_trades``, the
    # closed-FILL count, when a trade partial-closes across >1 fill). The board
    # shows ``closed_n`` as TRADES and ``daily_trades`` in the tooltip only.
    # ``recent_closed`` is the lane's most-recent closed trades (newest first),
    # an empty list when none.
    closed_n: int = 0
    recent_closed: list[ClosedTrade] = field(default_factory=list)
    # Mark-freshness label — set whenever THIS lane's own venue-native session
    # (SSOT: ``polaris.core.sizing.session.resolve_venue_session``) is closed:
    # Alpaca (RTH open/closed) and Capital (FX/indices weekend) both have real
    # closed windows, so ``upnl_usd`` there is derived from the last internal
    # mark (stale bar/tick) instead of a live probe/feed — this label + age
    # make that explicit rather than silently showing a live-looking number.
    # OKX (crypto, 24/7) never sets this — always "".
    marks_label: str = ""
    marks_age_sec: int = 0
    # Weekly per-exchange trace (Jin 2026-07-07) — Monday-anchored (UTC),
    # NON-DESTRUCTIVE (trace != reset; the running account above compounds
    # continuously across week boundaries). "This week so far" per exchange:
    # realized + unrealized PnL and the trade count since the current week's
    # Monday anchor. 0.0/0 when no row exists yet this week (graceful).
    weekly_start_equity: float = 0.0
    weekly_realized_pnl_usd: float = 0.0
    weekly_unrealized_pnl_usd: float = 0.0
    weekly_trades: int = 0
    # VIRTUAL ACCOUNT (Jin 2026-07-07) — the fresh $100k-per-exchange measurement
    # (``polaris.storage.virtual_account_equity.virtual_equity_now``), continuously
    # compounding, SEPARATE from the legacy real-venue ``equity_usd``/
    # ``starting_capital`` above (the old $157k-style venue reconciliation). THE
    # profit readout Jin reads at a glance. ``virtual_seed_usd`` is $100k unless a
    # ruin re-seed fired (then the latest reseeded_to). ``virtual_weekly_curve`` is
    # a short recent-week end-of-day-ish equity trace for the sparkline (oldest→
    # newest; currently just [start, current] — the weekly row is the only
    # persisted history point). Never a venue call; never feeds sizing/gating.
    virtual_seed_usd: float = 100_000.0
    virtual_equity_usd: float = 100_000.0
    virtual_weekly_curve: list[float] = field(default_factory=list)


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
class BenchmarkTier:
    """One 3-tier benchmark verdict row (relative / risk_adjusted / statistical).

    ``baseline`` is the comparison baseline for relative-tier rows (empty for the
    aggregate risk/statistical tiers). ``sharpe_spread`` is bot − baseline Sharpe.
    Display-only — sourced from the offline ``benchmark_results`` read-model."""

    tier: str
    baseline: str
    sharpe_spread: float
    passed: bool
    note: str


@dataclass(slots=True)
class ReplayBenchmarkPanel:
    """Offline replay/benchmark run rollup for the EDGE tab — display-only.

    The most-recent deterministic replay run measured under the REAL OKX fee
    schedule on the baseline clock: real-fee-net pnl, per-trade Sharpe + spread
    vs each baseline, PSR / deflated-Sharpe, the NIG net-R CI band, the
    IS-vs-OOS overfit spread, and the 3-tier verdict. ``present=False`` when no
    run has been persisted (graceful zero). NEVER feeds trading."""

    present: bool = False
    run_id: str = ""
    n: int = 0
    net_pnl_real_fee: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    turnover: float = 0.0
    fee_drag_real_r: float = 0.0
    psr: float = 0.0
    deflated_sharpe: float = 0.0
    net_pnl_r_lcb: float = 0.0
    net_pnl_r_ucb: float = 0.0
    is_oos_spread: float = 0.0
    verdict: str = ""
    sharpe_spreads: dict[str, float] = field(default_factory=dict)
    tiers: list[BenchmarkTier] = field(default_factory=list)


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
    # P1 offline replay/benchmark run (display-only; graceful empty when none).
    replay: ReplayBenchmarkPanel = field(default_factory=ReplayBenchmarkPanel)


@dataclass(slots=True)
class GateKillValueRow:
    """One (gate_id × cohort/regime) counterfactual-value row for the EDGE tab.

    Sourced from ``gate_kill_value.compute_kill_value_hints`` (07-08 BUILD) —
    the ``gate_kill_counterfactuals`` (07-02) self-refreshing ``fwd_r_24h``
    aggregated per gate × regime. ``mean_killed_fwd_r`` / ``mean_passed_fwd_r``
    are fee-adjusted (``fwd_r_24h - cost_r``). ``separation`` =
    ``mean_passed_fwd_r - mean_killed_fwd_r`` (positive = the gate correctly
    discriminates). ``anti_edge=True`` flags ``mean_killed_fwd_r > 0`` — the
    gate killed signals that would have WON — a ``/debate`` CANDIDATE only;
    display-only, never wired to any live gate threshold."""

    gate_id: int
    cohort: str
    n_killed: int
    n_passed: int
    mean_killed_fwd_r: float
    mean_passed_fwd_r: float
    separation: float
    anti_edge: bool


@dataclass(slots=True)
class GateKillValuePanel:
    """G3/G4 gate-kill counterfactual value rollup — EDGE tab, /debate evidence.

    ``present=False`` when no ``(gate_id, cohort)`` group clears the
    stratified sample floor (graceful zero). ``auto_apply`` is always
    ``False`` — this panel can only ever be READ by a human / ``/debate``,
    never by a live gate/sizing/exit decision."""

    present: bool = False
    auto_apply: bool = False
    rows: list[GateKillValueRow] = field(default_factory=list)


@dataclass(slots=True)
class SinceResetRollup:
    """Forward-edge rollup since the LATEST measurement reset (Jin 2026-06-23).

    '실측 위해서 메인로직 바뀌면 pnl 리셋해서 측정해줘.' — the edge measured ONLY over
    trades OPENED at/after the latest ``measurement_resets.reset_ts`` (so a trade
    counts only if it was opened under the new logic; ``opened_ts``, not
    ``closed_ts``). All metrics are over the fills.$ truth (fee-net), excluding
    RECONCILED tracking-failure rows. ``net_usd`` / ``equity_change_usd`` are the
    realised fee-net change over the window; ``avg_r`` is the stream-common R.
    Display-only; the ALL-TIME panels stay available alongside this. The snapshot
    carries None when no reset has been stamped (clean all-time fallback)."""

    reset_ts: int
    label: str
    git_sha: str
    equity_baseline_usd: float
    n: int
    pf: float
    net_usd: float
    win_pct: float
    avg_r: float
    equity_change_usd: float
    # Hardening #7 (2026-06-23): close_reason × cadence split over the same
    # since-reset window — surfaces the bar-vs-tick thesis-cut asymmetry the
    # streak threading is gated on. Display/measurement-only.
    cadence_split: list[CadenceReasonRow] = field(default_factory=list)


@dataclass(slots=True)
class CadenceReasonRow:
    """One (close_reason × cadence) cell of the since-reset rollup (hardening #7).

    ``cadence`` is 'bar' / 'tick' / 'unknown' (legacy NULL ``exit_cadence``);
    ``close_reason`` is the lineage exit_reason (thesis_cut / atr_trail_stop /
    protected_bep / loser_timeout / ...). ``n`` = closed positions in the cell,
    ``net_usd`` = fee-net realised $. Measurement-only — never a trading path."""

    close_reason: str
    cadence: str
    n: int
    net_usd: float


@dataclass(slots=True)
class StrategySinceReset:
    """Per-strategy forward edge since the latest reset (display-only).

    Same window key as ``SinceResetRollup`` (``positions.opened_ts >= reset_ts``),
    keyed by ``strategy_id`` so the per-strategy table can show the new-logic edge
    next to the all-time ``StrategyStat`` row. Reconciled rows excluded."""

    strategy_id: str
    n: int
    wr_pct: float
    pf: float
    avg_r: float
    net_usd: float


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
class ContextIntelRow:
    """One alt-data / context input the bot's regime fuser sees — display-only.

    The CONTEXT/INTEL panel collects EVERY context input the bot weighs (funding,
    crypto fear&greed, FRED macro, CFTC positioning, and — when the news collector
    lands rows — news sentiment) into one Bloomberg-dense list so the operator sees
    "the bot's eyes" at a glance. Sourced read-only from the ``altdata_snapshot``
    audit table (the LATEST row per ``source``); NEVER feeds sizing/gating/exit.

    ``latest_value`` is a one-line human summary of the freshest payload;
    ``signal`` is the coarse direction (bullish / bearish / neutral) that input
    leans (read-only label, not a decision); ``age_sec`` + ``fresh`` are the
    freshness (fresh = within the source's own refresh window → green; else grey).
    """

    source: str
    asset_class: str
    latest_value: str
    signal: str
    age_sec: int
    fresh: bool


@dataclass(slots=True)
class DashboardSnapshot:
    ts_now: int = 0
    starting_capital: float = STARTING_CAPITAL
    # Day 9 F12 fix — venue-split starting equity (OKX SPOT + Capital CFD).
    starting_capital_okx: float = 0.0
    starting_capital_capital: float = 0.0
    # P0-2 (Jin 2026-07-02) — Alpaca display-baseline leg, so the header
    # starting_capital (== okx + capital + alpaca) reconciles to the 3-venue sum.
    starting_capital_alpaca: float = 0.0
    equity_now: float = STARTING_CAPITAL
    # Codex P0 fix: ``cash_now`` was mislabelled — it's actually deployed
    # notional across open positions, not free cash / margin headroom (we
    # have no venue balance probe in the snapshot path). We expose it as
    # ``exposed_usd`` and let the renderer label it ``EXPOSED$``.
    exposed_usd: float = 0.0
    upnl_total: float = 0.0
    # Mark-freshness note (Jin 2026-07-08 dashboard-live-net fix): a single
    # global label here was misleading — it copied ONLY the Alpaca lane's
    # staleness onto ``upnl_total`` (a 3-venue sum) on the false premise that
    # Alpaca was the ONLY venue that can go stale. Capital CFD (FX/indices/gold)
    # also closes on weekends (``polaris.core.sizing.session._capital_session``
    # already models this) and was silently excluded. Mark freshness is now
    # PER-VENUE ONLY, on ``StreamSummary.marks_label`` / ``marks_age_sec``
    # (``streams[]`` — set for whichever lane is actually stale) — no global
    # rollup field to keep aligned/misleading.
    # 'Today' — floored at max(session_start, latest AEST midnight) (P0-2, Jin
    # 2026-07-02) so this never spans more than ~24h even on multi-day uptime.
    daily_pnl_usd: float = 0.0
    daily_trades: int = 0
    # 'SESSION' — the whole-uptime sum (the pre-P0-2 ``daily_pnl_usd`` meaning),
    # preserved separately so no information is lost; rendered under its own
    # 'SESSION' label, never folded into the 'Today' KPI.
    session_pnl_usd: float = 0.0
    session_trades: int = 0
    drawdown_pct: float = 0.0
    peak_equity: float = STARTING_CAPITAL
    sharpe_24h: float = 0.0
    open_positions_n: int = 0
    active_cells_n: int = 0
    total_cells_n: int = 0
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
    ticker_stats: list[TickerStat] = field(default_factory=list)
    # Per-gate DECISION summary (replaces the pass/kill funnel — flow_not_block
    # makes a pass/kill ratio structurally ~100% / zero-information). One row per
    # gate G1-G8 carrying its characteristic meaningful output for the window.
    gate_decisions: list[GateDecisionRow] = field(default_factory=list)
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
    reconciled_realized_usd: float = 0.0
    reconciled_loss_usd: float = 0.0
    reconciled_loss_n: int = 0
    asset_class_fallback_n: int = 0
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
    # Live gate-event feed (newest first) — last ~20 per-gate decisions decoded
    # from gate_events. Consumed by the desktop board's gate-pathway view.
    # Additive; dataclasses.asdict serializes it. Graceful empty when absent.
    recent_gate_events: list[GateEvent] = field(default_factory=list)
    # Per-strategy one-line descriptions {strategy_id: desc}, extracted from the
    # vault strategy notes (vault/20_strategies/*.md). Display-only chrome the
    # desktop board pairs with each strategy row. Graceful empty when missing.
    strategy_descriptions: dict[str, str] = field(default_factory=dict)
    # ADR-012 — observe-mode probe events (generic {name, ticker, venue, kind,
    # lean, confidence, reading, action, ts, gate_id}) from the SEPARATE
    # data/probes.sqlite sidecar. Display-only connective tissue; read fail-open
    # (empty on a missing/locked sidecar). dataclasses.asdict serializes it for
    # the web snapshot. NEVER feeds sizing/gating/exit — pure board column.
    probe_events: list[dict[str, Any]] = field(default_factory=list)
    # Measurement-reset baseline (Jin 2026-06-23) — the FORWARD edge measured only
    # over trades OPENED at/after the latest measurement_resets.reset_ts (the new
    # main-logic window). ``since_reset`` is None when no reset has been stamped
    # (the board falls back to all-time cleanly); ``strategy_since_reset`` is the
    # per-strategy slice of the same window. Display-only; ALL-TIME panels stay
    # available. dataclasses.asdict serializes both for the web snapshot.
    since_reset: SinceResetRollup | None = None
    strategy_since_reset: list[StrategySinceReset] = field(default_factory=list)
    # CONTEXT/INTEL tab (Jin 2026-06-24) — every alt-data / context input the bot's
    # regime fuser weighs (funding · crypto fear&greed · FRED macro · CFTC COT ·
    # news sentiment when present), the LATEST row per source from the read-only
    # ``altdata_snapshot`` audit table, summarised to one display line each. This
    # is "the bot's eyes" surfaced for the operator. dataclasses.asdict serializes
    # it for the web snapshot. NEVER feeds sizing/gating/exit — pure board column.
    context_intel: list[ContextIntelRow] = field(default_factory=list)
    # VIRTUAL ACCOUNT mode banner (Jin 2026-07-07) — "is it making profit + why
    # is it quiet" at a glance. ``virtual_account_enabled`` mirrors
    # ``POLARIS_VIRTUAL_ACCOUNT=1`` (read once at snapshot build time, display
    # only — never re-derives sizing behavior). ``mode_banner`` is the one-line
    # plain-English label the board renders so the fresh $100k×3 virtual
    # measurement is never confused with the legacy real-venue equity ($157k
    # style) reconciliation, which is OFF in this mode. Never feeds trading.
    virtual_account_enabled: bool = False
    mode_banner: str = ""
    # VIRTUAL ledger main-board aggregates (Jin 2026-07-08 dashboard-live-net
    # fix) — since_reset/daily/session equivalents scoped to the fresh VIRTUAL
    # ledger (per-venue anchor via ``virtual_account_equity``, aggregated across
    # the 3 registered venues — ``snapshot_q_virtual``), NOT the unfiltered
    # fills-table scan the LEGACY ``daily_pnl_usd`` / ``session_pnl_usd`` /
    # ``since_reset`` fields above still are (those stay byte-identical,
    # LEGACY-tab-only). The main board (desktop header + mobile status strip)
    # reads these when ``virtual_account_enabled``. Never feeds sizing/gating.
    virtual_daily_pnl_usd: float = 0.0
    virtual_daily_trades: int = 0
    virtual_session_pnl_usd: float = 0.0
    virtual_session_trades: int = 0
    virtual_since_reset: SinceResetRollup | None = None
    # G3/G4 gate-kill counterfactual value panel (07-08 BUILD) — EDGE tab,
    # /debate evidence surface only (see GateKillValuePanel docstring).
    # dataclasses.asdict serializes it for the web snapshot. Graceful zero
    # (present=False) when no cohort clears the stratified sample floor.
    gate_kill_value: GateKillValuePanel = field(default_factory=GateKillValuePanel)
