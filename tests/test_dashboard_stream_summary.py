"""Tests for dashboard stage-2 BACKEND — per-stream summary in the snapshot.

Read-only display layer (DEMO/PAPER, virtual funds). Seeds an in-memory/temp
SQLite DB with fills + positions across okx / capital / alpaca and asserts:

- exactly 3 ``StreamSummary`` lanes (one per registered stream, even a venue
  with zero activity yields a zeroed lane so all 3 lanes always render);
- per-stream sums reconcile to the existing global totals (``net_pnl_usd``,
  ``open_positions_n``) so the dashboard never lies;
- stream_id / label / color / venue / product_class are sourced from the
  streams SSOT (``polaris.core.streams.config``), not a second hardcoded map.

These tests touch only the snapshot/visualizer read path — no trading
behavior, sizing, gating, or venue calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.streams.config import STREAMS, VENUE_TO_STREAM, StreamConfig
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_models import StreamSummary
from polaris.scripts.dashboard.snapshot_queries import (
    _daily_realised_pnl,
    _now_s,
    _per_stream_summary,
)
from polaris.storage.schema import init_db

# str-keyed view of the SSOT (StreamSummary.stream_id is a plain str).
_CFG_BY_ID: dict[str, StreamConfig] = {
    cfg.stream_id: cfg for cfg in STREAMS.values()
}


def _seed(conn: sqlite3.Connection) -> None:
    """Seed fills + open positions across okx / capital (alpaca left empty)."""
    now_ms = _now_s() * 1000

    # fills: (fill_id, venue, instrument_id, strategy_id, side, size_usd,
    #         fill_price, fee_usd, slippage_bps, ts_ms, order_id,
    #         contribution_id, pnl_usd, is_close, base_qty, quote_qty, state)
    # slippage_bps on each fill → slippage_usd = slippage_bps/10000 * size_usd.
    #   okx:     10 bps * 1000 + 10 bps * 1050 = 1.0 + 1.05 = 2.05
    #   capital: 5 bps * 2000 + 5 bps * 1980  = 1.0 + 0.99 = 1.99
    fills = [
        # okx — one open + one closing fill (+50 pnl, -2 fee, 10 bps slip each)
        ("f1", "okx", "okx:BTC-USDT", "tsmom", "buy", 1000.0, 100.0, 1.0, 10.0,
         now_ms - 5000, "o1", None, 0.0, 0, 10.0, 1000.0, "filled"),
        ("f2", "okx", "okx:BTC-USDT", "tsmom", "sell", 1050.0, 105.0, 1.0, 10.0,
         now_ms - 4000, "o2", None, 50.0, 1, 10.0, 1050.0, "filled"),
        # capital — one open + one closing fill (-20 pnl, -3 fee, 5 bps slip each)
        ("f3", "capital", "capital:XAUUSD", "xau_indices_trend", "buy", 2000.0,
         1900.0, 1.5, 5.0, now_ms - 3000, "o3", None, 0.0, 0, 1.0, 2000.0,
         "filled"),
        ("f4", "capital", "capital:XAUUSD", "xau_indices_trend", "sell", 1980.0,
         1880.0, 1.5, 5.0, now_ms - 2000, "o4", None, -20.0, 1, 1.0, 1980.0,
         "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )

    # positions: okx 1 open, capital 2 open (drift collapses on logical key but
    # distinct symbols stay separate), alpaca 0.
    now_s = _now_s()
    positions = [
        # (position_id, venue, symbol, underlying_group_id, product_class,
        #  stream_id, strategy_id, entry_strategy_id, active_strategy_id,
        #  side, qty, status, opened_ts, closed_ts, swap_count)
        ("p1", "okx", "ETH-USDT", "", "spot", "A_okx_crypto", "tsmom",
         "tsmom", "tsmom", "long", 5.0, "open", now_s - 100, None, 0),
        ("p2", "capital", "EURUSD", "", "cfd", "B_capital_cfd",
         "fx_breakout_basket", "fx_breakout_basket", "fx_breakout_basket",
         "long", 1000.0, "open", now_s - 80, None, 0),
        ("p3", "capital", "GBPUSD", "", "cfd", "B_capital_cfd",
         "fx_breakout_basket", "fx_breakout_basket", "fx_breakout_basket",
         "long", 800.0, "open", now_s - 60, None, 0),
    ]
    conn.executemany(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts, swap_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        positions,
    )

    # gate_events: per-call AI token usage + model, attributed to a stream via
    # the position_id -> positions.venue join (gate_events has no venue column).
    #   okx (p1):      one gpt(mini) call  (1000 in + 500 out tokens)
    #                  one python call     (cost 0 — deterministic gate)
    #   capital (p2):  one gpt_p1(5.5) call (2000 in + 1000 out tokens)
    #                  one cached call      (cost 0)
    # alpaca: none (zeroed AI cost lane). A pre-position event (NULL position_id)
    # is also seeded — it must NOT be attributed to any stream.
    now_ts = _now_s()
    gate_events = [
        # (event_id, run_id, signal_id, position_id, gate_id, phase, decision,
        #  model_used, latency_ms, input_tokens, output_tokens, payload_json,
        #  error_text, created_ts)
        ("ge1", "r1", "s1", "p1", 5, "success", "SIZED", "gpt", 10,
         1000, 500, "{}", None, now_ts),
        ("ge2", "r1", "s1", "p1", 6, "success", "HOLD", "python", 1,
         0, 0, "{}", None, now_ts),
        ("ge3", "r2", "s2", "p2", 5, "success", "SIZED", "gpt_p1", 20,
         2000, 1000, "{}", None, now_ts),
        ("ge4", "r2", "s2", "p2", 6, "success", "HOLD", "cached", 0,
         500, 250, "{}", None, now_ts),
        # pre-position gate (no position_id) — unattributed, must not count
        # toward any stream's ai_cost.
        ("ge5", "r3", "s3", None, 1, "success", "PASS", "gpt", 10,
         9999, 9999, "{}", None, now_ts),
    ]
    conn.executemany(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, model_used, latency_ms, input_tokens, "
        "output_tokens, payload_json, error_text, created_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        gate_events,
    )

    # bars: last close per instrument so open positions get a mark (so
    # per-stream uPnL is non-trivial; reconciliation holds regardless).
    bars = [
        # (instrument_id, venue, symbol, close)
        ("okx:ETH-USDT", "okx", "ETH-USDT", 2010.0),
        ("capital:EURUSD", "capital", "EURUSD", 1.11),
        ("capital:GBPUSD", "capital", "GBPUSD", 1.29),
    ]
    conn.executemany(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?, '', ?, ?, '1m', ?, 0.0, 0.0, 0.0, ?, 0.0)",
        [(b[0], b[1], b[2], now_s, b[3]) for b in bars],
    )
    conn.commit()


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed(conn)
    return conn


def test_three_lanes_always_present(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    assert len(streams) == 3
    assert all(isinstance(s, StreamSummary) for s in streams)
    ids = {s.stream_id for s in streams}
    assert ids == set(STREAMS.keys())


def test_empty_venue_yields_zeroed_lane(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    alpaca = next(s for s in streams if s.stream_id == "C_alpaca_equity")
    assert alpaca.venue == "alpaca"
    assert alpaca.open_positions_n == 0
    assert alpaca.daily_trades == 0
    assert alpaca.net_pnl_usd == 0.0
    assert alpaca.upnl_usd == 0.0
    assert alpaca.exposed_usd == 0.0


def test_label_color_from_ssot(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    for s in streams:
        cfg = _CFG_BY_ID[s.stream_id]
        assert s.venue == cfg.venue
        assert s.product_class == cfg.product_class
        # venue resolves back to this stream via the SSOT reverse index.
        assert VENUE_TO_STREAM[s.venue] == s.stream_id
        # label + color are non-empty deterministic display attributes.
        assert s.label
        assert s.color
    # distinct color per lane (so the 3 lanes are visually separable).
    colors = [s.color for s in streams]
    assert len(set(colors)) == len(colors)


def test_per_stream_pnl_breakdown(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    okx = by_id["A_okx_crypto"]
    cap = by_id["B_capital_cfd"]
    # okx net realised = +50 pnl - (1 + 1) fees = +48; 1 closed trade.
    assert okx.net_pnl_usd == 48.0
    assert okx.daily_trades == 1
    assert okx.open_positions_n == 1
    # capital net realised = -20 pnl - (1.5 + 1.5) fees = -23; 1 closed trade.
    assert cap.net_pnl_usd == -23.0
    assert cap.daily_trades == 1
    assert cap.open_positions_n == 2


def test_reconciliation_invariant_pnl_and_open_n(tmp_path: Path) -> None:
    """SUM(per-stream) must equal the existing global totals (no lying)."""
    conn = _seeded_db(tmp_path)
    try:
        now_s = _now_s()
        streams = _per_stream_summary(conn, now_s=now_s)
        global_pnl, global_trades = _daily_realised_pnl(conn, now_s=now_s)
        # global open_positions_n via the snapshot collector below.
    finally:
        conn.close()

    sum_pnl = sum(s.net_pnl_usd for s in streams)
    sum_trades = sum(s.daily_trades for s in streams)
    assert round(sum_pnl, 6) == round(global_pnl, 6)
    assert sum_trades == global_trades


def test_collect_snapshot_populates_streams(tmp_path: Path) -> None:
    """End-to-end: collect_snapshot exposes streams whose sums reconcile to the
    global snapshot totals (open_positions_n, daily_pnl_usd)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed(conn)
    conn.close()

    snap = collect_snapshot(db_path)
    assert len(snap.streams) == 3
    sum_open = sum(s.open_positions_n for s in snap.streams)
    sum_pnl = sum(s.net_pnl_usd for s in snap.streams)
    assert sum_open == snap.open_positions_n
    assert round(sum_pnl, 6) == round(snap.daily_pnl_usd, 6)
    # uPnL reconciliation: per-stream uPnL sums to the global uPnL total.
    sum_upnl = sum(s.upnl_usd for s in snap.streams)
    assert round(sum_upnl, 6) == round(snap.upnl_total, 6)
    # exposed reconciliation: per-stream exposed sums to the global exposed.
    sum_exposed = sum(s.exposed_usd for s in snap.streams)
    assert round(sum_exposed, 6) == round(snap.exposed_usd, 6)


# ---------------------------------------------------------------------------
# Cost monitoring (display-only) — fee / slippage / AI$ / net-after-cost per
# stream. READ-ONLY evidence-based profit tracking; no trading behavior touched.
# ---------------------------------------------------------------------------


def test_per_stream_fee_usd(tmp_path: Path) -> None:
    """fee_usd per stream = SUM(fills.fee_usd) for that venue."""
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    # okx: 1.0 + 1.0 = 2.0 ; capital: 1.5 + 1.5 = 3.0 ; alpaca: 0.0
    assert round(by_id["A_okx_crypto"].fee_usd, 6) == 2.0
    assert round(by_id["B_capital_cfd"].fee_usd, 6) == 3.0
    assert by_id["C_alpaca_equity"].fee_usd == 0.0


def test_per_stream_slippage_usd(tmp_path: Path) -> None:
    """slippage_usd per stream derived from slippage_bps/10000 * size_usd."""
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    # okx: 10bps*1000 + 10bps*1050 = 1.0 + 1.05 = 2.05
    assert round(by_id["A_okx_crypto"].slippage_usd, 6) == 2.05
    # capital: 5bps*2000 + 5bps*1980 = 1.0 + 0.99 = 1.99
    assert round(by_id["B_capital_cfd"].slippage_usd, 6) == 1.99
    assert by_id["C_alpaca_equity"].slippage_usd == 0.0


def test_per_stream_ai_cost_usd_model_price_map(tmp_path: Path) -> None:
    """ai_cost_usd per stream = Σ (tokens/1000 * MODEL_PRICE_PER_1K[model]),
    attributed via the position_id -> positions.venue join. python/cached = 0."""
    from polaris.scripts.dashboard.snapshot_queries import MODEL_PRICE_PER_1K

    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    # okx: gpt(mini) 1500 tokens @ 0.00015/1k + python(0) = 1.5*0.00015 = 0.000225
    okx_expected = 1500 / 1000.0 * MODEL_PRICE_PER_1K["gpt"]
    assert round(by_id["A_okx_crypto"].ai_cost_usd, 9) == round(okx_expected, 9)
    # capital: gpt_p1 3000 tokens @ 0.005/1k + cached(0) = 3.0*0.005 = 0.015
    cap_expected = 3000 / 1000.0 * MODEL_PRICE_PER_1K["gpt_p1"]
    assert round(by_id["B_capital_cfd"].ai_cost_usd, 9) == round(cap_expected, 9)
    # alpaca: no gate_events → 0
    assert by_id["C_alpaca_equity"].ai_cost_usd == 0.0
    # cached + python rows cost 0 by the price map.
    assert MODEL_PRICE_PER_1K["python"] == 0.0
    assert MODEL_PRICE_PER_1K["cached"] == 0.0


def test_unattributed_gate_event_not_counted(tmp_path: Path) -> None:
    """A gate_event with NULL position_id (pre-position gate) must NOT inflate
    any stream's ai_cost (its 9999/9999 tokens are dropped)."""
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    total_ai = sum(s.ai_cost_usd for s in streams)
    # The 9999+9999-token unattributed event would dominate if counted.
    assert total_ai < 0.02  # only the okx (0.000225) + capital (0.015) calls


def test_net_after_cost_reconciliation(tmp_path: Path) -> None:
    """net_after_cost_usd == net_pnl_usd - ai_cost_usd.

    ``net_pnl_usd`` is ALREADY net of fees AND slippage (``fills.pnl_usd`` is
    derived from the actual fill price, so slippage vs. the expected price is
    already baked in) — ``slippage_usd`` is a separate, informational-only
    model estimate and must NOT be subtracted a second time. Only ai_cost (a
    real extra deduction not reflected in fills.pnl_usd) is subtracted.
    """
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    for s in streams:
        expected = s.net_pnl_usd - s.ai_cost_usd
        assert round(s.net_after_cost_usd, 9) == round(expected, 9)


def test_net_after_cost_economic_identity_no_double_slippage(tmp_path: Path) -> None:
    """Regression guard: net_after_cost must equal net_pnl − ai_cost (fees AND
    slippage already reflected in net_pnl, counted EXACTLY ONCE). Catches
    re-introducing the slippage double-subtraction.

    Seeded gross close pnl: okx +50 (fees 2, slip 2.05, ai 0.000225),
    capital −20 (fees 3, slip 1.99, ai 0.015). alpaca all-zero.
    """
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    okx = by_id["A_okx_crypto"]
    # gross 50 − fee 2 (== net_pnl) − ai 0.000225 = 47.999775 (slip NOT subtracted)
    assert round(okx.net_after_cost_usd, 6) == round(
        50.0 - okx.fee_usd - okx.ai_cost_usd, 6
    )
    cap = by_id["B_capital_cfd"]
    # gross −20 − fee 3 (== net_pnl) − ai 0.015 = −23.015 (slip NOT subtracted)
    assert round(cap.net_after_cost_usd, 6) == round(
        -20.0 - cap.fee_usd - cap.ai_cost_usd, 6
    )


def test_empty_venue_zero_costs(tmp_path: Path) -> None:
    """Alpaca (no activity) → all cost fields 0.0 and net_after_cost 0.0."""
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    alpaca = next(s for s in streams if s.stream_id == "C_alpaca_equity")
    assert alpaca.fee_usd == 0.0
    assert alpaca.slippage_usd == 0.0
    assert alpaca.ai_cost_usd == 0.0
    assert alpaca.net_after_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Mark-freshness (Jin 2026-07-08 dashboard-live-net fix) — Capital CFD closes
# on weekends (FX/indices/gold) just like Alpaca closes outside RTH; the
# per-lane ``marks_label``/``marks_age_sec`` must reflect THIS lane's own
# venue-native session (SSOT: ``resolve_venue_session``), not silently stay ""
# on the false premise that only Alpaca can go stale. OKX (crypto, 24/7) must
# never set it regardless of the clock.
# ---------------------------------------------------------------------------

_SATURDAY_UTC_NOON = 1783771200  # 2026-07-11 12:00:00 UTC — Capital fx_weekend
_WEDNESDAY_UTC_NOON = 1783512000  # 2026-07-08 12:00:00 UTC — Capital fx_open


def test_capital_marks_label_set_on_weekend(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_SATURDAY_UTC_NOON)
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    cap = by_id["B_capital_cfd"]
    assert cap.marks_label == "internal marks (venue closed)"
    assert cap.marks_age_sec >= 0
    # OKX is crypto, 24/7 — never stale regardless of the clock.
    assert by_id["A_okx_crypto"].marks_label == ""
    assert by_id["A_okx_crypto"].marks_age_sec == 0


def test_capital_marks_label_empty_on_weekday(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_WEDNESDAY_UTC_NOON)
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    assert by_id["B_capital_cfd"].marks_label == ""
    assert by_id["B_capital_cfd"].marks_age_sec == 0


def test_global_upnl_marks_fields_removed() -> None:
    """Regression guard: the misleading GLOBAL upnl_marks_label/age_sec fields
    (which copied ONLY the Alpaca lane's staleness onto the 3-venue
    upnl_total) are gone — freshness now lives PER-LANE only on StreamSummary."""
    from polaris.scripts.dashboard.snapshot_models import DashboardSnapshot

    assert "upnl_marks_label" not in DashboardSnapshot.__dataclass_fields__
    assert "upnl_marks_age_sec" not in DashboardSnapshot.__dataclass_fields__


def test_collect_snapshot_populates_cost_fields(tmp_path: Path) -> None:
    """End-to-end: collect_snapshot streams carry the new cost fields and the
    net-after-cost identity holds for every lane."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed(conn)
    conn.close()

    snap = collect_snapshot(db_path)
    assert len(snap.streams) == 3
    for s in snap.streams:
        # net_pnl already nets fees AND slippage (real fill price); only ai_cost
        # (not reflected in fills.pnl_usd) is subtracted — fees/slippage counted
        # exactly once, slippage_usd stays informational-only.
        expected = s.net_pnl_usd - s.ai_cost_usd
        assert round(s.net_after_cost_usd, 9) == round(expected, 9)
    by_id = {s.stream_id: s for s in snap.streams}
    assert round(by_id["A_okx_crypto"].fee_usd, 6) == 2.0
    assert round(by_id["B_capital_cfd"].fee_usd, 6) == 3.0
