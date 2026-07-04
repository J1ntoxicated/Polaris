"""Layer 0 — Alpaca cowork intel seed reader tests.

DEMO/PAPER only. This feed is ADDITIVE-ONLY (flow_not_block): it never blocks
or removes a universe candidate, only adds/uplifts. These tests pin the
fail-safe reader contract in ``cowork/watchlist_intel/CONTRACT.md``: schema
validation (bad rows skip, never a whole-file failure), expiry_ts fail-safe
(past expiry == absent file), missing-file quiet no-op, and ``_sample: true``
fixtures ignored. mtime-keyed cache — no hot-path re-read of an unchanged file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from polaris.core.universe.intel_seed import load_intel_seed

NOW = 1_783_000_000


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _candidate(
    symbol: str,
    *,
    thesis_tag: str = "pead_high",
    score: float = 0.8,
    evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "venue": "alpaca",
        "thesis_tag": thesis_tag,
        "score": score,
        "catalyst_ts": None,
        "evidence": evidence if evidence is not None else ["https://example.com/x"],
    }


def test_missing_file_is_quiet_noop(tmp_path: Path) -> None:
    """No feed file at all → empty result, no exception."""
    result = load_intel_seed(tmp_path / "nope" / "alpaca_seed.json", now_ts=NOW)
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_valid_feed_loads_seed_tags(tmp_path: Path) -> None:
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [
                _candidate("AAPL", thesis_tag="pead_high"),
                _candidate("NVDA", thesis_tag="sector_rs"),
            ],
        },
    )
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {"AAPL": "pead_high", "NVDA": "sector_rs"}
    assert result.symbols == frozenset({"AAPL", "NVDA"})


def test_expired_feed_is_ignored_fail_safe(tmp_path: Path) -> None:
    """expiry_ts in the past → treated as absent (never an error, never stuck)."""
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-01T00:00:00Z",
            "expiry_ts": "2026-07-01T01:00:00Z",  # long before NOW
            "candidates": [_candidate("AAPL")],
        },
    )
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_sample_fixture_is_ignored(tmp_path: Path) -> None:
    """_sample: true → never seeds (the illustrative example file)."""
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "_sample": True,
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [_candidate("SAMPLE_AAPL")],
        },
    )
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_malformed_json_is_noop_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "alpaca_seed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_bad_candidate_rows_are_skipped_not_fatal(tmp_path: Path) -> None:
    """One malformed candidate must not sink the whole file's good rows."""
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [
                _candidate("AAPL", thesis_tag="pead_high"),
                # Missing symbol.
                {"venue": "alpaca", "thesis_tag": "pead", "score": 0.5,
                 "evidence": ["https://x"]},
                # Unknown thesis_tag (not in the allowed list).
                _candidate("BADTAG", thesis_tag="not_a_real_tag"),
                # No evidence URL — CONTRACT requires >= 1.
                _candidate("NOEVID", evidence=[]),
                # score out of [0,1] range.
                _candidate("BADSCORE", score=1.5),
                # Good row after the bad ones.
                _candidate("MSFT", thesis_tag="analyst_revision"),
            ],
        },
    )
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {"AAPL": "pead_high", "MSFT": "analyst_revision"}


def test_missing_expiry_ts_is_noop_fail_safe(tmp_path: Path) -> None:
    """No expiry_ts at all → cannot prove freshness → fail-safe no-op."""
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "candidates": [_candidate("AAPL")],
        },
    )
    result = load_intel_seed(path, now_ts=NOW)
    assert result.seed_tags == {}


def test_cache_reused_when_mtime_unchanged(tmp_path: Path) -> None:
    """A second load with the same mtime must not re-read the file (hot-path cache)."""
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [_candidate("AAPL")],
        },
    )
    first = load_intel_seed(path, now_ts=NOW)
    # Mutate the file on disk WITHOUT changing its stat mtime (freeze the clock
    # by directly reusing os.utime to pin it) — the cache should still serve
    # the stale-in-memory (first) result rather than re-reading.
    stat = path.stat()
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [_candidate("NVDA")],
        },
    )
    import os

    os.utime(path, (stat.st_atime, stat.st_mtime))
    second = load_intel_seed(path, now_ts=NOW)
    assert second.seed_tags == first.seed_tags == {"AAPL": "pead_high"}


def test_cache_invalidated_when_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "alpaca_seed.json"
    _write(
        path,
        {
            "generated_at": "2026-07-04T11:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [_candidate("AAPL")],
        },
    )
    first = load_intel_seed(path, now_ts=NOW)
    assert first.seed_tags == {"AAPL": "pead_high"}
    time.sleep(0.02)
    _write(
        path,
        {
            "generated_at": "2026-07-04T12:30:00Z",
            "expiry_ts": "2099-01-01T00:00:00Z",
            "candidates": [_candidate("NVDA", thesis_tag="sector_rs")],
        },
    )
    second = load_intel_seed(path, now_ts=NOW)
    assert second.seed_tags == {"NVDA": "sector_rs"}
