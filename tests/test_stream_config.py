"""StreamConfig SSOT — behavior-identity tests (design §2.2).

The whole point of this step is that ``resolve_stream`` reproduces the existing
venue-binary branches *exactly*. We pin track / leverage / product_class to the
literal current code (``_production_run_signal`` lines 123-124,
``CFD_LEVERAGE_DEFAULT``) so any future drift fails loudly.
"""

from __future__ import annotations

import pytest

from polaris.core.streams import (
    STREAMS,
    VENUE_TO_STREAM,
    StreamConfig,
    resolve_stream,
)

# --- behavior identity vs the current venue-binary branches -----------------


@pytest.mark.parametrize(
    ("venue", "exp_track", "exp_leverage", "exp_product_class"),
    [
        ("okx", "A", 1.0, "spot"),
        ("capital", "B", 30.0, "cfd"),
    ],
)
def test_resolve_stream_matches_venue_binary(
    venue: str, exp_track: str, exp_leverage: float, exp_product_class: str
) -> None:
    """``track = "A" if venue=="okx" else "B"`` and
    ``leverage = 1.0 if venue=="okx" else CFD_LEVERAGE_DEFAULT`` reproduced."""
    s = resolve_stream(venue)
    # mirror of _production_run_signal.py:123-124
    binary_track = "A" if venue == "okx" else "B"
    binary_leverage = 1.0 if venue == "okx" else 30.0
    assert s.track == binary_track == exp_track
    assert s.sizing_profile.leverage == binary_leverage == exp_leverage
    assert s.product_class == exp_product_class


def test_resolve_stream_cfd_leverage_equals_current_default() -> None:
    """B leverage must equal the live ``CFD_LEVERAGE_DEFAULT`` constant."""
    from polaris.scripts._production_run_signal import CFD_LEVERAGE_DEFAULT

    assert resolve_stream("capital").sizing_profile.leverage == CFD_LEVERAGE_DEFAULT


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
