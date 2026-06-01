"""Layer 0 unit + property tests.

Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.universe._capital import (
    _capital_market_row_to_instrument,
    _capital_name_matches,
    fetch_capital_instruments,
)
from polaris.core.universe.discovery import (
    _filter_failure_reason,
    apply_active_filters,
    detect_listing_changes,
    merge_listing_timestamps,
    parse_okx_tickers,
    persist_universe,
    rank_active_universe,
)
from polaris.core.universe.schema import (
    FOCUS_QUOTA_ENV_PREFIX,
    FOCUS_TARGET_MAX,
    FOCUS_TARGET_MIN,
    UNIVERSE_RANK_TOP_N_DEFAULT,
    UNIVERSE_RANK_TOP_N_ENV,
    FilterThresholds,
    UniverseInstrument,
    default_thresholds,
    focus_min_quota_by_class,
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
# Continuous active-set ranking (flow_not_block)
# ---------------------------------------------------------------------------


def test_rank_returns_top_n() -> None:
    """Ranking caps the active set at top_n, sorted by descending score."""
    insts = [
        _make_inst(f"S{i}-USDT", vol=1e7 + i * 1e7, atr_pct=2.0 + i * 0.1)
        for i in range(50)
    ]
    out = rank_active_universe(insts, top_n=10)
    assert len(out) == 10
    # Highest vol/atr rows survive; the thinnest are cut.
    surviving = {ins.symbol for ins in out}
    assert "S49-USDT" in surviving
    assert "S0-USDT" not in surviving


def test_rank_includes_mid_liquidity_previously_spread_rejected() -> None:
    """A mid-liquidity symbol the hard spread gate would reject still flows."""
    th = default_thresholds()
    # Wide spread → old gate hard-rejected. Decent vol + atr → strong reward.
    mid = _make_inst(
        "MID-USDT", vol=8e7, atr_pct=5.0, spread_bps=th.max_spread_bps * 3.0, depth=5_000.0
    )
    # Confirm the old hard gate would have dropped it.
    assert apply_active_filters([mid]) == []
    # Fill out the population with weaker rows so MID still ranks into top_n.
    weak = [_make_inst(f"W{i}-USDT", vol=1e6, atr_pct=0.6, depth=2_000.0) for i in range(20)]
    out = rank_active_universe([mid, *weak], top_n=5)
    assert any(ins.symbol == "MID-USDT" for ins in out)


def test_rank_keeps_validity_hard() -> None:
    """Non-live and non-USDT-quote rows are still hard-excluded (validity)."""
    live = _make_inst("GOOD-USDT", vol=5e8)
    halted = _make_inst("HALT-USDT", state="halt", vol=9e9)  # huge vol but dead
    nonusdt = _make_inst("BTC-USDC", quote_ccy="USDC", vol=9e9)
    out = rank_active_universe([live, halted, nonusdt], top_n=10)
    syms = {ins.symbol for ins in out}
    assert syms == {"GOOD-USDT"}


def test_rank_empty_input_safe() -> None:
    assert rank_active_universe([], top_n=10) == []


def test_rank_ties_and_zero_division_safe() -> None:
    """Identical rows (zero stdev) must not raise and return up to top_n."""
    same = [_make_inst(f"T{i}-USDT", vol=1e8, atr_pct=3.0) for i in range(5)]
    out = rank_active_universe(same, top_n=3)
    assert len(out) == 3


def test_rank_top_n_cap_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    insts = [_make_inst(f"E{i}-USDT", vol=1e7 + i * 1e6) for i in range(60)]
    # Default (no env) → UNIVERSE_RANK_TOP_N_DEFAULT.
    monkeypatch.delenv(UNIVERSE_RANK_TOP_N_ENV, raising=False)
    assert len(rank_active_universe(insts)) == UNIVERSE_RANK_TOP_N_DEFAULT
    # Env override is honored and capped at FOCUS_TARGET_MAX.
    monkeypatch.setenv(UNIVERSE_RANK_TOP_N_ENV, "9999")
    assert len(rank_active_universe(insts)) == min(len(insts), FOCUS_TARGET_MAX)
    monkeypatch.setenv(UNIVERSE_RANK_TOP_N_ENV, "5")
    assert len(rank_active_universe(insts)) == 5


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
    assert _capital_name_matches({"name": "Currencies"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Indices"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Commodities"}, CAPITAL_P0_CATEGORY_TOKENS)
    # Crypto is OWNED by OKX track A (Jin 2026-05-30 STEP 0 (a)) — the "crypto"
    # token was removed, so a standalone "Crypto" node no longer matches.
    assert not _capital_name_matches({"name": "Crypto"}, CAPITAL_P0_CATEGORY_TOKENS)
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


# ---------------------------------------------------------------------------
# Asset-class focus quota (STEP 6 — crypto monopoly fix; flow_not_block)
# ---------------------------------------------------------------------------
#
# DEMO/PAPER virtual capital. The quota GUARANTEES under-represented asset
# classes (Capital FX/indices/commodity, Alpaca equity) a minimum number of
# focus slots so 24/7 crypto (huge vol) cannot monopolize the cross-venue focus.
# It is a FLOW INCREASE (more classes reach the order path), NOT a throttle: no
# entry is blocked, no notional is cut, and crypto coverage stays wide.
# vol=0.0 Capital rows (no 24h notional via nav) are still guaranteed slots.


def _cls(symbol: str, *, venue: str, asset_class: str, vol: float) -> UniverseInstrument:
    """Build an instrument for quota tests (cross-venue, mixed asset classes)."""
    return _make_inst(symbol, venue=venue, asset_class=asset_class, vol=vol)


def test_focus_min_quota_defaults_conservative() -> None:
    """Defaults: crypto unbounded (no min); FX/indices/commodity/equity floored."""
    q = focus_min_quota_by_class()
    assert q.get("crypto", 0) == 0  # crypto never floored — it dominates anyway
    for cls in ("forex", "indices", "commodity", "equity"):
        assert q.get(cls, 0) >= 1
    # Conservative: the guaranteed minimums must fit inside the focus window.
    assert sum(q.values()) <= FOCUS_TARGET_MAX


def test_focus_min_quota_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{FOCUS_QUOTA_ENV_PREFIX}FOREX", "5")
    monkeypatch.setenv(f"{FOCUS_QUOTA_ENV_PREFIX}EQUITY", "0")
    q = focus_min_quota_by_class()
    assert q["forex"] == 5
    assert q["equity"] == 0


def test_quota_guarantees_capital_classes_under_crypto_monopoly() -> None:
    """40 high-vol crypto + a few Capital FX/index/gold rows → Capital still in focus."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    capital = [
        _cls("EURUSD", venue="capital", asset_class="forex", vol=0.0),
        _cls("US500", venue="capital", asset_class="indices", vol=0.0),
        _cls("GOLD", venue="capital", asset_class="commodity", vol=0.0),
    ]
    focus = compute_dynamic_focus(crypto + capital, cycle_ts=NOW, target_size=30)
    classes = {(f.venue, f.symbol) for f in focus}
    assert ("capital", "EURUSD") in classes
    assert ("capital", "US500") in classes
    assert ("capital", "GOLD") in classes
    # Flow increase, not throttle: total size unchanged + crypto still dominates.
    assert len(focus) == 30
    crypto_in_focus = sum(1 for f in focus if f.venue == "okx")
    assert crypto_in_focus >= 27  # crypto keeps the bulk of the window


def test_quota_includes_vol_zero_capital_even_with_extra_capital_competition() -> None:
    """vol=0 Capital rows compete only with each other for the quota; top-scored win."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    # Many forex rows, all vol=0 but different ATR so scores differ; quota guarantees
    # at least the min count, drawn from the highest-scored forex rows.
    forex = [
        _make_inst(f"FX{i}", venue="capital", asset_class="forex", vol=0.0, atr_pct=0.3 + i * 0.05)
        for i in range(8)
    ]
    focus = compute_dynamic_focus(crypto + forex, cycle_ts=NOW, target_size=30)
    fx_in_focus = [f for f in focus if f.venue == "capital"]
    qmin = focus_min_quota_by_class()["forex"]
    assert len(fx_in_focus) >= qmin
    assert len(focus) == 30


def test_quota_noop_for_single_class_okx_crypto() -> None:
    """OKX-only crypto universe → every slot is crypto → quota is a no-op."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(50)]
    with_quota = compute_dynamic_focus(crypto, cycle_ts=NOW, target_size=30)
    # Identical to a manual top-30 by score: ordering + membership unchanged.
    assert len(with_quota) == 30
    assert all(f.venue == "okx" for f in with_quota)
    scores = [f.focus_score for f in with_quota]
    assert scores == sorted(scores, reverse=True)


def test_quota_noop_for_single_class_alpaca_equity() -> None:
    """Alpaca-only equity universe → quota is a no-op (no other class to guarantee)."""
    equity = [_cls(f"EQ{i}", venue="alpaca", asset_class="equity", vol=5e8 - i * 1e6) for i in range(40)]
    focus = compute_dynamic_focus(equity, cycle_ts=NOW, target_size=30)
    assert len(focus) == 30
    assert all(f.venue == "alpaca" for f in focus)


def test_quota_preserves_descending_score_order_and_ranks() -> None:
    """After quota promotion the output is still sorted by score desc with 1..N ranks."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    capital = [
        _cls("EURUSD", venue="capital", asset_class="forex", vol=0.0),
        _cls("GOLD", venue="capital", asset_class="commodity", vol=0.0),
    ]
    focus = compute_dynamic_focus(crypto + capital, cycle_ts=NOW, target_size=30)
    scores = [f.focus_score for f in focus]
    assert scores == sorted(scores, reverse=True)
    assert [f.rank for f in focus] == list(range(1, len(focus) + 1))


def test_quota_clamp_preserved_12_48() -> None:
    """Quota never breaks the 12-48 clamp invariant."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(60)]
    capital = [_cls("EURUSD", venue="capital", asset_class="forex", vol=0.0)]
    focus = compute_dynamic_focus(crypto + capital, cycle_ts=NOW)
    assert FOCUS_TARGET_MIN <= len(focus) <= FOCUS_TARGET_MAX


def test_quota_capped_by_available_rows_of_class() -> None:
    """If a class has fewer rows than its quota, take all it has (no fabrication)."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    capital = [_cls("EURUSD", venue="capital", asset_class="forex", vol=0.0)]  # only 1 forex
    focus = compute_dynamic_focus(crypto + capital, cycle_ts=NOW, target_size=30)
    fx = [f for f in focus if f.venue == "capital"]
    assert len(fx) == 1  # only one exists; quota cannot fabricate rows
    assert ("capital", "EURUSD") in {(f.venue, f.symbol) for f in focus}


# ---------------------------------------------------------------------------
# C2a — Capital commodity mis-classification (oil/energy COMPANY shares)
# ---------------------------------------------------------------------------


def _cap_market_row(epic: str, *, instrument_type: str | None = None) -> dict[str, object]:
    """Minimal tradeable Capital `markets` row (bid/offer present so it survives)."""
    row: dict[str, object] = {
        "epic": epic,
        "bid": 100.0,
        "offer": 100.1,
        "high": 101.0,
        "low": 99.0,
        "marketStatus": "TRADEABLE",
    }
    if instrument_type is not None:
        row["instrumentType"] = instrument_type
    return row


def test_oil_company_share_tagged_equity_not_commodity() -> None:
    """CVX/XOM live under an energy-named node but are SHARES → classify equity.

    Forensic: 67 Capital `commodity` rows were oil/energy COMPANY EQUITY share
    CFDs (CVX/XOM/COP/SLB...) mis-tagged because their nav node name carries an
    ``energ``/``oil`` token. The market row's ``instrumentType=SHARES`` is the
    authoritative signal — it must win over the node-name hint.
    """
    for epic in ("CVX", "XOM", "COP", "SLB"):
        inst = _capital_market_row_to_instrument(
            _cap_market_row(epic, instrument_type="SHARES"),
            asset_class_hint="Oil & Gas",  # energy-named node → would mis-tag commodity
            now_ts=NOW,
        )
        assert inst is not None
        assert inst.asset_class == "equity", f"{epic} should be equity, got {inst.asset_class}"


def test_real_commodity_cfd_stays_commodity() -> None:
    """A real commodity CFD (instrumentType=COMMODITIES) under the same node stays commodity."""
    inst = _capital_market_row_to_instrument(
        _cap_market_row("OIL_CRUDE", instrument_type="COMMODITIES"),
        asset_class_hint="Oil & Gas",
        now_ts=NOW,
    )
    assert inst is not None
    assert inst.asset_class == "commodity"


# ---------------------------------------------------------------------------
# C2b — Capital real commodities sit 3 levels deep; the nav walk must reach them
# ---------------------------------------------------------------------------


def _cap_mk(epic: str, itype: str) -> dict[str, object]:
    return {
        "epic": epic, "instrumentName": epic, "instrumentType": itype,
        "bid": 100.0, "offer": 100.1, "high": 101.0, "low": 99.0,
        "marketStatus": "TRADEABLE",
    }


@pytest.mark.asyncio
async def test_capital_walk_reaches_depth3_commodities() -> None:
    """Real precious-metals/energy commodities live at nav depth 3 (commodities_group
    → commodities → precious_metals → markets). The 2-level walk stopped at the
    empty 'commodities' node and fetched ZERO real commodities (forensic). The walk
    must descend deeper so GOLD/SILVER/OIL_CRUDE are fetched as commodity, while a
    depth-2 forex tree (currencies node → markets) stays unchanged."""
    # nodeId → response body
    tree: dict[str, dict[str, object]] = {
        "_root": {"nodes": [
            {"id": "commodities_group", "name": "commodities_group"},
            {"id": "forex", "name": "Forex"},
        ]},
        # commodities: 3 levels deep
        "commodities_group": {"markets": [], "nodes": [{"id": "commodities", "name": "Commodities"}]},
        "commodities": {"markets": [], "nodes": [
            {"id": "precious_metals", "name": "Precious metals"},
            {"id": "energies", "name": "Energies"},
        ]},
        "precious_metals": {"markets": [_cap_mk("GOLD", "COMMODITIES"), _cap_mk("SILVER", "COMMODITIES")], "nodes": []},
        "energies": {"markets": [_cap_mk("OIL_CRUDE", "COMMODITIES")], "nodes": []},
        # forex: 2 levels deep (unchanged path)
        "forex": {"markets": [], "nodes": [{"id": "currencies.usd", "name": "USD"}]},
        "currencies.usd": {"markets": [_cap_mk("EURUSD", "CURRENCIES")], "nodes": []},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/session"):
            return httpx.Response(200, headers={"CST": "c", "X-SECURITY-TOKEN": "s"}, json={})
        if path.endswith("/marketnavigation"):
            return httpx.Response(200, json=tree["_root"])
        node_id = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=tree.get(node_id, {"markets": [], "nodes": []}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://demo-api"
    ) as cli:
        out = await fetch_capital_instruments(
            api_key="k", email="e", password="p", client=cli, now_ts=NOW
        )

    by_sym = {i.symbol: i.asset_class for i in out}
    assert by_sym.get("GOLD") == "commodity", f"GOLD not fetched: {by_sym}"
    assert by_sym.get("SILVER") == "commodity"
    assert by_sym.get("OIL_CRUDE") == "commodity"
    assert by_sym.get("EURUSD") == "forex"  # depth-2 forex path unchanged


# ---------------------------------------------------------------------------
# Capital FX-majors keep/floor (P1 stream-coverage — flow_not_block, per-venue)
# ---------------------------------------------------------------------------
# DEMO/PAPER virtual capital. The vol-dominant + 0.45·ATR rank lets high-ATR
# EXOTIC FX crosses (USDZAR/NOKSEK) outrank quiet FX MAJORS (EURUSD/USDJPY/...),
# so the majors never reach the active set and fx_breakout_basket / session_breakout
# never receive a tradeable symbol. The fix GUARANTEES curated Capital FX majors
# are seated (active AND, via the forex quota, focus) ALONGSIDE the exotics —
# a FLOW INCREASE (seat BOTH, remove nothing), never a throttle. OKX/Alpaca
# ranking stays byte-identical (no global RANK_SCORE_W_* change).


def _cap_fx(symbol: str, *, atr_pct: float, spread_bps: float = 1.0, state: str = "live") -> UniverseInstrument:
    """Capital FX row (vol=0.0 like the real nav tree; ATR is the only score driver)."""
    return _make_inst(
        symbol,
        venue="capital",
        asset_class="forex",
        quote_ccy="USD",
        vol=0.0,
        atr_pct=atr_pct,
        spread_bps=spread_bps,
        depth=0.0,
        state=state,
    )


def test_is_capital_fx_major_normalizes_format() -> None:
    from polaris.core.universe.schema import is_capital_fx_major

    assert is_capital_fx_major("capital", "EURUSD")
    assert is_capital_fx_major("capital", "eur/usd")
    assert is_capital_fx_major("capital", "EURUSD_W")  # weekend epic variant
    assert is_capital_fx_major("CAPITAL", "USDJPY")
    # Not a major / not Capital → False.
    assert not is_capital_fx_major("capital", "USDZAR")
    assert not is_capital_fx_major("okx", "EURUSD")  # OKX is crypto-only; not floored


def test_rank_keeps_capital_fx_majors_when_exotics_outrank_on_atr() -> None:
    """Exotic crosses outrank majors on ATR, but the curated majors are STILL seated."""
    from polaris.core.universe.schema import CAPITAL_FX_MAJORS

    # High-ATR exotic crosses (would win the rank); enough of them to fill top_n.
    exotics = [
        _cap_fx(sym, atr_pct=atr)
        for sym, atr in (
            ("USDZAR", 0.98), ("NOKSEK", 0.80), ("USDMXN", 0.75),
            ("USDTRY", 0.90), ("EURNOK", 0.70), ("USDSEK", 0.65),
        )
    ]
    # Quiet majors — lowest ATR, would be cut by a pure top_n rank.
    majors = [_cap_fx(sym, atr_pct=0.08) for sym in sorted(CAPITAL_FX_MAJORS)]
    out = rank_active_universe([*exotics, *majors], top_n=5)
    seated = {ins.symbol for ins in out}
    # All curated majors are seated DESPITE losing the score sort (flow_not_block).
    for m in CAPITAL_FX_MAJORS:
        assert m in seated, f"{m} must be kept in the active set"
    # Exotics are NOT removed — the highest-ATR exotic still survives (seat BOTH).
    assert "USDZAR" in seated


def test_rank_capital_fx_major_floor_only_when_live() -> None:
    """A non-live (session-wait) FX major is NOT force-kept — state validity stays hard."""
    halted_major = _cap_fx("EURUSD", atr_pct=0.08, state="market_closed")
    live_exotic = _cap_fx("USDZAR", atr_pct=0.98)
    out = rank_active_universe([halted_major, live_exotic], top_n=5)
    seated = {ins.symbol for ins in out}
    assert "EURUSD" not in seated  # non-live major excluded by the hard validity gate
    assert "USDZAR" in seated


def test_rank_capital_floor_does_not_touch_okx_ranking() -> None:
    """OKX-only crypto universe: the Capital floor is a no-op (byte-identical active set)."""
    crypto = [_make_inst(f"C{i}-USDT", vol=1e7 + i * 1e7, atr_pct=2.0 + i * 0.1) for i in range(50)]
    out = rank_active_universe(crypto, top_n=10)
    assert len(out) == 10
    assert all(ins.venue == "okx" for ins in out)
    assert "C49-USDT" in {ins.symbol for ins in out}
    assert "C0-USDT" not in {ins.symbol for ins in out}


def test_quota_seats_majors_over_exotics_within_forex_quota() -> None:
    """When the forex quota promotes rows, curated MAJORS are prioritized over exotics."""
    from polaris.core.universe.schema import CAPITAL_FX_MAJORS, focus_min_quota_by_class

    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    # Exotics score higher (higher ATR); majors score lowest. Mix > the forex quota.
    exotics = [_cap_fx(sym, atr_pct=atr) for sym, atr in (
        ("USDZAR", 0.98), ("NOKSEK", 0.80), ("USDMXN", 0.75), ("USDTRY", 0.90),
    )]
    majors = [_cap_fx(sym, atr_pct=0.08) for sym in sorted(CAPITAL_FX_MAJORS)]
    focus = compute_dynamic_focus(crypto + exotics + majors, cycle_ts=NOW, target_size=30)
    qmin = focus_min_quota_by_class()["forex"]
    fx_in_focus = [f for f in focus if f.venue == "capital"]
    assert len(fx_in_focus) >= qmin
    # The forex quota seats curated majors, not just the highest-ATR exotics.
    majors_in_focus = sum(1 for f in fx_in_focus if f.symbol in CAPITAL_FX_MAJORS)
    assert majors_in_focus >= 1, "at least one curated major must reach focus via the quota"
    # Flow increase, not throttle: total size unchanged.
    assert len(focus) == 30


def test_quota_majors_priority_noop_when_no_majors_present() -> None:
    """All-exotic forex universe → quota behaves exactly as before (no major to prioritize)."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    exotics = [_cap_fx(sym, atr_pct=atr) for sym, atr in (
        ("USDZAR", 0.98), ("NOKSEK", 0.80), ("USDMXN", 0.75), ("USDTRY", 0.90), ("EURNOK", 0.70),
    )]
    focus = compute_dynamic_focus(crypto + exotics, cycle_ts=NOW, target_size=30)
    fx_in_focus = [f for f in focus if f.venue == "capital"]
    qmin = focus_min_quota_by_class()["forex"]
    assert len(fx_in_focus) >= min(qmin, len(exotics))
    assert len(focus) == 30


# ---------------------------------------------------------------------------
# Alpaca liquid-equity focus priority (P1.5 — megacaps reach focus; flow_not_block)
# ---------------------------------------------------------------------------
# DEMO/PAPER virtual capital. Equity rows carry REAL vol (close×volume) + an
# intraday-range ATR proxy. Megacaps/top-ETFs have huge dollar-volume but LOW
# realized ATR%, while penny/small-caps (ADTX/ZCMD) have tiny dollar-volume but
# HUGE ATR%. The vol+ATR composite + the equity focus quota can let high-ATR
# penny names seat AHEAD of liquid megacaps. Mirroring the Capital FX-major
# priority, the equity quota now PRIORITIZES curated liquid equities so megacaps
# /top-ETFs seat ALONGSIDE (never replacing) the penny names — a FLOW INCREASE,
# not a throttle, touching no global RANK_SCORE_W_* (OKX byte-identical no-op).


def _eq(symbol: str, *, vol: float, atr_pct: float) -> UniverseInstrument:
    """Alpaca equity row with real vol + ATR (mirrors enriched _alpaca rows)."""
    return _make_inst(
        symbol,
        venue="alpaca",
        asset_class="equity",
        quote_ccy="USD",
        vol=vol,
        atr_pct=atr_pct,
        spread_bps=2.0,
        depth=0.0,
    )


def test_is_liquid_equity_recognizes_megacaps_and_etfs() -> None:
    from polaris.core.universe.schema import is_liquid_equity

    assert is_liquid_equity("alpaca", "AAPL")
    assert is_liquid_equity("alpaca", "nvda")  # case-insensitive
    assert is_liquid_equity("alpaca", "SPY")  # top ETF
    assert is_liquid_equity("ALPACA", "MSFT")
    # Not a curated liquid name / not Alpaca → False.
    assert not is_liquid_equity("alpaca", "ADTX")  # penny small-cap
    assert not is_liquid_equity("okx", "AAPL")  # OKX is crypto-only; not floored


def test_liquid_seed_symbols_are_all_liquid_equity() -> None:
    """SSOT: every _alpaca seed symbol is recognized by the schema helper."""
    from polaris.core.universe._alpaca import LIQUID_SEED_SYMBOLS
    from polaris.core.universe.schema import is_liquid_equity

    for sym in LIQUID_SEED_SYMBOLS:
        assert is_liquid_equity("alpaca", sym), f"{sym} must be a liquid equity"


def test_quota_seats_megacaps_over_penny_names_within_equity_quota() -> None:
    """Mixed equity active set → the equity quota seats megacaps ahead of high-ATR penny names."""
    from polaris.core.universe.schema import focus_min_quota_by_class, is_liquid_equity

    # Crypto fills the bulk of the window with the top score.
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    # Penny/small-caps: tiny dollar-volume but HUGE ATR% → they out-score megacaps.
    penny = [
        _eq("ADTX", vol=2e6, atr_pct=18.0),
        _eq("ZCMD", vol=3e6, atr_pct=15.0),
        _eq("XPON", vol=1e6, atr_pct=14.0),
        _eq("GNS", vol=2e6, atr_pct=20.0),
    ]
    # Megacaps/top-ETFs: huge dollar-volume but LOW ATR% → would lose the score sort.
    megacaps = [
        _eq("AAPL", vol=8e9, atr_pct=1.2),
        _eq("MSFT", vol=7e9, atr_pct=1.1),
        _eq("NVDA", vol=9e9, atr_pct=2.0),
        _eq("SPY", vol=2e10, atr_pct=0.8),
    ]
    focus = compute_dynamic_focus(crypto + penny + megacaps, cycle_ts=NOW, target_size=30)
    eq_in_focus = [f for f in focus if f.venue == "alpaca"]
    qmin = focus_min_quota_by_class()["equity"]
    assert len(eq_in_focus) >= qmin
    # The equity quota seats curated megacaps, not just the highest-ATR penny names.
    liquid_in_focus = sum(1 for f in eq_in_focus if is_liquid_equity("alpaca", f.symbol))
    assert liquid_in_focus >= 1, "at least one megacap/top-ETF must reach focus via the quota"
    assert len(focus) == 30  # flow increase, not throttle: total size unchanged


def test_quota_equity_priority_noop_when_no_liquid_present() -> None:
    """All-penny equity universe → quota behaves exactly as before (no megacap to prioritize)."""
    crypto = [_cls(f"C{i}-USDT", venue="okx", asset_class="crypto", vol=1e9 - i * 1e6) for i in range(40)]
    penny = [
        _eq("ADTX", vol=2e6, atr_pct=18.0),
        _eq("ZCMD", vol=3e6, atr_pct=15.0),
        _eq("XPON", vol=1e6, atr_pct=14.0),
        _eq("GNS", vol=2e6, atr_pct=20.0),
        _eq("CEI", vol=1e6, atr_pct=12.0),
    ]
    focus = compute_dynamic_focus(crypto + penny, cycle_ts=NOW, target_size=30)
    eq_in_focus = [f for f in focus if f.venue == "alpaca"]
    qmin = focus_min_quota_by_class()["equity"]
    assert len(eq_in_focus) >= min(qmin, len(penny))
    assert len(focus) == 30
