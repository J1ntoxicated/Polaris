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
    _active_exclusion_reason,
    apply_active_filters,
    detect_listing_changes,
    liqfloor_trade_annotation,
    merge_listing_timestamps,
    parse_okx_tickers,
    persist_universe,
    rank_active_universe,
)
from polaris.core.universe.schema import (
    UNIVERSE_RANK_TOP_N_DEFAULT,
    UNIVERSE_RANK_TOP_N_ENV,
    UNIVERSE_WATCH_MAX_DEFAULT,
    UNIVERSE_WATCH_MAX_ENV,
    FilterThresholds,
    UniverseInstrument,
    default_thresholds,
    universe_watch_max,
)
from polaris.core.universe.watchlist import (
    compute_dynamic_focus,
    persist_focus,
    score_focus_candidates,
    should_evict_from_focus,
)

NOW = 1_780_000_000


async def _no_sleep(*_args: object, **_kwargs: object) -> None:
    """Patch for asyncio.sleep — zero out retry-backoff / walk-throttle in tests."""
    return None


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
    """A mid-liquidity name the TIGHT smoke spread gate rejects still flows in rank.

    The continuous rank must NOT replicate the over-cut smoke ``apply_active_filters``
    (tight 10bp spread / 25k depth → the 189->6 over-cut). A name whose 25bp spread
    trips the smoke gate but clears the COARSE universe-eligibility floor (OKX 30bp /
    20M$ / 25k depth, Jin-approved 2026-06-22) still reaches the active set — the
    floor excludes only loss-certain junk, the rank orders everything above it.
    """
    th = default_thresholds()
    # 25bp spread: > smoke 10bp gate (rejected there) but <= 30bp coarse floor (eligible).
    # Vol 8e7 > 20M floor, depth 50k > 25k floor → clears the coarse eligibility floor.
    mid = _make_inst(
        "MID-USDT", vol=8e7, atr_pct=5.0, spread_bps=th.max_spread_bps * 2.5, depth=50_000.0
    )
    # Confirm the old TIGHT smoke gate would have dropped it (on spread).
    assert apply_active_filters([mid]) == []
    # Fill out the population with weaker (but still eligible) rows so MID ranks in.
    weak = [
        _make_inst(f"W{i}-USDT", vol=2.5e7, atr_pct=0.6, spread_bps=8.0, depth=30_000.0)
        for i in range(20)
    ]
    out = rank_active_universe([mid, *weak], top_n=5)
    assert any(ins.symbol == "MID-USDT" for ins in out)


def test_rank_keeps_validity_hard() -> None:
    """Non-live rows are still hard-excluded (validity). STEP 2 scope-widen: a
    USD-equivalent (USDC/USD) or normalized crypto quote is now VALID — only the
    non-live (halt) row is dropped; the parser is the OKX admission SSOT now."""
    live = _make_inst("GOOD-USDT", vol=5e8)
    halted = _make_inst("HALT-USDT", state="halt", vol=9e9)  # huge vol but dead
    usdc = _make_inst("BTC-USDC", quote_ccy="USDC", vol=9e9)   # USD-equiv → valid
    crypto_q = _make_inst("BTC-ETH", quote_ccy="ETH", vol=9e9)  # normalized → valid
    out = rank_active_universe([live, halted, usdc, crypto_q], top_n=10)
    syms = {ins.symbol for ins in out}
    assert syms == {"GOOD-USDT", "BTC-USDC", "BTC-ETH"}
    assert "HALT-USDT" not in syms  # non-live still hard-excluded


def test_rank_empty_input_safe() -> None:
    assert rank_active_universe([], top_n=10) == []


def test_rank_ties_and_zero_division_safe() -> None:
    """Identical rows (zero stdev) must not raise and return up to top_n."""
    same = [_make_inst(f"T{i}-USDT", vol=1e8, atr_pct=3.0) for i in range(5)]
    out = rank_active_universe(same, top_n=3)
    assert len(out) == 3


def test_rank_top_n_cap_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Population above the DEFAULT (1500) so the default cap actually binds.
    insts = [
        _make_inst(f"E{i}-USDT", vol=1e7 + i * 1e6)
        for i in range(UNIVERSE_RANK_TOP_N_DEFAULT + 100)
    ]
    monkeypatch.delenv(UNIVERSE_WATCH_MAX_ENV, raising=False)
    # Default (no env) → UNIVERSE_RANK_TOP_N_DEFAULT (1500, decoupled from focus 48).
    monkeypatch.delenv(UNIVERSE_RANK_TOP_N_ENV, raising=False)
    assert len(rank_active_universe(insts)) == UNIVERSE_RANK_TOP_N_DEFAULT
    # Env override is honored and capped at the WATCH_MAX ceiling (NOT focus 48).
    monkeypatch.setenv(UNIVERSE_RANK_TOP_N_ENV, "99999")
    assert len(rank_active_universe(insts)) == min(len(insts), UNIVERSE_WATCH_MAX_DEFAULT)
    monkeypatch.setenv(UNIVERSE_RANK_TOP_N_ENV, "5")
    assert len(rank_active_universe(insts)) == 5


def test_watch_max_decoupled_from_focus_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # WATCH/TRADE decouple: the active/watch set can exceed FOCUS_TARGET_MAX (48).
    # 100 names + a raised rank top_n → active set > 48 (was clamped to 48 before).
    monkeypatch.delenv(UNIVERSE_WATCH_MAX_ENV, raising=False)
    insts = [_make_inst(f"W{i}-USDT", vol=1e7 + i * 1e6) for i in range(100)]
    out = rank_active_universe(insts, top_n=100)
    assert len(out) == 100  # > FOCUS_TARGET_MAX (48) — watch is no longer pinned
    # POLARIS_WATCH_MAX is the resource guard ceiling, env-tunable.
    monkeypatch.setenv(UNIVERSE_WATCH_MAX_ENV, "60")
    assert universe_watch_max() == 60
    assert len(rank_active_universe(insts, top_n=100)) == 60


def test_subfloor_okx_name_now_watched_not_cut() -> None:
    # WATCH/TRADE decouple: a sub-floor OKX name (thin vol / wide spread) that the
    # OLD floor pre-cut from the active set now SURVIVES into the watch set — the
    # floor is a curator trade-gate now, not a watch-gate (breadth unlock).
    subfloor = _make_inst("JUNK-USDT", vol=1e6, spread_bps=25.0, atr_pct=6.0, depth=1e4)
    liquid = [_make_inst(f"L{i}-USDT", vol=5e8) for i in range(5)]
    out = rank_active_universe([subfloor, *liquid], top_n=120)
    assert any(ins.symbol == "JUNK-USDT" for ins in out)


# ---------------------------------------------------------------------------
# Dynamic focus — STAGE 1 rank-attention gradient (all active watched, tier-graded)
# ---------------------------------------------------------------------------


def test_compute_dynamic_focus_watches_all_active() -> None:
    # The [12,48] window + quota are gone — every active row is watched, ranked,
    # and tier-graded (flow_not_block: no membership cut).
    actives = [
        _make_inst(f"COIN{i}-USDT", vol=1e8 * (50 - i), atr_pct=3.0 + (i % 5) * 0.4)
        for i in range(50)
    ]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    assert len(focus) == 50  # ALL active rows, not a 12-48 cut
    scores = [f.focus_score for f in focus]
    assert scores == sorted(scores, reverse=True)
    ranks = [f.rank for f in focus]
    assert ranks == list(range(1, len(focus) + 1))
    # Every row carries a cadence tier; the best is S, the worst is T.
    assert all(f.tier in {"S", "A", "B", "T"} for f in focus)
    assert focus[0].tier == "S"
    assert focus[-1].tier == "T"


def test_compute_dynamic_focus_listing_watch_bucket() -> None:
    fresh_listing = _make_inst("NEW-USDT", listing_ts=NOW - 1800)  # 30min old
    seasoned = _make_inst("BTC-USDT", vol=9e8, listing_ts=NOW - 365 * 24 * 3600)
    focus = compute_dynamic_focus([fresh_listing, seasoned], cycle_ts=NOW)
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
    # WATCH/TRADE decouple (2026-06-24): a sub-floor (low-vol) row is NO LONGER
    # excluded by the liquidity floor — if it is not in the active set it reports
    # 'below_rank_topN' (the floor is a curator TRADE annotation now, not active).
    good = _make_inst("BTC-USDT", vol=8e8)
    bad = _make_inst("DEAD-USDT", vol=1.0)  # sub-floor on vol — but still watchable
    persist_universe(memdb, [good, bad], is_active_set={good.instrument_id})
    rows = memdb.execute(
        "SELECT symbol, is_active, active_reason FROM universe ORDER BY symbol"
    ).fetchall()
    rmap = {r[0]: (r[1], r[2]) for r in rows}
    assert rmap["BTC-USDT"][0] == 1 and rmap["BTC-USDT"][1] is None
    assert rmap["DEAD-USDT"][0] == 0
    assert rmap["DEAD-USDT"][1] == "below_rank_topN"
    # The liquidity axis is now a TRADE-eligibility annotation, not active-exclusion.
    assert liqfloor_trade_annotation(bad) == "liqfloor:vol"


# ---------------------------------------------------------------------------
# B3 #4 — active_reason mirrors the REAL rank/floor selection path
# (rank_active_universe), not the legacy hard 4-axis labels.
# ---------------------------------------------------------------------------


def test_active_reason_below_rank_for_valid_floor_passing_loser() -> None:
    # A valid OKX USDT row that clears the eligibility floor but is NOT in the
    # active set fell below the continuous-rank top-N cut → 'below_rank_topN'
    # (the legacy path could NEVER emit this; it always blamed a 4-axis floor).
    ins = _make_inst("LOSER-USDT", vol=8e8, spread_bps=2.0, atr_pct=4.0, depth=2e5)
    assert _active_exclusion_reason(ins) == "below_rank_topN"


def test_active_reason_subfloor_now_below_rank_not_liqfloor() -> None:
    # WATCH/TRADE decouple (2026-06-24): a sub-floor name is NO LONGER excluded by
    # the liquidity floor — it is WATCHED, so if not selected its active-exclusion
    # reason is 'below_rank_topN', NOT 'liqfloor:*'. The liqfloor axis moves to the
    # TRADE-eligibility annotation.
    thin = _make_inst("THIN-USDT", vol=1e6, spread_bps=2.0, atr_pct=4.0, depth=2e5)
    wide = _make_inst("WIDE-USDT", vol=8e8, spread_bps=99.0, atr_pct=4.0, depth=2e5)
    assert _active_exclusion_reason(thin) == "below_rank_topN"
    assert _active_exclusion_reason(wide) == "below_rank_topN"


def test_liqfloor_trade_annotation_names_axis() -> None:
    # The re-homed floor is exposed as a TRADE-eligibility annotation: a sub-floor
    # name (watched) is annotated liqfloor:<axis>; a clearing name → None.
    thin = _make_inst("THIN-USDT", vol=1e6, spread_bps=2.0, atr_pct=4.0, depth=2e5)
    wide = _make_inst("WIDE-USDT", vol=8e8, spread_bps=99.0, atr_pct=4.0, depth=2e5)
    good = _make_inst("GOOD-USDT", vol=8e8, spread_bps=2.0, atr_pct=4.0, depth=2e5)
    assert liqfloor_trade_annotation(thin) == "liqfloor:vol"
    assert liqfloor_trade_annotation(wide) == "liqfloor:spread"
    assert liqfloor_trade_annotation(good) is None


def test_active_reason_crypto_quote_okx_below_rank_not_quote_ccy() -> None:
    # STEP 2 scope-widen: a crypto-quoted OKX name (BTC-ETH, quote=ETH) that the
    # parser admitted (it had a USD reference) is VALIDLY quoted — if it is not in
    # the active set its reason is 'below_rank_topN', NOT a 'quote_ccy=ETH'
    # exclusion (the parser is the admission SSOT; ETH is a real quote now).
    ins = _make_inst("BTC-ETH", quote_ccy="ETH", vol=8e8, spread_bps=2.0,
                     atr_pct=4.0, depth=2e5)
    assert _active_exclusion_reason(ins) == "below_rank_topN"


def test_active_reason_session_wait_for_non_live_capital() -> None:
    ins = _make_inst(
        "EURUSD", venue="capital", asset_class="forex", quote_ccy="USD",
        state="tradeable_off",
    )
    assert _active_exclusion_reason(ins) == "session_wait:tradeable_off"


def test_active_reason_state_for_non_live_okx() -> None:
    ins = _make_inst("HALT-USDT", state="halt")
    assert _active_exclusion_reason(ins) == "state=halt"


def test_active_reason_off_venue_class() -> None:
    # A crypto-CFD on Capital is off Capital's stream whitelist → routed to OKX.
    ins = _make_inst(
        "BTCUSD", venue="capital", asset_class="crypto", quote_ccy="USD",
    )
    assert _active_exclusion_reason(ins) == "off_venue_class:crypto"


def test_persist_universe_active_reason_uses_real_path(memdb) -> None:  # type: ignore[no-untyped-def]
    # Selection = rank_active_universe; the inactive loser's active_reason must
    # be a real-path label, NEVER a legacy 4-axis 'atr_pct=…<2.0' string.
    winner = _make_inst("BTC-USDT", vol=9e8)
    loser = _make_inst("ETH-USDT", vol=8e8)  # valid + floor-passing, just not picked
    persist_universe(memdb, [winner, loser], is_active_set={winner.instrument_id})
    reason = memdb.execute(
        "SELECT active_reason FROM universe WHERE symbol='ETH-USDT'"
    ).fetchone()[0]
    assert reason == "below_rank_topN"
    assert "atr_pct" not in (reason or "")


def test_capital_name_matches_p0_categories() -> None:
    from polaris.core.universe.discovery import CAPITAL_P0_CATEGORY_TOKENS

    assert _capital_name_matches({"name": "Forex"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Currencies"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Indices"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Commodities"}, CAPITAL_P0_CATEGORY_TOKENS)
    # Crypto is OWNED by OKX track A (Jin 2026-05-30 STEP 0 (a)) — the "crypto"
    # token was removed, so a standalone "Crypto" node no longer matches.
    assert not _capital_name_matches({"name": "Crypto"}, CAPITAL_P0_CATEGORY_TOKENS)
    # STEP 2 scope-widen (Jin 2026-06-24 "다 열어야지"): Shares / ETFs / Bonds /
    # Rates now MATCH so the walk descends into them (FETCH + persist). Active/
    # trade membership is still gated downstream by the stream asset-class whitelist.
    assert _capital_name_matches({"name": "Shares"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "ETFs"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Bonds"}, CAPITAL_P0_CATEGORY_TOKENS)
    assert _capital_name_matches({"name": "Rates"}, CAPITAL_P0_CATEGORY_TOKENS)


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


def test_parse_okx_tickers_admits_usd_equivalent_quotes() -> None:
    # STEP 2 scope-widen (Jin 2026-06-24 "다 열어야지", flow_not_block): USDC- and
    # USD-quoted spot pairs are USD-equivalent → admitted alongside USDT with the
    # SAME vol_24h_usd (volCcyQuote24h is already ~USD for a stablecoin quote).
    rows = [
        {
            "instId": "BTC-USDT", "last": "60000", "bidPx": "60000",
            "askPx": "60001", "high24h": "61000", "low24h": "59000",
            "volCcyQuote24h": "5e8", "bidSz": "1", "askSz": "1",
        },
        {
            "instId": "BTC-USDC", "last": "60000", "bidPx": "60000",
            "askPx": "60001", "high24h": "61000", "low24h": "59000",
            "volCcyQuote24h": "5e8", "bidSz": "1", "askSz": "1",
        },
        {
            "instId": "BTC-USD", "last": "60000", "bidPx": "60000",
            "askPx": "60001", "high24h": "61000", "low24h": "59000",
            "volCcyQuote24h": "5e8", "bidSz": "1", "askSz": "1",
        },
    ]
    out = parse_okx_tickers(rows, now_ts=NOW)
    assert sorted(i.symbol for i in out) == ["BTC-USD", "BTC-USDC", "BTC-USDT"]
    for i in out:
        assert i.vol_24h_usd == pytest.approx(5e8, rel=1e-6)
        assert i.asset_class == "crypto"


def test_parse_okx_tickers_normalizes_crypto_quote_vol_to_usd() -> None:
    # A crypto-quoted pair (BTC-ETH, quote=ETH) carries vol in ETH units. The
    # parser builds an in-payload quote→USD index from the USD-equivalent tickers
    # (ETH-USDT.last = ETH's USD price) and normalizes vol_24h_usd / depth to USD
    # so the vol-dominant rank compares like-for-like. WRONG normalization =
    # price error, so this is pinned (flow_not_block: admitted, ranked correctly).
    rows = [
        {
            "instId": "ETH-USDT", "last": "3000", "bidPx": "3000",
            "askPx": "3001", "high24h": "3100", "low24h": "2900",
            "volCcyQuote24h": "1e8", "bidSz": "10", "askSz": "10",
        },
        {
            "instId": "BTC-ETH", "last": "20", "bidPx": "20",
            "askPx": "20.02", "high24h": "21", "low24h": "19",
            "volCcyQuote24h": "1000",  # 1000 ETH of 24h notional
            "bidSz": "5", "askSz": "5",
        },
    ]
    out = {i.symbol: i for i in parse_okx_tickers(rows, now_ts=NOW)}
    assert "BTC-ETH" in out
    # 1000 ETH × $3000/ETH = $3,000,000 USD notional.
    assert out["BTC-ETH"].vol_24h_usd == pytest.approx(3_000_000.0, rel=1e-6)
    assert out["BTC-ETH"].quote_ccy == "ETH"
    # spread/atr are ratios → unit-free, unchanged by quote ccy.
    assert out["BTC-ETH"].atr_24h_pct == pytest.approx((21 - 19) / 20 * 100.0)


def test_parse_okx_tickers_drops_quote_with_no_usd_reference() -> None:
    # A pair whose quote has NO USD reference anywhere in the payload cannot be
    # USD-normalized (vol would be a wrong number) → excluded. This is a
    # normalization-quality keep, not a defensive block: the row carries no
    # rankable/priceable USD datum, so it is not a watch candidate.
    rows = [
        {
            "instId": "FOO-BAR", "last": "5", "bidPx": "5", "askPx": "5.01",
            "high24h": "6", "low24h": "4", "volCcyQuote24h": "1000",
            "bidSz": "1", "askSz": "1",
        }
    ]
    assert parse_okx_tickers(rows, now_ts=NOW) == []


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


@settings(max_examples=40, deadline=None)
@given(
    n=st.integers(min_value=0, max_value=15),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_property_compute_dynamic_focus_watches_all(n: int, seed: int) -> None:
    # STAGE 1: every active row is watched (no [12,48] cut), each tier-graded.
    actives = [
        _make_inst(
            f"P{i}-USDT",
            vol=1e8 + (seed + i) * 1e6,
            atr_pct=2.0 + ((seed + i) % 7) * 0.3,
        )
        for i in range(n)
    ]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    assert len(focus) == len(actives)  # all watched, none dropped
    assert all(f.tier in {"S", "A", "B", "T"} for f in focus)


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


@pytest.mark.asyncio
async def test_capital_walk_retries_transient_non200(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """STEP 2 coverage protection (Jin 2026-06-24): a transient non-200 on a nav
    node used to silently drop the whole sub-tree (coverage loss). Retry-backoff
    recovers it — the node returns 429 once, then 200, and its markets are fetched.
    NOT a trading defense: this protects UNIVERSE COVERAGE, never an order path."""
    import polaris.core.universe._capital as cap_mod
    monkeypatch.setattr(cap_mod.asyncio, "sleep", _no_sleep)  # zero backoff in test

    calls: dict[str, int] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/session"):
            return httpx.Response(200, headers={"CST": "c", "X-SECURITY-TOKEN": "s"}, json={})
        if path.endswith("/marketnavigation"):
            return httpx.Response(200, json={"nodes": [{"id": "forex", "name": "Forex"}]})
        node_id = path.rsplit("/", 1)[-1]
        calls[node_id] = calls.get(node_id, 0) + 1
        if node_id == "forex" and calls[node_id] == 1:
            return httpx.Response(429, json={})  # transient throttle on first try
        return httpx.Response(200, json={"markets": [_cap_mk("EURUSD", "CURRENCIES")], "nodes": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://demo-api"
    ) as cli:
        out = await fetch_capital_instruments(
            api_key="k", email="e", password="p", client=cli, now_ts=NOW
        )
    assert calls["forex"] >= 2  # retried after the 429
    assert any(i.symbol == "EURUSD" for i in out)  # recovered the markets


@pytest.mark.asyncio
async def test_capital_walk_admits_shares_node(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """STEP 2 scope-widen: the 'shares' token lets the walk DESCEND into a Shares
    node so company-share CFDs are FETCHED (classified equity). They are persisted
    + surfaced; whether they enter the ACTIVE/TRADE set is a separate stream-asset-
    class decision (Capital stream = forex/index/commodity today)."""
    import polaris.core.universe._capital as cap_mod
    monkeypatch.setattr(cap_mod.asyncio, "sleep", _no_sleep)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/session"):
            return httpx.Response(200, headers={"CST": "c", "X-SECURITY-TOKEN": "s"}, json={})
        if path.endswith("/marketnavigation"):
            return httpx.Response(200, json={"nodes": [{"id": "shares", "name": "Shares"}]})
        return httpx.Response(200, json={"markets": [_cap_mk("AAPL", "SHARES")], "nodes": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://demo-api"
    ) as cli:
        out = await fetch_capital_instruments(
            api_key="k", email="e", password="p", client=cli, now_ts=NOW
        )
    by_sym = {i.symbol: i.asset_class for i in out}
    assert by_sym.get("AAPL") == "equity"  # shares node descended + classified


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


def test_is_capital_index_major_matches_curated_set() -> None:
    from polaris.core.universe.schema import is_capital_index_major

    assert is_capital_index_major("capital", "US500")
    assert is_capital_index_major("capital", "us100")
    assert is_capital_index_major("CAPITAL", "DE40")
    assert is_capital_index_major("capital", "UK100")
    # Not a curated major / not Capital → False.
    assert not is_capital_index_major("capital", "J225")
    assert not is_capital_index_major("okx", "US500")


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


