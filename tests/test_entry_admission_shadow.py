"""Component C (SHADOW) — edge-first entry admission, behavior 0.

Pins the SHADOW contract (.claude/plans/okx_live_confidence_redesign_2026-05-31.md
§C). flow_not_block STRICT:

- COLD cell (n_eff < CELL_POOL_MIN_N_EFF) → admit ALWAYS, never would_suppress
  ("모호하면 통과") — even with deeply negative expectancy / a move below cost.
- WARM cell would_suppress ONLY when regime-conditioned expectancy net of the
  REAL round-trip cost < 0, OR the cost-aware expected move <= real cost.
- WARM +EV (expectancy net cost > 0 AND move clears cost) → admit.
- ``net_edge`` is NEVER an input to the decision function.
- The shadow log is no-op without a conn (byte-identical) and DDL bootstraps on
  fresh + legacy DBs.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from polaris.core.economics.entry_admission import (
    CELL_POOL_MIN_N_EFF,
    EntryAdmissionDecision,
    entry_admission_decision,
    expected_move_r_from_cell,
    real_round_trip_cost_r_from_usd,
)
from polaris.core.economics.entry_admission_shadow import (
    fetch_entry_admission_shadow,
    log_entry_admission,
)
from polaris.core.economics.fees import OKX_REAL_TAKER_BPS, real_fee_usd
from polaris.core.pipeline.payload_builder import PNL_R_USD_DENOM
from polaris.scripts._production_run_signal import _log_entry_admission_shadow
from polaris.storage.schema import _apply_post_migrations, init_db
from polaris.strategies.base import RawSignal

# ===========================================================================
# Pure decision rule — flow_not_block STRICT
# ===========================================================================


def test_cold_cell_admits_even_with_negative_expectancy() -> None:
    """COLD cell (n_eff < floor) → admit, never would_suppress — 모호하면 통과."""
    out = entry_admission_decision(
        cell_n_eff=CELL_POOL_MIN_N_EFF - 1.0,  # cold
        cell_avg_pnl_r=-2.0,  # deeply losing
        regime="chop",
        expected_move_r=0.0,  # move far below any cost
        real_round_trip_cost_r=0.1,
    )
    assert out.admit is True
    assert out.would_suppress is False
    assert out.reason == "cold_admit"


def test_cold_cell_zero_n_eff_admits() -> None:
    """n_eff == 0 (no sample at all) is the most ambiguous case → admit."""
    out = entry_admission_decision(
        cell_n_eff=0.0,
        cell_avg_pnl_r=-5.0,
        regime="crisis",
        expected_move_r=0.0,
        real_round_trip_cost_r=1.0,
    )
    assert out.admit is True
    assert out.would_suppress is False


def test_warm_positive_edge_admits() -> None:
    """WARM cell, expectancy net of cost > 0 AND move clears cost → admit."""
    out = entry_admission_decision(
        cell_n_eff=12.0,
        cell_avg_pnl_r=0.9,  # tsmom bull/trend
        regime="trend",
        expected_move_r=0.5,
        real_round_trip_cost_r=0.04,
    )
    assert out.admit is True
    assert out.would_suppress is False
    assert out.reason == "warm_positive_edge_admit"


def test_warm_negative_expectancy_net_cost_would_suppress() -> None:
    """WARM cell, avg_pnl_r net of real round-trip cost < 0 → would_suppress."""
    out = entry_admission_decision(
        cell_n_eff=10.0,
        cell_avg_pnl_r=-0.6,  # tsmom chop/crisis
        regime="chop",
        expected_move_r=0.5,
        real_round_trip_cost_r=0.04,
    )
    assert out.admit is False
    assert out.would_suppress is True
    assert out.reason == "warm_negative_expectancy_net_cost"
    assert out.basis == "regime_conditioned_expectancy"


def test_warm_positive_expectancy_but_below_cost_would_suppress() -> None:
    """Expectancy positive but < real round-trip cost → net negative → suppress."""
    out = entry_admission_decision(
        cell_n_eff=10.0,
        cell_avg_pnl_r=0.02,  # tiny positive
        regime="trend",
        expected_move_r=0.5,
        real_round_trip_cost_r=0.05,  # cost exceeds expectancy
    )
    assert out.would_suppress is True
    assert out.reason == "warm_negative_expectancy_net_cost"


def test_warm_move_below_cost_would_suppress() -> None:
    """WARM, expectancy net cost >= 0 but expected move <= cost → cost-aware suppress."""
    out = entry_admission_decision(
        cell_n_eff=8.0,
        cell_avg_pnl_r=0.2,  # net of 0.04 cost still positive → Rule 2 passes
        regime="trend",
        expected_move_r=0.03,  # below the round-trip cost
        real_round_trip_cost_r=0.04,
    )
    assert out.admit is False
    assert out.would_suppress is True
    assert out.reason == "warm_expected_move_below_cost"
    assert out.basis == "cost_aware_move_vs_cost"


def test_net_edge_not_a_parameter() -> None:
    """net_edge must never be consultable — not a parameter of the decision fn."""
    params = set(inspect.signature(entry_admission_decision).parameters)
    assert "net_edge" not in params
    assert "net_edge_r" not in params


# ===========================================================================
# Proxy helpers
# ===========================================================================


def test_expected_move_proxy_hit_rate_weighted() -> None:
    """Expected move proxy = hit-rate × positive-part of avg_pnl_r."""
    # 6 wins / 10 → 0.6 hit rate, avg up-move magnitude 0.5 → 0.3.
    assert expected_move_r_from_cell(n_eff=10.0, wins_eff=6.0, avg_pnl_r=0.5) == 0.3
    # Negative expectancy → no expected favourable move.
    assert expected_move_r_from_cell(n_eff=10.0, wins_eff=2.0, avg_pnl_r=-0.5) == 0.0
    # No sample → 0.0 (never divides by zero).
    assert expected_move_r_from_cell(n_eff=0.0, wins_eff=0.0, avg_pnl_r=1.0) == 0.0


def test_real_round_trip_cost_is_two_legs_in_atr_r() -> None:
    """Round-trip cost = 2 real fee legs divided by atr_usd → ATR-R (cost_usd/atr_usd)."""
    one_leg = real_fee_usd("okx", PNL_R_USD_DENOM)  # 10 bps of $50 = $0.05
    # entry_price=$50_000, atr_pct=0.005 → atr_usd = 50_000 × 0.005 × 2 = $500.
    atr_usd = 50_000.0 * 0.005 * 2.0
    cost_r = real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=one_leg, atr_usd=atr_usd
    )
    # 2 × 0.05 / 500 = 0.0002 R — the SAME atr_usd divisor as pnl_r = pnl_abs/atr_usd.
    expected = 2.0 * (OKX_REAL_TAKER_BPS / 10_000.0 * PNL_R_USD_DENOM) / atr_usd
    assert abs(cost_r - expected) < 1e-12
    # Degenerate atr_usd guard → 0.0 (a degenerate ATR can never manufacture a
    # would_suppress; fail-open to admit — flow_not_block).
    assert real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=1.0, atr_usd=0.0
    ) == 0.0
    assert real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=1.0, atr_usd=-5.0
    ) == 0.0


def test_cost_and_expectancy_share_one_atr_r_unit() -> None:
    """Unit-consistency proof: cost_r and avg_pnl_r both divide by the same atr_usd.

    A trade that earns exactly the round-trip fee (gross pnl_abs == round_trip_usd)
    has avg_pnl_r == cost_r when both use the SAME atr_usd, so expectancy net of
    cost is exactly 0 — no $-basis cancellation, the comparison is apples-to-apples.
    """
    entry_price, atr_pct = 30_000.0, 0.004
    atr_usd = entry_price * atr_pct * 2.0
    one_leg = real_fee_usd("okx", PNL_R_USD_DENOM)
    round_trip_usd = 2.0 * one_leg
    cost_r = real_round_trip_cost_r_from_usd(
        real_fee_one_leg_usd=one_leg, atr_usd=atr_usd
    )
    # cell avg_pnl_r is built from per-trade pnl_r = pnl_abs / atr_usd (ATR-R). A
    # trade whose gross pnl exactly equals the round-trip fee yields this avg.
    avg_pnl_r_equal_to_cost = round_trip_usd / atr_usd
    assert abs(avg_pnl_r_equal_to_cost - cost_r) < 1e-12
    # Net-of-cost expectancy is then exactly 0 (boundary → admit, not suppress).
    out = entry_admission_decision(
        cell_n_eff=10.0,
        cell_avg_pnl_r=avg_pnl_r_equal_to_cost,
        regime="trend",
        expected_move_r=avg_pnl_r_equal_to_cost + 1.0,  # clears cost → Rule 3 passes
        real_round_trip_cost_r=cost_r,
    )
    assert out.admit is True
    assert out.would_suppress is False


# ===========================================================================
# Shadow log writer
# ===========================================================================


def _decision() -> EntryAdmissionDecision:
    return entry_admission_decision(
        cell_n_eff=10.0, cell_avg_pnl_r=-0.6, regime="chop",
        expected_move_r=0.5, real_round_trip_cost_r=0.04,
    )


def test_shadow_log_writes_row(memdb: sqlite3.Connection) -> None:
    log_entry_admission(
        memdb,
        run_id="r1",
        signal_id="s1",
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="tsmom",
        regime="chop",
        cell_n_eff=10.0,
        cell_avg_pnl_r=-0.6,
        expected_move_r=0.5,
        real_round_trip_cost_r=0.04,
        decision=_decision(),
    )
    rows = fetch_entry_admission_shadow(memdb)
    assert len(rows) == 1
    row = rows[0]
    assert row["admit"] == 0
    assert row["would_suppress"] == 1
    assert row["reason"] == "warm_negative_expectancy_net_cost"
    assert row["basis"] == "regime_conditioned_expectancy"
    assert row["strategy_id"] == "tsmom"
    assert row["regime"] == "chop"


def test_shadow_log_would_suppress_filter(memdb: sqlite3.Connection) -> None:
    # One admit (cold) + one would_suppress (warm -EV).
    log_entry_admission(
        memdb, run_id="r", signal_id="cold", venue="okx", symbol="X",
        strategy_id="tsmom", regime="trend", cell_n_eff=1.0, cell_avg_pnl_r=-1.0,
        expected_move_r=0.0, real_round_trip_cost_r=0.1,
        decision=entry_admission_decision(
            cell_n_eff=1.0, cell_avg_pnl_r=-1.0, regime="trend",
            expected_move_r=0.0, real_round_trip_cost_r=0.1,
        ),
    )
    log_entry_admission(
        memdb, run_id="r", signal_id="warm", venue="okx", symbol="Y",
        strategy_id="tsmom", regime="chop", cell_n_eff=10.0, cell_avg_pnl_r=-0.6,
        expected_move_r=0.5, real_round_trip_cost_r=0.04, decision=_decision(),
    )
    assert len(fetch_entry_admission_shadow(memdb, would_suppress=True)) == 1
    assert len(fetch_entry_admission_shadow(memdb, would_suppress=False)) == 1
    assert len(fetch_entry_admission_shadow(memdb)) == 2


def test_shadow_log_noop_without_conn() -> None:
    """No conn → silent no-op (never crashes the hot path)."""
    log_entry_admission(
        None, run_id="r", signal_id="s", venue="okx", symbol="X",
        strategy_id="tsmom", regime="trend", cell_n_eff=10.0, cell_avg_pnl_r=0.5,
        expected_move_r=0.5, real_round_trip_cost_r=0.04, decision=_decision(),
    )


# ===========================================================================
# DDL bootstrap — fresh + legacy
# ===========================================================================


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def test_ddl_creates_table_on_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        assert _table_exists(conn, "entry_admission_shadow")
        # Insert round-trips through the fresh schema.
        log_entry_admission(
            conn, run_id="r", signal_id="s", venue="okx", symbol="X",
            strategy_id="tsmom", regime="chop", cell_n_eff=10.0,
            cell_avg_pnl_r=-0.6, expected_move_r=0.5, real_round_trip_cost_r=0.04,
            decision=_decision(),
        )
        assert len(fetch_entry_admission_shadow(conn)) == 1
    finally:
        conn.close()


# ===========================================================================
# Entry-seam wiring (_log_entry_admission_shadow) — behavior 0
# ===========================================================================


def _sig() -> RawSignal:
    return RawSignal(
        signal_id="sig-1",
        strategy_id="tsmom",
        symbol="BTC-USDT",
        side="long",
        strength=0.7,
        sizing_hint=1.0,
        ttl_bars=10,
        thesis_tag="t",
        correlation_group="BTC",
    )


def test_wire_cold_cell_logs_admit(memdb: sqlite3.Connection) -> None:
    """Entry seam: a cold cell logs an admit row (never would_suppress)."""
    _log_entry_admission_shadow(
        memdb,
        run_id="run-1",
        sig=_sig(),
        venue="okx",
        symbol="BTC-USDT",
        regime="trend",
        cell_routing={"n_eff": 1.0, "avg_pnl_r": -2.0, "wins_eff": 0.0},
        entry_price=50_000.0,
        atr_pct=0.005,
    )
    rows = fetch_entry_admission_shadow(memdb)
    assert len(rows) == 1
    assert rows[0]["admit"] == 1
    assert rows[0]["would_suppress"] == 0
    assert rows[0]["reason"] == "cold_admit"


def test_wire_warm_negative_logs_would_suppress(memdb: sqlite3.Connection) -> None:
    """Entry seam: a warm proven-negative cell logs a would_suppress row."""
    _log_entry_admission_shadow(
        memdb,
        run_id="run-1",
        sig=_sig(),
        venue="okx",
        symbol="BTC-USDT",
        regime="chop",
        # warm, deeply losing → expectancy net of the (tiny real) cost < 0.
        cell_routing={"n_eff": 15.0, "avg_pnl_r": -0.6, "wins_eff": 3.0},
        entry_price=50_000.0,
        atr_pct=0.005,
    )
    rows = fetch_entry_admission_shadow(memdb)
    assert len(rows) == 1
    assert rows[0]["would_suppress"] == 1
    assert rows[0]["reason"] == "warm_negative_expectancy_net_cost"
    # The REAL OKX round-trip cost in ATR-R is tiny but strictly positive
    # (2 × 10 bps of $50 / atr_usd $500 = 0.0002 R).
    assert rows[0]["real_round_trip_cost_r"] > 0.0


def test_wire_cost_r_is_price_invariant_whole_position_basis(
    memdb: sqlite3.Connection,
) -> None:
    """entry seam cost_r must match the close-path WHOLE-POSITION convention
    (``_production_close_effects.py`` FIX 1: ``atr_usd = size_usd * atr_pct *
    2.0``), so at the SAME representative notional (``PNL_R_USD_DENOM``) and the
    SAME ``atr_pct``, ``real_round_trip_cost_r`` is IDENTICAL regardless of the
    symbol's per-unit price (GBPUSD ~1.27 vs J225 ~40000) — the prior per-unit
    ``entry_price * atr_pct * 2.0`` basis let a low-price symbol's cost_r explode
    relative to a high-price symbol's at identical risk (92.3R vs 0.0R observed).
    """
    atr_pct = 0.004
    _log_entry_admission_shadow(
        memdb,
        run_id="run-lo", sig=_sig(), venue="okx", symbol="GBPUSD",
        regime="chop",
        cell_routing={"n_eff": 15.0, "avg_pnl_r": -0.6, "wins_eff": 3.0},
        entry_price=1.27,  # low per-unit price
        atr_pct=atr_pct,
    )
    _log_entry_admission_shadow(
        memdb,
        run_id="run-hi", sig=_sig(), venue="okx", symbol="J225",
        regime="chop",
        cell_routing={"n_eff": 15.0, "avg_pnl_r": -0.6, "wins_eff": 3.0},
        entry_price=40_000.0,  # high per-unit price
        atr_pct=atr_pct,
    )
    rows = {
        r["symbol"]: r["real_round_trip_cost_r"]
        for r in fetch_entry_admission_shadow(memdb)
    }
    assert rows["GBPUSD"] > 0.0
    assert rows["J225"] > 0.0
    assert abs(rows["GBPUSD"] - rows["J225"]) < 1e-12


def test_wire_noop_without_conn_byte_identical() -> None:
    """shadow_conn=None → no-op (byte-identical legacy path; never raises)."""
    _log_entry_admission_shadow(
        None,
        run_id="run-1",
        sig=_sig(),
        venue="okx",
        symbol="BTC-USDT",
        regime="trend",
        cell_routing={"n_eff": 15.0, "avg_pnl_r": -0.6, "wins_eff": 3.0},
        entry_price=50_000.0,
        atr_pct=0.005,
    )


def test_ddl_creates_table_on_legacy_db(tmp_path: Path) -> None:
    """A legacy DB (table absent) gets the table created on init_db re-open."""
    db = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(str(db))
    try:
        # Simulate a pre-Component-C DB: a minimal unrelated table, no
        # entry_admission_shadow.
        legacy.execute("CREATE TABLE marker (id INTEGER)")
        legacy.commit()
        assert not _table_exists(legacy, "entry_admission_shadow")
    finally:
        legacy.close()
    conn = init_db(db)  # re-open through the full bootstrap
    try:
        assert _table_exists(conn, "entry_admission_shadow")
        # Re-running post-migrations stays safe (idempotent).
        _apply_post_migrations(conn)
        log_entry_admission(
            conn, run_id="r", signal_id="s", venue="okx", symbol="X",
            strategy_id="tsmom", regime="chop", cell_n_eff=10.0,
            cell_avg_pnl_r=-0.6, expected_move_r=0.5, real_round_trip_cost_r=0.04,
            decision=_decision(),
        )
        assert len(fetch_entry_admission_shadow(conn)) == 1
    finally:
        conn.close()


# ===========================================================================
# storage-split (round 4 fix): the pipeline call site writes to state.md_conn
# ===========================================================================


@pytest.mark.asyncio
async def test_pipeline_entry_admission_shadow_writes_to_md_conn(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """entry_admission_shadow is marketdata-domain — the pipeline's
    Component-C shadow log must land in state.md_conn, not the trading conn
    ``run_pipeline_for_signal`` is otherwise driven by."""
    import polaris.scripts._production_run_signal as run_signal_mod
    from polaris.core.pipeline.gate_state import GateDecision, GateResult
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import ProdLoopState
    from polaris.storage.schema import ALL_DDL
    from polaris.strategies.spot_donchian import SpotDonchianStrategy

    class _FakeOrch:
        def __init__(self, **_: object) -> None:
            pass

        async def run(self, _ctx: object, *, start_gate: int) -> list[GateResult]:
            return [GateResult(decision=GateDecision.KILL, next_gate=None, payload={})]

    monkeypatch.setattr(run_signal_mod, "GateOrchestrator", _FakeOrch)

    md_conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        md_conn.execute(stmt)
    state = ProdLoopState(md_conn=md_conn)

    sig = RawSignal(
        signal_id="sig-admshadow", strategy_id="tsmom", symbol="BTC-USDT",
        side="long", strength=0.9, sizing_hint=0.5, ttl_bars=24,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    await run_signal_mod.run_pipeline_for_signal(
        conn=memdb, haiku=StubGPTClient(), state=state,
        strategy=SpotDonchianStrategy(), sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        regime="bull_trend", bars_atr_pct=0.01, last_price=100.0,
        universe_rows=[], now_ts=1_780_000_000,
        reserve_and_submit=lambda **_: None, phase="P0",
    )
    md_n = md_conn.execute(
        "SELECT COUNT(*) FROM entry_admission_shadow"
    ).fetchone()[0]
    trading_n = memdb.execute(
        "SELECT COUNT(*) FROM entry_admission_shadow"
    ).fetchone()[0]
    assert md_n == 1
    assert trading_n == 0
    md_conn.close()
