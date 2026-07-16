"""Increment 1 — entrance judgment LANDS in the Layer-0 focus pass (end-to-end).

DEMO/PAPER · AGGRESSIVE · flow_not_block · in-loop GPT = 0.

Proves the judgment is BUILT + LANDED (not "researched, unapplied"):
  * ``refresh_focus_watchlist`` persists ``opportunity_score`` + ``trade_eligible``
    on every focus row (the judgment is actually written).
  * The ambiguity sidecar receives one ``entrance_judgments`` row per candidate
    that is trade_eligible OR near the trade floor when a probe_conn is
    supplied — observe-only, AI-free (A3: deep never-actionable misses are
    dropped from the sidecar write, not from the judgment itself).
  * The WATCH set (full focus) stays a superset of the TRADE set (eligible) at the
    real ``get_focus_targets`` seam — flow-preserving decouple.
"""

from __future__ import annotations

from polaris.core.probes.tuning_log import open_probe_db
from polaris.scripts._production_layers import (
    get_focus_targets,
    refresh_focus_watchlist,
)
from polaris.storage.schema import init_db


def _seed_universe(conn, rows) -> None:
    for venue, symbol, vol, atr in rows:
        conn.execute(
            "INSERT OR REPLACE INTO universe "
            "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
            " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
            " depth_10bps_usd, last_seen_ts, is_active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                venue, symbol, f"{venue}:{symbol}", f"crypto:{symbol}", "crypto",
                "USDT", "live", vol, 2.0, atr, 1e6, 1000,
            ),
        )
    conn.commit()


def test_refresh_persists_opportunity_and_eligibility() -> None:
    conn = init_db(":memory:")
    _seed_universe(
        conn,
        [
            ("okx", "BTC-USDT", 9e8, 6.0),
            ("okx", "ETH-USDT", 3e8, 4.0),
            ("okx", "LOW-USDT", 1e7, 1.0),
        ],
    )
    n = refresh_focus_watchlist(conn, cycle_ts=2000)
    assert n == 3
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT symbol, opportunity_score, trade_eligible FROM watchlist_focus"
        )
    }
    # Every focus row carries a judged opportunity_score (the judgment LANDED).
    assert all(rows[s][0] is not None for s in rows)
    # The high-liquidity BTC out-scores the thin LOW name.
    assert rows["BTC-USDT"][0] > rows["LOW-USDT"][0]
    # trade_eligible is persisted as INT (1/0).
    assert all(rows[s][1] in (0, 1) for s in rows)
    conn.close()


def test_refresh_writes_ambiguity_sidecar(tmp_path) -> None:
    conn = init_db(":memory:")
    probe_conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_universe(conn, [("okx", "BTC-USDT", 9e8, 6.0), ("okx", "ETH-USDT", 3e8, 4.0)])
    refresh_focus_watchlist(conn, cycle_ts=2000, probe_conn=probe_conn, run_id="r1")
    rows = probe_conn.execute(
        "SELECT instrument_id, trade_eligible FROM entrance_judgments"
    ).fetchall()
    # A3 write-amp cut: the deep never-actionable (trade_eligible=0, far below
    # floor) candidate is not persisted to the sidecar; the eligible one is.
    ids = {r[0] for r in rows}
    assert "okx:BTC-USDT" in ids
    assert all(eligible == 1 for _, eligible in rows)
    conn.close()
    probe_conn.close()


def test_watch_superset_of_trade_after_refresh() -> None:
    conn = init_db(":memory:")
    _seed_universe(
        conn,
        [("okx", f"S{i}-USDT", 1e7 * (i + 1), 1.0 + i * 0.5) for i in range(8)],
    )
    refresh_focus_watchlist(conn, cycle_ts=2000)
    watch = {s for _v, s, _a, _g in get_focus_targets(conn, cycle_ts=2000, max_n=50)}
    trade = {
        s
        for _v, s, _a, _g in get_focus_targets(
            conn, cycle_ts=2000, max_n=50, eligible_only=True
        )
    }
    # flow_not_block: WATCH observes everyone; TRADE is a subset (never larger).
    assert trade <= watch
    assert watch  # non-empty
    conn.close()


def test_get_focus_targets_resolves_multi_venue_after_scoped_universe_query() -> None:
    """NIT — the universe resolution SELECT now filters by symbol too (not
    venue alone). Confirms multi-venue focus rows still resolve their
    asset_class/group_id correctly against the narrower query."""
    conn = init_db(":memory:")
    _seed_universe(conn, [("okx", "BTC-USDT", 9e8, 6.0)])
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        " depth_10bps_usd, last_seen_ts, is_active) "
        "VALUES ('capital', 'EURUSD', 'capital:EURUSD', 'forex:EURUSD', 'forex', "
        " 'USD', 'live', 5e8, 1.0, 0.5, 1e6, 1000, 1)"
    )
    conn.commit()
    refresh_focus_watchlist(conn, cycle_ts=2000)
    targets = {
        (v, s): (ac, g)
        for v, s, ac, g in get_focus_targets(conn, cycle_ts=2000, max_n=10)
    }
    assert targets[("okx", "BTC-USDT")][0] == "crypto"
    assert targets[("capital", "EURUSD")][0] == "forex"
    conn.close()


# ---------------------------------------------------------------------------
# Lean wiring (audit code_review_2026-06-24): the technical / regime / altdata
# lenses now feed opportunity_score from data the loop holds. These prove the
# leans LAND (change the score) and that absent data degrades to neutral.
# ---------------------------------------------------------------------------


class _RisingWriter:
    """Quote-writer stub: a monotonically rising tick window for one instrument."""

    def __init__(self, instrument_id: str) -> None:
        self._iid = instrument_id

    def feature_window(self, instrument_id: str):  # noqa: ANN201
        if instrument_id != self._iid:
            return []

        class _T:
            def __init__(self, mid: float) -> None:
                self.mid = mid

        return [_T(100.0), _T(103.0), _T(108.0)]


def _score_for(conn, symbol: str) -> float:
    # watchlist_focus PK is (cycle_ts, venue, symbol) — read the LATEST cycle's row.
    row = conn.execute(
        "SELECT opportunity_score FROM watchlist_focus WHERE symbol=? "
        "ORDER BY cycle_ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return float(row[0])


def test_technical_lean_lifts_opportunity_score() -> None:
    # Same universe, judged WITH vs WITHOUT a positive technical lean → the lean
    # must lift the score (signal-only rank tilt, never a sizing change).
    conn = init_db(":memory:")
    _seed_universe(conn, [("okx", "BTC-USDT", 5e8, 4.0), ("okx", "ETH-USDT", 5e8, 4.0)])
    refresh_focus_watchlist(conn, cycle_ts=2000)
    base = _score_for(conn, "BTC-USDT")
    refresh_focus_watchlist(
        conn, cycle_ts=2001, quote_writer=_RisingWriter("okx:BTC-USDT")
    )
    lifted = _score_for(conn, "BTC-USDT")
    # ETH (no window) is unchanged; BTC (rising window) is lifted.
    assert lifted > base
    conn.close()


def test_regime_lean_lifts_with_bull_regime() -> None:
    conn = init_db(":memory:")
    _seed_universe(conn, [("okx", "BTC-USDT", 5e8, 4.0)])
    refresh_focus_watchlist(conn, cycle_ts=2000)
    base = _score_for(conn, "BTC-USDT")
    # Seed a bull_trend regime for the BTC group (regime_state SSOT).
    conn.execute(
        "INSERT OR REPLACE INTO regime_state "
        "(venue, underlying_group_id, regime, updated_ts) VALUES (?,?,?,?)",
        ("okx", "crypto:BTC-USDT", "bull_trend", 2000),
    )
    conn.commit()
    refresh_focus_watchlist(conn, cycle_ts=2001)
    assert _score_for(conn, "BTC-USDT") > base
    conn.close()


def test_absent_leans_neutral_byte_identical() -> None:
    # No writer, no altdata, no regime row → the wired call must equal the
    # liquidity+ATR-only baseline (the three lenses degrade to neutral).
    conn = init_db(":memory:")
    _seed_universe(conn, [("okx", "BTC-USDT", 9e8, 6.0), ("okx", "ETH-USDT", 3e8, 4.0)])
    refresh_focus_watchlist(conn, cycle_ts=2000)
    a = {s: _score_for(conn, s) for s in ("BTC-USDT", "ETH-USDT")}
    refresh_focus_watchlist(
        conn, cycle_ts=2001, quote_writer=None, altdata_cache=None
    )
    b = {s: _score_for(conn, s) for s in ("BTC-USDT", "ETH-USDT")}
    assert a == b
    conn.close()
