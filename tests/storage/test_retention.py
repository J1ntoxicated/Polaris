"""Retention/pruning + WAL hygiene — destructive-op safety tests.

Validates against the REAL schema (``schema.init_db``), not a hand-rolled
fixture, so the prune targets and the protected ledger tables are exactly the
production columns. Covers the adversarial-review properties:

* ledger/state tables are never touched (even ``signals``, which HAS a ``ts``
  column that a naive prune would catch — the allowlist guard blocks it);
* only rows strictly older than the window are deleted, in-window kept;
* exact boundary (== cutoff is kept, < cutoff deleted);
* idempotent (second run deletes 0);
* the reclaiming WAL checkpoint shrinks the -wal file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.core.probes.tuning_log import open_probe_db
from polaris.storage.retention import (
    PROBE_RETENTION_SPEC,
    RETENTION_SPEC,
    RETENTION_SPEC_TRADING,
    checkpoint_wal,
    prune_table,
    run_probe_retention,
    run_retention,
    run_retention_job,
    run_retention_live,
)
from polaris.storage.schema import connect, init_db

NOW = 1_782_000_000  # fixed reference epoch-seconds


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "t.sqlite")
    yield conn
    conn.close()


def _insert_bar(conn: sqlite3.Connection, ts: int, *, interval: str = "1D") -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES ('I','G','okx','BTC-USDT',?,?,1,1,1,1,1)",
        (interval, ts),
    )


def _insert_baseline_sample(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO ticker_baseline_samples (instrument_id, underlying_group_id, "
        "metric, ts, value) VALUES ('I','G','atr',?,1.0)",
        (ts,),
    )


def _insert_focus(conn: sqlite3.Connection, cycle_ts: int) -> None:
    conn.execute(
        "INSERT INTO watchlist_focus (cycle_ts, venue, symbol, focus_score, "
        "focus_rank, target_bucket) VALUES (?,'okx','BTC-USDT',1.0,1,'core')",
        (cycle_ts,),
    )


def _insert_gate_event(conn: sqlite3.Connection, created_ts: int) -> None:
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, gate_id, phase, created_ts) "
        "VALUES (?, 'run', 1, 'eval', ?)",
        (f"ev-{created_ts}", created_ts),
    )


def _insert_news_timing_shadow(conn: sqlite3.Connection, ingestion_ts: int) -> None:
    conn.execute(
        "INSERT INTO news_timing_shadow "
        "(event_id, symbol, headline_id, ingestion_ts, created_ts) "
        "VALUES (?, 'BTC-USDT', ?, ?, ?)",
        (f"nts-{ingestion_ts}", f"h-{ingestion_ts}", ingestion_ts, ingestion_ts),
    )


def _insert_vwap_timing_shadow(conn: sqlite3.Connection, decision_ts: int) -> None:
    conn.execute(
        "INSERT INTO vwap_timing_shadow (event_id, run_id, decision_ts, created_ts) "
        "VALUES (?, 'r-test', ?, ?)",
        (f"vts-{decision_ts}", decision_ts, decision_ts),
    )


def _insert_calibration_pair(conn: sqlite3.Connection, created_ts: int) -> None:
    conn.execute(
        "INSERT INTO calibration_pairs (signal_id, created_ts) VALUES (?, ?)",
        (f"cp-{created_ts}", created_ts),
    )


def _insert_edgar_filing(conn: sqlite3.Connection, ingestion_ts: int) -> None:
    conn.execute(
        "INSERT INTO edgar_filings (symbol, cik, accession_number, form_type, "
        "ingestion_ts, created_ts) VALUES ('AAPL', '320193', ?, '8-K', ?, ?)",
        (f"acc-{ingestion_ts}", ingestion_ts, ingestion_ts),
    )


def _insert_filing_proximity_shadow(conn: sqlite3.Connection, cycle_ts: int) -> None:
    conn.execute(
        "INSERT INTO filing_proximity_shadow (symbol, cycle_ts, created_ts) "
        "VALUES ('AAPL', ?, ?)",
        (cycle_ts, cycle_ts),
    )


def _insert_stablecoin_liquidity(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO stablecoin_liquidity (ts, symbol, created_ts) "
        "VALUES (?, 'USDT', ?)",
        (ts, ts),
    )


def _insert_earnings_calendar(conn: sqlite3.Connection, ingestion_ts: int) -> None:
    # earnings_date is PK-paired with symbol; using str(ingestion_ts) keeps
    # each seeded row's key unique regardless of the window's cutoff arithmetic
    # (retention prunes on ingestion_ts, not this column's content).
    conn.execute(
        "INSERT INTO earnings_calendar (symbol, earnings_date, ingestion_ts, "
        "created_ts) VALUES ('AAPL', ?, ?, ?)",
        (str(ingestion_ts), ingestion_ts, ingestion_ts),
    )


def _insert_earnings_proximity_shadow(conn: sqlite3.Connection, cycle_ts: int) -> None:
    conn.execute(
        "INSERT INTO earnings_proximity_shadow (symbol, cycle_ts, created_ts) "
        "VALUES ('AAPL', ?, ?)",
        (cycle_ts, cycle_ts),
    )


# --- ledger / state tables: NEVER touched ----------------------------------


def test_ledger_tables_untouched(db: sqlite3.Connection) -> None:
    """positions/fills/signals/measurement_resets/universe survive retention.

    ``signals`` deliberately gets an ancient ``ts`` — far outside any window —
    to prove the allowlist (not a per-table heuristic) is what protects it.
    """
    db.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('p1','okx','BTC-USDT','s','s','s','long',1,'closed',1)"
    )
    db.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id) "
        "VALUES ('f1','okx','I','s','buy',1,1,1000,'o1')"
    )
    db.execute(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
        "score, thesis, ts) VALUES ('s','sig1','I','long',1,'t',1)"  # ancient ts
    )
    db.execute(
        "INSERT INTO measurement_resets (reset_ts, label) VALUES (1, 'r')"
    )
    db.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts) "
        "VALUES ('okx','BTC-USDT','I','G','crypto','USDT','live',1)"
    )
    db.commit()

    run_retention(db, now_ts=NOW)

    for table in ("positions", "fills", "signals", "measurement_resets", "universe"):
        n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n == 1, f"{table} must be preserved"


def test_prune_table_rejects_non_allowlisted() -> None:
    """A ledger table can never reach a DELETE through prune_table."""
    conn = sqlite3.connect(":memory:")
    for bad in ("signals", "positions", "fills", "orders", "universe"):
        with pytest.raises(ValueError, match="non-allowlisted"):
            prune_table(conn, bad, "ts", 86_400, now_ts=NOW)


def test_prune_table_rejects_wrong_column() -> None:
    """Right table, wrong ts column is also refused (no column injection)."""
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="non-allowlisted"):
        prune_table(conn, "bars", "opened_ts", 86_400, now_ts=NOW)


# --- window correctness -----------------------------------------------------


def test_quote_ticks_not_in_retention_spec() -> None:
    """Tick-stream decouple: quote_ticks is single-row LWW (bounded), so the
    prune-DELETE rule is removed — deleting the lone latest row on age would
    drop a still-valid price for a quiet instrument."""
    assert all(rule.table != "quote_ticks" for rule in RETENTION_SPEC)


def test_window_keeps_inside_deletes_outside(db: sqlite3.Connection) -> None:
    # The non-bars rules are 1:1 with a table; bars is now PER-INTERVAL (NIT-A),
    # so each bars rule must seed with ITS interval (a 1m row never matches the
    # 4H rule). The catch-all bars rule (bar_interval=None) is the whole-table
    # safety net — exercised separately by test_bars_unenumerated_interval below.
    non_bars = [r for r in RETENTION_SPEC if r.table != "bars"]
    bars_rules = [r for r in RETENTION_SPEC if r.table == "bars" and r.bar_interval]
    for rule in non_bars:
        cutoff = NOW - rule.retain_sec
        inserter = {
            "ticker_baseline_samples": _insert_baseline_sample,
            "watchlist_focus": _insert_focus,
            "gate_events": _insert_gate_event,
            "news_timing_shadow": _insert_news_timing_shadow,
            "vwap_timing_shadow": _insert_vwap_timing_shadow,
            "calibration_pairs": _insert_calibration_pair,
            "edgar_filings": _insert_edgar_filing,
            "filing_proximity_shadow": _insert_filing_proximity_shadow,
            "stablecoin_liquidity": _insert_stablecoin_liquidity,
            "earnings_calendar": _insert_earnings_calendar,
            "earnings_proximity_shadow": _insert_earnings_proximity_shadow,
        }[rule.table]
        inserter(db, cutoff - 1)   # strictly older -> deleted
        inserter(db, cutoff)       # exactly at cutoff -> kept (< is the test)
        inserter(db, cutoff + 1)   # newer -> kept
        inserter(db, NOW)          # now -> kept
    for rule in bars_rules:
        cutoff = NOW - rule.retain_sec
        assert rule.bar_interval is not None  # narrowed for mypy
        _insert_bar(db, cutoff - 1, interval=rule.bar_interval)  # older -> deleted
        _insert_bar(db, cutoff, interval=rule.bar_interval)      # at cutoff -> kept
        _insert_bar(db, cutoff + 1, interval=rule.bar_interval)  # newer -> kept
        _insert_bar(db, NOW, interval=rule.bar_interval)         # now -> kept
    db.commit()

    deleted = run_retention(db, now_ts=NOW)

    for rule in non_bars:
        cutoff = NOW - rule.retain_sec
        assert deleted[rule.table] == 1, f"{rule.table}: exactly one old row"
        rows = [
            r[0]
            for r in db.execute(
                f"SELECT {rule.ts_column} FROM {rule.table}"  # noqa: S608 test-local
            ).fetchall()
        ]
        assert cutoff - 1 not in rows, f"{rule.table}: old row gone"
        assert sorted(rows) == [cutoff, cutoff + 1, NOW], f"{rule.table}: in-window kept"

    # bars: each interval dropped exactly its one out-of-window row → the
    # accumulated count is one per enumerated interval.
    assert deleted["bars"] == len(bars_rules), "bars: one old row per interval"
    for rule in bars_rules:
        cutoff = NOW - rule.retain_sec
        rows = sorted(
            r[0] for r in db.execute(
                "SELECT ts FROM bars WHERE bar_interval = ?", (rule.bar_interval,)
            ).fetchall()
        )
        assert rows == [cutoff, cutoff + 1, NOW], f"bars/{rule.bar_interval}: kept window"


def test_bars_unenumerated_interval_uses_catchall(db: sqlite3.Connection) -> None:
    """NIT-A safety net: a bar in an interval NOT enumerated per-interval is held
    by the whole-table catch-all (1200d), never silently over-pruned by a short
    intraday window. A 100d-old '30m' bar (no per-interval rule) survives; a
    1300d-old one (outside the catch-all) is pruned."""
    _insert_bar(db, NOW - 100 * 86_400, interval="30m")   # inside 1200d catch-all
    _insert_bar(db, NOW - 1300 * 86_400, interval="30m")  # outside catch-all
    db.commit()
    run_retention(db, now_ts=NOW)
    kept = sorted(
        r[0] for r in db.execute(
            "SELECT ts FROM bars WHERE bar_interval = '30m'"
        ).fetchall()
    )
    assert kept == [NOW - 100 * 86_400], "catch-all keeps 100d, prunes 1300d"


def test_bars_intraday_short_window_prunes_dense_stream(db: sqlite3.Connection) -> None:
    """NIT-A core: a 1m bar past the 30d intraday window is pruned even though a
    1D bar at the SAME age is kept (the dense intraday stream is the disk driver;
    the deep 1D canvas is preserved). Directly guards Jin's disk-growth concern."""
    age = NOW - 40 * 86_400  # 40d: past 1m's 30d window, inside 1D's 1200d window
    _insert_bar(db, age, interval="1m")
    _insert_bar(db, age, interval="1D")
    db.commit()
    run_retention(db, now_ts=NOW)
    assert db.execute(
        "SELECT COUNT(*) FROM bars WHERE bar_interval = '1m'"
    ).fetchone()[0] == 0, "1m intraday pruned at 40d (>30d)"
    assert db.execute(
        "SELECT COUNT(*) FROM bars WHERE bar_interval = '1D'"
    ).fetchone()[0] == 1, "1D canvas kept at 40d (≪1200d)"


def test_idempotent_second_run_deletes_zero(db: sqlite3.Connection) -> None:
    _insert_bar(db, NOW - 1300 * 86_400)        # outside the 1200d bars window
    _insert_gate_event(db, NOW - 40 * 86_400)   # outside 30d
    db.commit()

    first = run_retention(db, now_ts=NOW)
    assert first["bars"] == 1
    assert first["gate_events"] == 1

    second = run_retention(db, now_ts=NOW)
    assert all(v == 0 for v in second.values()), second


def test_signal_lookback_bars_preserved(db: sqlite3.Connection) -> None:
    """A 330d-old 1D bar (deepest live lookback) MUST survive."""
    _insert_bar(db, NOW - 330 * 86_400, interval="1D")
    db.commit()
    run_retention(db, now_ts=NOW)
    assert db.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1


# --- WAL autocheckpoint (bounds the -wal between explicit checkpoints) ------


def test_connect_sets_wal_autocheckpoint(tmp_path: Path) -> None:
    """connect() arms an EXPLICIT PRAGMA wal_autocheckpoint so the -wal can never
    grow unbounded between the loop's PASSIVE checkpoints (live: 1.4 GB -wal).
    Asserting the exact value guards against a silent regression to 0 (disabled)
    or to the implicit default."""
    from polaris.storage.schema import WAL_AUTOCHECKPOINT_PAGES

    conn = connect(tmp_path / "auto.sqlite")
    try:
        pages = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert pages == WAL_AUTOCHECKPOINT_PAGES
        assert pages > 0  # 0 would DISABLE autocheckpoint → unbounded -wal
    finally:
        conn.close()


# --- WAL checkpoint ---------------------------------------------------------


def test_checkpoint_wal_shrinks_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "wal.sqlite"
    conn = init_db(db_path)
    for i in range(5000):
        _insert_bar(conn, NOW + i, interval="1m")
    conn.commit()
    wal_path = Path(str(db_path) + "-wal")
    grew = wal_path.exists() and wal_path.stat().st_size > 0
    conn.close()  # close so the reclaiming checkpoint can take the exclusive lock

    busy, _log, checkpointed = checkpoint_wal(db_path)

    assert busy == 0
    assert checkpointed >= 0
    if grew:
        after = wal_path.stat().st_size if wal_path.exists() else 0
        assert after == 0, "reclaiming checkpoint should truncate the -wal file"


def test_run_retention_job_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "job.sqlite"
    conn = init_db(db_path)
    _insert_bar(conn, NOW - 1300 * 86_400)  # outside the 1200d bars window
    _insert_bar(conn, NOW)
    conn.commit()
    conn.close()

    result = run_retention_job(db_path, now_ts=NOW)

    assert result["bars"] == 1
    assert "quote_ticks" not in result
    assert "__wal_checkpointed__" in result

    check = sqlite3.connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
    finally:
        check.close()


def test_run_retention_job_scoped_spec_trading_only(tmp_path: Path) -> None:
    """storage-split: ``spec=RETENTION_SPEC_TRADING`` scopes the job to
    gate_events only — a bars row outside its own window must survive this
    pass (bars is NOT in ``RETENTION_SPEC_TRADING``)."""
    db_path = tmp_path / "trading_job.sqlite"
    conn = init_db(db_path)
    _insert_bar(conn, NOW - 1300 * 86_400)  # outside the 1200d bars window
    conn.commit()
    conn.close()

    result = run_retention_job(db_path, now_ts=NOW, spec=RETENTION_SPEC_TRADING)
    assert "bars" not in result  # out of scope for the trading-only spec

    check = sqlite3.connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
    finally:
        check.close()


# --- LIVE retention (called periodically INSIDE the running loop) ----------


def test_run_retention_live_prunes_and_keeps_ledger(db: sqlite3.Connection) -> None:
    """The live path prunes old stream rows but NEVER the trading ledger.

    Same allowlist guarantee as the down-window job — the only difference is the
    live path does NOT run the reclaiming WAL checkpoint (the loop's PASSIVE
    producer owns that), so it never contends the writer lock.
    """
    _insert_bar(db, NOW - 40 * 86_400, interval="1m")  # outside 30d 1m -> deleted
    _insert_bar(db, NOW, interval="1m")                # kept
    db.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('p1','okx','BTC-USDT','s','s','s','long',1,'closed',1)"
    )
    db.execute(
        "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
        "score, thesis, ts) VALUES ('s','sig1','I','long',1,'t',1)"  # ancient ts
    )
    db.commit()

    deleted = run_retention_live(db, now_ts=NOW)

    assert deleted["bars"] == 1
    assert db.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1


def test_run_retention_live_degrades_never_crashes() -> None:
    """A broken/closed connection returns {} rather than raising into the loop."""
    conn = sqlite3.connect(":memory:")
    conn.close()  # any op now raises ProgrammingError
    assert run_retention_live(conn, now_ts=NOW) == {}


# --- PROBE sidecar retention (data/probes.sqlite, 2.2 GB unbounded) ---------


def _probe_db(tmp_path: Path) -> sqlite3.Connection:
    return open_probe_db(tmp_path / "probes.sqlite")


def _insert_probe_reading(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO probe_readings (reading_id, ts, gate_id, position_id, "
        "probe_id, kind, lean, confidence) VALUES (?,?,6,'p','pr','k',0.0,0.0)",
        (f"r-{ts}", ts),
    )


def _insert_probe_decision(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
        "position_id, mode, composite_lean, action) "
        "VALUES (?,?,?,'run','p','observe',0.0,'hold')",
        (f"d-{ts}", f"e-{ts}", ts),
    )


def _insert_entrance_judgment(conn: sqlite3.Connection, ts: int) -> None:
    conn.execute(
        "INSERT INTO entrance_judgments (judgment_id, ts, run_id, cycle_ts, "
        "instrument_id, venue, symbol, opportunity_score, trade_eligible) "
        "VALUES (?,?,'run',?,'I','okx','BTC-USDT',1.0,1)",
        (f"j-{ts}", ts, ts),
    )


def test_probe_retention_spec_covers_the_three_growth_tables() -> None:
    assert {r.table for r in PROBE_RETENTION_SPEC} == {
        "probe_readings",
        "probe_decisions",
        "entrance_judgments",
    }
    assert all(r.ts_column == "ts" for r in PROBE_RETENTION_SPEC)


def test_probe_retention_prunes_old_keeps_recent(tmp_path: Path) -> None:
    conn = _probe_db(tmp_path)
    try:
        for inserter in (
            _insert_probe_reading,
            _insert_probe_decision,
            _insert_entrance_judgment,
        ):
            inserter(conn, NOW - 60 * 86_400)  # outside 45d -> deleted
            inserter(conn, NOW)                 # kept

        deleted = run_probe_retention(conn, now_ts=NOW)

        for table in ("probe_readings", "probe_decisions", "entrance_judgments"):
            assert deleted[table] == 1
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 test-local
            ).fetchone()[0] == 1
    finally:
        conn.close()


def test_entrance_judgments_retention_is_7d_not_45d(tmp_path: Path) -> None:
    """A3: entrance_judgments window is 7d — an 8d-old row is pruned even though
    it is well inside the OTHER probe tables' 45d window (proves the per-table
    window, not just a shared 45d default)."""
    conn = _probe_db(tmp_path)
    try:
        _insert_entrance_judgment(conn, NOW - 8 * 86_400)   # outside 7d -> deleted
        _insert_entrance_judgment(conn, NOW - 6 * 86_400)   # inside 7d -> kept
        _insert_probe_decision(conn, NOW - 8 * 86_400)      # inside 45d -> kept

        deleted = run_probe_retention(conn, now_ts=NOW)

        assert deleted["entrance_judgments"] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM entrance_judgments"
        ).fetchone()[0] == 1
        assert deleted["probe_decisions"] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM probe_decisions"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_probe_retention_idempotent(tmp_path: Path) -> None:
    conn = _probe_db(tmp_path)
    try:
        _insert_probe_decision(conn, NOW - 60 * 86_400)
        run_probe_retention(conn, now_ts=NOW)
        second = run_probe_retention(conn, now_ts=NOW)
        assert all(v == 0 for v in second.values()), second
    finally:
        conn.close()


def test_probe_retention_rejects_non_allowlisted(tmp_path: Path) -> None:
    """prune_table's allowlist is the live-DB spec; probe pruning has its OWN
    allowlist, so a live-DB ledger name can never be pruned through either path."""
    conn = _probe_db(tmp_path)
    try:
        for bad in ("positions", "fills", "signals", "v_probe_outcomes"):
            with pytest.raises(ValueError, match="non-allowlisted"):
                run_probe_retention(conn, now_ts=NOW, _only_table=bad)
    finally:
        conn.close()


def test_probe_retention_degrades_never_crashes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.close()
    assert run_probe_retention(conn, now_ts=NOW) == {}
