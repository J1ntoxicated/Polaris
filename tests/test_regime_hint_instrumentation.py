"""Altdata hint instrumentation (in-memory counters, behavior 0).

DEMO/PAPER. The fuser's conviction-floor hint was INTENTIONALLY retired as a
regime input (regime_layered debate 2026-05-31 — weighted compose consumes the
same evidence scores). These counters only MEASURE that redundancy: how often
the hint diverges from the composed candidate / final SSOT label. No DB write,
no decision branch — ``hint_stats=None`` (the default) is byte-identical.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

import polaris.scripts._production_layers as layers_mod
from polaris.scripts._production_layers import compute_and_flip_regime

NOW = 1_780_000_000


class _StubCache:
    def __init__(self, sources: dict[str, dict[str, Any]]) -> None:
        self._sources = sources

    def get_for_group(
        self, underlying_group_id: str, *, now_ts: float | None = None
    ) -> dict[str, dict[str, Any]]:
        return self._sources


# F&G 90 (> greed 75) → bull score 1.5×1.25 = 1.875 ≥ 1.5 floor → hint=bull.
_BULL_HINT_CACHE = _StubCache({"crypto_fg": {"value": 90}})
# F&G 50 → no score fires → evidence present but hint None (below floor).
_NO_HINT_CACHE = _StubCache({"crypto_fg": {"value": 50}})


@pytest.fixture()
def _price_chop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the price candidate to a weak chop so the evidence hint wins tilt."""
    monkeypatch.setattr(
        layers_mod, "compute_real_regime_signal",
        lambda _bars, **_kw: ("chop", 0.1, {}),
    )


def _run(
    conn: sqlite3.Connection, *, cache: Any, stats: dict[str, int] | None,
    now_ts: int = NOW,
) -> str:
    return compute_and_flip_regime(
        conn, venue="okx", underlying_group_id="crypto:BTC", bars=[],
        now_ts=now_ts, altdata_cache=cache, hint_stats=stats,
    )


def test_hint_final_mismatch_counted(
    memdb: sqlite3.Connection, _price_chop: None,
) -> None:
    """Hint=bull tilts the candidate, but the 2-close gate holds the SSOT at
    its prior label → hint != final SSOT → final_mismatch increments."""
    stats: dict[str, int] = {}
    out = _run(memdb, cache=_BULL_HINT_CACHE, stats=stats)
    # First observation: regime_state row seeded with the candidate itself —
    # hint == candidate == final → total counts, no mismatch.
    assert out == "bull_trend"
    assert stats == {"hint_total": 1}
    # Flip the SSOT to chop (price-only, twice → confirmed), then re-run with
    # the bull hint: candidate tilts to bull but the gate keeps SSOT at chop.
    for ts in (NOW + 60, NOW + 120):
        _run(memdb, cache=_NO_HINT_CACHE, stats=None, now_ts=ts)
    out2 = _run(memdb, cache=_BULL_HINT_CACHE, stats=stats, now_ts=NOW + 180)
    assert out2 == "chop"  # gate held — pending 1x only
    assert stats["hint_total"] == 2
    assert stats["hint_final_mismatch"] == 1
    assert "hint_tilt_lost_to_price" not in stats  # hint DID win the tilt


def test_hint_below_floor_not_counted(
    memdb: sqlite3.Connection, _price_chop: None,
) -> None:
    stats: dict[str, int] = {}
    out = _run(memdb, cache=_NO_HINT_CACHE, stats=stats)
    assert out == "chop"
    assert stats == {}  # hint None → no counter moves


def test_hint_tilt_lost_to_price_counted(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strong price candidate out-convicts the hint → tilt_lost increments."""
    monkeypatch.setattr(
        layers_mod, "compute_real_regime_signal",
        lambda _bars, **_kw: ("bear_trend", 0.9, {}),  # conviction 2.7 > 1.875
    )
    stats: dict[str, int] = {}
    out = _run(memdb, cache=_BULL_HINT_CACHE, stats=stats)
    assert out == "bear_trend"
    assert stats["hint_total"] == 1
    assert stats["hint_tilt_lost_to_price"] == 1
    assert stats["hint_final_mismatch"] == 1  # final label is bear, hint bull


def test_default_none_stats_is_byte_identical(
    memdb: sqlite3.Connection, _price_chop: None,
) -> None:
    """hint_stats=None (every legacy caller) → same label, no side effects."""
    out_a = _run(memdb, cache=_BULL_HINT_CACHE, stats=None)
    row_a = memdb.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    fresh = sqlite3.connect(":memory:", isolation_level=None)
    from polaris.storage.schema import ALL_DDL

    for stmt in ALL_DDL:
        fresh.execute(stmt)
    out_b = _run(fresh, cache=_BULL_HINT_CACHE, stats={})
    row_b = fresh.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    fresh.close()
    assert out_a == out_b
    assert tuple(row_a) == tuple(row_b)


def test_no_db_write_per_call(memdb: sqlite3.Connection, _price_chop: None) -> None:
    """Counters are in-memory only: the ONLY tables the regime pass touches are
    the pre-existing regime_state writes (no new instrumentation table rows)."""
    stats: dict[str, int] = {}
    _run(memdb, cache=_BULL_HINT_CACHE, stats=stats)
    # No counterfactual/shadow rows were written by the hint instrumentation.
    assert memdb.execute(
        "SELECT COUNT(*) FROM gate_kill_counterfactuals"
    ).fetchone()[0] == 0
    assert memdb.execute(
        "SELECT COUNT(*) FROM gate_shadow_events"
    ).fetchone()[0] == 0
