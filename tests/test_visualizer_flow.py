"""Flow-page backend data builder tests (DEMO/PAPER, display-only, READ-ONLY).

Covers ``tools.visualizer.flow_data.build_flow_stats`` — the /api/flow_stats
payload consumed by the "Living Pipeline River" page. Nothing here touches
sizing/risk/orders; every table read is read-only synthetic in-memory SQLite.
"""

from __future__ import annotations

import json
import sqlite3

from tools.visualizer import flow_data as fd


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE gate_events (
            event_id TEXT PRIMARY KEY, gate_id INTEGER, decision TEXT,
            payload_json TEXT, position_id TEXT,
            signal_id TEXT, created_ts INTEGER
        );
        CREATE TABLE positions (position_id TEXT PRIMARY KEY, venue TEXT);
        CREATE TABLE signals (signal_id TEXT, instrument_id TEXT);
        CREATE TABLE fills (
            fill_id TEXT PRIMARY KEY, venue TEXT, ts_ms INTEGER, is_close INTEGER
        );
        CREATE TABLE gate_shadow_events (
            event_id TEXT PRIMARY KEY, gate_id INTEGER, mismatch INTEGER,
            created_ts INTEGER
        );
        """
    )
    return conn


NOW = 1_000_000


def test_empty_db_yields_zeroed_shape() -> None:
    conn = _memdb()
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert len(out["stages"]) == 8
    assert all(s["total"] == 0 for s in out["stages"])
    assert out["drops"] == []
    assert out["fills"]["total"] == 0
    assert out["summary"]["signal_to_sized_pct"] == 0.0


def test_stage_totals_and_venue_breakdown() -> None:
    conn = _memdb()
    conn.execute(
        "INSERT INTO signals VALUES ('sig1', 'okx:BTC-USDT')"
    )
    conn.execute(
        "INSERT INTO signals VALUES ('sig2', 'capital:EURUSD')"
    )
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e1', 2, 'PASS', NULL, NULL, 'sig1', ?)",
        (NOW - 10,),
    )
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e2', 2, 'PASS', NULL, NULL, 'sig2', ?)",
        (NOW - 20,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    g2 = next(s for s in out["stages"] if s["gate_id"] == 2)
    assert g2["total"] == 2
    assert g2["venues"]["okx"] == 1
    assert g2["venues"]["capital"] == 1
    assert out["summary"]["signals_n"] == 2


def test_drop_decisions_bucketed_by_reason() -> None:
    conn = _memdb()
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e1', 3, 'KILL', '{\"reason\":\"stale_flag\"}', "
        "NULL, NULL, ?)",
        (NOW - 5,),
    )
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e2', 3, 'KILL', '{\"reason\":\"stale_flag\"}', "
        "NULL, NULL, ?)",
        (NOW - 5,),
    )
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e3', 4, 'SKIPPED', NULL, NULL, NULL, ?)",
        (NOW - 5,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    top = out["drops"][0]
    assert top["reason"] == "stale_flag"
    assert top["n"] == 2
    assert out["summary"]["drops_total"] == 3


def test_ai_calls_and_verdict_extraction() -> None:
    # model_used stays "python" even when GPT was consulted (the judge only
    # ANNOTATES the deterministic result — ai_judge.apply_entry_verdict copies
    # model_used unchanged); escalation is the only honest "was GPT called" signal.
    conn = _memdb()
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e1', 3, 'PASS', "
        "'{\"ai_judge\":{\"verdict\":\"SIZE_UP\",\"escalation\":\"gpt_ok\"}}', "
        "NULL, NULL, ?)",
        (NOW - 5,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["summary"]["ai_calls_n"] == 1
    assert out["verdicts_recent"][0]["verdict"] == "SIZE_UP"
    assert out["verdicts_recent"][0]["color"] == "green"


def test_no_client_escalation_is_not_counted_as_an_ai_call() -> None:
    # no_client = the judge ran with no GPT client configured → immediate
    # fallback, no network call attempted. Must NOT inflate ai_calls_n even
    # though the verdict still shows up in the ticker.
    conn = _memdb()
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e1', 4, 'PASS', "
        "'{\"ai_judge\":{\"verdict\":\"PROCEED\",\"escalation\":\"no_client\"}}', "
        "NULL, NULL, ?)",
        (NOW - 5,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["summary"]["ai_calls_n"] == 0
    assert out["verdicts_recent"][0]["verdict"] == "PROCEED"


def test_window_excludes_stale_rows() -> None:
    conn = _memdb()
    conn.execute(
        "INSERT INTO gate_events VALUES "
        "('e1', 1, 'PASS', NULL, NULL, NULL, ?)",
        (NOW - fd.FLOW_WINDOW_SEC - 100,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    g1 = next(s for s in out["stages"] if s["gate_id"] == 1)
    assert g1["total"] == 0


def test_fills_and_shadow_mismatch_counts() -> None:
    conn = _memdb()
    conn.execute("INSERT INTO fills VALUES ('f1', 'okx', ?, 0)", (NOW * 1000,))
    conn.execute("INSERT INTO fills VALUES ('f2', 'capital', ?, 1)", (NOW * 1000,))
    conn.execute(
        "INSERT INTO gate_shadow_events VALUES ('s1', 3, 1, ?)", (NOW - 5,)
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["fills"]["total"] == 1  # is_close=1 excluded (entries only)
    assert out["fills"]["venues"]["okx"] == 1
    assert out["shadow_mismatch_n"] == 1


def test_shadow_mismatches_feed_the_same_drop_lane_as_kill_skip() -> None:
    # The page's legend reads "DROP (kill/skip/shadow)" — a shadow mismatch
    # must show up in `drops`/`drops_total`, not just the standalone counter.
    conn = _memdb()
    conn.execute(
        "INSERT INTO gate_shadow_events VALUES ('s1', 3, 1, ?)", (NOW - 5,)
    )
    conn.execute(
        "INSERT INTO gate_shadow_events VALUES ('s2', 3, 0, ?)", (NOW - 5,)
    )  # mismatch=0 must NOT count
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["shadow_mismatch_n"] == 1
    assert out["drops"] == [
        {"gate_id": 3, "label": "Validator", "reason": "shadow_mismatch", "n": 1}
    ]
    assert out["summary"]["drops_total"] == 1


def test_venue_of_alpaca_prefix_and_unknown_default() -> None:
    assert fd._venue_of(None, "alpaca:AAPL") == "alpaca"
    assert fd._venue_of("alp", None) == "alpaca"
    assert fd._venue_of(None, None) == "okx"
    assert fd._venue_of("", "") == "okx"


def test_graceful_on_missing_tables() -> None:
    conn = sqlite3.connect(":memory:")
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert len(out["stages"]) == 8
    assert out["fills"]["total"] == 0
    # pts-classes / survivor_admissions tables absent -> empty, never crash
    # (visual-wall director-mode backend extension, 2026-07-10).
    assert out["classes"] == []
    assert out["survivor_admissions_recent"] == []


def _memdb_with_classes() -> sqlite3.Connection:
    conn = _memdb()
    conn.executescript(
        """
        CREATE TABLE strategy_class (
            venue TEXT, strategy_id TEXT, strategy_class TEXT,
            window_w INTEGER, intent_ring TEXT
        );
        """
    )
    return conn


def test_classes_reports_class_and_window_fill() -> None:
    conn = _memdb_with_classes()
    conn.execute(
        "INSERT INTO strategy_class VALUES "
        "('okx', 'donchian55', 'PROVE', 20, '[1.0, -0.5, 2.0]')"
    )
    conn.execute(
        "INSERT INTO strategy_class VALUES "
        "('capital', 'connors_rsi2', 'EARN', 20, ?)",
        (json.dumps([0.1] * 25),),  # ring longer than window_w -> capped
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    by_id = {c["strategy_id"]: c for c in out["classes"]}
    assert by_id["donchian55"] == {
        "venue": "okx", "strategy_id": "donchian55",
        "strategy_class": "PROVE", "window_w": 20, "filled": 3,
    }
    assert by_id["connors_rsi2"]["filled"] == 20  # capped at window_w


def test_classes_empty_when_table_absent() -> None:
    conn = _memdb()  # no strategy_class table
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["classes"] == []


def test_survivor_admissions_recent_empty_when_table_absent() -> None:
    conn = _memdb()  # no survivor_admissions table
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["survivor_admissions_recent"] == []


def test_survivor_admissions_recent_reads_when_table_present() -> None:
    conn = _memdb()
    conn.executescript(
        "CREATE TABLE survivor_admissions "
        "(symbol TEXT, venue TEXT, admitted_ts INTEGER);"
    )
    conn.execute(
        "INSERT INTO survivor_admissions VALUES ('SOL-USDT', 'okx', ?)",
        (NOW - 5,),
    )
    conn.execute(  # stale (outside the 1h window) -> excluded
        "INSERT INTO survivor_admissions VALUES ('OLD-USDT', 'okx', ?)",
        (NOW - fd.FLOW_WINDOW_SEC - 100,),
    )
    out = fd.build_flow_stats(conn, now_s=NOW)
    assert out["survivor_admissions_recent"] == [
        {"symbol": "SOL-USDT", "venue": "okx", "ts": NOW - 5}
    ]
