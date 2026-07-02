"""Exit maturity gate — per-timeframe hold_frac + native bars-seen counting.

[[waveB_sizing_params_2026-07-02]] agenda 3. The wall-clock maturity gate
(``EXIT_THESIS_BREAK_HOLD_FRAC``) gated both break paths but measured maturity
in ELAPSED WALL-CLOCK seconds, which a session close / weekend gap can distort
(a 1D position held 18h over a weekend has SEEN 0 new 1D bars, not "18h of
horizon"). This suite covers: (a) hold_frac-per-timeframe + required_bars
arithmetic + env override, (b) bars-seen vs wall-clock semantic divergence
across a data gap, (c) the J225 48s live-incident e2e both suppressed (0 bars
seen) and allowed (3 bars seen) via the bars-seen path, (d) the -1.0R rail /
ATR trail / G6 crisis layer separation, (e) 1m/intraday behaviour preserved.

DEMO/PAPER only. NEVER sizes / blocks entry / halts — exit-timing only.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.live_recalc.exit_engine import (
    Bucket,
    ManagementMode,
    ThesisGivebackParams,
    assess_thesis,
    hold_frac_for_timeframe,
    required_bars_for_horizon,
)
from polaris.scripts._production_atr import native_bars_seen_since
from polaris.storage.schema_ddl_core import (
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_BARS_INSTRUMENT_TS_INDEX,
)

GB = ThesisGivebackParams(arm_r=0.30, frac=0.50, hard_frac=0.60)


def _assess(**kw: object) -> ManagementMode:
    base: dict[str, object] = dict(
        side="long", bucket=Bucket.TREND, mfe_r=0.0, mae_r=0.0, pnl_r=0.0,
        momentum_drift=0.0, atr_slope=0.0, ofi=None, flow_confirmed=None,
        regime="trend", entry_regime="trend", held_seconds=60,
        horizon_seconds=600, giveback=GB,
    )
    base.update(kw)
    return assess_thesis(**base)  # type: ignore[arg-type]


# --- (a) hold_frac-per-timeframe + required_bars arithmetic + env override ---


def test_daily_or_slower_hold_frac_is_010() -> None:
    assert hold_frac_for_timeframe("1D") == pytest.approx(0.10)


def test_intraday_hold_frac_is_005() -> None:
    assert hold_frac_for_timeframe("1H") == pytest.approx(0.05)
    assert hold_frac_for_timeframe("1m") == pytest.approx(0.05)
    assert hold_frac_for_timeframe(None) == pytest.approx(0.05)


def test_global_env_override_wins_both_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC", "0.20")
    import importlib

    from polaris.core.live_recalc import exit_params

    importlib.reload(exit_params)
    try:
        assert exit_params.hold_frac_for_timeframe("1D") == pytest.approx(0.20)
        assert exit_params.hold_frac_for_timeframe("1m") == pytest.approx(0.20)
    finally:
        monkeypatch.delenv("POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC", raising=False)
        importlib.reload(exit_params)


def test_required_bars_ceil_and_daily_floor() -> None:
    # 21-bar 1D horizon (21*86400s), hold_frac=0.10 -> 2.1 bars -> ceil=3.
    horizon_seconds = 21 * 86400
    required = required_bars_for_horizon(
        horizon_seconds=horizon_seconds, native_bar_interval_seconds=86400,
        horizon_bars=21,
    )
    assert required == 3


def test_required_bars_floor_is_2_when_horizon_bars_ge_10() -> None:
    # A tiny hold_frac*horizon rounds below 2 bars, but horizon_bars=10 forces
    # the 2-bar floor (never eats less than 2 bars of a >=10-bar horizon).
    required = required_bars_for_horizon(
        horizon_seconds=10, native_bar_interval_seconds=3600, horizon_bars=10,
    )
    assert required == 2


def test_required_bars_floor_is_1_for_short_horizon_under_10_bars() -> None:
    # A short (<10-bar) horizon strategy takes the 1-bar floor so the gate
    # never eats 25%+ of a short horizon (a flat 2-bar floor on a 5-bar
    # horizon would suppress 40% of it).
    required = required_bars_for_horizon(
        horizon_seconds=10, native_bar_interval_seconds=3600, horizon_bars=5,
    )
    assert required == 1


def test_required_bars_never_returns_zero_on_degenerate_interval() -> None:
    required = required_bars_for_horizon(
        horizon_seconds=100, native_bar_interval_seconds=0, horizon_bars=21,
    )
    assert required >= 1


# --- (b) bars-seen vs wall-clock divergence across a session/data gap -------


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(DDL_BARS)
    c.execute(DDL_BARS_INDEX)
    c.execute(DDL_BARS_INSTRUMENT_TS_INDEX)
    return c


def _seed_bars(
    conn: sqlite3.Connection, *, interval: str, timestamps: list[int],
    instrument_id: str = "okx:BTC-USDT",
) -> None:
    venue, _, symbol = instrument_id.partition(":")
    for ts in timestamps:
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', ?, ?, ?, ?, 100, 101, 99, 100, 1e4, 1e4, "
            " 1, 100, 100, 100, 1.0, 'rest')",
            (instrument_id, venue, symbol, interval, ts),
        )


def test_bars_seen_diverges_from_wall_clock_across_data_gap(
    conn: sqlite3.Connection,
) -> None:
    # A position opened at NOW on a 1D strategy; a WEEKEND/data gap means 18h
    # of wall-clock elapsed but ZERO new 1D bars have closed. Wall-clock alone
    # would report "18h/24h = 75% of one bar-fraction elapsed"; bars-seen
    # correctly reports 0 — the thesis has NOT actually developed.
    now = 1_780_000_000
    _seed_bars(conn, interval="1D", timestamps=[now])  # only the open-time bar
    seen = native_bars_seen_since(
        conn, instrument_id="okx:BTC-USDT", timeframe="1D",
        open_ts=now, now_ts=now + 18 * 3600,
    )
    held_seconds = 18 * 3600
    assert seen == 0
    assert held_seconds > 0  # wall-clock elapsed is nonzero, seen is not


def test_bars_seen_counts_only_bars_closed_after_open(conn: sqlite3.Connection) -> None:
    now = 1_780_000_000
    _seed_bars(
        conn, interval="1D",
        timestamps=[now, now + 86400, now + 2 * 86400, now + 3 * 86400],
    )
    seen = native_bars_seen_since(
        conn, instrument_id="okx:BTC-USDT", timeframe="1D",
        open_ts=now, now_ts=now + 3 * 86400,
    )
    assert seen == 3  # the open-time bar itself does not count


# --- (c) J225 48s e2e — momentum + corroborated paths, bars-seen ------------


def test_j225_momentum_only_break_suppressed_with_zero_bars_seen() -> None:
    # index_dual_momentum_rotation (1D, 21-bar horizon). fill -> first recalc
    # 48s later. Momentum-only break, ZERO native bars seen since open — must
    # HOLD (not CUT), matching the pre-existing wall-clock fixture's verdict.
    horizon_seconds = 21 * 86400
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.05, momentum_drift=-0.015,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=48, horizon_seconds=horizon_seconds, timeframe="1D",
        native_bars_seen=0, native_bar_interval_seconds=86400, horizon_bars=21,
    )
    assert m is not ManagementMode.CUT


def test_j225_corroborated_break_suppressed_with_zero_bars_seen() -> None:
    # SAME position, a CORROBORATED break (regime-flip-against) instead of
    # momentum-only — must ALSO be suppressed pre-maturity (both paths gated
    # uniformly).
    horizon_seconds = 21 * 86400
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.05, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=48, horizon_seconds=horizon_seconds, timeframe="1D",
        native_bars_seen=0, native_bar_interval_seconds=86400, horizon_bars=21,
    )
    assert m is not ManagementMode.CUT


def test_j225_break_allowed_once_3_bars_seen() -> None:
    # required_bars_for_horizon(21d horizon, 1D interval, 21 bars) == 3. Once
    # the position has SEEN 3 native 1D bars, a genuine corroborated break
    # CUTs (matured — the gate no longer suppresses it).
    horizon_seconds = 21 * 86400
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=3 * 86400, horizon_seconds=horizon_seconds, timeframe="1D",
        native_bars_seen=3, native_bar_interval_seconds=86400, horizon_bars=21,
    )
    assert m is ManagementMode.CUT


def test_j225_2_bars_seen_still_suppressed_below_3_bar_requirement() -> None:
    horizon_seconds = 21 * 86400
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=2 * 86400, horizon_seconds=horizon_seconds, timeframe="1D",
        native_bars_seen=2, native_bar_interval_seconds=86400, horizon_bars=21,
    )
    assert m is not ManagementMode.CUT


# --- (d) -1.0R rail / ATR trail / G6 crisis guard: untouched layer ----------


def test_maturity_gate_bars_seen_never_touches_pnl_rail_layer() -> None:
    # LAYER SEPARATION: assess_thesis (bars-seen maturity gate) is EXIT-TIMING
    # only — a deeply red (-1.0R) fresh 1D position with ONLY a sub-floor
    # immaterial drift as the break signal must NOT escalate to CUT via this
    # gate. The G6 -1.0R hard rail is a SEPARATE caller-owned layer.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-1.0, momentum_drift=-0.0005,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=48, horizon_seconds=21 * 86400, timeframe="1D",
        native_bars_seen=0, native_bar_interval_seconds=86400, horizon_bars=21,
    )
    assert m is not ManagementMode.CUT


# --- (e) 1m/intraday strategy behaviour approximately preserved -------------


def test_1m_strategy_bars_seen_path_cuts_immediately_like_wall_clock() -> None:
    # A 1m-timeframe strategy: required_bars_for_horizon(900s horizon, 60s
    # bar, small horizon_bars) resolves to a tiny bar count so a position that
    # has SEEN a handful of 1m bars still cuts on a genuine break — same
    # practical behaviour as the wall-clock path's near-immediate cut.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.03, momentum_drift=-0.6,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=60, horizon_seconds=900, timeframe="1m",
        native_bars_seen=2, native_bar_interval_seconds=60, horizon_bars=15,
    )
    assert m is ManagementMode.CUT


def test_none_bars_seen_inputs_fall_back_to_wall_clock_inert_for_legacy_caller() -> None:
    # A caller that has NOT migrated to bars-seen (all three None) must fall
    # back to the pre-existing wall-clock fraction — byte-identical for that
    # call site (instruction ③: safe fallback for unmigrated callers).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.3, momentum_drift=-0.15,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=int(21 * 86400 * 0.11), horizon_seconds=21 * 86400,
        timeframe="1D",
        # native_bars_seen / native_bar_interval_seconds / horizon_bars
        # omitted -> default None -> wall-clock fallback.
    )
    assert m is ManagementMode.CUT
