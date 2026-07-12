"""Tests for score_F (pts-classes group B — fee-normalized edge classification).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). score_F
is a capital-routing SIGNAL feeding ``strategy_class`` (group A) — never a
block/reject filter; computing/rolling it up does not touch sizing/9-stack/
-1.0R rail. BENCH/PROVE-classed strategies keep signaling/learning regardless
of their score_F value (aggressive_always_profit / no_block_filter_architecture).

Formula (task spec): score_F = Σ(net_i / max(abs(fee_i), 0.0001 * notional_i))
over every CLOSED flat->nonzero->flat lifecycle (one ``positions`` row),
partial/scale-in-out fills summed internally per lifecycle (SSOT U3: positions
+ fills + position_strategy_segments). Spread-only venues (fee_usd=0 reported)
fold a modeled spread into fee_i via ``economics.fees.real_fee_bps``. Rollup is
a batch-commit sweeper — zero writes in the position-close hot path.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.score_f import (
    compute_score_f,
    f_track_cap,
    rollup_score_f,
)
from polaris.storage.schema import init_db

# NOTE: seed_tag cohort aggregation (score_f_by_seed_tag) has its own test
# module — tests/test_score_f_seed_tag_cohort.py — to keep this file within
# the file-size cap; _mk_position here grew a seed_tag kwarg (default "")
# purely so that module can reuse this file's fixtures unmodified.


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    strategy_id: str = "gold_riskoff_trend_amplify",
    status: str = "closed",
    opened_ts: int = 1_700_000_000,
    closed_ts: int | None = 1_700_003_600,
    seed_tag: str = "",
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts, seed_tag) VALUES (?, ?, ?, ?, ?, ?, 'long', 1.0, ?, ?, ?, ?)",
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         status, opened_ts, closed_ts, seed_tag),
    )


def _mk_fill(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    strategy_id: str,
    size_usd: float,
    fee_usd: float,
    pnl_usd: float,
    is_close: bool,
    ts_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, venue, f"{venue}:{symbol}", strategy_id, size_usd,
         fee_usd, ts_ms, uuid.uuid4().hex, position_id, pnl_usd, int(is_close)),
    )


# ---------------------------------------------------------------------------
# partial / scale-in-out lifecycle aggregation
# ---------------------------------------------------------------------------


def test_partial_closes_net_within_one_lifecycle(conn):
    """Two partial-close slices of ONE position sum their net pnl_usd and fees
    into a SINGLE lifecycle contribution (not two separate score_F terms)."""
    _mk_position(conn, position_id="p1")
    _mk_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=0.0, is_close=False, ts_ms=1_700_000_000_000)
    _mk_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=500.0,
              fee_usd=0.5, pnl_usd=40.0, is_close=True, ts_ms=1_700_001_000_000)
    _mk_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=500.0,
              fee_usd=0.5, pnl_usd=60.0, is_close=True, ts_ms=1_700_002_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.position_id == "p1"
    # net_i = 40 + 60 = 100 (both close slices net together)
    assert ev.net_usd == pytest.approx(100.0)
    # fee_i = ALL fills (open + both close legs) = 1.0 + 0.5 + 0.5 = 2.0
    assert ev.fee_usd == pytest.approx(2.0)


def test_scale_in_out_multiple_entries_and_exits(conn):
    """Scale-in (2 entries) + scale-out (2 exits) still collapse to ONE
    lifecycle event for the position."""
    _mk_position(conn, position_id="p2")
    _mk_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=500.0,
              fee_usd=0.5, pnl_usd=0.0, is_close=False, ts_ms=1_700_000_000_000)
    _mk_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=500.0,
              fee_usd=0.5, pnl_usd=0.0, is_close=False, ts_ms=1_700_000_500_000)
    _mk_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=600.0,
              fee_usd=0.6, pnl_usd=-30.0, is_close=True, ts_ms=1_700_001_000_000)
    _mk_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=400.0,
              fee_usd=0.4, pnl_usd=90.0, is_close=True, ts_ms=1_700_002_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.net_usd == pytest.approx(60.0)  # -30 + 90
    assert ev.fee_usd == pytest.approx(2.0)  # 0.5+0.5+0.6+0.4


def test_swapped_strategy_fills_still_counted_by_instrument_scope(conn):
    """A Layer 6 strategy swap can leave later fills carrying a DIFFERENT
    ``fills.strategy_id`` than the position's current ``strategy_id`` — score_F
    must scope by (contribution_id, instrument_id), NOT strategy_id, so a
    swapped position's full net/fee still lands in the lifecycle (mirrors
    ``ladder._net_pnl_usd_for_position`` / ``close_pnl_usd_total`` SSOT)."""
    _mk_position(conn, position_id="p_swap", strategy_id="strategy_after_swap")
    _mk_fill(conn, position_id="p_swap", venue="okx", symbol="BTC-USDT",
              strategy_id="strategy_before_swap", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=0.0, is_close=False, ts_ms=1_700_000_000_000)
    _mk_fill(conn, position_id="p_swap", venue="okx", symbol="BTC-USDT",
              strategy_id="strategy_after_swap", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=75.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="strategy_after_swap",
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.net_usd == pytest.approx(75.0)
    assert ev.fee_usd == pytest.approx(2.0)  # BOTH legs, despite differing strategy_id


def test_only_closed_positions_scored(conn):
    """An open (not-yet-flat) lifecycle contributes zero events."""
    _mk_position(conn, position_id="p3", status="open", closed_ts=None)
    _mk_fill(conn, position_id="p3", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=0.0, is_close=False, ts_ms=1_700_000_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    assert events == []


def test_multiple_lifecycles_sum_into_score_f(conn):
    """score_F is the SUM of per-lifecycle terms, not an average. FLIP
    (fee_split_flip_r2_2026-07-12 item 1): score_contrib is now gross_bps
    (net_i/notional_i*10_000) by default — legacy_score_contrib still holds
    the pre-flip net/fee_denom formula unconditionally."""
    _mk_position(conn, position_id="p4", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p4", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)
    _mk_position(conn, position_id="p5", closed_ts=1_700_004_600)
    _mk_fill(conn, position_id="p5", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=2.0, pnl_usd=-20.0, is_close=True, ts_ms=1_700_004_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    total = sum(ev.score_contrib for ev in events)
    # p4: 100/1000*10000 = 1000.0 gbps ; p5: -20/1000*10000 = -200.0 gbps
    assert total == pytest.approx(800.0)
    legacy_total = sum(ev.legacy_score_contrib for ev in events)
    # p4: 100 / max(1.0, 0.1) = 100.0 ; p5: -20 / max(2.0, 0.1) = -10.0
    assert legacy_total == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# fee=0 floor (0.0001 * notional)
# ---------------------------------------------------------------------------


def test_fee_zero_uses_notional_floor(conn):
    """fee_i=0 (e.g. a bugged/zero-fee fill) falls back to the
    0.0001*notional floor rather than dividing by zero."""
    _mk_position(conn, position_id="p6")
    _mk_fill(conn, position_id="p6", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=10_000.0,
              fee_usd=0.0, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    assert len(events) == 1
    ev = events[0]
    # notional_i = 10_000 (the only fill's size_usd); floor = 0.0001*10000=1.0
    assert ev.fee_denom_usd == pytest.approx(1.0)
    assert ev.score_contrib == pytest.approx(50.0)  # 50 / 1.0


def test_fee_floor_uses_max_not_fee_when_fee_tiny(conn):
    """A nonzero-but-tiny fee still gets floored by 0.0001*notional when the
    floor is LARGER than the real fee (max() composition, never min())."""
    _mk_position(conn, position_id="p7")
    _mk_fill(conn, position_id="p7", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=10_000.0,
              fee_usd=0.0001, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    ev = events[0]
    # abs(fee)=0.0001 vs notional floor=1.0 -> floor wins
    assert ev.fee_denom_usd == pytest.approx(1.0)


def test_fee_floor_uses_fee_when_fee_dominates(conn):
    """When the real fee exceeds the notional floor, the real fee is used
    (max() composition — never silently discounted to the floor)."""
    _mk_position(conn, position_id="p8")
    _mk_fill(conn, position_id="p8", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=100.0,
              fee_usd=5.0, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    ev = events[0]
    # abs(fee)=5.0 vs notional floor=0.01 -> real fee wins
    assert ev.fee_denom_usd == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# modeled spread for spread-only venues (Capital: fills.fee_usd reports 0)
# ---------------------------------------------------------------------------


def test_capital_spread_only_venue_models_spread_into_fee(conn):
    """Capital CFD demo reports fee_usd=0 on every fill (spread-only venue).
    score_F must NOT treat that as a true zero-cost trade — it folds in the
    modeled spread proxy (``economics.fees.real_fee_bps`` — CAPITAL_REAL_BPS,
    3 bps/leg) per fill leg instead of falling through to the tiny notional
    floor, which would overstate score_F for the Capital track."""
    _mk_position(conn, position_id="p9", venue="capital", symbol="XAUUSD")
    _mk_fill(conn, position_id="p9", venue="capital", symbol="XAUUSD",
              strategy_id="gold_riskoff_trend_amplify", size_usd=10_000.0,
              fee_usd=0.0, pnl_usd=50.0, is_close=False, ts_ms=1_700_000_000_000)
    _mk_fill(conn, position_id="p9", venue="capital", symbol="XAUUSD",
              strategy_id="gold_riskoff_trend_amplify", size_usd=10_000.0,
              fee_usd=0.0, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="capital", strategy_id="gold_riskoff_trend_amplify",
    )
    ev = events[0]
    # modeled: 3bps * 10000 per leg * 2 legs = 6.0 usd, which beats the
    # 0.0001*20000=2.0 notional floor -> modeled spread wins.
    assert ev.fee_denom_usd == pytest.approx(6.0)
    assert ev.fee_usd == pytest.approx(6.0)


def test_okx_real_fee_reported_not_modeled(conn):
    """OKX is NOT a spread-only venue (fills carry a real fee_usd) — score_F
    uses the reported fee, not a modeled-spread substitute."""
    _mk_position(conn, position_id="p10", venue="okx", symbol="BTC-USDT")
    _mk_fill(conn, position_id="p10", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=7.0, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify",
    )
    assert events[0].fee_usd == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# daily rollup — batch-commit sweeper, no hot-path write
# ---------------------------------------------------------------------------


def test_rollup_inserts_one_event_per_closed_lifecycle(conn):
    _mk_position(conn, position_id="p11", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p11", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)

    n = rollup_score_f(conn, now_ts=1_700_010_000)
    assert n == 1
    row = conn.execute(
        "SELECT position_id, score_contrib FROM score_f_events"
    ).fetchone()
    assert row[0] == "p11"
    # FLIP: score_contrib = gross_bps = 100/1000*10000 (was legacy net/fee_denom=100.0).
    assert row[1] == pytest.approx(1000.0)


def test_rollup_is_idempotent_no_double_count(conn):
    """A second rollup pass over the SAME closed positions inserts zero NEW
    rows (append-only ledger, PK on position_id) — re-scanning never
    double-counts a lifecycle already scored."""
    _mk_position(conn, position_id="p12", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p12", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)

    first = rollup_score_f(conn, now_ts=1_700_010_000)
    second = rollup_score_f(conn, now_ts=1_700_020_000)
    assert first == 1
    assert second == 0
    count = conn.execute("SELECT COUNT(*) FROM score_f_events").fetchone()[0]
    assert count == 1


def test_rollup_advances_watermark_and_scans_new_closes_next_pass(conn):
    """After rollup, a NEW close (later closed_ts) is picked up on the NEXT
    sweeper pass without re-touching the already-scored lifecycle."""
    _mk_position(conn, position_id="p13", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p13", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)
    rollup_score_f(conn, now_ts=1_700_010_000)

    _mk_position(conn, position_id="p14", closed_ts=1_700_050_000)
    _mk_fill(conn, position_id="p14", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=-40.0, is_close=True, ts_ms=1_700_049_000_000)
    n = rollup_score_f(conn, now_ts=1_700_060_000)
    assert n == 1
    ids = {r[0] for r in conn.execute("SELECT position_id FROM score_f_events")}
    assert ids == {"p13", "p14"}


def test_rollup_does_not_write_in_position_close_hot_path(conn):
    """Sanity: closing a position (INSERT/UPDATE on positions/fills alone)
    creates ZERO score_f_events rows until the sweeper explicitly runs —
    proves rollup is a separate batch pass, not inline hot-path logic."""
    _mk_position(conn, position_id="p15", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p15", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)
    count = conn.execute("SELECT COUNT(*) FROM score_f_events").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# F_track_cap = median(denom_i) over last-W closes, PROVE fallback
# ---------------------------------------------------------------------------


def test_f_track_cap_median_of_recent_denoms(conn):
    for i, (fee, size) in enumerate([(1.0, 1000.0), (2.0, 1000.0), (3.0, 1000.0)]):
        pid = f"cap{i}"
        _mk_position(conn, position_id=pid, closed_ts=1_700_003_600 + i)
        _mk_fill(conn, position_id=pid, venue="okx", symbol="BTC-USDT",
                  strategy_id="gold_riskoff_trend_amplify", size_usd=size,
                  fee_usd=fee, pnl_usd=10.0, is_close=True,
                  ts_ms=1_700_003_000_000 + i)
    rollup_score_f(conn, now_ts=1_700_010_000)

    result = f_track_cap(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify", window_w=20,
        prove_fallback_usd=0.5,
    )
    assert result.f_track_cap_usd == pytest.approx(2.0)  # median(1,2,3)
    assert result.n_observed == 3
    assert not result.used_fallback


def test_f_track_cap_falls_back_to_prove_median_when_no_history(conn):
    """07:30 frozen, no live+shadow history yet -> current PROVE candidate
    median expected fee is used (never a crash / never zero-division)."""
    result = f_track_cap(
        conn, venue="okx", strategy_id="brand_new_strategy", window_w=20,
        prove_fallback_usd=1.25,
    )
    assert result.f_track_cap_usd == pytest.approx(1.25)
    assert result.n_observed == 0
    assert result.used_fallback


def test_f_track_cap_window_w_caps_lookback(conn):
    """Only the last window_w closes (by closed_ts) feed the median — an
    older outlier outside the window must not skew the cap."""
    _mk_position(conn, position_id="old", closed_ts=1_000)
    _mk_fill(conn, position_id="old", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=999.0, pnl_usd=10.0, is_close=True, ts_ms=999_000)
    for i, fee in enumerate([1.0, 1.0, 1.0]):
        pid = f"recent{i}"
        _mk_position(conn, position_id=pid, closed_ts=2_000 + i)
        _mk_fill(conn, position_id=pid, venue="okx", symbol="BTC-USDT",
                  strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
                  fee_usd=fee, pnl_usd=10.0, is_close=True, ts_ms=2_000_000 + i)
    rollup_score_f(conn, now_ts=3_000)

    result = f_track_cap(
        conn, venue="okx", strategy_id="gold_riskoff_trend_amplify", window_w=3,
        prove_fallback_usd=0.5,
    )
    assert result.f_track_cap_usd == pytest.approx(1.0)
    assert result.n_observed == 3


# ---------------------------------------------------------------------------
# fee-split v1 FLIP (fee_split_flip_r2_2026-07-12 item 1) — gross_bps judged
# axis, fee_drag_bps reporting axis, POLARIS_SCOREF_NET_LEGACY rollback.
# ---------------------------------------------------------------------------


def test_fee_drag_bps_is_reporting_only_and_correct(conn):
    """fee_drag_bps = fee_usd/notional_usd*10_000 — never equal to
    score_contrib (the judged axis), computed alongside it."""
    _mk_position(conn, position_id="p16", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p16", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=5.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(conn, venue="okx", strategy_id="gold_riskoff_trend_amplify")
    ev = events[0]
    assert ev.fee_drag_bps == pytest.approx(50.0)  # 5/1000*10000
    assert ev.score_contrib == pytest.approx(1000.0)  # gross_bps, unaffected by fee_drag_bps


def test_zero_notional_gross_bps_and_fee_drag_bps_are_zero_not_crash(conn):
    """No fills (notional_usd=0) degrades gross_bps/fee_drag_bps to 0.0,
    never a ZeroDivisionError."""
    _mk_position(conn, position_id="p17", closed_ts=1_700_003_600)
    events = compute_score_f(conn, venue="okx", strategy_id="gold_riskoff_trend_amplify")
    assert len(events) == 1
    assert events[0].score_contrib == 0.0
    assert events[0].fee_drag_bps == 0.0


def test_legacy_net_axis_env_flag_restores_pre_flip_score_contrib(conn, monkeypatch):
    """POLARIS_SCOREF_NET_LEGACY=1 (item 8 rollback) makes score_contrib the
    legacy net/fee_denom formula again — one flag, full revert — while
    fee_drag_bps/notional_usd/gross_usd keep populating unconditionally."""
    monkeypatch.setenv("POLARIS_SCOREF_NET_LEGACY", "1")
    _mk_position(conn, position_id="p18", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p18", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(conn, venue="okx", strategy_id="gold_riskoff_trend_amplify")
    ev = events[0]
    assert ev.score_contrib == pytest.approx(100.0)  # legacy: 100/max(1.0,0.1)
    assert ev.score_contrib == pytest.approx(ev.legacy_score_contrib)
    assert ev.fee_drag_bps == pytest.approx(10.0)  # unconditional: 1/1000*10000
    assert ev.notional_usd == pytest.approx(1000.0)  # unconditional


def test_legacy_flag_off_by_default(conn):
    """No env var set -> gross_bps is the default judged axis (the flip)."""
    _mk_position(conn, position_id="p19", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p19", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)

    events = compute_score_f(conn, venue="okx", strategy_id="gold_riskoff_trend_amplify")
    ev = events[0]
    assert ev.score_contrib == pytest.approx(1000.0)  # gross_bps
    assert ev.legacy_score_contrib == pytest.approx(100.0)  # legacy still computed


def test_fee_drag_bps_persisted_by_rollup(conn):
    _mk_position(conn, position_id="p20", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="p20", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=3.0, pnl_usd=50.0, is_close=True, ts_ms=1_700_003_000_000)
    rollup_score_f(conn, now_ts=1_700_010_000)
    row = conn.execute(
        "SELECT fee_drag_bps FROM score_f_events WHERE position_id = 'p20'"
    ).fetchone()
    assert row[0] == pytest.approx(30.0)  # 3/1000*10000
