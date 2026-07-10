"""StreamConfig SSOT — behavior-identity + per-market leverage tests.

``resolve_stream`` reproduces the venue-binary track/product_class branches
exactly. Leverage is per-market for Capital CFD (T7, /debate-CONFIRMED
b565392): FX 30 / index 20 / commodity 20 / crypto-CFD 2, with the live
``CapitalMarketConstraint.leverage`` overriding the asset-class fallback. OKX
spot leverage stays the invariant fixed 1.0.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.streams import (
    STREAMS,
    VENUE_TO_STREAM,
    StreamConfig,
    derive_leverage,
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
        resolve_stream("kraken")


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


def test_constraint_parses_opening_hours_into_field() -> None:
    """``_payload_to_constraint`` rides the per-epic ``openingHours`` onto the
    constraint (US500 Mon close 21:00) — the per-epic EOD probe's source."""
    from polaris.venues.capital.constraint_translator import _payload_to_constraint

    body = {
        "instrument": {
            "type": "INDICES",
            "openingHours": {
                "mon": ["00:00 - 21:00", "21:05 - 00:00"],
                "tue": ["00:00 - 21:00", "21:05 - 00:00"],
                "wed": ["00:00 - 21:00", "21:05 - 00:00"],
                "thu": ["00:00 - 21:00", "21:05 - 00:00"],
                "fri": ["00:00 - 21:00"],
                "sat": [],
                "sun": ["22:00 - 00:00"],
                "zone": "UTC",
            },
        },
        "dealingRules": {},
        "snapshot": {},
    }
    c = _payload_to_constraint("US500", body)
    assert c.opening_hours is not None
    assert c.opening_hours[0] == ((0, 21 * 3600), (21 * 3600 + 300, 86400))


def test_constraint_opening_hours_none_when_absent() -> None:
    """No ``openingHours`` in the payload → ``opening_hours`` stays None (the
    caller then uses the legacy asset-class fallback)."""
    from polaris.venues.capital.constraint_translator import _payload_to_constraint

    body = {"instrument": {"type": "INDICES"}, "dealingRules": {}, "snapshot": {}}
    assert _payload_to_constraint("EPIC", body).opening_hours is None


# --- registry shape: 3 venues incl. Track C / Alpaca (T11) ------------------


def test_three_streams_registered() -> None:
    assert set(STREAMS) == {"A_okx_crypto", "B_capital_cfd", "C_alpaca_equity"}
    assert set(VENUE_TO_STREAM) == {"okx", "capital", "alpaca"}


def test_track_c_present() -> None:
    assert {s.track for s in STREAMS.values()} == {"A", "B", "C"}


def test_stream_a_long_only_stream_b_short_allowed_but_encoded() -> None:
    assert resolve_stream("okx").allow_short is False
    # B short is *allowed* in the encoding but is not exercised this step.
    assert resolve_stream("capital").allow_short is True


# --- roster / asset_class / adapter mapping reflect current code ------------


def test_stream_rosters_match_current_strategy_venue_mapping() -> None:
    a = resolve_stream("okx")
    b = resolve_stream("capital")
    # volume_burst un-registered 2026-06-27 (#61 — live-churn KILL) +
    # spot_donchian un-registered 2026-06-27 (#56 — stop-bleeders KILL): both
    # dropped from the OKX roster so it stays in sync with STRATEGY_REGISTRY.
    assert a.strategy_roster == frozenset(
        {"rsi_bb_pullback"}
    )
    # session_breakout un-registered 2026-07-06 (B1 prune, live-ledger
    # forensic, -$933.65 fee-bleed) — dropped from the Capital roster.
    assert b.strategy_roster == frozenset(
        {"fx_breakout_basket", "xau_indices_trend"}
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


# --- T8: Track type extended to A/B/C + _sizer_payload 3-way decode ----------


def test_t8_track_alias_includes_c() -> None:
    """Both independent Track aliases must admit "C" (caps + type only; the C
    stream itself is NOT registered until T11)."""
    from typing import get_args

    from polaris.core.sizing.schema import Track as SizingTrack
    from polaris.core.streams.config import Track as StreamTrack

    assert set(get_args(SizingTrack)) == {"A", "B", "C"}
    assert set(get_args(StreamTrack)) == {"A", "B", "C"}


def test_t11_c_stream_registered() -> None:
    """T11 registers the C stream (T8 added only the type + caps)."""
    assert {s.track for s in STREAMS.values()} == {"A", "B", "C"}


def _create_bare_position_risk_tables(conn: sqlite3.Connection) -> None:
    """Minimal ``position_risk_state`` + ``positions`` pair for
    ``_read_portfolio_state`` unit tests — the latter is required by the
    ghost-row-defense JOIN (2026-07-10, ``_sizer_payload._read_portfolio_state``)."""
    conn.execute(
        """
        CREATE TABLE position_risk_state (
            venue TEXT, symbol TEXT, instrument_id TEXT, underlying_group_id TEXT,
            cluster_id TEXT, strategy TEXT, track TEXT, signal_strength REAL,
            open_risk_pct REAL, notional_usd REAL, opened_ts INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE positions (
            venue TEXT, symbol TEXT, strategy_id TEXT, status TEXT,
            opened_ts INTEGER
        )
        """
    )


def test_t8_sizer_payload_decodes_track_c() -> None:
    """``_read_portfolio_state`` must 3-way decode a stored "C" track to "C",
    never silently collapse it to "B"."""
    from polaris.core.pipeline._sizer_payload import _read_portfolio_state

    conn = sqlite3.connect(":memory:")
    _create_bare_position_risk_tables(conn)
    rows = [
        ("alpaca", "AAPL", "AAPL", "AAPL", None, "equity_mom", "C", 0.8, 0.05, 500.0, 1),
        ("okx", "BTC-USDT", "BTC-USDT", "BTC", "crypto:BTC", "tsmom", "A", 0.7, 0.04, 400.0, 2),
        ("capital", "EURUSD", "EURUSD", "EUR", "cfd:FX_MAJORS", "fx_breakout_basket", "B", 0.6, 0.03, 300.0, 3),
    ]
    conn.executemany(
        "INSERT INTO position_risk_state VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.executemany(
        "INSERT INTO positions VALUES (?,?,?,'open',?)",
        [(r[0], r[1], r[5], r[10]) for r in rows],
    )
    conn.commit()
    state = _read_portfolio_state(conn, equity_usd=10_000.0, track="C")
    by_symbol = {p.symbol: p.track for p in state.open_positions}
    assert by_symbol == {"AAPL": "C", "BTC-USDT": "A", "EURUSD": "B"}


def test_t8_sizer_payload_unknown_track_does_not_crash() -> None:
    """An unexpected stored track value is passed through as-is (no silent
    collapse to B); decode never raises on a stray value."""
    from polaris.core.pipeline._sizer_payload import _read_portfolio_state

    conn = sqlite3.connect(":memory:")
    _create_bare_position_risk_tables(conn)
    conn.execute(
        "INSERT INTO position_risk_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("x", "X", "X", "X", None, "s", "Z", 0.5, 0.01, 100.0, 1),
    )
    conn.execute("INSERT INTO positions VALUES ('x', 'X', 's', 'open', 1)")
    conn.commit()
    state = _read_portfolio_state(conn, equity_usd=10_000.0, track="C")
    assert [p.track for p in state.open_positions] == ["Z"]


# --- T11: C_alpaca_equity stream registered (additive; A/B unchanged) --------


def test_resolve_stream_alpaca_is_track_c_equity() -> None:
    """``alpaca`` resolves to the C stream: equity product_class, track C,
    long-only (P0), AlpacaAdapter dispatch."""
    s = resolve_stream("alpaca")
    assert s.stream_id == "C_alpaca_equity"
    assert s.track == "C"
    assert s.product_class == "equity"
    assert s.asset_classes == frozenset({"equity"})
    assert s.allow_short is False  # P0 long-only
    assert s.adapter_ref == "AlpacaAdapter"


def test_resolve_stream_alpaca_leverage_is_fixed_one() -> None:
    """Track C equity is cash (no leverage): fixed 1.0 via derive_leverage,
    asset_class-independent (leverage_source="fixed_1")."""
    s = resolve_stream("alpaca")
    assert s.sizing_profile.leverage_source == "fixed_1"
    assert s.sizing_profile.leverage == 1.0
    assert derive_leverage(s, "equity") == 1.0
    assert derive_leverage(s, "anything") == 1.0


def test_venue_to_stream_maps_alpaca_to_c() -> None:
    assert VENUE_TO_STREAM["alpaca"] == "C_alpaca_equity"


def test_resolve_stream_alpaca_accepts_matching_product_class() -> None:
    assert resolve_stream("alpaca", "equity").stream_id == "C_alpaca_equity"


def test_alpaca_external_reject_codes_avoid_unjust_hard_halt() -> None:
    """Realistic Alpaca paper rejects (market-closed / PDT / buying-power /
    forbidden) are EXTERNAL non-fault codes so they never trip an unjust
    HARD_HALT (lesson 1a315a3).

    H2: these are the SEMANTIC TOKENS the AlpacaAdapter emits after normalizing
    its numeric code + HTTP status (``classify_reject_code``). The raw numeric
    codes / bare HTTP statuses are NO LONGER emitted, so the set holds the four
    semantic external tokens only."""
    codes = resolve_stream("alpaca").external_reject_codes
    assert codes >= frozenset(
        {
            "forbidden",
            "pdt_block",
            "insufficient_buying_power",
            "market_closed",
        }
    )
    # H2: a 422-style validation reject normalizes to ``validation_rejected`` —
    # it must NOT be external so a genuinely anomalous reject still faults.
    from polaris.venues.alpaca.reject_codes import REJECT_VALIDATION

    assert REJECT_VALIDATION not in codes
