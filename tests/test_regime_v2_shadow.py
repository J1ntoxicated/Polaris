"""Regime v2 twinlight SHADOW (backgate-plan W2-d, behavior-0).

DEMO/PAPER virtual account. Spec source:
``vault/50_research/backgate-plan/design-regime-v2-rollout.md`` W2 +
``vault/50_research/debates/regime_factory_2026-07-10.md`` (R1 FINAL-SPEC).

The v2 6-state classifier (direction up/down/flat x volatility
normal/expansion) is computed + recorded BARE ALONGSIDE the canonical v1
4-label regime — it has ZERO live-behavior consumers until the W4 flip
ladder. These tests prove: (1) ``classify_regime_v2`` is a total pure
function over the 4 documented features (returns trend / ADX slope /
BB-width percentile / ATR percentile); (2) ``fetch_regime_v2`` fails open
like ``_safe_lookup_regime``; (3) the shadow wiring inside
``compute_and_flip_regime`` and the ``entry_regime_v2`` open-stamp NEVER
alter the v1 SSOT label / ``entry_regime`` — the ``_safe_lookup`` degrade
pattern replicated per the design doc's mandate.
"""

from __future__ import annotations

import math
import sqlite3
import time
from unittest.mock import Mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import polaris.core.live_recalc.regime_v2 as regime_v2_mod
import polaris.scripts._production_layers as layers_mod
from polaris.core.data.schema import Bar
from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.core.live_recalc.regime_v2 import (
    REGIME_V2_VALUES,
    classify_regime_v2,
    fetch_regime_v2,
)
from polaris.scripts._production_layers import compute_and_flip_regime
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import RawSignal

NOW = 1_780_000_000


def _bar(ts: int, close: float, high: float | None = None, low: float | None = None) -> Bar:
    hi = high if high is not None else close * 1.001
    lo = low if low is not None else close * 0.999
    return Bar(
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        venue="okx", symbol="BTC-USDT", bar_interval="1m", ts=ts,
        open=close, high=hi, low=lo, close=close, volume=1000.0,
        notional_usd=close * 1000.0, trade_count=10, vwap=close,
        bid_close=close, ask_close=close, spread_bps_close=2.0, source="test",
    )


def _bars(n: int) -> list[Bar]:
    """Content-agnostic bars — only ``len`` matters once the indicator calls
    the branch-matrix tests exercise are monkeypatched."""
    return [_bar(ts=1_700_000_000 + i * 60, close=60_000.0) for i in range(n)]


def _jitter_series(n: int, *, base: float, drift: float, noise: float) -> list[Bar]:
    """Deterministic (no RNG) synthetic bars for the real-pipeline hypothesis
    sweep — jitter is a pure function of the bar index."""
    bars = []
    for i in range(n):
        close = base + i * drift + math.sin(i * 0.7) * noise * base
        band = abs(noise) * base * 0.5 + 1e-6
        bars.append(_bar(ts=1_700_000_000 + i * 60, close=close, high=close + band, low=close - band))
    return bars


def _sig(signal_id: str = "sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


# ---------------------------------------------------------------------------
# classify_regime_v2 — branch matrix (indicator primitives monkeypatched so
# only THIS function's own threshold logic is under test; the primitives
# themselves — compute_momentum / compute_adx_14 / ecdf_percentile — already
# have their own total-function test coverage elsewhere).
# ---------------------------------------------------------------------------


def _patch(
    monkeypatch: pytest.MonkeyPatch, *, momentum: float, adx_now: float,
    adx_then: float, vol_pct: float,
) -> None:
    monkeypatch.setattr(
        regime_v2_mod, "compute_momentum", lambda _closes, **_kw: momentum
    )
    monkeypatch.setattr(
        regime_v2_mod, "compute_adx_14", Mock(side_effect=[adx_now, adx_then])
    )
    monkeypatch.setattr(
        regime_v2_mod, "ecdf_percentile", lambda _value, _population: vol_pct
    )


@pytest.mark.parametrize(
    "momentum,vol_pct,expected",
    [
        (0.01, 0.30, "up_normal"),
        (0.01, 0.90, "up_expansion"),
        (-0.01, 0.30, "down_normal"),
        (-0.01, 0.90, "down_expansion"),
        (0.0, 0.30, "flat_normal"),
        (0.0, 0.90, "flat_expansion"),
    ],
)
def test_classify_regime_v2_branch_matrix(
    monkeypatch: pytest.MonkeyPatch, momentum: float, vol_pct: float, expected: str,
) -> None:
    _patch(monkeypatch, momentum=momentum, adx_now=20.0, adx_then=20.0, vol_pct=vol_pct)
    assert classify_regime_v2(_bars(60)) == expected


def test_classify_regime_v2_direction_flat_band_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """momentum_pct == the 0.3% band edge is NOT > band -> flat; just above
    the band flips to up. (momentum returns a FRACTION, *100 -> pct.)"""
    _patch(monkeypatch, momentum=0.003, adx_now=20.0, adx_then=20.0, vol_pct=0.3)
    assert classify_regime_v2(_bars(60)) == "flat_normal"
    _patch(monkeypatch, momentum=0.0031, adx_now=20.0, adx_then=20.0, vol_pct=0.3)
    assert classify_regime_v2(_bars(60)) == "up_normal"


def test_classify_regime_v2_borderline_vol_needs_rising_adx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vol_score in the [0.50, 0.66) borderline band only tips to expansion
    when the ADX slope (4th feature) is POSITIVE (strengthening trend)."""
    _patch(monkeypatch, momentum=0.01, adx_now=30.0, adx_then=20.0, vol_pct=0.55)
    assert classify_regime_v2(_bars(60)) == "up_expansion"
    _patch(monkeypatch, momentum=0.01, adx_now=20.0, adx_then=20.0, vol_pct=0.55)
    assert classify_regime_v2(_bars(60)) == "up_normal"
    _patch(monkeypatch, momentum=0.01, adx_now=15.0, adx_then=20.0, vol_pct=0.55)
    assert classify_regime_v2(_bars(60)) == "up_normal"  # falling ADX never tips


def test_classify_regime_v2_short_bars_degrades_to_flat_normal() -> None:
    """Below the min-bars floor -> the neutral state, no indicator ever runs
    (no monkeypatch needed: real compute_* on 10 bars would themselves
    degrade too, but the guard short-circuits before touching them)."""
    assert classify_regime_v2(_bars(10)) == "flat_normal"
    assert classify_regime_v2([]) == "flat_normal"


@given(
    n=st.integers(min_value=0, max_value=250),
    base=st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    drift=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    noise=st.floats(min_value=0.0, max_value=0.05, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=60)
def test_classify_regime_v2_never_raises_and_is_total(
    n: int, base: float, drift: float, noise: float,
) -> None:
    """Property: for ANY bar-history shape, classify_regime_v2 never raises
    and always returns a member of REGIME_V2_VALUES (real primitives, no
    monkeypatch — proves the composed indicator chain stays total)."""
    bars = _jitter_series(n, base=base, drift=drift, noise=noise)
    assert classify_regime_v2(bars) in REGIME_V2_VALUES


# ---------------------------------------------------------------------------
# fetch_regime_v2 — fail-open _safe_lookup replica
# ---------------------------------------------------------------------------


def test_fetch_regime_v2_missing_row_returns_none(memdb: sqlite3.Connection) -> None:
    assert fetch_regime_v2(memdb, venue="okx", underlying_group_id="crypto:BTC") is None


def test_fetch_regime_v2_returns_stamped_value(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, "
        "confidence, regime_v2, updated_ts) VALUES (?, ?, 'chop', 0.5, ?, ?)",
        ("okx", "crypto:BTC", "up_expansion", NOW),
    )
    assert fetch_regime_v2(memdb, venue="okx", underlying_group_id="crypto:BTC") == "up_expansion"


def test_fetch_regime_v2_null_column_returns_none(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, "
        "confidence, updated_ts) VALUES (?, ?, 'chop', 0.5, ?)",
        ("okx", "crypto:BTC", NOW),
    )
    assert fetch_regime_v2(memdb, venue="okx", underlying_group_id="crypto:BTC") is None


def test_fetch_regime_v2_fails_open_on_pre_migration_db() -> None:
    """A legacy DB whose ``regime_state`` predates the W2-d ALTER (no
    ``regime_v2`` column) must degrade to None, never raise."""
    legacy = sqlite3.connect(":memory:", isolation_level=None)
    legacy.execute(
        "CREATE TABLE regime_state (venue TEXT, underlying_group_id TEXT, "
        "regime TEXT, confidence REAL, updated_ts INTEGER)"
    )
    legacy.execute(
        "INSERT INTO regime_state VALUES ('okx', 'crypto:BTC', 'chop', 0.5, ?)",
        (NOW,),
    )
    assert fetch_regime_v2(legacy, venue="okx", underlying_group_id="crypto:BTC") is None
    legacy.close()


# ---------------------------------------------------------------------------
# compute_and_flip_regime wiring — behavior-0 proof
# ---------------------------------------------------------------------------


def _run(conn: sqlite3.Connection, *, crosstab: dict[str, int] | None = None, now_ts: int = NOW) -> str:
    return compute_and_flip_regime(
        conn, venue="okx", underlying_group_id="crypto:BTC", bars=[],
        now_ts=now_ts, regime_v2_crosstab=crosstab,
    )


def test_regime_v2_shadow_written_alongside_v1(memdb: sqlite3.Connection) -> None:
    out = _run(memdb)
    assert out == "chop"  # bars=[] -> compute_real_regime_signal degrades to chop
    row = memdb.execute(
        "SELECT regime, regime_v2 FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    assert row[0] == "chop"
    assert row[1] == "flat_normal"  # bars=[] -> classify_regime_v2 min-bars degrade


def test_regime_v2_classify_failure_never_touches_v1_label(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        layers_mod, "classify_regime_v2",
        Mock(side_effect=RuntimeError("shadow blew up")),
    )
    out = _run(memdb)  # must not raise
    assert out == "chop"
    row = memdb.execute(
        "SELECT regime, regime_v2 FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    assert row[0] == "chop"
    assert row[1] is None  # the UPDATE never ran — fail-open, no partial write


def test_regime_v2_label_and_v1_label_byte_identical_regardless_of_shadow(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v1 SSOT (regime / consecutive_candidate / consecutive_count) is
    IDENTICAL whether the regime_v2 shadow succeeds or explodes — proves the
    twinlight has zero behavioral coupling to the v1 flip gate."""
    out_ok = _run(memdb)
    row_ok = memdb.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()

    fresh = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        fresh.execute(stmt)
    monkeypatch.setattr(
        layers_mod, "classify_regime_v2", Mock(side_effect=RuntimeError("boom")),
    )
    out_broken = _run(fresh)
    row_broken = fresh.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    fresh.close()

    assert out_ok == out_broken
    assert tuple(row_ok) == tuple(row_broken)


def test_regime_v2_crosstab_accumulates(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        layers_mod, "classify_regime_v2",
        Mock(side_effect=["up_normal", "up_expansion"]),
    )
    crosstab: dict[str, int] = {}
    _run(memdb, crosstab=crosstab, now_ts=NOW)
    _run(memdb, crosstab=crosstab, now_ts=NOW + 60)
    assert crosstab == {"chop|up_normal": 1, "chop|up_expansion": 1}


def test_regime_v2_crosstab_none_default_is_byte_identical_to_dict(
    memdb: sqlite3.Connection,
) -> None:
    out_none = _run(memdb, crosstab=None)
    row_none = memdb.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()

    fresh = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        fresh.execute(stmt)
    out_dict = _run(fresh, crosstab={})
    row_dict = fresh.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    fresh.close()

    assert out_none == out_dict
    assert tuple(row_none) == tuple(row_dict)


# ---------------------------------------------------------------------------
# entry_regime_v2 open-stamp — regression-safe (entry_regime v1 untouched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_regime_v2_stamped_from_shadow_column(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    memdb.execute(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, "
        "confidence, regime_v2, updated_ts) VALUES (?, ?, 'bull_trend', 0.8, ?, ?)",
        ("okx", "crypto:BTC", "up_expansion", NOW),
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("open-v2"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()), real_roundtrip=False,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT entry_regime, entry_regime_v2 FROM positions "
        "WHERE symbol='BTC-USDT' AND venue='okx'"
    ).fetchone()
    assert row[0] == "bull_trend"  # v1 stamp — unaffected, same as pre-W2
    assert row[1] == "up_expansion"  # v2 shadow stamp — new, additive only


@pytest.mark.asyncio
async def test_entry_regime_v2_null_when_unseeded_never_blocks_open(
    memdb: sqlite3.Connection,
) -> None:
    """No regime_state row at all (bootstrap) -> entry_regime_v2 stays NULL,
    entry_regime (v1) is likewise NULL (pre-existing degrade, unchanged) —
    the open must still succeed (flow_not_block)."""
    reset_process_fence()
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("open-v2-null"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()), real_roundtrip=False,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT entry_regime, entry_regime_v2 FROM positions "
        "WHERE symbol='BTC-USDT' AND venue='okx'"
    ).fetchone()
    assert row[0] == ""  # v1 fetch_regime() or "" — pre-existing degrade
    assert row[1] is None  # v2 fetch_regime_v2() — nullable degrade
