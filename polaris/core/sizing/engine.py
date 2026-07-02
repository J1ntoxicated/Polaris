"""Layer 3 — T4 sizing engine (compose proposed_risk_pct + headroom min() clip).

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q1 placement, Q5 hard cap)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4 formula)

T4 chain (single ``min()`` clip — Q5 spec):

    proposed = base × continuous × tier × cell × listing_watchdog
    final    = min(proposed, single_trade_cap, per_symbol_remaining,
                   underlying_remaining, cluster_remaining, track_remaining,
                   venue_daily_remaining, total_daily_remaining)
    notional = final × equity × leverage(venue)

The ``binding_cap`` field on :class:`SizingFinal` tells the caller which input
clipped — used by audit log + post-trade reflector.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from dataclasses import dataclass

from polaris.core.cell_matrix import CellKeyP0
from polaris.core.learners.base import (
    NEUTRAL_MULT,
    clip_individual_mult,
    clip_product_mult,
    evaluate_triple_block,
)
from polaris.core.learners.regime import RegimeMultLearner
from polaris.core.learners.session import SessionMultLearner
from polaris.core.metrics.risk_unit import STOP_ATR_MULT, r_budget_for_venue
from polaris.core.regime_fit import regime_fit, regime_scalar
from polaris.core.sizing.amplifier import resolve_tier_amplifier
from polaris.core.sizing.cell_mult_application import resolve_cell_routing_mult
from polaris.core.sizing.cluster_cap import cluster_remaining_pct, resolve_cluster_id
from polaris.core.sizing.kelly import kelly_or_cold_start
from polaris.core.sizing.r_budget_sizer import (
    CapQtyInputs,
    compute_r_budget_qty,
    fold_strength_scalar,
    record_shadow_observation,
    resolve_sizing_mode,
    stop_dist_usd,
)
from polaris.core.sizing.schema import (
    CONT_SCALAR_MAX,
    CONT_SCALAR_MIN,
    DEFAULT_BASE_RISK_PCT,
    LISTING_WATCHDOG_AGE_HOURS,
    LISTING_WATCHDOG_MULT,
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
    PortfolioState,
    PositionRiskState,
    SizingFinal,
    SizingProposal,
    StrategyRiskState,
    Track,
    equity_shadow_cap_pct,
    notional_hard_cap_usd,
    per_symbol_cfd_pct,
    per_symbol_equity_pct,
    per_symbol_spot_pct,
    target_vol,
    total_daily_risk_ceiling_pct,
    track_a_daily_venue_pct,
    track_a_gross_pct,
    track_b_daily_venue_pct,
    track_b_gross_pct,
    track_c_daily_venue_pct,
    track_c_gross_pct,
    underlying_group_pct,
)
from polaris.core.sizing.session import resolve_venue_session
from polaris.core.sizing.vol_target import ewma_realized_vol, vol_targeted_scalar
from polaris.core.streams import resolve_stream

logger = logging.getLogger(__name__)

__all__ = [
    "EQUITY_SHADOW_CAP_STRATEGIES",
    "SignalIntent",
    "compute_proposed",
    "compute_size",
    "continuous_scalar",
    "equity_shadow_validation_cap",
    "ewma_realized_vol",
    "headroom_min",
    "venue_per_symbol_cap",
    "vol_targeted_scalar",
]


@dataclass(frozen=True, slots=True)
class SignalIntent:
    """Input bundle for :func:`compute_size`.

    All fields are deterministic at the moment of sizing — this dataclass is
    the **single carrier** between Validator/Watcher and the T4 engine.
    """

    signal_id: str
    venue: str
    symbol: str
    instrument_id: str
    underlying_group_id: str
    asset_class: str
    strategy: str
    track: Track
    regime: str
    direction: str
    signal_strength: float  # raw [0, 2.0] – higher = stronger
    listing_age_hours: float
    leverage: float = 1.0
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT
    session: str | None = None  # None → derive_session(now_ts) at sizing time
    # Ex-ante (past-only) realized vol → vol-targeted scalar; None → legacy
    # strength ramp. Never a forward/look-ahead value. See vol_target.py.
    realized_vol: float | None = None
    # Stream routing dimensions (design §2.2). Default "" so existing callers
    # stay source-compatible; they carry the resolved StreamConfig identity
    # alongside the legacy ``venue``/``track`` fields — purely descriptive, not
    # a sizing multiplier (9-stack chain untouched).
    product_class: str = ""
    stream_id: str = ""
    # Signal family (momentum / reversion) for regime-fit conviction shaping.
    # Default "momentum" (the trend-following majority); the bar path derives it
    # from the correlation-group bucket, the tick path threads TickIntent.family.
    # Folded into the SAME single continuous scalar via regime_scalar (seam1) —
    # NOT a new multiplier (9-stack count unchanged).
    signal_family: str = "momentum"
    # #32 axis-C SIZE_UP conviction. When the A+B-gated entry judge emits SIZE_UP
    # (robust + active), the entry sizer threads a >1.0 boost here; it is folded
    # into the SAME single continuous scalar then re-clamped to [MIN, MAX] — exactly
    # the regime_scalar precedent (NOT a new T4 multiplier; the 9-stack count is
    # unchanged). Default 1.0 = byte-identical (no SIZE_UP / shadow / absent intent).
    # flow_not_block: it can only push the scalar toward its band ceiling, never cut.
    judge_conviction: float = 1.0
    # Wave B agenda ② (vault/50_research/debates/waveB_sizing_params_2026-07-02.md):
    # G3 signal_validator's MODIFY strength_scalar in [0.5, 1.5]. Folded into the
    # SAME single continuous scalar via fold_strength_scalar (clamp ONCE on the
    # pre-clip product — NOT a new T4 multiplier slot). Default 1.0 = byte-identical
    # (absent / non-MODIFY / stale-cycle). Valid only within the SAME decision
    # cycle that produced it — callers must not carry a stale value forward.
    strength_scalar: float = 1.0
    # Wave B agenda ① R-budget shadow-compute inputs (waveB_sizing_params_2026-07-02.md).
    # Both default 0.0 — the R-budget qty shadow path treats <=0 as "not supplied"
    # (data-integrity fallback: skip shadow-compute, log the fallback, existing
    # %-of-equity path is UNCHANGED). Callers that don't thread these stay
    # byte-identical (no shadow observation recorded for them).
    entry_price: float = 0.0
    atr_pct: float = 0.0
    # Fee-floored R-unit ATR multiplier (risk_unit.py STOP_ATR_MULT distance),
    # the SAME per-strategy value ``_stop_atr_mult_for_strategy`` resolves —
    # threaded in by the scripts-layer caller (core must not import scripts;
    # test_core_layering.py) since only that layer has both the ATR% and the
    # strategy-registry context to resolve the fee floor. Default 0.0 → the
    # shadow wire falls back to the plain module ``STOP_ATR_MULT`` (2.0)
    # default, never the fee-aware floor — callers that care about the floor
    # MUST thread the real value.
    stop_atr_mult: float = 0.0


# ---------------------------------------------------------------------------
# Continuous scalar (anti-collapse — Phase 0 L3)
# ---------------------------------------------------------------------------


def continuous_scalar(signal_strength: float) -> float:
    """Map signal_strength to ``[0.75, 1.50]`` linearly.

    - strength = 1.0 (baseline) → 1.0
    - strength ≤ 0.5 → 0.75
    - strength ≥ 1.5 → 1.50

    Non-finite or negative input returns the lower bound (anti-collapse —
    never produce 0 unless caller explicitly cuts the signal upstream).
    """
    if not math.isfinite(signal_strength) or signal_strength <= 0.0:
        return CONT_SCALAR_MIN
    # Linear interp anchored at 1.0 → 1.0; slope chosen so 0.5→0.75 and 1.5→1.5.
    if signal_strength <= 0.5:
        return CONT_SCALAR_MIN
    if signal_strength >= 1.5:
        return CONT_SCALAR_MAX
    if signal_strength <= 1.0:
        # 0.5 → 0.75, 1.0 → 1.0 (slope 0.5 over 0.5 = 1.0).
        return 0.75 + (signal_strength - 0.5) * 0.5
    # 1.0 → 1.0, 1.5 → 1.5 (slope 1.0 over 0.5 = 1.0).
    return 1.0 + (signal_strength - 1.0) * 1.0


# ---------------------------------------------------------------------------
# Listing watchdog
# ---------------------------------------------------------------------------


def listing_watchdog_mult(listing_age_hours: float) -> float:
    """Return 0.5 if listing is younger than 24h, else 1.0."""
    if not math.isfinite(listing_age_hours):
        return 1.0
    if listing_age_hours < float(LISTING_WATCHDOG_AGE_HOURS):
        return LISTING_WATCHDOG_MULT
    return 1.0


# ---------------------------------------------------------------------------
# Per-venue caps
# ---------------------------------------------------------------------------


def venue_per_symbol_cap(venue: str, product_class: str | None = None) -> float:
    """Per-symbol % cap for the venue (env-overridable; high default).

    The spot/cfd/equity decision routes through the StreamConfig SSOT
    (``product_class``) instead of a venue literal, but the cap *value* is still
    read live from the env-aware ``per_symbol_*_pct`` knobs (POLARIS_CAP_*) — the
    registry holds no cached cap. Behavior is identical for A/B: capital→cfd cap,
    every other (incl. unknown) venue→spot cap (resolve_stream KeyError → spot
    fallback preserves the prior default branch). Track C (equity) returns the
    equity per-symbol cap. ``product_class`` may be passed explicitly to reach
    the equity branch before the C stream is registered (T11).
    """
    if product_class is None:
        try:
            product_class = resolve_stream(venue).product_class
        except KeyError:
            product_class = "spot"
    if product_class == "cfd":
        return per_symbol_cfd_pct()
    if product_class == "equity":
        return per_symbol_equity_pct()
    return per_symbol_spot_pct()


def track_gross_cap(track: Track) -> float:
    if track == "A":
        return track_a_gross_pct()
    elif track == "C":
        return track_c_gross_pct()
    return track_b_gross_pct()


def track_daily_cap(track: Track) -> float:
    if track == "A":
        return track_a_daily_venue_pct()
    elif track == "C":
        return track_c_daily_venue_pct()
    return track_b_daily_venue_pct()


# The two UNVALIDATED daily-equity strategies (equity-gate-relax). Their feed-gate
# was relaxed so they trade live on yfinance daily signal; a small shadow cap
# bounds the unvalidated bleed while the edge is verified. Membership is explicit
# (not a venue/asset_class wildcard) so the cap can NEVER reach an edge strategy —
# this is allocation sizing of new strategies, not a defensive dampen.
EQUITY_SHADOW_CAP_STRATEGIES: frozenset[str] = frozenset(
    {"equity_vol_expansion_pocket_pivot", "equity_52wk_high_breakout"}
)


def equity_shadow_validation_cap(strategy: str) -> float:
    """Per-trade %-of-equity ceiling for the unvalidated daily-equity strategies.

    Returns the small ``equity_shadow_cap_pct()`` for the two shadow-validation
    equity strategies, else ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` (a no-op — every
    other strategy keeps the existing absolute ceiling). Folded into the SAME
    ``headroom_min`` single-trade slot as the absolute ceiling via ``min()`` — it
    is NOT a T4 chain multiplier (9-stack / tier / cell / -1.0R rail untouched).
    flow_not_block: the signal still flows and trades, just bounded small until the
    edge clears (Jin lifts the env to full size)."""
    if strategy in EQUITY_SHADOW_CAP_STRATEGIES:
        return equity_shadow_cap_pct()
    return SINGLE_TRADE_ABSOLUTE_CEILING_PCT


def notional_ceiling_pct(*, venue: str, equity_usd: float, leverage: float) -> float:
    """Venue notional hard cap (USD), expressed as %-of-equity for the SAME
    ``headroom_min`` single-trade min() slot (P0-3 fix — replaces the silent
    post-hoc ``max(10, min(x, 5_000))`` dollar clamp formerly applied AFTER
    T4 in ``_production_run_signal.py``, which clipped an already-correct
    ``final_notional_usd`` and made the continuous scalar / tier amplifier
    inert above $5k). ``notional = final_risk_pct × equity × leverage``, so
    dividing the dollar cap by ``equity × leverage`` yields the equivalent
    %-of-equity ceiling that composes inside the single min() clip instead of
    fighting it — no new T4 multiplier, 9-stack ban intact.

    A non-positive ``equity_usd × leverage`` denominator (never expected on the
    live path) returns 0.0 so the term never explodes into an unbounded cap.
    """
    denom = equity_usd * leverage
    if denom <= 0.0:
        return 0.0
    return notional_hard_cap_usd(venue) / denom


# ---------------------------------------------------------------------------
# Wave B agenda ① — R-budget qty shadow wire
# (vault/50_research/debates/waveB_sizing_params_2026-07-02.md)
#
# Runs the qty-aimed R-budget compute IN PARALLEL to the %-of-equity T4 chain
# above — SHADOW mode logs + persists an observation but never alters
# ``final_risk_pct`` / ``notional``; ACTIVE mode (only reached once the
# shadow-verify gate has flipped, or via the operator env override) makes the
# R-budget qty PRIMARY and demotes every existing exposure cap to a
# qty-converted min() term. Data-integrity fallback (ATR zero/stale/absent
# entry_price) ALWAYS falls through to the existing %-of-equity result
# unchanged, in both modes — this wire never blocks an order.
# ---------------------------------------------------------------------------


def _cap_qty_inputs_from_pcts(
    *,
    single_trade_cap_pct: float,
    per_symbol_remaining_pct: float,
    margin_qty_basis_pct: float,
    equity_usd: float,
    leverage: float,
    entry_price: float,
) -> CapQtyInputs:
    """Convert the SAME %-of-equity caps already resolved in ``compute_size``
    into qty ceilings — ``qty = pct × equity × leverage / entry_price``, the
    identical basis ``notional_ceiling_pct`` composes on. No new cap concept:
    this only re-expresses caps already computed for the %-chain in units.
    """
    basis = equity_usd * leverage
    def _to_qty(pct: float) -> float:
        return (pct * basis) / entry_price
    return CapQtyInputs(
        single_trade_qty=_to_qty(single_trade_cap_pct),
        per_symbol_qty=_to_qty(per_symbol_remaining_pct),
        margin_qty=_to_qty(margin_qty_basis_pct),
    )


def _run_r_budget_shadow(
    conn: sqlite3.Connection,
    *,
    intent: SignalIntent,
    proposal: SizingProposal,
    single_trade_cap_pct: float,
    per_symbol_remaining: float,
    portfolio: PortfolioState,
    ts: int,
) -> float | None:
    """Shadow/active R-budget qty compute.

    SHADOW (default): computes + logs + records an observation but returns
    ``None`` — the caller's %-equity ``final_notional_usd`` is UNCHANGED.
    ACTIVE (only once the shadow-verify gate has flipped, or via operator env
    override): returns the R-budget qty result converted to notional USD
    (``qty_after_caps × entry_price``) — the caller substitutes this as
    PRIMARY, demoting the %-equity caps below it (per the debate spec, this
    is the ONE swap point; no new multiplier stack).

    Data-integrity fallback (never a sizing judgment, never active in this
    branch): ``entry_price``/``atr_pct`` <= 0, non-positive ``base_risk_pct``,
    or stale/zero ATR (``compute_r_budget_qty`` → ``None``) → returns ``None``
    in EITHER mode, so the existing %-equity path always wins on a data gap.
    """
    if intent.entry_price <= 0.0 or intent.atr_pct <= 0.0:
        logger.info(
            "[T4/rbudget-shadow] %s/%s sid=%s SKIP data-integrity-fallback "
            "entry_price=%.6f atr_pct=%.6f (existing %%-equity path unchanged)",
            intent.venue, intent.symbol, intent.signal_id,
            intent.entry_price, intent.atr_pct,
        )
        return None
    if intent.base_risk_pct <= 0.0:
        return None

    # ``intent.stop_atr_mult`` is threaded by the scripts-layer caller (the ONLY
    # layer with both the ATR% and the strategy-registry context to resolve the
    # fee-aware floor via ``_stop_atr_mult_for_strategy`` — core must not import
    # scripts, test_core_layering.py). <=0 (not supplied) falls back to the
    # plain module default — still correct (never a crash), just not
    # fee-floor-aware for callers that haven't threaded it yet.
    stop_atr_mult = intent.stop_atr_mult if intent.stop_atr_mult > 0.0 else STOP_ATR_MULT
    t4_mult = proposal.proposed_risk_pct / intent.base_risk_pct
    # SSOT R_budget (risk_unit.py Step N) = BASE_RISK_PCT(0.02) × venue starting
    # equity — the SAME per-venue equity resolver this repo already treats as
    # canonical (realised_r_stream's denominator), avoiding a second competing
    # "2%-of-equity" definition.
    r_budget_usd = r_budget_for_venue(intent.venue)
    if r_budget_usd <= 0.0:
        logger.info(
            "[T4/rbudget-shadow] %s/%s sid=%s SKIP unknown-venue-r-budget "
            "(existing %%-equity path unchanged)",
            intent.venue, intent.symbol, intent.signal_id,
        )
        return None
    caps = _cap_qty_inputs_from_pcts(
        single_trade_cap_pct=single_trade_cap_pct,
        per_symbol_remaining_pct=per_symbol_remaining,
        margin_qty_basis_pct=single_trade_cap_pct,
        equity_usd=portfolio.equity_usd,
        leverage=intent.leverage,
        entry_price=intent.entry_price,
    )
    result = compute_r_budget_qty(
        r_budget_usd=r_budget_usd,
        t4_mult=t4_mult,
        entry_price=intent.entry_price,
        atr_pct=intent.atr_pct,
        stop_atr_mult=stop_atr_mult,
        caps=caps,
    )
    if result is None:
        logger.info(
            "[T4/rbudget-shadow] %s/%s sid=%s SKIP stop_dist<=0 "
            "(stale/zero ATR — existing %%-equity path unchanged)",
            intent.venue, intent.symbol, intent.signal_id,
        )
        return None

    mode = resolve_sizing_mode(conn)
    legacy_qty = (
        (proposal.proposed_risk_pct * portfolio.equity_usd * intent.leverage) / intent.entry_price
    )
    logger.info(
        "[T4/rbudget-shadow] %s/%s sid=%s mode=%s legacy_qty=%.6f rbudget_qty=%.6f "
        "unclipped=%.6f binding=%s target_realization=%.4f CAP_DOMINATED=%s "
        "r_budget_usd=%.2f t4_mult=%.4f stop_dist_usd=%.6f",
        intent.venue, intent.symbol, intent.signal_id, mode,
        legacy_qty, result.qty_after_caps, result.qty_unclipped, result.binding_cap,
        result.realization, result.cap_dominated, r_budget_usd, t4_mult,
        stop_dist_usd(
            entry_price=intent.entry_price, atr_pct=intent.atr_pct, stop_atr_mult=stop_atr_mult,
        ),
    )
    if mode == "off":
        return None
    record_shadow_observation(conn, now_ts=ts)
    if mode == "active":
        return max(0.0, result.qty_after_caps * intent.entry_price)
    return None


# ---------------------------------------------------------------------------
# Compose proposed (T4 numerator — pre-cap)
# ---------------------------------------------------------------------------


def compute_proposed(
    *,
    base_risk_pct: float,
    continuous: float,
    tier_amp: float,
    cell_mult: float,
    listing_mult: float = 1.0,
    session_mult: float = 1.0,
    regime_mult: float = 1.0,
    triple_block_mult: float = 1.0,
) -> SizingProposal:
    """Pure compositional T4 step. No clipping happens on base/tier/cell.

    L5 learner mults (session/regime/triple_block) are each individually clipped
    to ``LEARNER_INDIVIDUAL_MULT_CLIP`` and their product is re-clipped to
    ``LEARNER_PRODUCT_CLIP`` — guards against 9-stack collapse and runaway top.
    Aggressive top (3.0×) on base/tier/cell is preserved; the hard MAX
    ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` + ``headroom_min`` still cap downstream.

    Negative or non-finite base/continuous/tier/cell/listing → ``ValueError``.
    """
    if not all(math.isfinite(x) for x in (base_risk_pct, continuous, tier_amp, cell_mult, listing_mult)):
        raise ValueError("compute_proposed: non-finite multiplier")
    if any(x < 0.0 for x in (base_risk_pct, continuous, tier_amp, cell_mult, listing_mult)):
        raise ValueError("compute_proposed: negative multiplier")
    s_clip = clip_individual_mult(session_mult)
    r_clip = clip_individual_mult(regime_mult)
    t_clip = clip_individual_mult(triple_block_mult)
    learner_product = clip_product_mult(s_clip * r_clip * t_clip)
    proposed = (
        base_risk_pct * continuous * tier_amp * cell_mult * listing_mult * learner_product
    )
    return SizingProposal(
        base_risk_pct=base_risk_pct,
        continuous_scalar=continuous,
        tier_amplifier=tier_amp,
        cell_routing_mult=cell_mult,
        listing_watchdog_mult=listing_mult,
        proposed_risk_pct=proposed,
        session_mult=s_clip,
        regime_mult=r_clip,
        triple_block_mult=t_clip,
    )


# ---------------------------------------------------------------------------
# Headroom min() composer (single clip — Q5 spec)
# ---------------------------------------------------------------------------


def headroom_min(
    *,
    proposed_risk_pct: float,
    single_trade_cap: float,
    per_symbol_remaining: float,
    underlying_remaining: float | None,
    cluster_remaining: float | None,
    track_remaining: float,
    venue_daily_remaining: float,
    total_daily_remaining: float,
) -> tuple[float, str]:
    """Single-pass ``min()`` over proposed + every available cap.

    Returns ``(final_risk_pct, binding_cap_name)``. ``binding_cap`` is the
    string name of the lowest constraint (audit field; if multiple ties, the
    earlier one in the spec priority order wins).

    Audit priority (Q5):
        single → per-symbol → underlying → cluster → track → venue → total
    None values for underlying / cluster mean "not configured" — they do not
    participate in the min().
    """
    candidates: list[tuple[str, float]] = [
        ("proposed", proposed_risk_pct),
        ("single_trade", single_trade_cap),
        ("per_symbol", per_symbol_remaining),
        ("track", track_remaining),
        ("venue_daily", venue_daily_remaining),
        ("total_daily", total_daily_remaining),
    ]
    if underlying_remaining is not None:
        candidates.insert(3, ("underlying", underlying_remaining))
    if cluster_remaining is not None:
        candidates.insert(4 if underlying_remaining is not None else 3,
                          ("cluster", cluster_remaining))

    final_name, final_val = min(candidates, key=lambda c: c[1])
    return max(0.0, final_val), final_name


# ---------------------------------------------------------------------------
# Per-symbol remaining (vs open positions)
# ---------------------------------------------------------------------------


def per_symbol_remaining_pct(
    *,
    venue: str,
    symbol: str,
    open_positions: list[PositionRiskState],
) -> float:
    cap = venue_per_symbol_cap(venue)
    used = sum(p.open_risk_pct for p in open_positions if p.venue == venue and p.symbol == symbol)
    return max(0.0, cap - used)


def underlying_remaining_pct(
    *,
    underlying_group_id: str,
    open_positions: list[PositionRiskState],
    cap_pct: float | None = None,
) -> float:
    cap = underlying_group_pct() if cap_pct is None else cap_pct
    used = sum(p.open_risk_pct for p in open_positions if p.underlying_group_id == underlying_group_id)
    return max(0.0, cap - used)


# ---------------------------------------------------------------------------
# Top-level compose (T4 main entry)
# ---------------------------------------------------------------------------


def compute_size(
    conn: sqlite3.Connection,
    *,
    intent: SignalIntent,
    risk_state: StrategyRiskState,
    portfolio: PortfolioState,
    now_ts: int | None = None,
) -> SizingFinal:
    """Full T4 sizing for one validated signal.

    Steps:
    1. Resolve continuous_scalar from ``signal_strength``.
    2. Resolve tier_amplifier from ``StrategyRiskState`` (win_streak / hit / n).
    3. Resolve cell_routing_mult from Layer 4 SSOT.
    4. Resolve listing_watchdog_mult from ``listing_age_hours``.
    5. Compose proposed (compute_proposed).
    6. Resolve Kelly + Cold-Start single cap.
    7. Resolve per-symbol / underlying / cluster / track / venue / total
       remaining caps from PortfolioState.
    8. Apply ``headroom_min`` for the single clip.
    9. Compute notional = final_risk_pct × equity × leverage.

    Always finite, always ``≤ SINGLE_TRADE_ABSOLUTE_CEILING_PCT × equity``.
    """
    ts = now_ts if now_ts is not None else int(time.time())

    # (1) continuous scalar — vol-aware when ex-ante realized_vol is supplied,
    # else the legacy signal-strength ramp (backward compat). Still ONE scalar
    # in the chain (no new multiplier — 9-stack ban preserved).
    if intent.realized_vol is not None:
        cont = vol_targeted_scalar(
            realized_vol=intent.realized_vol, target_vol=target_vol()
        )
    else:
        cont = continuous_scalar(intent.signal_strength)

    # (1b)+(1c)+(1d) regime-fit / #32 axis-C judge_conviction / Wave B agenda ②
    # strength_scalar — THREE shaping folds into the SAME single continuous
    # scalar (NOT new multipliers; the 9-stack count is unchanged), combined as
    # ONE raw pre-clip product and clamped to [CONT_SCALAR_MIN, CONT_SCALAR_MAX]
    # EXACTLY ONCE at the end via fold_strength_scalar. Clamping after each fold
    # individually (as (1b) and (1c) used to) and THEN multiplying the next
    # factor onto the already-clamped result loses information asymmetrically at
    # the 0.75/1.50 boundary — the Wave B R2 BREAK #2 pathology
    # (waveB_sizing_params_2026-07-02.md) fold_strength_scalar's own contract
    # forbids ("raw_cont_preclip MUST be the pre-clip value, before any existing
    # clamp is applied"). Good (family × regime) fit / SIZE_UP conviction / a
    # >1.0 strength_scalar all push the raw product up; a bad fit / weak
    # strength_scalar shrinks it — the single trailing clamp is the only floor/
    # ceiling (never 0 — anti-collapse; flow_not_block). Defaults (fit=0 →
    # regime_scalar=1.0, judge_conviction=1.0, strength_scalar=1.0) reproduce
    # the pre-Wave-B byte-identical chain.
    _rf_cont_pre = cont
    _rf_fit = regime_fit(intent.signal_family, intent.regime)
    _rf_scalar = regime_scalar(_rf_fit)
    raw_cont_preclip = cont * _rf_scalar * intent.judge_conviction
    cont = fold_strength_scalar(raw_cont_preclip, strength_scalar=intent.strength_scalar)
    logger.info(
        "[regime-fit/seam1-size] sym=%s strat=%s family=%s regime=%s "
        "fit=%+.2f scalar=%.3f cont %.4f->%.4f (ONE-scalar fold, 9-stack intact)",
        intent.symbol,
        intent.strategy,
        intent.signal_family,
        intent.regime,
        _rf_fit,
        _rf_scalar,
        _rf_cont_pre,
        cont,
    )

    # (2) tier amplifier
    tier_amp = resolve_tier_amplifier(
        win_streak=risk_state.win_streak,
        n_closed=risk_state.closed_trades,
        hit_rate_10=risk_state.hit_rate_10,
    )
    amplifier_on = tier_amp > 1.0

    # (3) cell mult
    cell_mult = resolve_cell_routing_mult(
        conn,
        CellKeyP0(
            exchange=intent.venue,
            strategy=intent.strategy,
            ticker=intent.symbol,
            regime=intent.regime,
        ),
        now_ts=ts,
    )

    # (4) listing watchdog
    listing_mult = listing_watchdog_mult(intent.listing_age_hours)

    # (4.5) L5 learner mults — session × regime × triple_block
    # Aggressive bias preserved: sparse / disabled / no row → NEUTRAL_MULT (1.0).
    # Each mult individually clipped by BaseLearner.get_mult; product re-clip in
    # compute_proposed.
    # Venue-native session (D2): the lookup key MUST match the key the close
    # path records (resolve_venue_session) — otherwise session_mult never finds
    # its learned row and stays at the neutral floor. An explicit intent.session
    # still wins for callers that pre-resolve it.
    session_label = (
        intent.session
        if intent.session
        else resolve_venue_session(intent.venue, ts)
    )
    session_learner = SessionMultLearner(conn)
    regime_learner = RegimeMultLearner(conn)
    session_mult = session_learner.get_mult(
        ticker=intent.symbol,
        strategy_id=intent.strategy,
        regime=intent.regime,
        session=session_label,
    ).value
    regime_mult = regime_learner.get_mult(
        ticker=intent.symbol,
        strategy_id=intent.strategy,
        regime=intent.regime,
        session=session_label,
    ).value
    block = evaluate_triple_block(
        conn,
        ticker=intent.symbol,
        strategy_id=intent.strategy,
        regime=intent.regime,
        now_ts=ts,
    )
    triple_block_mult = block.size_mult if block else NEUTRAL_MULT

    # (5) proposed
    proposal = compute_proposed(
        base_risk_pct=intent.base_risk_pct,
        continuous=cont,
        tier_amp=tier_amp,
        cell_mult=cell_mult,
        listing_mult=listing_mult,
        session_mult=session_mult,
        regime_mult=regime_mult,
        triple_block_mult=triple_block_mult,
    )

    # (6) Kelly + Cold-Start single cap
    decision = kelly_or_cold_start(
        n_closed=risk_state.closed_trades,
        p=risk_state.kelly_p,
        q=risk_state.kelly_q,
        amplifier_on=amplifier_on,
    )
    single_cap_pct = decision.single_cap_pct

    # (7) Headroom inputs
    per_symbol_remaining = per_symbol_remaining_pct(
        venue=intent.venue, symbol=intent.symbol, open_positions=portfolio.open_positions,
    )
    underlying_remaining = underlying_remaining_pct(
        underlying_group_id=intent.underlying_group_id, open_positions=portfolio.open_positions,
    )
    cluster_id = resolve_cluster_id(
        underlying_group_id=intent.underlying_group_id,
        asset_class=intent.asset_class,
        symbol=intent.symbol,
    )
    cluster_rem = cluster_remaining_pct(
        cluster_id=cluster_id, open_positions=portfolio.open_positions,
    )
    track_rem = max(0.0, track_gross_cap(intent.track) - portfolio.track_used_pct.get(intent.track, 0.0))
    venue_daily_cap = track_daily_cap(intent.track)
    venue_daily_rem = max(0.0, venue_daily_cap - portfolio.venue_daily_used_pct)
    total_daily_rem = max(0.0, total_daily_risk_ceiling_pct() - portfolio.total_daily_used_pct)

    # (8) Single clip
    # flow_not_block: weak signals flow at their normal computed size. The only
    # containment is the headroom_min budget caps (cluster/daily/track) below —
    # there is NO per-signal weak-signal zeroing (removed; defensive throttle).
    # The single-trade slot folds in (a) the Kelly/cold-start cap, (b) the hard
    # absolute ceiling, (c) the equity shadow validation cap (a no-op for every
    # non-equity-validation strategy), and (d) the venue notional hard cap
    # (P0-3 fix — replaces the old post-hoc dollar clamp; see
    # ``notional_ceiling_pct`` docstring). All four are pure min() terms in the
    # SAME slot — no new T4 multiplier, 9-stack ban + -1.0R rail intact.
    notional_cap_pct = notional_ceiling_pct(
        venue=intent.venue, equity_usd=portfolio.equity_usd, leverage=intent.leverage,
    )
    single_trade_sub_terms: list[tuple[str, float]] = [
        ("kelly_cold_start", single_cap_pct),
        ("absolute_ceiling", SINGLE_TRADE_ABSOLUTE_CEILING_PCT),
        ("equity_shadow_cap", equity_shadow_validation_cap(intent.strategy)),
        ("notional_hard_cap", notional_cap_pct),
    ]
    single_trade_name, single_trade_cap = min(single_trade_sub_terms, key=lambda t: t[1])
    final_risk_pct, binding = headroom_min(
        proposed_risk_pct=proposal.proposed_risk_pct,
        single_trade_cap=single_trade_cap,
        per_symbol_remaining=per_symbol_remaining,
        underlying_remaining=underlying_remaining,
        cluster_remaining=cluster_rem,
        track_remaining=track_rem,
        venue_daily_remaining=venue_daily_rem,
        total_daily_remaining=total_daily_rem,
    )
    if binding == "single_trade":
        # Structured binding log (P0-3 test requirement): name the SPECIFIC
        # sub-term that clipped inside the single-trade slot, not just the
        # opaque "single_trade" audit string — makes the venue notional hard
        # cap (and every sibling sub-term) visible in the log, unlike the old
        # silent post-hoc clamp it replaces.
        logger.info(
            "[T4/single-trade-clip] %s/%s sid=%s binding=%s cap_pct=%.4f "
            "notional_hard_cap_usd=%.2f",
            intent.venue, intent.symbol, intent.signal_id, single_trade_name,
            single_trade_cap, notional_hard_cap_usd(intent.venue),
        )

    # (9) notional
    notional = final_risk_pct * portfolio.equity_usd * intent.leverage
    logger.info(
        "[T4] %s/%s sid=%s strat=%s base=%.4f cont=%.2f tier=%.1fx cell=%.1fx "
        "list=%.1fx ses=%.2f reg=%.2f blk=%.2f → proposed_pct=%.4f "
        "single_cap=%.4f → final_pct=%.4f (binding=%s) notional=%.2f USD "
        "lev=%.1f kelly=%.4f cold=%s",
        intent.venue,
        intent.symbol,
        intent.signal_id,
        intent.strategy,
        intent.base_risk_pct,
        cont,
        tier_amp,
        cell_mult,
        listing_mult,
        proposal.session_mult,
        proposal.regime_mult,
        proposal.triple_block_mult,
        proposal.proposed_risk_pct,
        single_cap_pct,
        final_risk_pct,
        binding,
        notional,
        intent.leverage,
        decision.kelly_fraction,
        decision.cold_start,
    )

    # Wave B agenda ① — R-budget qty shadow/active wire (never runs when the
    # %-equity path already zeroed out; nothing to compare a dead signal
    # against, and it would just log noise on every KILL).
    if notional > 0.0:
        rbudget_notional = _run_r_budget_shadow(
            conn,
            intent=intent,
            proposal=proposal,
            single_trade_cap_pct=single_trade_cap,
            per_symbol_remaining=per_symbol_remaining,
            portfolio=portfolio,
            ts=ts,
        )
        if rbudget_notional is not None:
            # ACTIVE mode: R-budget qty is PRIMARY — swap the notional/risk_pct
            # output to the qty-aimed value (the ONE swap point; no new T4
            # multiplier stack, the pre-cap ``proposal`` audit trail is
            # unchanged for attribution).
            notional = rbudget_notional
            final_risk_pct = (
                notional / (portfolio.equity_usd * intent.leverage)
                if portfolio.equity_usd * intent.leverage > 0.0
                else 0.0
            )
            binding = "r_budget_active"

    return SizingFinal(
        proposed=proposal,
        final_risk_pct=final_risk_pct,
        final_notional_usd=notional,
        leverage=intent.leverage,
        binding_cap=binding,
    )
