"""The Live Gate Activity feed must label HOLD/ADJUST monitor/exit events with a
ticker + strategy even when their payload has no signal envelope — back-filled
via the linked position (position_id) and signal (signal_id). Display-only."""
import json
import sqlite3

from polaris.scripts.dashboard.snapshot_queries import _recent_gate_events


def _conn(with_links: bool = True) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        """CREATE TABLE gate_events (
            event_id TEXT, signal_id TEXT, position_id TEXT,
            gate_id INTEGER, decision TEXT, payload_json TEXT, created_ts INTEGER
        )"""
    )
    if with_links:
        c.execute(
            "CREATE TABLE positions (position_id TEXT, symbol TEXT, strategy_id TEXT)"
        )
        c.execute(
            "CREATE TABLE signals (signal_id TEXT, instrument_id TEXT, strategy_id TEXT)"
        )
    return c


def _insert_event(c, *, gid, decision, payload, ts, sig_id=None, pos_id=None):
    c.execute(
        """INSERT INTO gate_events
           (event_id, signal_id, position_id, gate_id, decision, payload_json, created_ts)
           VALUES (?,?,?,?,?,?,?)""",
        (f"e{ts}", sig_id, pos_id, gid, decision, payload, ts),
    )


def test_monitor_event_backfilled_from_position():
    c = _conn()
    c.execute(
        "INSERT INTO positions VALUES (?,?,?)",
        ("pos1", "BTC-USDT", "tsmom_btc"),
    )
    # G6 ADJUST_EXIT: payload carries no signal envelope -> blank without the JOIN.
    # (Repetitive G6/G7 HOLD is volume-guarded OUT of the feed; an active
    # intervention like ADJUST_EXIT is the meaningful monitor event that is shown
    # and still exercises the position-JOIN backfill.)
    _insert_event(c, gid=6, decision="ADJUST_EXIT", payload="{}", ts=100, pos_id="pos1")
    ev = _recent_gate_events(c, n=5)
    assert len(ev) == 1
    assert ev[0].symbol == "BTC-USDT"
    assert ev[0].strategy == "tsmom_btc"


def test_adjust_event_backfilled_from_signal():
    c = _conn()
    c.execute(
        "INSERT INTO signals VALUES (?,?,?)",
        ("sig9", "ETH-USDT", "micro_reversion"),
    )
    _insert_event(c, gid=7, decision="ADJUST", payload="{}", ts=200, sig_id="sig9")
    ev = _recent_gate_events(c, n=5)
    assert ev[0].symbol == "ETH-USDT"
    assert ev[0].strategy == "micro_reversion"


def test_payload_takes_precedence_over_join():
    c = _conn()
    c.execute(
        "INSERT INTO positions VALUES (?,?,?)",
        ("pos2", "BTC-USDT", "wrong_strat"),
    )
    payload = json.dumps({"raw_signal": {"symbol": "SOL-USDT", "strategy_id": "vb"}})
    _insert_event(c, gid=2, decision="PASS", payload=payload, ts=300, pos_id="pos2")
    ev = _recent_gate_events(c, n=5)
    assert ev[0].symbol == "SOL-USDT"
    assert ev[0].strategy == "vb"


def test_g6_g7_hold_excluded_meaningful_kept():
    """Volume guard: repetitive G6/G7 HOLD is excluded from the feed (it is
    tallied in the per-gate decision summary instead), while meaningful
    decisions (G5 SIZED, G6 ADJUST_EXIT, G7 EXIT_NOW, G8 REFLECTED) stay."""
    c = _conn()
    _insert_event(c, gid=6, decision="HOLD", payload="{}", ts=10, pos_id="p")
    _insert_event(c, gid=7, decision="HOLD", payload="{}", ts=11, pos_id="p")
    _insert_event(c, gid=6, decision="ADJUST_EXIT", payload="{}", ts=12, pos_id="p")
    _insert_event(c, gid=7, decision="EXIT_NOW", payload="{}", ts=13, pos_id="p")
    _insert_event(c, gid=5, decision="SIZED", payload="{}", ts=14, pos_id="p")
    ev = _recent_gate_events(c, n=10)
    decisions = {(e.gate_id, e.decision) for e in ev}
    assert (6, "HOLD") not in decisions
    assert (7, "HOLD") not in decisions
    assert (6, "ADJUST_EXIT") in decisions
    assert (7, "EXIT_NOW") in decisions
    assert (5, "SIZED") in decisions


def test_g5_sized_detail_decoded_into_feed():
    """G5 SIZED feed row carries the T4 size detail (risk%/notional/scalar/tier)."""
    c = _conn()
    payload = json.dumps(
        {
            "sized": {
                "final_risk_pct": 0.0152,
                "final_notional_usd": 24068.0,
                "leverage": 20.0,
                "proposal": {
                    "continuous_scalar": 0.76,
                    "tier_amplifier": 2.0,
                    "cell_routing_mult": 1.0,
                },
            }
        }
    )
    _insert_event(c, gid=5, decision="SIZED", payload=payload, ts=20, pos_id="p")
    ev = _recent_gate_events(c, n=5)
    assert len(ev) == 1
    det = ev[0].detail
    assert det["risk_pct"] == 1.52
    assert det["notional_usd"] == 24068.0
    assert det["scalar"] == 0.76
    assert det["tier"] == 2.0
    assert det["cell"] == 1.0


def test_graceful_when_link_tables_absent():
    c = _conn(with_links=False)
    _insert_event(c, gid=6, decision="ADJUST_EXIT", payload="{}", ts=400, pos_id="pos1")
    # No positions/signals tables -> _safe_query returns [] -> empty feed, no crash.
    assert _recent_gate_events(c, n=5) == []
