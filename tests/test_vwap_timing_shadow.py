"""G4 VWAP/AVWAP entry-timing SHADOW (frontgate-scan item #6, behavior-0).

DEMO/PAPER virtual capital only. Covers:
- ``compute_session_vwap`` / ``compute_signal_day_avwap`` pure typical-price
  VWAP calc (volume>0 only, MIN_VOLUME_BARS floor, known-at-time windows).
- ``distance_bps`` sign/scale.
- ``_frontgate_bars.fetch_known_bars`` look-ahead guard (verifier note #5).
- ``log_vwap_timing_shadow`` DB write + no-op guards.
- The ``vwap_timing_shadow`` migration (fresh DB, additive table).
- Behavior-0: G3 (``signal_validator_gate`` — G4's content relocated here,
  P2a group A) PASS/MODIFY still returns the byte-identical decision/payload
  with or without the new shadow tag.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.pipeline.agents._frontgate_bars import bar_cutoff_ts, fetch_known_bars
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.agents.vwap_timing_shadow import (
    MIN_VOLUME_BARS,
    compute_session_vwap,
    compute_signal_day_avwap,
    distance_bps,
    log_vwap_timing_shadow,
)
from polaris.core.pipeline.gate_state import (
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.storage.schema import init_db
from polaris.strategies.base import BarView

DAY = 86_400
LONDON_OPEN_H = 7 * 3600  # _SESSION_OPEN_HOURS boundary


def _bar(ts: int, price: float, volume: float = 10.0) -> BarView:
    return BarView(ts=ts, open=price, high=price, low=price, close=price, volume=volume)


# ---------------------------------------------------------------------------
# distance_bps
# ---------------------------------------------------------------------------


def test_distance_bps_positive_above_anchor() -> None:
    assert round(distance_bps(101.505, 101.0), 4) == 50.0


def test_distance_bps_negative_below_anchor() -> None:
    assert round(distance_bps(100.495, 101.0), 4) == -50.0


def test_distance_bps_zero_anchor_is_zero() -> None:
    assert distance_bps(100.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# compute_session_vwap
# ---------------------------------------------------------------------------


def test_session_vwap_typical_price_weighted_average() -> None:
    day_start = 10 * DAY
    boundary = day_start + LONDON_OPEN_H
    decision_ts = boundary + 3 * 60
    bars = [
        _bar(boundary, 100.0),
        _bar(boundary + 60, 101.0),
        _bar(boundary + 120, 102.0),
    ]
    anchor = compute_session_vwap(bars, decision_ts=decision_ts)
    assert anchor.reason == ""
    assert anchor.bar_count == 3
    assert anchor.price is not None
    assert round(anchor.price, 4) == 101.0


def test_session_vwap_excludes_zero_volume_bars() -> None:
    day_start = 10 * DAY
    boundary = day_start + LONDON_OPEN_H
    decision_ts = boundary + 4 * 60
    bars = [
        _bar(boundary, 100.0),
        _bar(boundary + 60, 9999.0, volume=0.0),  # zero-volume — must be ignored
        _bar(boundary + 120, 101.0),
        _bar(boundary + 180, 102.0),
    ]
    anchor = compute_session_vwap(bars, decision_ts=decision_ts)
    assert anchor.bar_count == 3
    assert anchor.price is not None
    assert round(anchor.price, 4) == 101.0


def test_session_vwap_insufficient_volume_bars_returns_reason() -> None:
    day_start = 10 * DAY
    boundary = day_start + LONDON_OPEN_H
    decision_ts = boundary + 2 * 60
    assert MIN_VOLUME_BARS >= 3
    bars = [_bar(boundary, 100.0), _bar(boundary + 60, 101.0)]  # only 2 volume bars
    anchor = compute_session_vwap(bars, decision_ts=decision_ts)
    assert anchor.price is None
    assert anchor.reason == "insufficient_volume_bars"


def test_session_vwap_no_anchor_when_no_bars() -> None:
    anchor = compute_session_vwap([], decision_ts=10 * DAY + LONDON_OPEN_H)
    assert anchor.price is None
    assert anchor.reason == "no_session_anchor"


def test_session_vwap_walks_back_to_prior_day_when_before_first_open() -> None:
    """Look-ahead safety: an anchor before ANY bar in-window returns None."""
    day_start = 10 * DAY
    decision_ts = day_start + 1 * 3600  # 01:00 UTC — before London/NY today
    bars = [_bar(day_start - 10 * 60, 100.0)]  # bar is BEFORE yesterday's NY open
    anchor = compute_session_vwap(bars, decision_ts=decision_ts)
    assert anchor.price is None  # no bar at/after the walked-back boundary


# ---------------------------------------------------------------------------
# compute_signal_day_avwap
# ---------------------------------------------------------------------------


def test_signal_day_avwap_uses_utc_midnight_anchor() -> None:
    day_start = 10 * DAY
    decision_ts = day_start + 5 * 3600
    bars = [
        _bar(day_start - 60, 999.0),  # yesterday — must be excluded
        _bar(day_start, 100.0),
        _bar(day_start + 60, 101.0),
        _bar(day_start + 120, 102.0),
    ]
    anchor = compute_signal_day_avwap(bars, decision_ts=decision_ts)
    assert anchor.bar_count == 3
    assert anchor.price is not None
    assert round(anchor.price, 4) == 101.0


def test_signal_day_avwap_no_bars_today() -> None:
    day_start = 10 * DAY
    anchor = compute_signal_day_avwap(
        [_bar(day_start - 60, 100.0)], decision_ts=day_start + 3600
    )
    assert anchor.price is None
    assert anchor.reason == "no_day_bars"


# ---------------------------------------------------------------------------
# _frontgate_bars — look-ahead guard
# ---------------------------------------------------------------------------


def test_bar_cutoff_ts_excludes_forming_and_last_closed_bar() -> None:
    decision_ts = 1_000_120  # not on a minute boundary
    cutoff = bar_cutoff_ts(decision_ts)
    assert cutoff == (1_000_120 // 60) * 60 - 60


def test_fetch_known_bars_excludes_bars_after_cutoff(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "bars.sqlite")
    try:
        decision_ts = 20 * DAY
        cutoff = bar_cutoff_ts(decision_ts)
        _seed_1m(conn, venue="okx", symbol="BTC-USDT", ts=cutoff, close=100.0)
        _seed_1m(conn, venue="okx", symbol="BTC-USDT", ts=cutoff + 60, close=999.0)  # future
        conn.commit()
        bars = fetch_known_bars(conn, venue="okx", symbol="BTC-USDT", decision_ts=decision_ts)
        assert [b.ts for b in bars] == [cutoff]
    finally:
        conn.close()


def test_fetch_known_bars_none_conn_is_empty() -> None:
    assert fetch_known_bars(None, venue="okx", symbol="X", decision_ts=1) == []


def _seed_1m(
    conn: sqlite3.Connection, *, venue: str, symbol: str, ts: int, close: float,
) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) VALUES "
        "(?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 10.0)",
        (f"{venue}:{symbol}", f"{venue}.{symbol}", venue, symbol, ts, close, close, close, close),
    )


# ---------------------------------------------------------------------------
# log_vwap_timing_shadow — DB write
# ---------------------------------------------------------------------------


def test_log_vwap_timing_shadow_writes_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "shadow.sqlite")
    try:
        day_start = 10 * DAY
        boundary = day_start + LONDON_OPEN_H
        bars = [_bar(boundary, 100.0), _bar(boundary + 60, 101.0), _bar(boundary + 120, 102.0)]
        log_vwap_timing_shadow(
            conn, run_id="run-1", signal_id="sig-1", venue="okx", symbol="BTC-USDT",
            decision_ts=boundary + 180, current_price=101.505, bar_views=bars,
        )
        row = conn.execute(
            "SELECT run_id, session_vwap, session_vwap_distance_bps, "
            "session_vwap_reason, fill_price, unresolvable FROM vwap_timing_shadow"
        ).fetchone()
        assert row[0] == "run-1"
        assert round(row[1], 4) == 101.0
        assert round(row[2], 4) == 50.0
        assert row[3] == ""
        assert row[4] is None  # stage 2 not resolved yet
        assert row[5] == 0
    finally:
        conn.close()


def test_log_vwap_timing_shadow_noop_without_conn() -> None:
    log_vwap_timing_shadow(
        None, run_id="r", signal_id="s", venue="okx", symbol="X",
        decision_ts=1, current_price=100.0, bar_views=[],
    )


def test_log_vwap_timing_shadow_noop_zero_price(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "zero.sqlite")
    try:
        log_vwap_timing_shadow(
            conn, run_id="r", signal_id="s", venue="okx", symbol="X",
            decision_ts=1, current_price=0.0, bar_views=[],
        )
        assert conn.execute("SELECT COUNT(*) FROM vwap_timing_shadow").fetchone()[0] == 0
    finally:
        conn.close()


def test_vwap_timing_shadow_table_exists_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(vwap_timing_shadow)")}
        assert {
            "run_id", "signal_id", "venue", "symbol", "decision_ts", "current_price",
            "session_vwap", "session_vwap_distance_bps", "avwap", "avwap_distance_bps",
            "fill_price", "fill_ts", "side", "session_vwap_improvement_bps",
            "avwap_improvement_bps", "resolve_attempts", "unresolvable",
        } <= cols
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Behavior-0: G3 PASS unaffected by the frontgate tag (relocated from G4,
# P2a group A — called from signal_validator_gate now)
# ---------------------------------------------------------------------------


def _g3_ctx() -> GateContext:
    now = int(time.time())
    return GateContext(
        run_id="run-vwap",
        signal_id="sig-vwap",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "raw_signal": {"symbol": "BTC-USDT", "strategy": "s1"},
            "cell_routing": {"quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5},
            "tick_window": [{"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}],
            "cell_quartile": "mid",
            "regime": "trend_up",
        },
        started_ts=now,
        state=SignalLifecycle.RAW,
    )


async def test_g3_pass_byte_identical_with_shadow_conn(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "wired.sqlite")
    try:
        result_no_shadow = await signal_validator_gate(_g3_ctx())
        result_shadow = await signal_validator_gate(_g3_ctx(), shadow_conn=conn)
        assert result_shadow.decision == result_no_shadow.decision == GateDecision.PASS
        assert result_shadow.payload == result_no_shadow.payload
        assert result_shadow.model_used == result_no_shadow.model_used == "python"
        # The tag DID fire (a row was written) — proves the wiring is live, not dead code.
        assert conn.execute("SELECT COUNT(*) FROM vwap_timing_shadow").fetchone()[0] == 1
    finally:
        conn.close()


async def test_g3_shadow_tag_fail_open_on_bad_conn() -> None:
    """A closed/broken shadow_conn must never crash the live gate (fail-open)."""
    conn = sqlite3.connect(":memory:")
    conn.close()  # deliberately broken — any .execute() raises ProgrammingError
    result = await signal_validator_gate(_g3_ctx(), shadow_conn=conn)
    assert result.decision == GateDecision.PASS


async def test_g3_frontgate_shadow_routes_bars_and_vwap_write_to_md_conn(
    tmp_path: Path,
) -> None:
    """storage-split (round 4 fix): bars/vwap_timing_shadow are marketdata-
    domain — when ``md_conn`` is supplied, the frontgate tagger reads bars
    from AND writes vwap_timing_shadow to it, never the trading
    ``shadow_conn`` (which stays reserved for gate_shadow_events)."""
    shadow_conn = init_db(tmp_path / "trading.sqlite")
    md_conn = init_db(tmp_path / "md.sqlite")
    try:
        result = await signal_validator_gate(
            _g3_ctx(), shadow_conn=shadow_conn, md_conn=md_conn,
        )
        assert result.decision == GateDecision.PASS
        assert md_conn.execute(
            "SELECT COUNT(*) FROM vwap_timing_shadow"
        ).fetchone()[0] == 1
        assert shadow_conn.execute(
            "SELECT COUNT(*) FROM vwap_timing_shadow"
        ).fetchone()[0] == 0
    finally:
        shadow_conn.close()
        md_conn.close()
