"""Increment 1 — entrance judgment LANDS in the Layer-0 focus pass (end-to-end).

DEMO/PAPER · AGGRESSIVE · flow_not_block · in-loop GPT = 0.

Proves the judgment is BUILT + LANDED (not "researched, unapplied"):
  * ``refresh_focus_watchlist`` persists ``opportunity_score`` + ``trade_eligible``
    on every focus row (the judgment is actually written).
  * The ambiguity sidecar receives one ``entrance_judgments`` row per candidate
    when a probe_conn is supplied — observe-only, AI-free.
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
    n = probe_conn.execute("SELECT COUNT(*) FROM entrance_judgments").fetchone()[0]
    assert n == 2  # one row per judged candidate (observe-only, AI-free)
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
