"""LiquidityProbe / FundingProbe — orphan-feed probe axes (P3 promotion).

DEMO/PAPER · aggressive · flow_not_block. vault/50_research/
built-not-wired-audit.md flagged universe liquidity (vol_24h/spread) +
okx_funding as collected-but-unconsumed. Both probes ride the SAME G6
observe-only attach as the Slice-1 catalog (never an action/close/size/block)
and reuse the codebase's existing eligibility-floor / funding thresholds as
their calibration reference (no fresh magic numbers).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.probes import ProbeContext
from polaris.core.probes.catalog import FundingProbe, LiquidityProbe
from polaris.core.probes.liquidity_read import read_liquidity_snapshot
from polaris.core.universe.schema import liquidity_floor_for_venue


def _ctx(**over: object) -> ProbeContext:
    base: dict[str, object] = dict(
        position_id="p1",
        venue="okx",
        symbol="BTC-USDT",
        underlying_group_id="crypto:BTC",
        side="long",
        entry_price=100.0,
        last_price=101.0,
        atr_pct=0.01,
        pnl_r=0.3,
        mfe_r=0.6,
        mae_r=-0.1,
        held_seconds=120,
        volume_now=10.0,
        volume_z=0.5,
        atr_slope=0.001,
        recent_ticks=[],
        exit_state="open",
        regime="trend",
        seconds_to_close=None,
        now_ts=1000,
    )
    base.update(over)
    return ProbeContext(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# LiquidityProbe
# --------------------------------------------------------------------------- #


def test_liquidity_abstains_when_both_data_points_absent() -> None:
    assert LiquidityProbe().evaluate(_ctx()) is None


def test_liquidity_abstains_when_deep_and_tight() -> None:
    """Neutral baseline: plenty of volume + a zero spread → no lean at all
    (never favorable — deep/tight liquidity is not a reward)."""
    floor = liquidity_floor_for_venue("okx")
    reading = LiquidityProbe().evaluate(
        _ctx(vol_24h_usd=floor.min_vol_24h_usd * 10.0, spread_bps=0.0)
    )
    assert reading is None


def test_liquidity_thin_volume_produces_adverse_lean() -> None:
    floor = liquidity_floor_for_venue("okx")
    reading = LiquidityProbe().evaluate(
        _ctx(vol_24h_usd=floor.min_vol_24h_usd * 0.1, spread_bps=1.0)
    )
    assert reading is not None
    assert reading.lean < 0.0
    assert reading.probe_id == "liquidity"
    assert reading.kind == "liquidity"


def test_liquidity_wide_spread_produces_adverse_lean() -> None:
    floor = liquidity_floor_for_venue("okx")
    reading = LiquidityProbe().evaluate(
        _ctx(vol_24h_usd=floor.min_vol_24h_usd * 10.0, spread_bps=50.0)
    )
    assert reading is not None
    assert reading.lean < 0.0


def test_liquidity_never_returns_a_favorable_lean() -> None:
    """Structural: LiquidityProbe is ADVERSE-ONLY by construction — sweep a
    wide range and confirm lean is always <= 0."""
    floor = liquidity_floor_for_venue("okx")
    for vol_mult in (0.0, 0.5, 1.0, 5.0, 50.0):
        for spread in (0.0, 1.0, 10.0, 100.0):
            reading = LiquidityProbe().evaluate(
                _ctx(
                    vol_24h_usd=floor.min_vol_24h_usd * vol_mult,
                    spread_bps=spread,
                )
            )
            if reading is not None:
                assert reading.lean <= 0.0


def test_liquidity_partial_data_still_evaluates() -> None:
    """Only spread known (vol_24h_usd absent) still produces a reading."""
    reading = LiquidityProbe().evaluate(_ctx(vol_24h_usd=None, spread_bps=50.0))
    assert reading is not None
    assert reading.lean < 0.0


# --------------------------------------------------------------------------- #
# FundingProbe
# --------------------------------------------------------------------------- #


def test_funding_abstains_when_absent() -> None:
    assert FundingProbe().evaluate(_ctx(funding_rate=None)) is None


def test_funding_abstains_when_exactly_zero() -> None:
    assert FundingProbe().evaluate(_ctx(funding_rate=0.0)) is None


def test_funding_long_pays_positive_funding_is_adverse() -> None:
    reading = FundingProbe().evaluate(_ctx(side="long", funding_rate=0.0005))
    assert reading is not None
    assert reading.lean < 0.0


def test_funding_short_collects_positive_funding_is_favorable() -> None:
    reading = FundingProbe().evaluate(_ctx(side="short", funding_rate=0.0005))
    assert reading is not None
    assert reading.lean > 0.0


def test_funding_long_collects_negative_funding_is_favorable() -> None:
    reading = FundingProbe().evaluate(_ctx(side="long", funding_rate=-0.0005))
    assert reading is not None
    assert reading.lean > 0.0


def test_funding_short_pays_negative_funding_is_adverse() -> None:
    reading = FundingProbe().evaluate(_ctx(side="short", funding_rate=-0.0005))
    assert reading is not None
    assert reading.lean < 0.0


def test_funding_extreme_rate_saturates_lean() -> None:
    reading = FundingProbe().evaluate(_ctx(side="long", funding_rate=0.01))
    assert reading is not None
    assert reading.lean == -1.0


# --------------------------------------------------------------------------- #
# read_liquidity_snapshot (sidecar reader, mirrors latest_probe_tighten)
# --------------------------------------------------------------------------- #


def _universe_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE universe (venue TEXT, symbol TEXT, vol_24h_usd REAL, "
        "PRIMARY KEY (venue, symbol))"
    )
    return conn


def _quote_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE quote_ticks (instrument_id TEXT PRIMARY KEY, spread_bps REAL)"
    )
    return conn


def test_read_liquidity_snapshot_both_present() -> None:
    u = _universe_db()
    u.execute(
        "INSERT INTO universe VALUES ('okx', 'BTC-USDT', 500000000.0)"
    )
    q = _quote_db()
    q.execute("INSERT INTO quote_ticks VALUES ('okx:BTC-USDT', 2.5)")
    snap = read_liquidity_snapshot(
        universe_conn=u, quote_conn=q, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
    )
    assert snap.vol_24h_usd == 500000000.0
    assert snap.spread_bps == 2.5


def test_read_liquidity_snapshot_missing_rows_fail_open() -> None:
    u = _universe_db()
    q = _quote_db()
    snap = read_liquidity_snapshot(
        universe_conn=u, quote_conn=q, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
    )
    assert snap.vol_24h_usd is None
    assert snap.spread_bps is None


def test_read_liquidity_snapshot_none_conns_fail_open() -> None:
    snap = read_liquidity_snapshot(
        universe_conn=None, quote_conn=None, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
    )
    assert snap.vol_24h_usd is None
    assert snap.spread_bps is None


def test_read_liquidity_snapshot_missing_table_fail_open() -> None:
    u = sqlite3.connect(":memory:")  # no universe table at all
    q = sqlite3.connect(":memory:")  # no quote_ticks table at all
    snap = read_liquidity_snapshot(
        universe_conn=u, quote_conn=q, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
    )
    assert snap.vol_24h_usd is None
    assert snap.spread_bps is None


# --------------------------------------------------------------------------- #
# wire_probe_sidecar — the two new probes join the SAME composite bus
# --------------------------------------------------------------------------- #


def test_wire_probe_sidecar_registers_liquidity_and_funding(tmp_path: Path) -> None:
    from polaris.scripts._production_probe_attach import wire_probe_sidecar
    from polaris.scripts.production_paper_loop import ProdLoopState

    state = ProdLoopState()
    wire_probe_sidecar(state, probe_db_path=str(tmp_path / "probes.sqlite"))
    assert state.probe_bus is not None
    probe_ids = {p.probe_id for p in state.probe_bus._probes}  # type: ignore[attr-defined]
    assert "liquidity" in probe_ids
    assert "funding" in probe_ids
