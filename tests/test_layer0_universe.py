"""Layer 0 unit + property tests.

Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.universe.discovery import (
    _capital_name_matches,
    _filter_failure_reason,
    apply_active_filters,
    detect_listing_changes,
    merge_listing_timestamps,
    parse_okx_tickers,
    persist_universe,
)
from polaris.core.universe.schema import (
    FOCUS_TARGET_MAX,
    FOCUS_TARGET_MIN,
    FilterThresholds,
    UniverseInstrument,
    default_thresholds,
)
from polaris.core.universe.watchlist import (
    compute_dynamic_focus,
    compute_dynamic_target_size,
    persist_focus,
    score_focus_candidates,
    should_evict_from_focus,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inst(
    symbol: str = "BTC-USDT",
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    quote_ccy: str = "USDT",
    state: str = "live",
    vol: float = 5e8,
    spread_bps: float = 2.0,
    atr_pct: float = 4.0,
    depth: float = 200_000.0,
    listing_ts: int | None = None,
    last_seen: int = NOW,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol.split('-')[0]}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state=state,
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        signal_density_7d=0.0,
        listing_ts=listing_ts,
        last_seen_ts=last_seen,
    )


# ---------------------------------------------------------------------------
# 4-axis filter
# ---------------------------------------------------------------------------


def test_4_axis_filter_pass_default() -> None:
    btc = _make_inst("BTC-USDT", vol=8e8, spread_bps=1.0, atr_pct=4.0, depth=400_000.0)
    out = apply_active_filters([btc])
    assert out == [btc]


def test_4_axis_filter_rejects_thin_vol() -> None:
    th = default_thresholds()
    weak = _make_inst("FOO-USDT", vol=th.min_vol_24h_usd / 10.0)
    assert apply_active_filters([weak]) == []


def test_4_axis_filter_rejects_wide_spread() -> None:
    bad = _make_inst("FOO-USDT", spread_bps=25.0)
    assert apply_active_filters([bad]) == []


def test_4_axis_filter_rejects_dead_atr() -> None:
    dead = _make_inst("FOO-USDT", atr_pct=0.5)
    assert apply_active_filters([dead]) == []


def test_4_axis_filter_rejects_thin_depth() -> None:
    thin = _make_inst("FOO-USDT", depth=1_000.0)
    assert apply_active_filters([thin]) == []


def test_4_axis_filter_rejects_non_live() -> None:
    halt = _make_inst("FOO-USDT", state="halt")
    assert apply_active_filters([halt]) == []


def test_4_axis_filter_is_hard_no_zero_relaxation() -> None:
    """Spec L0 Q2: all axes must pass. Zero vol/depth = fail (no venue exemption)."""
    cfd_zero = _make_inst(
        "EURUSD",
        venue="capital",
        asset_class="forex",
        quote_ccy="USD",
        vol=0.0,
        depth=0.0,
        spread_bps=2.0,
        atr_pct=3.0,
    )
    assert apply_active_filters([cfd_zero]) == []

    # Same row with real liquidity proxies populated → passes.
    cfd_with_proxy = _make_inst(
        "EURUSD",
        venue="capital",
        asset_class="forex",
        quote_ccy="USD",
        vol=5e8,
        depth=200_000.0,
        spread_bps=2.0,
        atr_pct=3.0,
    )
    assert apply_active_filters([cfd_with_proxy]) == [cfd_with_proxy]


def test_4_axis_filter_custom_thresholds() -> None:
    th = FilterThresholds(
        min_vol_24h_usd=1.0, max_spread_bps=100.0, min_atr_24h_pct=0.0, min_depth_10bps_usd=0.0
    )
    sample = _make_inst("FOO-USDT", vol=10.0, spread_bps=50.0, atr_pct=0.1, depth=1.0)
    assert apply_active_filters([sample], th) == [sample]


# ---------------------------------------------------------------------------
# Dynamic focus
# ---------------------------------------------------------------------------


def test_dynamic_target_size_within_bounds() -> None:
    assert compute_dynamic_target_size(active_count=100) == 30
    # Spec L0 Q3: clip to [12, 48] regardless of active_count. Downstream
    # `compute_dynamic_focus` truncates to len(active), so a small universe is
    # naturally bounded by available rows.
    assert compute_dynamic_target_size(active_count=5) == 30
    huge = compute_dynamic_target_size(active_count=200, recent_signal_density_top_q=0.9)
    assert FOCUS_TARGET_MIN <= huge <= FOCUS_TARGET_MAX


def test_dynamic_target_size_low_concentration_shrinks() -> None:
    out = compute_dynamic_target_size(active_count=100, top_score_concentration=0.1)
    assert out <= 30


def test_compute_dynamic_focus_sorted_desc_and_bounded() -> None:
    actives = [
        _make_inst(f"COIN{i}-USDT", vol=1e8 * (50 - i), atr_pct=3.0 + (i % 5) * 0.4)
        for i in range(50)
    ]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    assert FOCUS_TARGET_MIN <= len(focus) <= FOCUS_TARGET_MAX
    scores = [f.focus_score for f in focus]
    assert scores == sorted(scores, reverse=True)
    ranks = [f.rank for f in focus]
    assert ranks == list(range(1, len(focus) + 1))


def test_compute_dynamic_focus_listing_watch_bucket() -> None:
    fresh_listing = _make_inst("NEW-USDT", listing_ts=NOW - 1800)  # 30min old
    seasoned = _make_inst("BTC-USDT", vol=9e8, listing_ts=NOW - 365 * 24 * 3600)
    focus = compute_dynamic_focus([fresh_listing, seasoned], cycle_ts=NOW, target_size=2)
    by_id = {(f.venue, f.symbol): f for f in focus}
    assert by_id[("okx", "NEW-USDT")].bucket == "listing_watch"
    assert by_id[("okx", "BTC-USDT")].bucket in {"core", "satellite"}


def test_score_focus_candidates_empty_returns_empty() -> None:
    assert score_focus_candidates([]) == []


# ---------------------------------------------------------------------------
# Listing detection
# ---------------------------------------------------------------------------


def test_new_listing_detection() -> None:
    prev = [_make_inst("BTC-USDT"), _make_inst("ETH-USDT")]
    curr = [_make_inst("BTC-USDT"), _make_inst("ETH-USDT"), _make_inst("ZEC-USDT")]
    new, delisted = detect_listing_changes(prev, curr, now_ts=NOW)
    assert [n.symbol for n in new] == ["ZEC-USDT"]
    # New row must have listing_ts populated for the watchdog to fire.
    assert new[0].listing_ts == NOW
    assert delisted == []


def test_merge_listing_timestamps_preserves_prev_and_stamps_new() -> None:
    prev = [_make_inst("BTC-USDT", listing_ts=NOW - 365 * 24 * 3600)]
    curr_raw = [_make_inst("BTC-USDT"), _make_inst("NEW-USDT")]
    merged = merge_listing_timestamps(prev, curr_raw, now_ts=NOW)
    by_sym = {ins.symbol: ins for ins in merged}
    assert by_sym["BTC-USDT"].listing_ts == NOW - 365 * 24 * 3600  # carried over
    assert by_sym["NEW-USDT"].listing_ts == NOW  # freshly stamped


def test_compute_dynamic_focus_listing_watch_via_merged_ts() -> None:
    """End-to-end watchdog: detect_listing_changes stamps listing_ts → bucket fires."""
    prev: list = []
    curr_raw = [_make_inst("BTC-USDT", vol=9e8), _make_inst("NEW-USDT", vol=8e8)]
    merged = merge_listing_timestamps(prev, curr_raw, now_ts=NOW)
    focus = compute_dynamic_focus(merged, cycle_ts=NOW, target_size=2)
    by_sym = {f.symbol: f for f in focus}
    # Both are < 24h old → both listing_watch.
    assert by_sym["NEW-USDT"].bucket == "listing_watch"
    assert by_sym["BTC-USDT"].bucket == "listing_watch"


def test_delisting_detection() -> None:
    prev = [_make_inst("BTC-USDT"), _make_inst("OLD-USDT")]
    curr = [_make_inst("BTC-USDT")]
    new, delisted = detect_listing_changes(prev, curr)
    assert new == []
    assert delisted == ["okx:OLD-USDT"]


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


def test_should_evict_from_focus_only_bottom_quartile_dead() -> None:
    assert should_evict_from_focus(cell_quartile=0.0, trades_28d=0, signal_hits_7d=0)
    assert not should_evict_from_focus(cell_quartile=0.5, trades_28d=0, signal_hits_7d=0)
    assert not should_evict_from_focus(cell_quartile=0.0, trades_28d=3, signal_hits_7d=0)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persist_universe_upsert(memdb) -> None:  # type: ignore[no-untyped-def]
    inst = _make_inst("BTC-USDT")
    persist_universe(memdb, [inst])
    row = memdb.execute("SELECT venue, symbol, is_active FROM universe").fetchone()
    assert row == ("okx", "BTC-USDT", 1)


def test_persist_universe_marks_filtered_inactive(memdb) -> None:  # type: ignore[no-untyped-def]
    good = _make_inst("BTC-USDT", vol=8e8)
    bad = _make_inst("DEAD-USDT", vol=1.0)
    persist_universe(memdb, [good, bad], is_active_set={good.instrument_id})
    rows = memdb.execute("SELECT symbol, is_active FROM universe ORDER BY symbol").fetchall()
    assert dict(rows) == {"BTC-USDT": 1, "DEAD-USDT": 0}


def test_persist_universe_writes_active_reason(memdb) -> None:  # type: ignore[no-untyped-def]
    good = _make_inst("BTC-USDT", vol=8e8)
    bad = _make_inst("DEAD-USDT", vol=1.0)  # vol axis fails
    persist_universe(memdb, [good, bad], is_active_set={good.instrument_id})
    rows = memdb.execute(
        "SELECT symbol, is_active, active_reason FROM universe ORDER BY symbol"
    ).fetchall()
    rmap = {r[0]: (r[1], r[2]) for r in rows}
    assert rmap["BTC-USDT"][0] == 1 and rmap["BTC-USDT"][1] is None
    assert rmap["DEAD-USDT"][0] == 0
    assert rmap["DEAD-USDT"][1] is not None and "vol" in rmap["DEAD-USDT"][1]


def test_filter_failure_reason_first_axis() -> None:
    th = default_thresholds()
    halt = _make_inst("X-USDT", state="halt")
    spread = _make_inst("X-USDT", spread_bps=99.0)
    atr = _make_inst("X-USDT", atr_pct=0.1)
    vol = _make_inst("X-USDT", vol=10.0)
    depth = _make_inst("X-USDT", depth=10.0)
    assert _filter_failure_reason(halt, th).startswith("state=")
    assert _filter_failure_reason(spread, th).startswith("spread_bps=")
    assert _filter_failure_reason(atr, th).startswith("atr_pct=")
    assert _filter_failure_reason(vol, th).startswith("vol_usd=")
    assert _filter_failure_reason(depth, th).startswith("depth_usd=")


def test_capital_name_matches_p0_categories() -> None:
    from polaris.core.universe.discovery import CAPITAL_P0_CATEGORY_TOKENS

    assert _capital_name_matches({"name": "Forex"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Indices"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Commodities"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Crypto"}, CAPITAL_P0_CATEGORY_TOKENS)
    # Shares = P2 by spec; must NOT match.
    assert not _capital_name_matches({"name": "Shares"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert not _capital_name_matches({"name": "ETFs"}, CAPITAL_P0_CATEGORY_TOKENS)


def test_persist_focus_upsert(memdb) -> None:  # type: ignore[no-untyped-def]
    actives = [_make_inst(f"X{i}-USDT", vol=(i + 1) * 1e8) for i in range(20)]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    persist_focus(memdb, focus)
    count = memdb.execute(
        "SELECT COUNT(*) FROM watchlist_focus WHERE cycle_ts = ?", (NOW,)
    ).fetchone()[0]
    assert count == len(focus)


# ---------------------------------------------------------------------------
# OKX parser
# ---------------------------------------------------------------------------


def test_parse_okx_tickers_filters_non_usdt() -> None:
    rows = [
        {
            "instId": "BTC-USDT",
            "last": "60000",
            "bidPx": "60000",
            "askPx": "60001",
            "high24h": "61000",
            "low24h": "59000",
            "volCcyQuote24h": "5e8",
            "bidSz": "1",
            "askSz": "1",
        },
        {
            "instId": "BTC-USDC",
            "last": "60000",
            "bidPx": "60000",
            "askPx": "60001",
            "high24h": "61000",
            "low24h": "59000",
            "volCcyQuote24h": "5e8",
            "bidSz": "1",
            "askSz": "1",
        },
    ]
    out = parse_okx_tickers(rows, now_ts=NOW)
    assert [i.symbol for i in out] == ["BTC-USDT"]
    assert out[0].vol_24h_usd == pytest.approx(5e8, rel=1e-6)
    assert out[0].atr_24h_pct == pytest.approx((61000 - 59000) / 60000 * 100.0)


def test_parse_okx_tickers_drops_zero_price() -> None:
    rows = [
        {
            "instId": "ZERO-USDT",
            "last": "0",
            "bidPx": "0",
            "askPx": "0",
            "high24h": "0",
            "low24h": "0",
            "volCcyQuote24h": "0",
            "bidSz": "0",
            "askSz": "0",
        }
    ]
    assert parse_okx_tickers(rows, now_ts=NOW) == []


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=80, deadline=None)
@given(
    active_count=st.integers(min_value=0, max_value=300),
    sig=st.floats(min_value=0.0, max_value=1.0),
    conc=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_dynamic_target_size_bounded(active_count: int, sig: float, conc: float) -> None:
    out = compute_dynamic_target_size(
        active_count=active_count,
        recent_signal_density_top_q=sig,
        top_score_concentration=conc,
    )
    assert 0 <= out <= FOCUS_TARGET_MAX
    if active_count >= FOCUS_TARGET_MIN:
        assert FOCUS_TARGET_MIN <= out <= FOCUS_TARGET_MAX


@settings(max_examples=40, deadline=None)
@given(
    n=st.integers(min_value=0, max_value=15),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_property_compute_dynamic_focus_size(n: int, seed: int) -> None:
    actives = [
        _make_inst(
            f"P{i}-USDT",
            vol=1e8 + (seed + i) * 1e6,
            atr_pct=2.0 + ((seed + i) % 7) * 0.3,
        )
        for i in range(n)
    ]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    assert len(focus) <= len(actives)
    assert len(focus) <= FOCUS_TARGET_MAX
