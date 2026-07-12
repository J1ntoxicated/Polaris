"""news_timing_shadow writes routed through the shared DBWriter
(db-writer-reader-split roadmap 1.10, 2026-07-12 audit item [B] —
``log_news_timing_shadow``'s same-table consecutive-INSERT batch, now
chunk-submitted as executemany).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path (still an
``executemany`` internally, but the returned rowcount is asserted against the
pre-migration per-row-execute behaviour too) — covered by
``test_news_timing_shadow.py``. This file covers the new opt-in path: an
end-to-end real-DBWriter run lands the same rows a direct write would, a
batch bigger than the chunk size splits across multiple jobs, and the kill
switch falls back to the direct-conn path.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from polaris.core.altdata.news_timing_shadow import log_news_timing_shadow
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db

UTC = _dt.UTC
NOW = _dt.datetime(2026, 7, 12, 13, 0, 0, tzinfo=UTC)


def _articles(n: int) -> list[dict[str, object]]:
    return [
        {
            "id": i, "headline": f"Headline {i}", "symbols": ["AAPL"],
            "created_at": "2026-07-12T12:00:00Z",
        }
        for i in range(n)
    ]


def _scored(n: int) -> dict[str, dict[str, float]]:
    return {str(i): {"sentiment": 0.1, "relevance": 0.5} for i in range(n)}


def test_db_writer_end_to_end_lands_same_rows_as_direct_write(tmp_path: Path) -> None:
    articles, scored = _articles(3), _scored(3)

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    n_golden = log_news_timing_shadow(golden_conn, articles=articles, scored=scored, now=NOW)
    golden_rows = golden_conn.execute(
        "SELECT symbol, headline_id, sentiment FROM news_timing_shadow ORDER BY headline_id"
    ).fetchall()
    golden_conn.close()
    assert n_golden == 3
    assert len(golden_rows) == 3

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        n = log_news_timing_shadow(
            conn, articles=articles, scored=scored, now=NOW, db_writer=dbw,
        )
    finally:
        dbw.stop()
    rows = conn.execute(
        "SELECT symbol, headline_id, sentiment FROM news_timing_shadow ORDER BY headline_id"
    ).fetchall()
    conn.close()
    assert n == 3  # attempted count (db_writer mode) matches the actual row count here
    assert rows == golden_rows


def test_db_writer_chunks_large_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_NEWS_TIMING_DB_CHUNK_ROWS", "10")
    articles, scored = _articles(25), _scored(25)

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=64, drain_ms=10)
    dbw.start()
    conn = connect(db)
    submitted_labels: list[str] = []
    orig_submit = dbw.submit

    def _spy_submit(fn: object, *, durable: bool = False, label: str = "") -> object:
        submitted_labels.append(label)
        return orig_submit(fn, durable=durable, label=label)  # type: ignore[arg-type]

    monkeypatch.setattr(dbw, "submit", _spy_submit)
    try:
        n = log_news_timing_shadow(
            conn, articles=articles, scored=scored, now=NOW, db_writer=dbw,
        )
    finally:
        dbw.stop()
    written = conn.execute("SELECT COUNT(*) FROM news_timing_shadow").fetchone()[0]
    conn.close()
    assert n == 25
    assert written == 25
    news_jobs = [lbl for lbl in submitted_labels if lbl == "news_timing_shadow"]
    assert len(news_jobs) == 3  # 25 rows / chunk_rows=10 -> 3 chunks


def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    articles, scored = _articles(2), _scored(2)

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        n = log_news_timing_shadow(
            conn, articles=articles, scored=scored, now=NOW, db_writer=dbw,
        )
        written = conn.execute("SELECT COUNT(*) FROM news_timing_shadow").fetchone()[0]
    finally:
        dbw.stop()
    conn.close()
    assert n == 2
    assert written == 2
