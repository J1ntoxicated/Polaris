"""Layer 3 — Sizing dataclasses + constants (T4 formula building blocks).

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q1 placement, Q2 CS-3, Q3 cluster, Q5 hard cap)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4 formula + caps)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final, Literal

from polaris.core.classes.r_pool import TrackMember

# ---------------------------------------------------------------------------
# Constants (P0)
# ---------------------------------------------------------------------------

KELLY_FRACTION_K: Final[float] = 0.50
"""Fractional Kelly multiplier (ADR-005 — k=0.5)."""

CS3_N_THRESHOLD: Final[int] = 20
"""Closed trades < this → Cold-Start mode CS-3 (Kelly off, single 6%/7%)."""

CS3_SINGLE_DEFAULT_PCT: Final[float] = 0.06
"""Cold-start single-trade cap (no amplifier)."""

CS3_SINGLE_AMPLIFIED_PCT: Final[float] = 0.07
"""Cold-start single-trade cap (amplifier on)."""

SINGLE_TRADE_DEFAULT_PCT: Final[float] = 0.08
"""ADR-005 max single-trade risk (default)."""

SINGLE_TRADE_AMPLIFIED_PCT: Final[float] = 0.09
"""ADR-005 max single-trade risk (amplifier on)."""

SINGLE_TRADE_ABSOLUTE_CEILING_PCT: Final[float] = 0.09
"""ADR-005 absolute single-trade ceiling (hard MAX)."""

# ---------------------------------------------------------------------------
# Structural %-of-equity caps (DEMO/PAPER data-collection — AGGRESSIVE bias).
#
# These bound the *risk budget*; the number of concurrent open positions is only
# limited indirectly when a budget is exhausted. For DEMO data collection the
# defaults are set HIGH (~0.99/1.00) so they no longer bind at low position
# counts — the bot can open many concurrent positions. They remain finite
# members of the T4 single ``headroom_min()`` clip (no cap removed, no 9-stack);
# the hard-MAX ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` is the per-trade backstop.
#
# Every cap is env-overridable via the ``POLARIS_CAP_*`` vars resolved by the
# ``*_pct()`` helpers below (operator can dial a tighter budget without code
# change).
# ---------------------------------------------------------------------------

PER_SYMBOL_SPOT_PCT: Final[float] = 0.99
"""Per-symbol cap, OKX SPOT (env: ``POLARIS_CAP_PER_SYMBOL_SPOT_PCT``)."""

PER_SYMBOL_CFD_PCT: Final[float] = 0.99
"""Per-symbol cap, Capital CFD (env: ``POLARIS_CAP_PER_SYMBOL_CFD_PCT``)."""

PER_SYMBOL_EQUITY_PCT: Final[float] = 0.99
"""Per-symbol cap, Track C equity (env: ``POLARIS_CAP_PER_SYMBOL_EQUITY_PCT``)."""

TRACK_A_GROSS_PCT: Final[float] = 0.99
"""Track A gross cap (env: ``POLARIS_CAP_TRACK_A_GROSS_PCT``)."""

TRACK_B_GROSS_PCT: Final[float] = 1.00
"""Track B gross cap (env: ``POLARIS_CAP_TRACK_B_GROSS_PCT``)."""

TRACK_C_GROSS_PCT: Final[float] = 3.0
"""Track C gross cap on buying_power basis (/debate-CONFIRMED b565392;
env: ``POLARIS_CAP_TRACK_C_GROSS_PCT``)."""

TRACK_A_DAILY_VENUE_PCT: Final[float] = 0.99
"""Track A daily venue risk (env: ``POLARIS_CAP_TRACK_A_DAILY_VENUE_PCT``)."""

TRACK_B_DAILY_VENUE_PCT: Final[float] = 0.99
"""Track B daily venue risk (env: ``POLARIS_CAP_TRACK_B_DAILY_VENUE_PCT``)."""

TRACK_C_DAILY_VENUE_PCT: Final[float] = 0.99
"""Track C daily venue risk (env: ``POLARIS_CAP_TRACK_C_DAILY_VENUE_PCT``)."""

TOTAL_DAILY_RISK_CEILING_PCT: Final[float] = 0.99
"""Total daily risk ceiling (env: ``POLARIS_CAP_TOTAL_DAILY_PCT``)."""

UNDERLYING_GROUP_PCT: Final[float] = 0.99
"""Per-underlying-group cap (env: ``POLARIS_CAP_UNDERLYING_PCT``)."""

CLUSTER_BTC_ETH_PCT: Final[float] = 0.99
"""Cluster cap — BTC/ETH spot (env: ``POLARIS_CAP_CLUSTER_BTC_ETH_PCT``)."""

CLUSTER_XAU_INDICES_PCT: Final[float] = 0.99
"""Cluster cap — XAU/indices CFD (env: ``POLARIS_CAP_CLUSTER_XAU_INDICES_PCT``)."""

CLUSTER_FX_MAJORS_PCT: Final[float] = 0.99
"""Cluster cap — FX majors (env: ``POLARIS_CAP_CLUSTER_FX_MAJORS_PCT``)."""

EQUITY_SHADOW_CAP_DEFAULT_PCT: Final[float] = 0.02
"""Shadow validation cap — small per-trade %-of-equity ceiling applied ONLY to the
two UNVALIDATED daily-equity strategies (``equity_vol_expansion_pocket_pivot`` /
``equity_52wk_high_breakout``) while their live edge is being verified.

This is prudent ALLOCATION sizing of a brand-new, not-yet-validated strategy — NOT
a defensive dampen of an edge strategy. It is a pure ``min()`` containment in the
SAME ``headroom_min`` slot as ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` (it adds NO
multiplier to the T4 base×continuous×tier×cell chain — the 9-stack ban / tier
amplifier / cell mult / -1.0R rail are all untouched). Alpaca equity is
commission-free so the live demo P&L is the clean true-edge measurement; bounding
each entry small means a repeat of the prior -$104.58 stays negligible while the
edge is judged. env: ``POLARIS_EQUITY_SHADOW_CAP_PCT`` — Jin raises it (or unsets
toward the absolute ceiling) to full size once the edge clears."""

EQUITY_SHADOW_CAP_ENV: Final[str] = "POLARIS_EQUITY_SHADOW_CAP_PCT"

# Per-trade absolute notional hard cap (USD), venue-keyed. This REPLACES the
# silent post-hoc ``max(10.0, min(x, 5_000.0))`` clamp formerly applied in
# ``_production_run_signal.py`` AFTER T4 had already computed a correct
# ``final_notional_usd`` — that clamp was unlogged and collapsed every signal
# above $5k (e.g. cont=0.75 and cont=1.50 converged to the identical $5,000,
# silently voiding the 1.5/2/3x tier amplifier since 2026-05-29 ee82437). This
# constant instead enters the SAME single ``headroom_min()`` min() slot as
# ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` (no new T4 multiplier, 9-stack ban
# intact) — expressed in %-of-equity terms via ``notional_ceiling_pct`` (engine)
# so it composes with leverage instead of silently fighting it. Values are
# generous (not a defensive throttle): OKX spot cash notional vs Capital's
# leveraged CFD notional differ by construction, so the ceiling is per-venue.
# env: ``POLARIS_NOTIONAL_CAP_<VENUE>_USD`` (Jin raises per venue on demand).
NOTIONAL_HARD_CAP_OKX_USD: Final[float] = 50_000.0
NOTIONAL_HARD_CAP_CAPITAL_USD: Final[float] = 50_000.0
NOTIONAL_HARD_CAP_ALPACA_USD: Final[float] = 50_000.0
NOTIONAL_HARD_CAP_DEFAULT_USD: Final[float] = 50_000.0


# ---------------------------------------------------------------------------
# Env-override resolvers (read at call time so operator can dial caps without a
# code change). Same idiom as ``polaris.core.sizing.constants._read_float_env``.
# Non-numeric / missing / empty → the high default above.
# ---------------------------------------------------------------------------


def _cap_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def per_symbol_spot_pct() -> float:
    return _cap_env("POLARIS_CAP_PER_SYMBOL_SPOT_PCT", PER_SYMBOL_SPOT_PCT)


def per_symbol_cfd_pct() -> float:
    return _cap_env("POLARIS_CAP_PER_SYMBOL_CFD_PCT", PER_SYMBOL_CFD_PCT)


def per_symbol_equity_pct() -> float:
    return _cap_env("POLARIS_CAP_PER_SYMBOL_EQUITY_PCT", PER_SYMBOL_EQUITY_PCT)


def track_a_gross_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_A_GROSS_PCT", TRACK_A_GROSS_PCT)


def track_b_gross_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_B_GROSS_PCT", TRACK_B_GROSS_PCT)


def track_c_gross_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_C_GROSS_PCT", TRACK_C_GROSS_PCT)


def track_a_daily_venue_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_A_DAILY_VENUE_PCT", TRACK_A_DAILY_VENUE_PCT)


def track_b_daily_venue_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_B_DAILY_VENUE_PCT", TRACK_B_DAILY_VENUE_PCT)


def track_c_daily_venue_pct() -> float:
    return _cap_env("POLARIS_CAP_TRACK_C_DAILY_VENUE_PCT", TRACK_C_DAILY_VENUE_PCT)


def total_daily_risk_ceiling_pct() -> float:
    return _cap_env("POLARIS_CAP_TOTAL_DAILY_PCT", TOTAL_DAILY_RISK_CEILING_PCT)


def underlying_group_pct() -> float:
    return _cap_env("POLARIS_CAP_UNDERLYING_PCT", UNDERLYING_GROUP_PCT)


def cluster_btc_eth_pct() -> float:
    return _cap_env("POLARIS_CAP_CLUSTER_BTC_ETH_PCT", CLUSTER_BTC_ETH_PCT)


def cluster_xau_indices_pct() -> float:
    return _cap_env("POLARIS_CAP_CLUSTER_XAU_INDICES_PCT", CLUSTER_XAU_INDICES_PCT)


def cluster_fx_majors_pct() -> float:
    return _cap_env("POLARIS_CAP_CLUSTER_FX_MAJORS_PCT", CLUSTER_FX_MAJORS_PCT)


def equity_shadow_cap_pct() -> float:
    """Shadow validation cap %-of-equity (env: ``POLARIS_EQUITY_SHADOW_CAP_PCT``).

    Read at call time so Jin can raise it without a code change. Non-numeric /
    missing / empty → the small default. Applied only to the two unvalidated
    daily-equity strategies via ``equity_shadow_validation_cap`` (engine)."""
    return _cap_env(EQUITY_SHADOW_CAP_ENV, EQUITY_SHADOW_CAP_DEFAULT_PCT)


def notional_hard_cap_usd(venue: str) -> float:
    """Per-trade absolute notional hard cap (USD) for ``venue``.

    Env: ``POLARIS_NOTIONAL_CAP_<VENUE>_USD`` (venue upper-cased), e.g.
    ``POLARIS_NOTIONAL_CAP_OKX_USD``. Unknown venue falls back to the
    generous default (never a silent zero — flow_not_block).
    """
    default_by_venue = {
        "okx": NOTIONAL_HARD_CAP_OKX_USD,
        "capital": NOTIONAL_HARD_CAP_CAPITAL_USD,
        "alpaca": NOTIONAL_HARD_CAP_ALPACA_USD,
    }
    default = default_by_venue.get(venue.lower(), NOTIONAL_HARD_CAP_DEFAULT_USD)
    return _cap_env(f"POLARIS_NOTIONAL_CAP_{venue.upper()}_USD", default)


def target_vol() -> float:
    """Target per-period realized vol for the vol-targeted scalar.

    Env: ``POLARIS_TARGET_VOL``. Non-numeric / missing / non-positive → default.
    """
    v = _cap_env("POLARIS_TARGET_VOL", DEFAULT_TARGET_VOL)
    return v if v > 0.0 else DEFAULT_TARGET_VOL


# ---------------------------------------------------------------------------
# P1-11 item 3 — single post-T4 entry-notional clip slot, shared by the bar
# path (``_production_run_signal.py``) and the tick path
# (``_production_tick_engine.py``). Prior to this the bar path alone applied a
# $5,000 ceiling before submit while the tick path submitted the raw T4
# ``final_notional_usd`` uncapped — a bar/tick cap asymmetry, not a new
# multiplier (this is a downstream ``min()``/``max()`` clamp on the T4 OUTPUT,
# the same slot family as ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` / headroom_min,
# not an added stack entry). env-tunable so Jin can raise/lower without a code
# change (aggressive bias: default MERELY restores bar-path parity, it does not
# newly throttle either path).
# ---------------------------------------------------------------------------

ENTRY_NOTIONAL_FLOOR_USD: Final[float] = 10.0
"""Minimum submitted entry notional (env: ``POLARIS_ENTRY_NOTIONAL_FLOOR_USD``)."""

ENTRY_NOTIONAL_CEILING_USD: Final[float] = float("inf")
"""Schema-level ceiling default = UNBOUNDED (merge 2026-07-02, P0-3 x P1-11
unification): the venue-aware notional cap is OWNED by the T4 engine single
``headroom_min()`` clip slot (``notional_ceiling_pct``, structured
``binding=`` log) and both bar/tick consume the already-capped T4
``final_notional_usd`` -- a second silent $5k re-clip here was the exact
P0-3 pathology. ``POLARIS_ENTRY_NOTIONAL_CEILING_USD`` remains as a manual
ops override only."""
"""Maximum submitted entry notional (env: ``POLARIS_ENTRY_NOTIONAL_CEILING_USD``)."""


def entry_notional_floor_usd() -> float:
    return _cap_env("POLARIS_ENTRY_NOTIONAL_FLOOR_USD", ENTRY_NOTIONAL_FLOOR_USD)


def entry_notional_ceiling_usd() -> float:
    return _cap_env("POLARIS_ENTRY_NOTIONAL_CEILING_USD", ENTRY_NOTIONAL_CEILING_USD)


def clip_entry_notional_usd(notional_usd: float) -> float:
    """Clamp a T4 ``final_notional_usd`` into ``[floor, ceiling]`` before submit.

    The bar path's clip (was an inline ``max(10.0, min(final_notional_usd,
    5_000.0))`` in ``_production_run_signal.py``) — floor-BUMPS a small residual
    up to a submittable minimum. The tick path intentionally does NOT reuse the
    floor half (its own sub-minimum handling is a DROP, not a bump — "flooring
    up would breach the binding cap that clipped it"); it reuses only
    ``entry_notional_ceiling_usd()`` via ``clip_entry_notional_ceiling_usd``
    below, so the two paths share the SAME ceiling slot without disturbing the
    tick path's deliberate sub-min drop-not-bump semantics.
    """
    return max(entry_notional_floor_usd(), min(notional_usd, entry_notional_ceiling_usd()))


def clip_entry_notional_ceiling_usd(notional_usd: float) -> float:
    """Ceiling-only half of :func:`clip_entry_notional_usd` (P1-11 item 3).

    Applies the SAME shared ``entry_notional_ceiling_usd()`` slot the bar path's
    ``clip_entry_notional_usd`` uses, without the floor-bump — for callers (the
    tick path) whose own sub-minimum handling is a drop, not a bump.
    """
    return min(notional_usd, entry_notional_ceiling_usd())


# Tier amplifier triggers
TIER_3WIN_AMP: Final[float] = 1.5
TIER_5WIN_AMP: Final[float] = 2.0
TIER_8WIN_AMP: Final[float] = 3.0
TIER_RESET_AMP: Final[float] = 1.0

TIER_3WIN_MIN_N_LOW: Final[int] = 8
TIER_3WIN_MIN_N_HIGH: Final[int] = 10
TIER_3WIN_HIT_LOW: Final[float] = 0.75
TIER_3WIN_HIT_HIGH: Final[float] = 0.70
TIER_5WIN_MIN_N: Final[int] = 10
TIER_5WIN_HIT: Final[float] = 0.70
TIER_8WIN_MIN_N: Final[int] = 10
TIER_8WIN_HIT: Final[float] = 0.70

# Listing watchdog
LISTING_WATCHDOG_AGE_HOURS: Final[int] = 24
LISTING_WATCHDOG_MULT: Final[float] = 0.5

# Fill-rate cut
FILL_RATE_CUT_THRESHOLD: Final[float] = 0.70
FILL_RATE_RESUME_THRESHOLD: Final[float] = 0.60

# Continuous scalar bounds (ADR-005 T4)
CONT_SCALAR_MIN: Final[float] = 0.75
CONT_SCALAR_MAX: Final[float] = 1.50

# Vol-targeting (T4 single scalar made vol-aware — #8).
# The continuous scalar can be derived from realized volatility via inverse
# weighting ``target_vol / realized_vol`` (ex-ante / past-only — no look-ahead),
# clipped to the SAME [CONT_SCALAR_MIN, CONT_SCALAR_MAX] band. This replaces the
# one scalar with a vol-aware value; it does NOT add a multiplier to the chain
# (9-stack ban preserved). High realized vol → smaller scalar; low → larger.
DEFAULT_TARGET_VOL: Final[float] = 0.02
"""Target per-period realized vol the scalar normalises toward (env:
``POLARIS_TARGET_VOL``). When realized == target the scalar is 1.0 (neutral)."""

EWMA_VOL_LAMBDA: Final[float] = 0.94
"""EWMA decay for realized-vol estimate (RiskMetrics-style). Recent returns
weighted more; ``vol = sqrt(EWMA of squared returns around zero)``."""

# Default base risk (% of equity) when caller didn't specify.
DEFAULT_BASE_RISK_PCT: Final[float] = 0.02


Track = Literal["A", "B", "C"]


@dataclass(frozen=True, slots=True)
class SizingProposal:
    """Pre-cap T4 proposal: ``base × continuous × tier × cell × listing × L5_learner_product``.

    Each multiplier captured for audit so post-trade reflector can attribute
    sizing back to the inputs that drove it. L5 learner mults
    (session/regime/triple_block) default to 1.0 (neutral) for backward compat.
    """

    base_risk_pct: float
    continuous_scalar: float
    tier_amplifier: float
    cell_routing_mult: float
    listing_watchdog_mult: float
    proposed_risk_pct: float
    # L5 learner wire (2026-05-26 p0_l5_l3_sizing_wire)
    session_mult: float = 1.0
    regime_mult: float = 1.0
    triple_block_mult: float = 1.0


@dataclass(frozen=True, slots=True)
class SizingFinal:
    """Post-cap T4 output (composer of headroom min() result)."""

    proposed: SizingProposal
    final_risk_pct: float
    final_notional_usd: float
    leverage: float
    binding_cap: str  # which cap clipped (audit string)


@dataclass(frozen=True, slots=True)
class StrategyRiskState:
    """Per-strategy rolling risk state (informs Kelly + amplifier)."""

    venue: str
    strategy: str
    closed_trades: int
    kelly_p: float
    kelly_q: float
    kelly_fraction: float
    win_streak: int
    hit_rate_10: float
    updated_ts: int


@dataclass(frozen=True, slots=True)
class PositionRiskState:
    """Open-position contribution to caps."""

    venue: str
    symbol: str
    instrument_id: str
    underlying_group_id: str
    cluster_id: str | None
    strategy: str
    track: Track
    signal_strength: float
    open_risk_pct: float
    notional_usd: float
    opened_ts: int


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Snapshot of live risk usage (composer input)."""

    equity_usd: float
    venue_daily_used_pct: float  # used so far today, this venue
    total_daily_used_pct: float  # used so far today, all venues
    track_used_pct: dict[Track, float] = field(default_factory=dict)
    open_positions: list[PositionRiskState] = field(default_factory=list)
    fill_rate_active_cut: bool = False  # post-hysteresis state
    # pts-classes group E (R-pool) — per-track BENCH-freed R headroom (USD)
    # and the track's current (venue,strategy_id) roster, threaded straight
    # into ``polaris.core.classes.r_pool.allocate_r_pool`` by ``compute_size``.
    # Default {} for every pre-group-E caller (byte-identical: an empty dict
    # means zero freed R / zero members, same as "not configured" elsewhere
    # in this module).
    bench_freed_usd: dict[Track, float] = field(default_factory=dict)
    track_members: dict[Track, list[TrackMember]] = field(default_factory=dict)
