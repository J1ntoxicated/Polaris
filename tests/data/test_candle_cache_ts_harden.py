"""Unit test — candle_cache timestamp hardening (Fwd PR5).

MSG-CANDLE-TS-HARDEN. Covers:
  1. save() drops rows without 'ts' and skips write entirely when nothing
     in the incoming list carries a timestamp (legacy no-ts file stays
     untouched on disk).
  2. save() writes only ts-carrying rows when the list is mixed.
  3. load() filters ts-less rows from legacy files (returns [] + warn).
  4. load() round-trips ts-carrying rows unchanged.

Run: ``pytest tests/data/test_candle_cache_ts_harden.py``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from invasion.data import candle_cache as cc


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp folder + reset warn-once caches."""
    monkeypatch.setattr(cc, "DATA_DIR", tmp_path / "candles")
    cc._legacy_no_ts_warned.clear()
    cc._save_drop_warned.clear()
    cc._cache_save_times.clear()
    # Neutralise DataStore side-effect — we only care about file IO here.
    monkeypatch.setattr(
        "invasion.data.store.DataStore",
        lambda *a, **k: _NullStore(),
        raising=False,
    )
    yield


class _NullStore:
    def insert_market_data(self, *a, **k):  # noqa: D401
        return None


def test_save_skips_when_no_ts(tmp_path):
    cc.save("UNITTEST_NOTS", [{"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}], "HOUR")
    # File must not exist — legacy no-ts payload rejected.
    f = cc._file("UNITTEST_NOTS", "HOUR")
    assert not f.exists(), "save() should skip write when no candle carries 'ts'"


def test_save_keeps_only_ts_rows():
    mixed = [
        {"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},          # no ts → drop
        {"ts": 1_700_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        {"ts": 1_700_003_600, "o": 2, "h": 3, "l": 1.5, "c": 2.5, "v": 20},
    ]
    cc.save("UNITTEST_MIX", mixed, "HOUR")
    f = cc._file("UNITTEST_MIX", "HOUR")
    assert f.exists()
    on_disk = json.loads(f.read_text())
    assert len(on_disk) == 2
    assert all("ts" in c for c in on_disk)
    assert [c["ts"] for c in on_disk] == [1_700_000_000, 1_700_003_600]


def test_load_filters_legacy_no_ts():
    f = cc._file("UNITTEST_LEGACY", "HOUR")
    f.parent.mkdir(parents=True, exist_ok=True)
    legacy = [{"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10} for _ in range(3)]
    f.write_text(json.dumps(legacy))
    # Disable staleness gate (mtime is now, but keep max_age generous).
    out = cc.load("UNITTEST_LEGACY", "HOUR", max_age=10_000_000)
    assert out == [], "load() must strip ts-less rows from legacy files"


def test_load_roundtrips_ts_rows():
    ts_rows = [
        {"ts": 1_700_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        {"ts": 1_700_003_600, "o": 2, "h": 3, "l": 1.5, "c": 2.5, "v": 20},
    ]
    cc.save("UNITTEST_RT", ts_rows, "HOUR")
    out = cc.load("UNITTEST_RT", "HOUR", max_age=10_000_000)
    assert out == ts_rows
