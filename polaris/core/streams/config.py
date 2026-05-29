"""StreamConfig SSOT — 2-venue, behavior-identical encoding (design §2.2).

A *stream* is the 1st-class routing/isolation dimension. ``resolve_stream`` is
the lookup that replaces the venue-binary branches (track / leverage_source /
product_class / adapter dispatch). This step encodes **current behavior only**:

- A_okx_crypto  : okx / spot / {crypto}        / track A / leverage 1.0    / long-only
- B_capital_cfd : capital / cfd / {forex,index,commodity} / track B / leverage 30.0 / short allowed (unused this step)

No per-market leverage, no Track C, no short activation, no Alpaca — those are
later P0 steps. Stream supplies leverage/caps/dispatch, NEVER a multiplier:
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
    "Track",
    "resolve_stream",
]

StreamId = Literal["A_okx_crypto", "B_capital_cfd"]
Track = Literal["A", "B"]


@dataclass(frozen=True, slots=True)
class SizingProfile:
    """Sizing-relevant stream attributes (design §2.2 — facet3 absorbed).

    ``leverage`` encodes current behavior literally: A=1.0 (spot, no leverage),
    B=30.0 (== current ``CFD_LEVERAGE_DEFAULT``). ``leverage_source`` is a label
    only — actual per-market translation is a later step (not wired here).
    Per-symbol caps stay env-overridable downstream (POLARIS_CAP_*); the value
    here is the static default knob name, not a multiplier.
    """

    leverage_source: str  # "fixed_1" | "per_market_constraint"
    leverage: float  # current literal value (A=1.0, B=30.0)
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


# Current CFD leverage default (mirrors _production_run_signal.CFD_LEVERAGE_DEFAULT).
_CFD_LEVERAGE_DEFAULT = 30.0
_BASE_RISK_PCT = 0.02

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
            leverage=_CFD_LEVERAGE_DEFAULT,
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
