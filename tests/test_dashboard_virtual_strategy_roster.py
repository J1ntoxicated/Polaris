"""Dashboard NEW-reality backend — VIRTUAL ACCOUNT + STRATEGY ROSTER (Jin 2026-07-07).

DEMO/PAPER, VIRTUAL ACCOUNT. Read-only display layer — no venue calls, no
trading behavior. Pins the fields Jin needs to see "is it making profit + why
is it quiet" at a glance:

  1. Per-exchange VIRTUAL equity (``virtual_equity_usd`` /
     ``virtual_seed_usd``) surfaced on ``StreamSummary``, derived purely from
     the internal fills ledger (zero venue calls) — SEPARATE from the legacy
     real-venue ``equity_usd`` / ``starting_capital``.
  2. STRATEGY ROSTER: ``strategy_class`` (EARN/PROVE/BENCH) + signal activity
     (``signals_24h`` / ``last_signal_ts``) on ``StrategyStat``, including
     strategies that have signalled but never traded (so a quiet, setup-driven
     book still appears on the roster instead of vanishing).
  3. Snapshot-level MODE banner distinguishing VIRTUAL from legacy real-venue
     reconciliation.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_q_strategy import _strategy_stats
from polaris.scripts.dashboard.snapshot_q_streams import (
    VIRTUAL_SEED_USD,
    _per_stream_summary,
)
from polaris.storage.schema import init_db


def _now_s() -> int:
    import time

    return int(time.time())


def _seed_signals_and_class(conn: sqlite3.Connection, *, now_s: int) -> None:
    """Seed a signals table + strategy_class rows for two strategies:

    - ``gold_trend_chandelier_1d``: EARN class, 3 signals in the last 24h,
      no trades yet this session (the "quiet by design" slow-trend book).
    - ``rsi_bb_pullback``: PROVE class, 1 signal 3 days ago (stale — must
      surface a true last-signal age, not a false recent one), 0 trades.
    """
    signals = [
        # (strategy_id, signal_id, instrument_id, correlation_group, direction,
        #  score, thesis, ts, payload_json)
        ("gold_trend_chandelier_1d", "s1", "capital:XAUUSD", "cfd_gold_trend",
         "long", 0.8, "trend", now_s - 3600, "{}"),
        ("gold_trend_chandelier_1d", "s2", "capital:XAUUSD", "cfd_gold_trend",
         "long", 0.7, "trend", now_s - 7200, "{}"),
        ("gold_trend_chandelier_1d", "s3", "capital:XAUUSD", "cfd_gold_trend",
         "long", 0.75, "trend", now_s - 100, "{}"),
        ("rsi_bb_pullback", "s4", "okx:BTC-USDT", "spot_mean_reversion",
         "long", 0.6, "pullback", now_s - 3 * 86_400, "{}"),
    ]
    conn.executemany(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, "
        "correlation_group, direction, score, thesis, ts, payload_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        signals,
    )
    classes = [
        # (venue, strategy_id, strategy_class, window_w, f_track_cap, dwell,
        #  epoch_id, last_transition_ts, kill_state, ladder_step,
        #  open_lifecycle_id, qty, cum_fees, cum_pnl, intent_ring, shadow_ring,
        #  probe_fee_24h, last_promotion_ts)
        ("capital", "gold_trend_chandelier_1d", "EARN", 20, 1.0, 0, 1, 0,
         "ACTIVE", 0, "", 0.0, 0.0, 0.0, "[]", "[]", 0.0, None),
        ("okx", "rsi_bb_pullback", "PROVE", 20, 1.0, 0, 1, 0,
         "ACTIVE", 0, "", 0.0, 0.0, 0.0, "[]", "[]", 0.0, None),
    ]
    conn.executemany(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, f_track_cap, dwell, epoch_id, last_transition_ts, "
        "kill_state, ladder_step, open_lifecycle_id, qty, cum_fees, cum_pnl, "
        "intent_ring, shadow_ring, probe_fee_24h, last_promotion_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        classes,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. VIRTUAL ACCOUNT per exchange — zero venue calls, separate from legacy.
# ---------------------------------------------------------------------------


def test_streams_carry_virtual_seed_and_equity(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    conn.commit()
    now_s = _now_s()
    streams = _per_stream_summary(conn, now_s=now_s)
    conn.close()
    assert len(streams) == 3
    for s in streams:
        # No fills seeded -> equity == the fresh $100k seed exactly.
        assert s.virtual_seed_usd == VIRTUAL_SEED_USD
        assert s.virtual_equity_usd == VIRTUAL_SEED_USD


def test_virtual_equity_reflects_realized_pnl_not_legacy_starting(
    tmp_path: Path,
) -> None:
    """A closed win on okx bumps virtual_equity_usd by the net PnL, while the
    legacy ``equity_usd``/``starting_capital`` (real-venue split) is untouched
    by this seed — the two numbers are independent measurements."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_ms = _now_s() * 1000
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f1','okx','okx:BTC-USDT','tsmom','sell',1000.0,100.0,1.0,0.0,"
        f"{now_ms},'o1',NULL,200.0,1,10.0,1000.0,'filled')"
    )
    conn.commit()
    now_s = _now_s()
    streams = _per_stream_summary(conn, now_s=now_s)
    conn.close()
    okx = next(s for s in streams if s.venue == "okx")
    # net = 200 gross - 1 fee = 199 -> virtual equity = 100_000 + 199
    assert round(okx.virtual_equity_usd, 2) == round(VIRTUAL_SEED_USD + 199.0, 2)
    assert okx.virtual_seed_usd == VIRTUAL_SEED_USD
    # Legacy real-venue equity_usd (starting_capital + net_pnl + upnl) is computed
    # via a SEPARATE formula/constant (demo_starting_equity_okx) — same net_pnl
    # input, but virtual_equity_usd is independently anchored on VIRTUAL_SEED_USD,
    # not on whatever legacy starting_capital resolves to in this environment.
    assert okx.virtual_equity_usd == VIRTUAL_SEED_USD + okx.net_pnl_usd


def test_collect_snapshot_streams_carry_virtual_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    assert len(snap.streams) == 3
    for s in snap.streams:
        assert s.virtual_seed_usd == VIRTUAL_SEED_USD
        assert s.virtual_equity_usd == VIRTUAL_SEED_USD


# ---------------------------------------------------------------------------
# 2. MODE banner — VIRTUAL vs legacy real-venue reconciliation.
# ---------------------------------------------------------------------------


def test_mode_banner_virtual_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    assert snap.virtual_account_enabled is True
    assert "VIRTUAL" in snap.mode_banner
    assert "100k" in snap.mode_banner or "$100,000" in snap.mode_banner


def test_mode_banner_virtual_off_default(tmp_path: Path) -> None:
    os.environ.pop("POLARIS_VIRTUAL_ACCOUNT", None)
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    conn.commit()
    conn.close()
    snap = collect_snapshot(db_path)
    assert snap.virtual_account_enabled is False
    assert snap.mode_banner  # non-empty even in the legacy path


def test_mode_banner_on_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The zero-DB early-return path also carries the mode banner."""
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    snap = collect_snapshot(tmp_path / "does_not_exist.sqlite")
    assert snap.virtual_account_enabled is True
    assert "VIRTUAL" in snap.mode_banner


# ---------------------------------------------------------------------------
# 3. STRATEGY ROSTER + SIGNAL/SETUP STATE ("why quiet").
# ---------------------------------------------------------------------------


def test_strategy_stats_carry_class_and_signal_activity(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = _now_s()
    _seed_signals_and_class(conn, now_s=now_s)
    stats = _strategy_stats(conn, now_s=now_s, positions=[])
    conn.close()
    by_id = {s.strategy_id: s for s in stats}

    gold = by_id["gold_trend_chandelier_1d"]
    assert gold.strategy_class == "EARN"
    assert gold.signals_24h == 3
    assert gold.last_signal_ts > 0

    rsi = by_id["rsi_bb_pullback"]
    assert rsi.strategy_class == "PROVE"
    # The single signal is 3 days old -> NOT counted in the 24h window.
    assert rsi.signals_24h == 0
    # But last_signal_ts must still reflect the true (stale) last fire, not 0.
    assert rsi.last_signal_ts > 0
    assert (now_s - rsi.last_signal_ts) > 86_400


def test_strategy_with_signals_but_no_trades_still_on_roster(
    tmp_path: Path,
) -> None:
    """A strategy that signalled but never traded (quiet, setup-driven) must
    still appear in strategy_stats — otherwise "why quiet" has no row to show."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = _now_s()
    _seed_signals_and_class(conn, now_s=now_s)
    stats = _strategy_stats(conn, now_s=now_s, positions=[])
    conn.close()
    ids = {s.strategy_id for s in stats}
    assert "gold_trend_chandelier_1d" in ids
    assert "rsi_bb_pullback" in ids
    gold = next(s for s in stats if s.strategy_id == "gold_trend_chandelier_1d")
    assert gold.open_n == 0
    assert gold.closed_n == 0
    assert gold.pnl_usd == 0.0


def test_strategy_class_defaults_to_earn_when_unbootstrapped(
    tmp_path: Path,
) -> None:
    """A strategy with signals but no strategy_class row yet -> defaults to
    EARN (same fail-open default resolve_strategy_class uses)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = _now_s()
    conn.execute(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, "
        "correlation_group, direction, score, thesis, ts, payload_json) "
        "VALUES ('fx_breakout_basket','s1','capital:EURUSD','cfd_fx_trend',"
        f"'long',0.5,'breakout',{now_s},'{{}}')"
    )
    conn.commit()
    stats = _strategy_stats(conn, now_s=now_s, positions=[])
    conn.close()
    fx = next(s for s in stats if s.strategy_id == "fx_breakout_basket")
    assert fx.strategy_class == "EARN"
    assert fx.signals_24h == 1


def test_collect_snapshot_strategy_stats_carry_roster_fields(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    now_s = _now_s()
    _seed_signals_and_class(conn, now_s=now_s)
    conn.close()
    snap = collect_snapshot(db_path)
    by_id = {s.strategy_id: s for s in snap.strategy_stats}
    assert by_id["gold_trend_chandelier_1d"].strategy_class == "EARN"
    assert by_id["gold_trend_chandelier_1d"].signals_24h == 3
    assert by_id["rsi_bb_pullback"].strategy_class == "PROVE"
