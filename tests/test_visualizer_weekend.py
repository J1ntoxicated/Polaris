"""Weekend-tab backend tests (DEMO/PAPER, display-only, READ-ONLY).

Covers the four-section weekend operations board builder behind the dashboard
``/api/weekend`` endpoint. Nothing here touches sizing/risk/orders — the module
reads ``fills`` / ``weekend_shadow_orders`` / ``bars`` / ``maker_fill_shadow``
read-only and resolves would-be forward P&L offline from stored bars. Tests
assert the weekend-detection clock, the next-session-open countdown, the
would-be R rail logic (target / stop / open, long / short, degenerate), and the
SQLite section builders against a synthetic in-memory DB + graceful empty.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from tools.visualizer import weekend_data as wd

# ── weekend detection ───────────────────────────────────────────────────────


def _utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.UTC).timestamp())


def test_is_weekend_true_on_saturday_and_sunday() -> None:
    # 2026-06-27 is a Saturday, 2026-06-28 a Sunday.
    assert wd.is_weekend_utc(_utc(2026, 6, 27, 12))
    assert wd.is_weekend_utc(_utc(2026, 6, 28, 3))


def test_is_weekend_false_on_weekday() -> None:
    # 2026-06-29 is a Monday, 2026-06-24 a Wednesday.
    assert not wd.is_weekend_utc(_utc(2026, 6, 29, 9))
    assert not wd.is_weekend_utc(_utc(2026, 6, 24, 14))


def test_is_weekend_graceful_on_garbage() -> None:
    assert not wd.is_weekend_utc("not-a-number")  # type: ignore[arg-type]
    assert not wd.is_weekend_utc(-5)


# ── next-session-open countdown ─────────────────────────────────────────────


def test_next_session_opens_one_per_region_sorted() -> None:
    out = wd.next_session_opens(_utc(2026, 6, 24, 9))  # Wed 09:00 UTC
    regions = {r["region"] for r in out}
    assert regions == {"asia", "europe", "us"}
    # Sorted soonest-first + every open is strictly in the future.
    secs = [r["seconds_until"] for r in out]
    assert secs == sorted(secs)
    assert all(s > 0 for s in secs)


def test_next_session_opens_skips_weekend_to_monday() -> None:
    # Saturday → the next opens must all land on Monday (a weekday), never Sun.
    out = wd.next_session_opens(_utc(2026, 6, 27, 12))  # Sat
    assert out, "weekend must still project the upcoming Monday opens"
    for r in out:
        wday = dt.datetime.fromtimestamp(r["open_ts"], tz=dt.UTC).weekday()
        assert wday < 5, f"{r['region']} open landed on a weekend day {wday}"


def test_next_session_opens_us_today_when_before_open() -> None:
    # Wed 12:00 UTC is before the US 13:30 UTC open → US open is later TODAY.
    out = wd.next_session_opens(_utc(2026, 6, 24, 12))
    us = next(r for r in out if r["region"] == "us")
    open_dt = dt.datetime.fromtimestamp(us["open_ts"], tz=dt.UTC)
    assert (open_dt.hour, open_dt.minute) == (13, 30)
    assert open_dt.date() == dt.date(2026, 6, 24)


def test_next_session_opens_graceful_on_garbage() -> None:
    assert wd.next_session_opens("x") == []  # type: ignore[arg-type]


# ── would-be R rail logic ───────────────────────────────────────────────────


def test_would_be_long_hits_target_first() -> None:
    # entry 100, atr 1% → R = 1.0 price. +1R target = 101, -1R stop = 99.
    out = wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[100.5, 101.2, 98.0],
        forward_highs=[100.6, 101.3, 98.5],
        forward_lows=[100.2, 100.9, 97.8],
    )
    assert out.status == "target"
    assert out.r == pytest.approx(1.0)


def test_would_be_long_hits_stop_first() -> None:
    out = wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[99.5, 98.5],
        forward_highs=[99.7, 98.8],
        forward_lows=[98.9, 98.0],   # first bar low 98.9 <= stop 99 → stop
    )
    assert out.status == "stop"
    assert out.r == pytest.approx(-1.0)


def test_would_be_short_hits_target_first() -> None:
    # short: target is BELOW entry (99), stop ABOVE (101).
    out = wd.resolve_would_be_r(
        side="short", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[99.5, 98.8],
        forward_highs=[99.9, 99.2],
        forward_lows=[99.0, 98.5],   # low 98.5 <= target 99 → target
    )
    assert out.status == "target"
    assert out.r == pytest.approx(1.0)


def test_would_be_open_marks_to_last_close() -> None:
    # Never reaches +1R (101) or -1R (99) → open, marked at last close 100.5.
    out = wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[100.2, 100.5],
        forward_highs=[100.3, 100.6],
        forward_lows=[100.1, 100.4],
    )
    assert out.status == "open"
    # move = 0.5, R-price = 1.0 → +0.5R.
    assert out.r == pytest.approx(0.5)


def test_would_be_profit_target_above_one_r() -> None:
    # profit_target_r = 2.0 → target 102; a +2R hit pays +2.0R.
    out = wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=2.0,
        forward_closes=[101.0, 102.5],
        forward_highs=[101.2, 102.6],
        forward_lows=[100.8, 101.9],
    )
    assert out.status == "target"
    assert out.r == pytest.approx(2.0)


def test_would_be_degenerate_inputs_flat_open() -> None:
    assert wd.resolve_would_be_r(
        side="long", entry_mark=0.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[1.0],
    ) == wd._WouldBe(0.0, "open")
    assert wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.0, profit_target_r=1.0,
        forward_closes=[101.0],
    ) == wd._WouldBe(0.0, "open")
    assert wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[],
    ) == wd._WouldBe(0.0, "open")


def test_would_be_close_only_fallback() -> None:
    # No highs/lows supplied → close acts as both → target hit on close 101.
    out = wd.resolve_would_be_r(
        side="long", entry_mark=100.0, atr_pct=0.01, profit_target_r=1.0,
        forward_closes=[100.5, 101.0],
    )
    assert out.status == "target"


# ── SQLite section builders ─────────────────────────────────────────────────


def _seed_db() -> sqlite3.Connection:
    """In-memory DB with the four weekend tables + minimal rows."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE fills (
            fill_id TEXT, venue TEXT, instrument_id TEXT, strategy_id TEXT,
            side TEXT, fee_usd REAL, ts_ms INTEGER, pnl_usd REAL, is_close INTEGER
        );
        CREATE TABLE weekend_shadow_orders (
            event_id TEXT, run_id TEXT, strategy_id TEXT, venue TEXT, symbol TEXT,
            side TEXT, entry_mark REAL, notional_usd REAL, atr_pct REAL,
            profit_target_r REAL, created_ts INTEGER
        );
        CREATE TABLE maker_fill_shadow (event_id TEXT, created_ts INTEGER);
        CREATE TABLE bars (
            venue TEXT, symbol TEXT, bar_interval TEXT, ts INTEGER,
            high REAL, low REAL, close REAL
        );
        """
    )
    return conn


def test_settlement_real_fee_net_rollup() -> None:
    conn = _seed_db()
    now = _utc(2026, 6, 28, 12)
    recent = (now - 3600) * 1000  # 1h ago, in window
    old = (now - 10 * 24 * 3600) * 1000  # 10d ago, OUT of 7d window
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "fee_usd, ts_ms, pnl_usd, is_close) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("a", "okx", "okx:BTC-USDT", "strat_x", "sell", 2.0, recent, 50.0, 1),
            ("b", "okx", "okx:ETH-USDT", "strat_x", "sell", 1.0, recent, -20.0, 1),
            ("c", "okx", "okx:LTC-USDT", "strat_y", "sell", 3.0, recent, 80.0, 1),
            # OUT of window — must be excluded.
            ("d", "okx", "okx:BTC-USDT", "strat_x", "sell", 9.0, old, 999.0, 1),
            # is_close=0 (an entry) — never counted.
            ("e", "okx", "okx:BTC-USDT", "strat_x", "buy", 1.0, recent, 0.0, 0),
        ],
    )
    out = wd._settlement(conn, now_ts=now)
    by_strat = {(r["strategy_id"], r["venue"]): r for r in out["rows"]}
    # strat_x: net = (50-2) + (-20-1) = 27 ; strat_y: 80-3 = 77.
    assert by_strat[("strat_x", "okx")]["net_usd"] == pytest.approx(27.0)
    assert by_strat[("strat_y", "okx")]["net_usd"] == pytest.approx(77.0)
    # total over window = 27 + 77 (the 999 old fill excluded).
    assert out["total_net_usd"] == pytest.approx(104.0)
    # best = strat_y (+77 net), worst = the -21 net leg.
    assert out["best"]["net_usd"] == pytest.approx(77.0)
    assert out["worst"]["net_usd"] == pytest.approx(-21.0)


def test_settlement_empty_when_no_fills() -> None:
    conn = _seed_db()
    out = wd._settlement(conn, now_ts=_utc(2026, 6, 28))
    assert out == {"rows": [], "best": None, "worst": None, "total_net_usd": 0.0}


def test_shadow_incubator_resolves_orders() -> None:
    conn = _seed_db()
    created = _utc(2026, 6, 28, 0)
    # One funding order on COIN-USDT, entry 100, atr 1% → target 101 / stop 99.
    conn.execute(
        "INSERT INTO weekend_shadow_orders (event_id, run_id, strategy_id, "
        "venue, symbol, side, entry_mark, notional_usd, atr_pct, "
        "profit_target_r, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("e1", "r1", "weekend_funding_capitulation_maker", "okx", "COIN-USDT",
         "long", 100.0, 2000.0, 0.01, 1.0, created),
    )
    # Forward bars: rises to 101.5 (target hit).
    conn.executemany(
        "INSERT INTO bars (venue, symbol, bar_interval, ts, high, low, close) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("okx", "COIN-USDT", "1m", created + 60, 100.5, 100.1, 100.4),
            ("okx", "COIN-USDT", "1m", created + 120, 101.6, 100.9, 101.5),
        ],
    )
    out = wd._shadow_incubator(conn)
    assert out["order_n"] == 1
    assert out["maker_fill_shadow_n"] == 0
    assert out["maker_persist_wired"] is False
    strat = out["strategies"][0]
    assert strat["strategy_id"] == "weekend_funding_capitulation_maker"
    assert strat["n"] == 1
    assert strat["target"] == 1
    assert strat["mean_r"] == pytest.approx(1.0)


def test_readiness_freshness_and_opens() -> None:
    conn = _seed_db()
    now = _utc(2026, 6, 28, 12)
    conn.execute(
        "INSERT INTO weekend_shadow_orders (event_id, strategy_id, venue, "
        "symbol, side, entry_mark, atr_pct, profit_target_r, created_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("e1", "s", "okx", "BTC-USDT", "long", 1.0, 0.01, 1.0, now),
    )
    conn.execute(
        "INSERT INTO bars (venue, symbol, bar_interval, ts, high, low, close) "
        "VALUES (?,?,?,?,?,?,?)",
        ("okx", "BTC-USDT", "1m", now - 120, 1.0, 1.0, 1.0),
    )
    out = wd._readiness(conn, now_ts=now)
    assert {r["region"] for r in out["session_opens"]} == {"asia", "europe", "us"}
    fresh = out["bar_freshness"]
    assert fresh[0]["symbol"] == "BTC-USDT"
    assert fresh[0]["age_sec"] == pytest.approx(120, abs=2)


def test_build_weekend_full_shape_and_graceful() -> None:
    conn = _seed_db()
    out = wd.build_weekend(conn, now_ts=_utc(2026, 6, 28, 12))
    assert out["is_weekend"] is True
    for key in ("settlement", "shadow", "readiness", "funding_strategy"):
        assert key in out
    # Empty DB → graceful empty sections, never a raise.
    assert out["settlement"]["rows"] == []
    assert out["shadow"]["strategies"] == []


def test_build_weekend_missing_tables_graceful() -> None:
    # A bare DB with NONE of the weekend tables → every section degrades empty.
    conn = sqlite3.connect(":memory:")
    out = wd.build_weekend(conn, now_ts=_utc(2026, 6, 24, 12))
    assert out["is_weekend"] is False
    assert out["settlement"]["rows"] == []
    assert out["shadow"]["order_n"] == 0
    assert out["shadow"]["maker_fill_shadow_n"] == 0
    assert out["readiness"]["bar_freshness"] == []
