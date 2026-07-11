"""shadow_distribution_guard: pure report functions (fixture rows, no DB I/O
for the distribution math) + a thin real-schema read-wrapper check."""

from __future__ import annotations

import math
import sqlite3

from polaris.storage.schema import ALL_DDL
from tools.ops import shadow_distribution_guard as guard


def test_distribution_summary_all_identical_flags_dominant_share_one() -> None:
    """design-monitoring.md W1 §4 worked example: calibration predicted_p
    collapsed to a single constant — dominant_share must hit 1.0."""
    d = guard.distribution_summary([0.5, 0.5, 0.5, 0.5])
    assert d.n_valid == 4
    assert d.dominant_value == 0.5
    assert d.dominant_share == 1.0
    assert d.n_distinct == 1
    assert d.mean == 0.5
    assert d.stddev == 0.0


def test_distribution_summary_nan_and_none_counted_as_missing() -> None:
    d = guard.distribution_summary([1.0, None, float("nan"), 3.0])
    assert d.n == 4
    assert d.n_valid == 2
    assert d.n_missing == 2
    assert d.mean == 2.0


def test_distribution_summary_empty_degrades_without_raising() -> None:
    d = guard.distribution_summary([])
    assert d == guard.DistributionSummary(0, 0, 0, None, None, 0, None, 0.0)


def test_distribution_summary_all_missing_degrades_without_raising() -> None:
    d = guard.distribution_summary([None, None])
    assert d.n_valid == 0
    assert d.mean is None
    assert d.dominant_share == 0.0


def test_symbol_concentration_top_share() -> None:
    top, share = guard.symbol_concentration(["BTC", "BTC", "BTC", "ETH"])
    assert top == "BTC"
    assert share == 0.75


def test_symbol_concentration_empty() -> None:
    assert guard.symbol_concentration([]) == ("", 0.0)


def test_dedup_ratio_no_duplicates_is_zero() -> None:
    assert guard.dedup_ratio(["a", "b", "c"]) == 0.0


def test_dedup_ratio_heavy_duplication() -> None:
    # 4 rows, 1 distinct key -> 1 - 1/4 = 0.75
    assert guard.dedup_ratio(["g1", "g1", "g1", "g1"]) == 0.75


def test_dedup_ratio_empty_is_zero() -> None:
    assert guard.dedup_ratio([]) == 0.0


def test_query_fingerprint_deterministic_and_whitespace_normalized() -> None:
    a = guard.query_fingerprint("SELECT x FROM t\n  WHERE y = 1")
    b = guard.query_fingerprint("SELECT x FROM t WHERE y = 1")
    assert a == b
    assert len(a) == 12


def test_query_fingerprint_changes_with_query_text() -> None:
    a = guard.query_fingerprint("SELECT x FROM t")
    b = guard.query_fingerprint("SELECT y FROM t")
    assert a != b


def test_channel_distribution_report_combines_all_three_signals() -> None:
    rows = [
        {"value": 0.5, "symbol": "BTC-USDT", "dedup_key": "g1"},
        {"value": 0.5, "symbol": "BTC-USDT", "dedup_key": "g1"},
        {"value": 0.6, "symbol": "ETH-USDT", "dedup_key": "g2"},
    ]
    report = guard.channel_distribution_report(
        "calibration_pairs", rows, query="SELECT value FROM calibration_pairs",
    )
    assert report.channel == "calibration_pairs"
    assert report.fingerprint == guard.query_fingerprint(
        "SELECT value FROM calibration_pairs",
    )
    assert report.distribution.n == 3
    assert report.top_symbol == "BTC-USDT"
    assert math.isclose(report.top_symbol_share, 2 / 3)
    assert math.isclose(report.dedup_ratio, 1 - 2 / 3)  # 2 distinct keys / 3 rows


def test_canonical_query_no_dedup_col_selects_null() -> None:
    cq = guard.CHANNEL_QUERIES["calibration_pairs"]
    q = guard.canonical_query(cq, limit=5)
    assert "NULL AS dedup_key" in q
    assert "LIMIT 5" in q


def test_canonical_query_with_dedup_col() -> None:
    cq = guard.CHANNEL_QUERIES["news_timing_shadow"]
    q = guard.canonical_query(cq, limit=5)
    assert "dedup_group_id AS dedup_key" in q


def test_read_channel_rows_matches_real_schema_and_fingerprints_own_query() -> None:
    """Real ALL_DDL schema — proves the 6 canonical SELECTs are column-valid
    against the actual table shape, not just against fixture dicts."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO calibration_pairs (signal_id, ticker, predicted_p_pos,"
        " created_ts) VALUES ('s1', 'BTC-USDT', 0.62, 100)",
    )
    conn.commit()
    rows, query = guard.read_channel_rows(conn, "calibration_pairs")
    assert rows == [{"value": 0.62, "symbol": "BTC-USDT", "dedup_key": None}]
    assert guard.query_fingerprint(query) == guard.query_fingerprint(
        guard.canonical_query(guard.CHANNEL_QUERIES["calibration_pairs"]),
    )
    conn.close()


def test_run_report_covers_all_six_channels_on_empty_db() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    lines = guard.run_report(conn)
    conn.close()
    assert len(lines) == len(guard.CHANNEL_QUERIES)
    for channel, line in zip(guard.CHANNEL_QUERIES, lines, strict=True):
        assert line.startswith(f"channel={channel} ")
        assert "n=0" in line
