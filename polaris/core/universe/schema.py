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
# Continuous active-set ranking (flow_not_block — replaces hard 4-axis cut)
# ---------------------------------------------------------------------------
# The hard 4-axis gate over-cut the candidate set (189 → 6), starving cold-start
# samples. Liquidity / spread / depth / ATR are no longer hard blocks: they
# become a continuous composite score and the top-N rows become the active set.
# Hard keep is validity only (state=live; OKX USDT-quote already enforced at
# parse time). Weak candidates still flow — the downstream cell-matrix
# down-routes them. Aggressive bias preserved (flow_not_block).
UNIVERSE_RANK_TOP_N_DEFAULT: Final[int] = 40
UNIVERSE_RANK_TOP_N_ENV: Final[str] = "POLARIS_UNIVERSE_RANK_TOP_N"

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


@dataclass(frozen=True, slots=True)
class FocusSelection:
    """One row of the focus watchlist for a given cycle."""

    cycle_ts: int
    venue: str
    symbol: str
    focus_score: float
    rank: int
    bucket: FocusBucket
