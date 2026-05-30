"""StreamConfig SSOT — 2-venue, behavior-identical encoding (design §2.2).

A *stream* is the 1st-class routing/isolation dimension. ``resolve_stream`` is
the lookup that replaces the venue-binary branches (track / leverage_source /
product_class / adapter dispatch). This step encodes **current behavior only**:

- A_okx_crypto   : okx / spot / {crypto}        / track A / leverage 1.0 (fixed) / long-only
- B_capital_cfd  : capital / cfd / {forex,index,commodity} / track B / leverage PER-MARKET (T7: FX 30 / index 20 / commodity 20 / crypto 2; live constraint overrides) / short allowed (unused this step)
- C_alpaca_equity: alpaca / equity / {equity}    / track C / leverage 1.0 (fixed, cash) / long-only (T11, P0)

Per-market CFD leverage (T7) is supplied by ``fallback_leverage_for_asset_class``
(live ``CapitalMarketConstraint.leverage`` overrides). Track C (Alpaca US
equity, T11) is additive — A/B stay behavior-identical. Stream supplies
leverage/caps/dispatch, NEVER a multiplier:
the T4 chain (base×continuous×tier×cell×listing×learner), headroom_min(), and
the 0.09 SINGLE_TRADE_ABSOLUTE_CEILING are all untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "STREAMS",
    "VENUE_TO_STREAM",
    "SizingProfile",
    "StreamConfig",
    "StreamId",
    "StreamProfile",
    "Track",
    "derive_leverage",
    "fallback_leverage_for_asset_class",
    "resolve_stream",
    "resolve_stream_profile",
]

StreamId = Literal["A_okx_crypto", "B_capital_cfd", "C_alpaca_equity"]
Track = Literal["A", "B", "C"]
# "C" is the equity track (Alpaca US equity). Registered in STREAMS at T11 as
# C_alpaca_equity (additive — A/B unchanged): fixed leverage 1.0 (cash equity),
# long-only (P0), AlpacaAdapter dispatch.


@dataclass(frozen=True, slots=True)
class SizingProfile:
    """Sizing-relevant stream attributes (design §2.2 — facet3 absorbed).

    ``leverage`` is the stream's leverage SSOT only for the *fixed* case:
    A=1.0 (spot, no leverage — INVARIANT). For B (Capital CFD) leverage is
    NOT a single stream constant — it is **per-market** (T7,
    ``leverage_source="per_market_constraint"``): the live venue
    ``CapitalMarketConstraint.leverage`` when > 0, else the asset-class
    fallback (``fallback_leverage_for_asset_class``: FX 30 / index 20 /
    commodity 20 / crypto-CFD 2). The ``leverage`` field below for B is kept
    as the documented FX-modal default (30.0) so a path lacking an
    asset_class/constraint degrades to the most-common CFD case, but the
    runtime driver is the per-market helper — there is no second flat-30 SSOT.
    Per-symbol caps stay env-overridable downstream (POLARIS_CAP_*); the value
    here is a static default knob, not a multiplier.
    """

    leverage_source: str  # "fixed_1" | "per_market_constraint"
    leverage: float  # A=1.0 (invariant); B=30.0 FX-modal default (per-market at runtime)
    base_risk_pct: float  # 0.02 common
    cluster_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StreamConfig:
    """Single carrier for one stream's routing/isolation attributes."""

    stream_id: StreamId
    venue: str  # okx | capital
    product_class: str  # spot | cfd  (== StrategyMetadata semantic)
    asset_classes: frozenset[str]  # {crypto} | {forex,index,commodity}
    track: Track
    allow_short: bool  # A=False; B=True (allowed but unused this step)
    adapter_ref: str  # adapter class label ("OKXAdapter" | "CapitalAdapter")
    universe_source: str  # okx_tickers | capital_navigation
    strategy_roster: frozenset[str]
    sizing_profile: SizingProfile
    session_calendar: str  # always_on | fx_indices_cal
    cost_model: str  # spot_taker | cfd_spread
    external_reject_codes: frozenset[str]  # venue-specific non-fault codes


# Capital CFD per-market leverage fallback (T7, /debate-CONFIRMED b565392).
# Applied ONLY when the live venue constraint leverage is 0/absent — the live
# CapitalMarketConstraint.leverage (> 0) always takes precedence. This is NOT a
# T4 sizing-chain multiplier: leverage is the existing ``intent.leverage`` field
# that drives notional (engine.py:471), set per-market instead of a flat 30.
# Keys are normalized lowercase; both singular/plural asset-class spellings map.
_FALLBACK_LEVERAGE_BY_ASSET_CLASS: dict[str, float] = {
    "forex": 30.0,
    "fx": 30.0,
    "index": 20.0,
    "indices": 20.0,
    "commodity": 20.0,
    "commodities": 20.0,
    "crypto": 2.0,
    "cryptocurrency": 2.0,
    "cryptocurrencies": 2.0,
}
# Unmapped CFD asset_class → crypto-CFD floor (2.0): never 0 (no 0x notional)
# and never the erroneous flat 30x. A correctness floor, not a defensive damper.
_FALLBACK_LEVERAGE_DEFAULT = 2.0

# B's SizingProfile.leverage FX-modal default (see SizingProfile docstring). The
# runtime driver for Capital is fallback_leverage_for_asset_class / the live
# constraint — this constant is NOT a second flat-leverage SSOT.
_CFD_FX_MODAL_LEVERAGE = 30.0
_BASE_RISK_PCT = 0.02


def fallback_leverage_for_asset_class(asset_class: str) -> float:
    """Per-market CFD leverage fallback keyed on ``asset_class`` (T7).

    /debate-CONFIRMED mapping: FX/forex → 30, index → 20, commodity → 20,
    crypto-CFD → 2. Case-insensitive; accepts singular or plural spellings
    (``index``/``indices``, ``commodity``/``commodities``). An unmapped class
    returns the crypto-CFD floor (2.0) so notional is never 0x and never the
    erroneous flat 30x. The live ``CapitalMarketConstraint.leverage`` (> 0)
    overrides this fallback — callers apply it only when the venue value is
    0/absent. Pure function; OKX spot does not use it (stays fixed 1.0).
    """
    return _FALLBACK_LEVERAGE_BY_ASSET_CLASS.get(
        (asset_class or "").strip().lower(), _FALLBACK_LEVERAGE_DEFAULT
    )


def derive_leverage(stream: StreamConfig, asset_class: str) -> float:
    """Runtime leverage for a (stream, asset_class) (T7).

    Fixed-leverage streams (``leverage_source="fixed_1"`` — OKX spot) keep their
    INVARIANT profile leverage (1.0), ignoring asset_class. Per-market streams
    (``per_market_constraint`` — Capital CFD) resolve leverage from the
    asset_class via :func:`fallback_leverage_for_asset_class` (FX 30 / index 20
    / commodity 20 / crypto 2). The live ``CapitalMarketConstraint.leverage``
    overrides at the constraint_translator layer (when a per-symbol constraint
    is loaded); this function is the path used when only the asset_class is
    available. Pure; no T4 multiplier — leverage feeds ``intent.leverage``.
    """
    if stream.sizing_profile.leverage_source == "per_market_constraint":
        return fallback_leverage_for_asset_class(asset_class)
    return stream.sizing_profile.leverage


STREAMS: dict[StreamId, StreamConfig] = {
    "A_okx_crypto": StreamConfig(
        stream_id="A_okx_crypto",
        venue="okx",
        product_class="spot",
        asset_classes=frozenset({"crypto"}),
        track="A",
        allow_short=False,
        adapter_ref="OKXAdapter",
        universe_source="okx_tickers",
        strategy_roster=frozenset(
            {"volume_burst", "tsmom", "rsi_bb_pullback", "spot_donchian"}
        ),
        sizing_profile=SizingProfile(
            leverage_source="fixed_1",
            leverage=1.0,
            base_risk_pct=_BASE_RISK_PCT,
            cluster_ids=("crypto:BTC", "crypto:ETH"),
        ),
        session_calendar="always_on",
        cost_model="spot_taker",
        external_reject_codes=frozenset(),
    ),
    "B_capital_cfd": StreamConfig(
        stream_id="B_capital_cfd",
        venue="capital",
        product_class="cfd",
        asset_classes=frozenset({"forex", "index", "commodity"}),
        track="B",
        allow_short=True,
        adapter_ref="CapitalAdapter",
        universe_source="capital_navigation",
        strategy_roster=frozenset(
            {"fx_breakout_basket", "xau_indices_trend", "session_breakout"}
        ),
        sizing_profile=SizingProfile(
            leverage_source="per_market_constraint",
            leverage=_CFD_FX_MODAL_LEVERAGE,  # FX-modal default; per-market at runtime
            base_risk_pct=_BASE_RISK_PCT,
            cluster_ids=("cfd:XAU_INDICES", "cfd:FX_MAJORS"),
        ),
        session_calendar="fx_indices_cal",
        cost_model="cfd_spread",
        external_reject_codes=frozenset(
            {
                "MARKET_CLOSED",
                "MARKET_OFFLINE",
                "INSTRUMENT_NOT_TRADEABLE",
                "REJECTED",
            }
        ),
    ),
    # Track C — Alpaca US equity (T11, additive; A/B above are byte-for-byte
    # unchanged). Cash equity → fixed leverage 1.0 (no leverage source). P0 is
    # long-only (allow_short=False). The C *stream* registration here makes
    # resolve_stream("alpaca")/VENUE_TO_STREAM["alpaca"] resolve automatically
    # and feeds _is_external_reject the Alpaca non-fault codes below. Stream
    # supplies leverage/caps/dispatch only — NO T4 multiplier (chain untouched).
    "C_alpaca_equity": StreamConfig(
        stream_id="C_alpaca_equity",
        venue="alpaca",
        product_class="equity",
        asset_classes=frozenset({"equity"}),
        track="C",
        allow_short=False,  # P0 long-only
        adapter_ref="AlpacaAdapter",
        universe_source="alpaca_assets",
        strategy_roster=frozenset(
            {"equity_tsmom", "equity_rsi_bb_pullback", "equity_gap_go"}
        ),
        sizing_profile=SizingProfile(
            leverage_source="fixed_1",
            leverage=1.0,  # cash equity — INVARIANT, no leverage
            base_risk_pct=_BASE_RISK_PCT,
            cluster_ids=("equity:MEGA_CAP",),
        ),
        session_calendar="us_equity_cal",
        cost_model="equity_commission",
        # Alpaca paper rejects that are EXTERNAL (not strategy/client faults) —
        # market-closed / PDT / buying-power / auth-forbidden. Classified as
        # non-fault so they never trip an unjust HARD_HALT (lesson 1a315a3).
        # H2: these are the SEMANTIC TOKENS the AlpacaAdapter now emits
        # (``classify_reject_code`` normalizes Alpaca's numeric code + HTTP
        # status into this vocabulary). A 422-style validation reject normalizes
        # to ``validation_rejected`` — intentionally ABSENT here so a genuinely
        # anomalous reject still records a fault (can eventually halt).
        external_reject_codes=frozenset(
            {
                "forbidden",
                "pdt_block",
                "insufficient_buying_power",
                "market_closed",
            }
        ),
    ),
}

# Reverse index: venue → stream_id. One venue == one stream in this 2-venue step.
VENUE_TO_STREAM: dict[str, StreamId] = {
    cfg.venue: cfg.stream_id for cfg in STREAMS.values()
}


def resolve_stream(venue: str, product_class: str | None = None) -> StreamConfig:
    """Return the :class:`StreamConfig` for ``venue`` (+ optional product_class).

    Mirrors the current venue-binary behavior exactly: ``okx`` → A_okx_crypto,
    ``capital`` → B_capital_cfd. ``product_class`` is accepted for forward
    compatibility (one venue may host multiple streams later) and, when given,
    is validated against the resolved stream.

    Raises ``KeyError`` for an unknown venue and ``ValueError`` on a
    product_class mismatch — both are programming errors, not runtime states.
    """
    v = (venue or "").lower()
    stream_id = VENUE_TO_STREAM.get(v)
    if stream_id is None:
        raise KeyError(f"no stream registered for venue {venue!r}")
    cfg = STREAMS[stream_id]
    if product_class is not None and product_class != cfg.product_class:
        raise ValueError(
            f"product_class {product_class!r} does not match stream "
            f"{cfg.stream_id} (expected {cfg.product_class!r})"
        )
    return cfg


# ---------------------------------------------------------------------------
# StreamProfile — first-class per-stream seam for the gate pipeline (Phase 0).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamProfile:
    """Per-stream gate-context seam (gate architecture Phase 0, design Option A).

    A read-only projection of :class:`StreamConfig` resolved ONCE per signal and
    threaded into ``GateContext.stream_profile`` so every gate / payload builder
    reads ``ctx.stream_profile`` instead of re-deriving ``if venue == ...``
    branches. The UNIFIED 8-gate pipeline stays unified — A/B/C vary via this
    single injected carrier, never via forked gates or scattered branches.

    Phase 0 is a STRUCTURAL ENABLER ONLY: the fields below merely mirror the
    StreamConfig values the pipeline already uses, so threading the profile
    produces byte-identical gate decisions today. The reserved hook fields
    (``regime_evidence`` / ``guard_hooks``) are intentionally EMPTY and unread in
    P0 — they are the place where P1+ phases attach per-stream evidence/guards
    WITHOUT re-plumbing GateContext. Stream supplies leverage/caps/dispatch/
    context only — NEVER a T4 multiplier (9-stack collapse stays blocked).
    """

    stream_id: StreamId
    venue: str
    product_class: str
    asset_classes: frozenset[str]
    session_calendar: str
    cost_model: str
    allow_short: bool
    leverage_source: str
    external_reject_codes: frozenset[str]
    # Reserved per-stream enrichment hooks (P1+). Empty + unread in P0 — present
    # so future phases attach evidence/guards without re-threading GateContext.
    regime_evidence: frozenset[str] = frozenset()
    guard_hooks: frozenset[str] = frozenset()

    @classmethod
    def from_stream(cls, cfg: StreamConfig) -> StreamProfile:
        """Project a :class:`StreamConfig` into a P0 (hook-empty) profile."""
        return cls(
            stream_id=cfg.stream_id,
            venue=cfg.venue,
            product_class=cfg.product_class,
            asset_classes=cfg.asset_classes,
            session_calendar=cfg.session_calendar,
            cost_model=cfg.cost_model,
            allow_short=cfg.allow_short,
            leverage_source=cfg.sizing_profile.leverage_source,
            external_reject_codes=cfg.external_reject_codes,
        )


def resolve_stream_profile(
    venue: str, product_class: str | None = None
) -> StreamProfile:
    """Resolve the :class:`StreamProfile` for ``venue`` (one lookup, P0).

    Thin wrapper over :func:`resolve_stream` — the single place the pipeline
    builds the per-signal stream seam. Raises the same ``KeyError`` /
    ``ValueError`` as ``resolve_stream`` for an unknown venue / product_class
    mismatch (both programming errors, not runtime states).
    """
    return StreamProfile.from_stream(resolve_stream(venue, product_class))
