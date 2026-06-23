"""Layer 0 dataclasses + constants — UniverseInstrument + FocusSelection.

Spec source: vault/30_components/layer-0-universe-discovery.md (Dataclass + Constants).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Refresh / focus / listing constants (Q1, Q3, Q6)
# ---------------------------------------------------------------------------

OKX_UNIVERSE_REFRESH_SEC: Final[int] = 300
CAPITAL_UNIVERSE_REFRESH_SEC: Final[int] = 600

FOCUS_TARGET_BASE: Final[int] = 30
FOCUS_TARGET_MIN: Final[int] = 12
FOCUS_TARGET_MAX: Final[int] = 48

NEW_LISTING_WATCH_HOURS: Final[int] = 24
NEW_LISTING_SIZE_MULT: Final[float] = 0.5
NEW_LISTING_MAX_CONCURRENT: Final[int] = 1

UNDERLYING_BTC_GROSS_CAP: Final[float] = 0.60

# ---------------------------------------------------------------------------
# 4-axis hard filter defaults (Q2)
# ---------------------------------------------------------------------------

DEFAULT_MIN_VOL_24H_USD: Final[float] = 30_000_000.0
DEFAULT_MAX_SPREAD_BPS: Final[float] = 10.0
DEFAULT_MIN_ATR_24H_PCT: Final[float] = 2.0
DEFAULT_MIN_DEPTH_10BPS_USD: Final[float] = 25_000.0

# Asset-class-specific ATR floors (Day 2 patch).
# Crypto majors run ~3-6% daily; FX majors ~0.4-1%; indices ~0.5-1.5%; commodities ~1-3%.
# A single 2% gate would lock out every CFD venue, so the filter falls back to
# per-class floors when ``asset_class`` is set on the instrument.
ATR_FLOOR_BY_CLASS: Final[dict[str, float]] = {
    "crypto": 2.0,
    "forex": 0.3,
    "indices": 0.4,
    "commodity": 0.5,
    "equity": 1.0,
    "other": 0.5,
}

ALLOWED_QUOTE_CCY_OKX: Final[frozenset[str]] = frozenset({"USDT"})

# ---------------------------------------------------------------------------
# Universe-eligibility liquidity floor (Jin-approved 2026-06-22 — flow_not_block)
# ---------------------------------------------------------------------------
# A per-venue *instrument-quality* eligibility floor: an instrument is tradeable-
# quality (eligible for the active set) only if it clears its venue's liquidity
# floor. This is the SAME KIND of hard keep as the existing ``state=='live'`` and
# OKX-USDT-quote gates — a membership test on WHICH instruments are tradeable,
# NOT a per-signal block, size-cut, or entry-veto. Signals + the G3/G4 gate flow
# freely on whatever passes; the ATR-z signal-richness rank orders the survivors.
#
# North-star: an instrument whose round-trip spread (e.g. 175bp) exceeds the
# strategy edge (~0.3R) is loss-certain — excluding it is QUALITY, not defense.
# Measured junk it removes: OKX NC-USDT 19912bp / STRK 19871bp / BNT 175bp +
# vol≈$0 names; Alpaca TNON $0.59 / ADTX $0.017 sub-$1 gappers + $26k-vol pennies.
#
# Thresholds are per-venue and env-overridable (``POLARIS_LIQFLOOR_<VENUE>_<KEY>``)
# so they are a /debate calibration target, never magic-in-place. A threshold of
# 0 (or unset where the datum is natively 0) disables that axis for the venue —
# Capital exposes no native vol/depth (==0.0), so its floor keys on SPREAD ONLY
# (a vol/price floor there would zero the venue). "Unknown" data (price/vol==0)
# is treated as NOT-floored-out (flow_not_block: never drop on a missing datum).
LIQFLOOR_ENV_PREFIX: Final[str] = "POLARIS_LIQFLOOR_"


@dataclass(frozen=True, slots=True)
class LiquidityFloor:
    """Per-venue eligibility floor (0 on an axis = that axis disabled)."""

    max_spread_bps: float = 0.0
    min_vol_24h_usd: float = 0.0
    min_depth_10bps_usd: float = 0.0
    min_price: float = 0.0


# Defaults sized to the MEASURED junk (Diagnosis 2026-06-22), generous so they
# act as a coarse eligibility floor leaving the continuous rank to order the rest
# (NOT a re-introduction of the 189->6 over-cut hard 4-axis gate).
_DEFAULT_LIQUIDITY_FLOOR_BY_VENUE: Final[dict[str, LiquidityFloor]] = {
    # OKX crypto — all axes REAL today (parse_okx_tickers).
    "okx": LiquidityFloor(
        max_spread_bps=30.0,
        min_vol_24h_usd=20_000_000.0,
        min_depth_10bps_usd=25_000.0,
        min_price=0.0,
    ),
    # Alpaca equity — vol real when enriched; price plumbed via last_price.
    # spread is a placeholder (2.0) on Alpaca → do NOT floor on it.
    "alpaca": LiquidityFloor(
        max_spread_bps=0.0,
        min_vol_24h_usd=5_000_000.0,
        min_depth_10bps_usd=0.0,
        min_price=1.0,
    ),
    # Capital CFD — only spread is native (vol/depth==0.0); SPREAD-ONLY floor.
    "capital": LiquidityFloor(
        max_spread_bps=40.0,
        min_vol_24h_usd=0.0,
        min_depth_10bps_usd=0.0,
        min_price=0.0,
    ),
}


def _liqfloor_env_override(venue: str, key: str, default: float) -> float:
    """Read ``POLARIS_LIQFLOOR_<VENUE>_<KEY>`` → float; default on unset/garbage."""
    raw = os.environ.get(f"{LIQFLOOR_ENV_PREFIX}{venue.upper()}_{key.upper()}")
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def liquidity_floor_for_venue(venue: str) -> LiquidityFloor:
    """Per-venue eligibility floor (env-overridable). Unknown venue → no floor.

    Each axis is independently overridable via ``POLARIS_LIQFLOOR_<VENUE>_<KEY>``
    where KEY ∈ {MAX_SPREAD_BPS, MIN_VOL_24H_USD, MIN_DEPTH_10BPS_USD, MIN_PRICE}.
    An unregistered venue gets an all-zero floor (permissive — smoke-safe).
    """
    base = _DEFAULT_LIQUIDITY_FLOOR_BY_VENUE.get((venue or "").lower())
    if base is None:
        return LiquidityFloor()
    v = (venue or "").lower()
    return LiquidityFloor(
        max_spread_bps=_liqfloor_env_override(v, "MAX_SPREAD_BPS", base.max_spread_bps),
        min_vol_24h_usd=_liqfloor_env_override(v, "MIN_VOL_24H_USD", base.min_vol_24h_usd),
        min_depth_10bps_usd=_liqfloor_env_override(
            v, "MIN_DEPTH_10BPS_USD", base.min_depth_10bps_usd
        ),
        min_price=_liqfloor_env_override(v, "MIN_PRICE", base.min_price),
    )

# ---------------------------------------------------------------------------
# Continuous active-set ranking (flow_not_block — replaces hard 4-axis cut)
# ---------------------------------------------------------------------------
# The hard 4-axis gate over-cut the candidate set (189 → 6), starving cold-start
# samples. Liquidity / spread / depth / ATR are no longer hard blocks: they
# become a continuous composite score and the top-N rows become the active set.
# Hard keep is validity only (state=live; OKX USDT-quote already enforced at
# parse time). Weak candidates still flow — the downstream cell-matrix
# down-routes them. Aggressive bias preserved (flow_not_block).
UNIVERSE_RANK_TOP_N_DEFAULT: Final[int] = 120
UNIVERSE_RANK_TOP_N_ENV: Final[str] = "POLARIS_UNIVERSE_RANK_TOP_N"

# Watch-set ceiling (Jin 2026-06-24 — WATCH/TRADE decouple): the active/watch set
# is now DECOUPLED from the focus window (FOCUS_TARGET_MAX). The liquidity floor
# moved OFF the WATCH gate (it no longer pre-cuts the candidate set) and ONTO the
# curator TRADE gate, so the bot WATCHES dozens+ names (was 2 on OKX) while the
# trade-eligible subset stays slippage-disciplined. ``WATCH_MAX`` caps the active
# set itself (env-tunable, resource guard); focus/WS stay bounded downstream
# (FOCUS_CYCLE_TARGET / WS_SYMBOLS_PER_VENUE). Was conflated with FOCUS_TARGET_MAX
# (48) by the old ``min(top_n, FOCUS_TARGET_MAX)`` clamp — split here so watch can
# exceed 48 while focus stays 12-48. flow_not_block: widening WATCH = flow up.
UNIVERSE_WATCH_MAX_DEFAULT: Final[int] = 120
UNIVERSE_WATCH_MAX_ENV: Final[str] = "POLARIS_WATCH_MAX"


def universe_watch_max() -> int:
    """Active/watch-set ceiling (env ``POLARIS_WATCH_MAX`` → default 120; >= 1).

    The hard cap on how many names the bot WATCHES (active set / bar-poll pool).
    Decoupled from FOCUS_TARGET_MAX so watch can exceed the focus window. Resource
    guard: raising it widens REST bar fan-out + DB writes (the binding cost), so
    it is env-tunable for live calibration. Invalid/unset → default.
    """
    raw = os.environ.get(UNIVERSE_WATCH_MAX_ENV)
    if raw is None or raw == "":
        return UNIVERSE_WATCH_MAX_DEFAULT
    try:
        val = int(float(raw))
    except ValueError:
        return UNIVERSE_WATCH_MAX_DEFAULT
    return max(1, val)

# Composite reward weights (vol + realized-vol proxy), z-normalized population.
RANK_SCORE_W_VOL: Final[float] = 0.55
RANK_SCORE_W_ATR: Final[float] = 0.45
# Soft penalties (subtracted from the reward, also z-normalized population).
# Thin depth / wide spread lower the rank but never hard-reject — anomaly edge
# on thinner names is preserved (low-liquidity → anomaly profit thesis).
RANK_PENALTY_W_SPREAD: Final[float] = 0.30
RANK_PENALTY_W_DEPTH: Final[float] = 0.15

# ---------------------------------------------------------------------------
# Pre-rank score weights (Q3)
# ---------------------------------------------------------------------------

RANK_WEIGHT_VOL_Z: Final[float] = 0.35
RANK_WEIGHT_SIGNAL_DENSITY_Z: Final[float] = 0.25
RANK_WEIGHT_ATR_Z: Final[float] = 0.20
RANK_WEIGHT_DEPTH_Z: Final[float] = 0.10
RANK_WEIGHT_CELL_Z: Final[float] = 0.10
# Increment 1 (2026-06-24): the EntranceJudge opportunity term. When the focus
# pass supplies per-instrument ``opportunity_score`` (the 5-lens entrance
# judgment), its grouped-z lifts a high-judgment candidate's focus rank — so
# focus reflects the PROBE JUDGMENT, not just liquidity-z (the audit's "judgment
# layer unbuilt" fix). RANKING input only (flow_not_block): never a size cut or
# veto. Defaults to 0-effect when no scores are supplied (byte-identical focus).
RANK_WEIGHT_OPPORTUNITY_Z: Final[float] = 0.20

# ---------------------------------------------------------------------------
# Asset-class focus quota (STEP 6 — crypto-monopoly fix; flow_not_block)
# ---------------------------------------------------------------------------
# DEMO/PAPER virtual capital. The cross-venue focus is sorted globally by score
# (vol-dominant). 24/7 crypto carries huge 24h notional while Capital CFD rows
# expose no 24h notional via the nav tree (``vol_24h_usd=0.0``), so a pure score
# sort lets crypto MONOPOLIZE the focus window and starves FX / indices / gold /
# equity → those classes never reach the order path.
#
# The quota GUARANTEES each present-but-under-represented asset class a minimum
# number of focus slots, drawn from its own highest-scored rows. This is a FLOW
# INCREASE (more asset classes trade), NOT a throttle: crypto coverage stays
# wide (it keeps the bulk of the window), no entry is blocked, no notional is
# cut. A single-asset-class universe (OKX-only crypto / Alpaca-only equity)
# satisfies every quota trivially → the quota is a NO-OP there.
#
# Defaults are CONSERVATIVE (small guaranteed floors). crypto has NO floor — it
# dominates the score sort on its own. Each class is env-overridable
# (``POLARIS_FOCUS_QUOTA_<CLASS>``) — a /debate calibration target.
FOCUS_QUOTA_ENV_PREFIX: Final[str] = "POLARIS_FOCUS_QUOTA_"
_DEFAULT_FOCUS_MIN_QUOTA: Final[dict[str, int]] = {
    "crypto": 0,  # never floored — wins the score sort outright (24/7 + high vol)
    "forex": 4,
    "indices": 3,
    "commodity": 2,
    "equity": 4,
    "other": 0,
}


def focus_min_quota_by_class() -> dict[str, int]:
    """Per-asset-class minimum focus-slot quota (env-overridable; clamped >= 0).

    Read ``POLARIS_FOCUS_QUOTA_<CLASS>`` (e.g. ``POLARIS_FOCUS_QUOTA_FOREX``) to
    override a class floor; unset/invalid keeps the conservative default. The
    guaranteed minimums fit inside the focus window by construction. /debate
    calibration target.
    """
    out: dict[str, int] = {}
    for cls, default in _DEFAULT_FOCUS_MIN_QUOTA.items():
        raw = os.environ.get(f"{FOCUS_QUOTA_ENV_PREFIX}{cls.upper()}")
        val = default
        if raw is not None and raw != "":
            try:
                val = int(float(raw))
            except ValueError:
                val = default
        out[cls] = max(0, val)
    return out


# ---------------------------------------------------------------------------
# Capital FX-majors keep/floor (P1 stream-coverage — flow_not_block, per-venue)
# ---------------------------------------------------------------------------
# The continuous active-rank score is vol-dominant + 0.45·ATR. Capital exposes
# NO 24h notional via the nav tree (``vol_24h_usd=0.0``), so FX names rank purely
# on ATR — and the high-ATR EXOTIC crosses (USDZAR ~0.98%, NOKSEK ~0.80%)
# outrank the quiet FX MAJORS (USDJPY ~0.078%, EURUSD/GBPUSD/USDCAD). The majors
# then never reach the active set, so ``fx_breakout_basket`` / ``session_breakout``
# (BASKET = EURUSD/GBPUSD/AUDUSD/USDJPY/USDCAD) never receive a tradeable symbol.
#
# This is a TARGETED, per-venue keep: when a curated Capital FX major is live it
# is ALWAYS kept in the active set (and prioritized within the forex focus quota)
# ALONGSIDE the exotics. It is a FLOW INCREASE (seat BOTH majors + exotics,
# remove nothing), NOT a throttle, and it touches NO global ranking weight
# (``RANK_SCORE_W_*`` stay byte-identical → OKX/Alpaca ranking unchanged).
CAPITAL_FX_MAJORS: Final[frozenset[str]] = frozenset(
    {"EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD"}
)


def _normalize_fx_symbol(symbol: str) -> str:
    """Normalize a venue epic to the bare FX pair for major matching.

    Mirrors the strategy-side normalization (``.upper().replace("/", "")``) and
    additionally strips a ``.``/``_`` separator and a trailing weekend marker so
    epics like ``EUR/USD``, ``EURUSD.``, ``EURUSD_W`` all reduce to ``EURUSD``.
    """
    s = symbol.upper().replace("/", "").replace(".", "")
    if "_" in s:
        s = s.split("_", 1)[0]
    return s


def is_capital_fx_major(venue: str, symbol: str) -> bool:
    """True iff ``(venue, symbol)`` is a curated Capital FX major (case/format-insensitive)."""
    return (venue or "").lower() == "capital" and _normalize_fx_symbol(symbol) in CAPITAL_FX_MAJORS


# ---------------------------------------------------------------------------
# Alpaca liquid-equity focus priority (P1.5 stream-coverage — flow_not_block, per-venue)
# ---------------------------------------------------------------------------
# Equity rows carry REAL liquidity (``vol_24h_usd`` = close×volume) + an intraday-
# range ATR proxy. Megacaps / top ETFs have HUGE dollar-volume but LOW realized
# ATR%; penny / small-caps (ADTX, ZCMD) have tiny dollar-volume but HUGE ATR%.
# The vol+0.45·ATR composite + the equity focus quota can therefore let the
# high-ATR penny names seat AHEAD of the liquid megacaps — exactly the Alpaca
# analogue of the Capital exotics-vs-majors problem above. ``equity_rsi_bb`` /
# ``equity_tsmom`` / ``equity_gap_go`` want the LIQUID names at the RTH open.
#
# This is the per-venue EQUITY mirror of ``CAPITAL_FX_MAJORS``: a curated liquid
# set is PRIORITIZED within the equity focus quota so megacaps/top-ETFs seat
# ALONGSIDE (never replacing) the penny names — a FLOW INCREASE, not a throttle.
# SSOT lives here; ``_alpaca.LIQUID_SEED_SYMBOLS`` (ordered tuple for probing) is
# derived from this set. Touches NO global ranking weight (``RANK_SCORE_W_*``
# byte-identical → OKX/Capital ranking unchanged; non-Alpaca venue → no-op).
LIQUID_EQUITY_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        # Megacaps
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
        "BRK.B", "JPM", "V", "MA", "UNH", "XOM", "LLY", "JNJ", "WMT", "PG", "HD",
        "COST", "ORCL", "NFLX", "AMD", "ADBE", "CRM", "BAC", "KO", "PEP", "INTC",
        # Top ETFs by dollar-volume
        "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "TLT", "GLD", "XLF", "XLE",
        "XLK", "SMH", "SOXL", "TQQQ", "EEM", "HYG", "ARKK",
    }
)


def is_liquid_equity(venue: str, symbol: str) -> bool:
    """True iff ``(venue, symbol)`` is a curated liquid equity (megacap / top ETF).

    Per-venue (Alpaca) and case-insensitive. Equity epics are exact tickers (no
    FX-style separators), so matching is a simple upper-cased membership test.
    """
    return (venue or "").lower() == "alpaca" and symbol.upper() in LIQUID_EQUITY_SYMBOLS


FocusBucket = Literal["core", "satellite", "listing_watch"]


@dataclass(frozen=True, slots=True)
class FilterThresholds:
    """4-axis hard-filter knobs (learner-tunable in P1)."""

    min_vol_24h_usd: float = DEFAULT_MIN_VOL_24H_USD
    max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS
    min_atr_24h_pct: float = DEFAULT_MIN_ATR_24H_PCT
    min_depth_10bps_usd: float = DEFAULT_MIN_DEPTH_10BPS_USD


def default_thresholds() -> FilterThresholds:
    """Return spec-defaults thresholds (used by smoke + tests)."""
    return FilterThresholds()


@dataclass(frozen=True, slots=True)
class UniverseInstrument:
    """Snapshot row of an instrument considered for the active universe."""

    venue: str
    symbol: str
    instrument_id: str
    underlying_group_id: str
    asset_class: str
    quote_ccy: str
    state: str
    vol_24h_usd: float
    spread_bps: float
    atr_24h_pct: float
    depth_10bps_usd: float
    signal_density_7d: float = 0.0
    listing_ts: int | None = None
    last_seen_ts: int = 0
    # Last traded price (Alpaca close), for the universe min_price eligibility
    # floor. 0.0 = unknown (e.g. crypto/CFD venues that don't plumb it) → the
    # price axis is then skipped for that row (flow_not_block: never drop on a
    # missing datum). Keyword-default so all existing constructors stay valid.
    last_price: float = 0.0


def passes_liquidity_floor(ins: UniverseInstrument) -> bool:
    """True iff ``ins`` clears its venue's eligibility liquidity floor.

    Per-axis, each axis is checked ONLY when (a) the venue's floor for it is > 0
    AND (b) the instrument carries a real datum for it (> 0). A missing datum
    (0.0 vol/price/depth, common for un-enriched or non-native fields) is NEVER a
    drop — flow_not_block: we exclude on a KNOWN-bad value, never on an unknown.

    Spread is the exception: a 0.0 spread is the best possible (tightest), so the
    max-spread axis applies whenever the venue floor is set (>0), comparing the
    row's spread (any value, incl. its placeholder) against the cap.
    """
    floor = liquidity_floor_for_venue(ins.venue)
    if floor.max_spread_bps > 0.0 and ins.spread_bps > floor.max_spread_bps:
        return False
    if (
        floor.min_vol_24h_usd > 0.0
        and ins.vol_24h_usd > 0.0
        and ins.vol_24h_usd < floor.min_vol_24h_usd
    ):
        return False
    if (
        floor.min_depth_10bps_usd > 0.0
        and ins.depth_10bps_usd > 0.0
        and ins.depth_10bps_usd < floor.min_depth_10bps_usd
    ):
        return False
    return not (
        floor.min_price > 0.0
        and ins.last_price > 0.0
        and ins.last_price < floor.min_price
    )


@dataclass(frozen=True, slots=True)
class FocusSelection:
    """One row of the focus watchlist for a given cycle.

    Increment 1 (2026-06-24): ``opportunity_score`` (the [0,1] EntranceJudge
    multi-lens judgment) + ``trade_eligible`` (decouples the WATCH set from the
    TRADE set) are keyword-defaulted (None / True) so every existing constructor
    stays valid and an un-judged row is flow-preservingly trade-eligible.
    """

    cycle_ts: int
    venue: str
    symbol: str
    focus_score: float
    rank: int
    bucket: FocusBucket
    opportunity_score: float | None = None
    trade_eligible: bool = True
