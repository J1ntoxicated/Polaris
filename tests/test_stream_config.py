"""StreamConfig SSOT — behavior-identity + per-market leverage tests.

``resolve_stream`` reproduces the venue-binary track/product_class branches
exactly. Leverage is per-market for Capital CFD (T7, /debate-CONFIRMED
b565392): FX 30 / index 20 / commodity 20 / crypto-CFD 2, with the live
``CapitalMarketConstraint.leverage`` overriding the asset-class fallback. OKX
spot leverage stays the invariant fixed 1.0.
"""

from __future__ import annotations

import pytest

from polaris.core.streams import (
    STREAMS,
    VENUE_TO_STREAM,
    StreamConfig,
    fallback_leverage_for_asset_class,
    resolve_stream,
)

# --- behavior identity vs the current venue-binary branches -----------------


@pytest.mark.parametrize(
    ("venue", "exp_track", "exp_product_class"),
    [
        ("okx", "A", "spot"),
        ("capital", "B", "cfd"),
    ],
)
def test_resolve_stream_matches_venue_binary(
    venue: str, exp_track: str, exp_product_class: str
) -> None:
    """``track = "A" if venue=="okx" else "B"`` reproduced. Leverage is now
    per-market for B (T7) so it is asserted via the fallback helper below, not
    here — OKX spot stays the invariant 1.0."""
    s = resolve_stream(venue)
    # mirror of _production_run_signal.py track branch
    binary_track = "A" if venue == "okx" else "B"
    assert s.track == binary_track == exp_track
    assert s.product_class == exp_product_class


def test_okx_spot_leverage_is_invariant_one() -> None:
    """OKX spot leverage MUST remain 1.0 (notional behavior-identical)."""
    assert resolve_stream("okx").sizing_profile.leverage == 1.0


# --- T7 per-market fallback leverage (asset_class -> leverage) ---------------


@pytest.mark.parametrize(
    ("asset_class", "exp_leverage"),
    [
        ("forex", 30.0),
        ("fx", 30.0),
        ("index", 20.0),
        ("indices", 20.0),
        ("commodity", 20.0),
        ("commodities", 20.0),
        ("crypto", 2.0),
    ],
)
def test_fallback_leverage_per_asset_class(
    asset_class: str, exp_leverage: float
) -> None:
    """/debate-CONFIRMED (b565392): FX 30 / index 20 / commodity 20 / crypto-CFD 2."""
    assert fallback_leverage_for_asset_class(asset_class) == exp_leverage


def test_fallback_leverage_is_case_insensitive() -> None:
    assert fallback_leverage_for_asset_class("FOREX") == 30.0
    assert fallback_leverage_for_asset_class("Indices") == 20.0


def test_fallback_leverage_unknown_class_defaults_conservative_cfd() -> None:
    """An unmapped CFD asset_class falls to the crypto-CFD floor (2.0), never 0
    and never the erroneous flat 30 — so notional can never be silently 0x."""
    assert fallback_leverage_for_asset_class("other") == 2.0
    assert fallback_leverage_for_asset_class("") == 2.0


def test_resolve_stream_is_case_insensitive() -> None:
    assert resolve_stream("OKX").stream_id == "A_okx_crypto"
    assert resolve_stream("Capital").stream_id == "B_capital_cfd"


# --- product_class forward-compat validation --------------------------------


def test_resolve_stream_accepts_matching_product_class() -> None:
    assert resolve_stream("okx", "spot").stream_id == "A_okx_crypto"
    assert resolve_stream("capital", "cfd").stream_id == "B_capital_cfd"


def test_resolve_stream_rejects_mismatched_product_class() -> None:
    with pytest.raises(ValueError):
        resolve_stream("okx", "cfd")


def test_resolve_stream_unknown_venue_raises() -> None:
    with pytest.raises(KeyError):
        resolve_stream("alpaca")


# --- T7 constraint_translator asset-class fallback (never-0 invariant) -------


@pytest.mark.parametrize(
    ("instrument_type", "exp_leverage"),
    [
        ("CURRENCIES", 30.0),
        ("INDICES", 20.0),
        ("COMMODITIES", 20.0),
        ("CRYPTOCURRENCIES", 2.0),
    ],
)
def test_constraint_applies_asset_class_fallback_when_leverage_absent(
    instrument_type: str, exp_leverage: float
) -> None:
    """When the venue payload has neither ``leverage`` nor ``marginFactor``,
    CapitalMarketConstraint.leverage falls back per instrument_type and is
    NEVER 0 (the prior gap left it 0.0 -> 0x notional)."""
    from polaris.venues.capital.constraint_translator import _payload_to_constraint

    body = {"instrument": {"type": instrument_type}, "dealingRules": {}, "snapshot": {}}
    c = _payload_to_constraint("EPIC", body)
    assert c.leverage == exp_leverage


def test_constraint_live_venue_leverage_overrides_fallback() -> None:
    """A live ``leverage > 0`` from the venue takes precedence over the
    asset-class fallback (live constraint wins)."""
    from polaris.venues.capital.constraint_translator import _payload_to_constraint

    body = {
        "instrument": {"type": "CURRENCIES", "leverage": 50.0},
        "dealingRules": {},
        "snapshot": {},
    }
    assert _payload_to_constraint("EPIC", body).leverage == 50.0


def test_constraint_margin_factor_still_wins_over_fallback() -> None:
    """1/marginFactor (live) still takes precedence over the fallback."""
    from polaris.venues.capital.constraint_translator import _payload_to_constraint

    body = {
        "instrument": {"type": "INDICES", "marginFactor": 0.05},  # -> 20:1
        "dealingRules": {},
        "snapshot": {},
    }
    assert _payload_to_constraint("EPIC", body).leverage == pytest.approx(20.0)


# --- registry shape: only 2 venues, no Track C / Alpaca / short-in-use ------


def test_only_two_streams_registered() -> None:
    assert set(STREAMS) == {"A_okx_crypto", "B_capital_cfd"}
    assert set(VENUE_TO_STREAM) == {"okx", "capital"}


def test_no_track_c_present() -> None:
    assert {s.track for s in STREAMS.values()} == {"A", "B"}


def test_stream_a_long_only_stream_b_short_allowed_but_encoded() -> None:
    assert resolve_stream("okx").allow_short is False
    # B short is *allowed* in the encoding but is not exercised this step.
    assert resolve_stream("capital").allow_short is True


# --- roster / asset_class / adapter mapping reflect current code ------------


def test_stream_rosters_match_current_strategy_venue_mapping() -> None:
    a = resolve_stream("okx")
    b = resolve_stream("capital")
    assert a.strategy_roster == frozenset(
        {"volume_burst", "tsmom", "rsi_bb_pullback", "spot_donchian"}
    )
    assert b.strategy_roster == frozenset(
        {"fx_breakout_basket", "xau_indices_trend", "session_breakout"}
    )
    # No strategy belongs to both streams.
    assert a.strategy_roster.isdisjoint(b.strategy_roster)


def test_asset_classes_partition() -> None:
    a = resolve_stream("okx")
    b = resolve_stream("capital")
    assert a.asset_classes == frozenset({"crypto"})
    assert b.asset_classes == frozenset({"forex", "index", "commodity"})


def test_adapter_ref_matches_venue() -> None:
    assert resolve_stream("okx").adapter_ref == "OKXAdapter"
    assert resolve_stream("capital").adapter_ref == "CapitalAdapter"


def test_external_reject_codes_match_capital_set() -> None:
    """B carries the Capital venue-specific non-fault reject codes; A has none."""
    assert resolve_stream("okx").external_reject_codes == frozenset()
    assert resolve_stream("capital").external_reject_codes == frozenset(
        {"MARKET_CLOSED", "MARKET_OFFLINE", "INSTRUMENT_NOT_TRADEABLE", "REJECTED"}
    )


def test_streamconfig_is_frozen() -> None:
    s = resolve_stream("okx")
    assert isinstance(s, StreamConfig)
    with pytest.raises((AttributeError, TypeError)):
        s.track = "B"  # type: ignore[misc]
