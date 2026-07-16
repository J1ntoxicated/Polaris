"""News sentiment IC offline probe (frontgate-scan item #7, stage 2).
DEMO/PAPER virtual capital only. Read-only digest — pure SELECT join of
``news_timing_shadow`` against 1H ``bars``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from polaris.scripts.news_ic_probe import compute_news_ic
from polaris.storage.schema import init_db

HOUR = 3600


def _seed_news(conn, *, symbol: str, ingestion_ts: int, sentiment: float) -> None:
    conn.execute(
        "INSERT INTO news_timing_shadow (event_id, symbol, headline_id, "
        "ingestion_ts, dedup_group_id, sentiment, relevance, n_syndicate, "
        "created_ts) VALUES (?, ?, ?, ?, '1', ?, 0.5, 1, ?)",
        (uuid.uuid4().hex, symbol, uuid.uuid4().hex, ingestion_ts, sentiment, ingestion_ts),
    )


def _seed_bar(conn, *, symbol: str, ts: int, close: float) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) VALUES "
        "(?, ?, 'alpaca', ?, '1H', ?, ?, ?, ?, ?, 10.0)",
        (f"alpaca:{symbol}", f"alpaca.{symbol}", symbol, ts, close, close, close, close),
    )


def test_below_min_n_is_not_present(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "thin.sqlite")
    try:
        _seed_news(conn, symbol="AAPL", ingestion_ts=0, sentiment=0.5)
        _seed_bar(conn, symbol="AAPL", ts=0, close=100.0)
        _seed_bar(conn, symbol="AAPL", ts=24 * HOUR, close=101.0)
        conn.commit()
        out = compute_news_ic(conn, min_n=300)
        assert out["present"] is False
    finally:
        conn.close()


def test_perfect_positive_correlation_ic_near_one(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "perfect.sqlite")
    try:
        for i in range(10):
            sym = f"SYM{i}"
            sentiment = -1.0 + i * (2.0 / 9.0)  # spans -1..+1
            _seed_news(conn, symbol=sym, ingestion_ts=0, sentiment=sentiment)
            _seed_bar(conn, symbol=sym, ts=0, close=100.0)
            # Forward return exactly proportional to sentiment → IC == 1.0.
            _seed_bar(conn, symbol=sym, ts=24 * HOUR, close=100.0 + sentiment)
        conn.commit()
        out = compute_news_ic(conn, min_n=10)
        assert out["present"] is True
        assert out["n"] == 10
        assert out["ic"] > 0.99
        assert out["auto_apply"] is False
    finally:
        conn.close()


def test_missing_forward_bar_excluded_from_sample(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "missing.sqlite")
    try:
        _seed_news(conn, symbol="AAPL", ingestion_ts=0, sentiment=0.5)
        _seed_bar(conn, symbol="AAPL", ts=0, close=100.0)  # no forward bar
        conn.commit()
        out = compute_news_ic(conn, min_n=1)
        assert out["present"] is False
        assert out["n"] == 0
    finally:
        conn.close()


def test_no_rows_not_present(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "empty.sqlite")
    try:
        assert compute_news_ic(conn)["present"] is False
    finally:
        conn.close()


def test_missing_table_graceful() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        assert compute_news_ic(conn)["present"] is False
    finally:
        conn.close()


def test_main_resolves_marketdata_sibling_not_trading_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """storage-split (round 4 fix): news_timing_shadow + bars are BOTH
    marketdata-domain — main() must read the marketdata sibling, not the
    (post-split, always-empty) trading db_path passed via --db."""
    from polaris.scripts.news_ic_probe import main
    from polaris.storage.schema_marketdata import (
        init_marketdata_db,
        marketdata_db_path_for,
    )

    trading_path = tmp_path / "polaris_live.sqlite"
    init_db(trading_path).close()  # exists, but has NO news_timing_shadow/bars
    md_path = marketdata_db_path_for(trading_path)
    md_conn = init_marketdata_db(md_path)
    for i in range(10):
        sym = f"SYM{i}"
        sentiment = -1.0 + i * (2.0 / 9.0)  # spans -1..+1
        _seed_news(md_conn, symbol=sym, ingestion_ts=0, sentiment=sentiment)
        _seed_bar(md_conn, symbol=sym, ts=0, close=100.0)
        _seed_bar(md_conn, symbol=sym, ts=24 * HOUR, close=100.0 + sentiment)
    md_conn.commit()
    md_conn.close()

    monkeypatch.setattr(
        "sys.argv",
        ["news_ic_probe", "--db", str(trading_path), "--min-n", "10"],
    )
    main()
    out = capsys.readouterr().out
    assert "'present': True" in out
    assert "'n': 10" in out
