"""STEP 5 — regime 계층 합성 (P2-P5) tests.

DEMO/PAPER only — virtual funds. Regime synthesis is SIGNAL-only: it emits a
regime LABEL (+ confidence/evidence context for G3/G7). It NEVER sizes, blocks,
exits, or halts. The 2-consecutive-close confirm gate is unchanged; evidence
never bypasses it (price-derived crisis stays immediate, evidence-derived
crisis must clear the 2-close gate).

Aggressive bias preserved — no defensive throttle, no sizing dampen, no entry
block. Synthesis only relabels; it adds nothing that reduces flow.
"""

from __future__ import annotations

import sqlite3
import time

from polaris.core.altdata.cache import AltDataCache
from polaris.core.data.schema import Bar
from polaris.core.live_recalc.regime_flip import detect_regime_flip, fetch_regime
from polaris.scripts._production_indicators import (
    compute_real_regime,
    compute_real_regime_signal,
)
from polaris.scripts._production_layers import (
    compose_regime_candidate,
    compute_and_flip_regime,
)


def _make_bar(*, ts: int, close: float, high: float | None = None,
              low: float | None = None) -> Bar:
    hi = high if high is not None else close * 1.001
    lo = low if low is not None else close * 0.999
    return Bar(
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        venue="okx", symbol="BTC-USDT", bar_interval="1m", ts=ts,
        open=close, high=hi, low=lo, close=close, volume=1000.0,
        notional_usd=close * 1000.0, trade_count=10, vwap=close,
        bid_close=close, ask_close=close, spread_bps_close=2.0, source="test",
    )


def _series(n: int, base: float = 60_000.0, drift: float = 1.0) -> list[Bar]:
    return [_make_bar(ts=1_700_000_000 + i * 60, close=base + i * drift) for i in range(n)]


def _crash_series(n: int = 120, base: float = 60_000.0) -> list[Bar]:
    bars = _series(n, base=base, drift=0.0)
    for i in range(-30, 0):
        b = bars[i]
        bars[i] = _make_bar(ts=b.ts, close=b.close * 0.92)
    return bars


# ── P2: compute_real_regime_signal (label, strength, evidence) ─────────────────


def test_signal_returns_label_strength_evidence() -> None:
    bars = _series(120, drift=5.0)
    label, strength, evidence = compute_real_regime_signal(bars)
    assert label in ("bull_trend", "bear_trend", "chop", "crisis")
    assert 0.0 <= strength <= 1.0
    assert isinstance(evidence, dict)


def test_signal_label_identical_to_wrapper() -> None:
    """P2 invariant: label산출 동일 — the signal label must match the existing
    label-only ``compute_real_regime`` for every branch (no flip drift)."""
    for bars in (
        _series(120, drift=5.0),     # bull
        _series(120, drift=-3.0),    # bear/chop
        _series(120, drift=0.0),     # chop
        _crash_series(),             # crisis
        _series(10),                 # bootstrap → chop
    ):
        label, _, _ = compute_real_regime_signal(bars)
        assert label == compute_real_regime(bars)


def test_wrapper_is_label_only_str() -> None:
    """compute_real_regime stays a label-only str wrapper (back-compat)."""
    out = compute_real_regime(_series(120, drift=5.0))
    assert isinstance(out, str)
    assert out == "bull_trend"


def test_signal_strength_higher_for_stronger_trend() -> None:
    """Strength is a magnitude boost: a steeper, more efficient trend → higher
    strength. (Strength is a보강값 only — it does NOT widen label flip conditions.)"""
    weak_label, weak_s, _ = compute_real_regime_signal(_series(120, drift=5.0))
    strong_label, strong_s, _ = compute_real_regime_signal(_series(120, drift=40.0))
    assert weak_label == strong_label == "bull_trend"
    assert strong_s > weak_s


def test_signal_crisis_strength_high() -> None:
    label, strength, _ = compute_real_regime_signal(_crash_series())
    assert label == "crisis"
    assert strength >= 0.8  # crisis = high-conviction signal


# ── P3: compose_regime_candidate (price base + evidence conviction tilt) ───────


def test_compose_no_evidence_keeps_price() -> None:
    """No evidence scores → price candidate stands unchanged (fallback)."""
    cand = compose_regime_candidate("bull_trend", 0.5, {})
    assert cand == "bull_trend"


def test_compose_strong_evidence_tilts_borderline() -> None:
    """Weak price conviction + strong opposing evidence → tilt to evidence."""
    evidence_scores = {"bull_trend": 0.0, "bear_trend": 3.0, "chop": 0.0, "crisis": 0.0}
    cand = compose_regime_candidate("chop", 0.1, evidence_scores)
    assert cand == "bear_trend"


def test_compose_strong_price_resists_weak_evidence() -> None:
    """Strong price conviction + weak evidence → price label held."""
    evidence_scores = {"bull_trend": 0.0, "bear_trend": 1.6, "chop": 0.0, "crisis": 0.0}
    cand = compose_regime_candidate("bull_trend", 0.95, evidence_scores)
    assert cand == "bull_trend"


def test_compose_never_downgrades_price_crisis() -> None:
    """SIGNAL safety: a price-derived crisis is never tilted away by evidence."""
    evidence_scores = {"bull_trend": 5.0, "bear_trend": 0.0, "chop": 0.0, "crisis": 0.0}
    cand = compose_regime_candidate("crisis", 1.0, evidence_scores)
    assert cand == "crisis"


# ── P4 🔴BLOCKING: evidence-crisis candidate_source 2-close (price = immediate) ─


def test_price_crisis_immediate_flip(memdb: sqlite3.Connection) -> None:
    """price-derived crisis keeps the immediate-flip fast path."""
    detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                       candidate="bull_trend", now_ts=100)
    d = detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                           candidate="crisis", now_ts=200,
                           candidate_source="price")
    assert d.confirmed is True
    assert d.reason == "immediate_crisis"
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "crisis"


def test_evidence_crisis_requires_2_close(memdb: sqlite3.Connection) -> None:
    """evidence-derived crisis must clear the SAME 2-close gate (no bypass)."""
    detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                       candidate="bull_trend", now_ts=100)
    d1 = detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                            candidate="crisis", now_ts=200,
                            candidate_source="evidence")
    assert d1.confirmed is False  # first evidence-crisis close → pending, NOT immediate
    assert d1.consecutive_count == 1
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "bull_trend"
    d2 = detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                            candidate="crisis", now_ts=300,
                            candidate_source="evidence")
    assert d2.confirmed is True
    assert d2.reason == "confirmed_2x"
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "crisis"


def test_default_candidate_source_is_price(memdb: sqlite3.Connection) -> None:
    """Back-compat: default candidate_source = price → immediate crisis flip."""
    detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                       candidate="bull_trend", now_ts=100)
    d = detect_regime_flip(memdb, venue="okx", underlying_group_id="crypto:BTC",
                           candidate="crisis", now_ts=200)
    assert d.confirmed is True
    assert d.reason == "immediate_crisis"


# ── P5: dynamic confidence (no fixed 0.5) ──────────────────────────────────────


def test_dynamic_confidence_persisted(memdb: sqlite3.Connection) -> None:
    """compute_and_flip_regime computes confidence from L3 strength + evidence
    agreement; persisted confidence is no longer the fixed 0.5 placeholder."""
    bars = _series(120, drift=40.0)  # strong bull
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 82}, ttl_sec=9999, now_ts=0.0)
    cache.set("okx_funding", {"X": {"fundingRate": 0.005}}, ttl_sec=9999, now_ts=0.0)
    regime = compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=int(time.time()), altdata_cache=cache,
    )
    assert regime in ("bull_trend", "bear_trend", "chop", "crisis")
    row = memdb.execute(
        "SELECT confidence FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    assert row is not None
    assert 0.0 <= float(row[0]) <= 1.0
    assert float(row[0]) != 0.5  # dynamic, not the old fixed placeholder


def test_dynamic_confidence_in_range_price_only(memdb: sqlite3.Connection) -> None:
    """Price-only path (no altdata) still yields a dynamic confidence in [0,1]."""
    bars = _series(120, drift=5.0)
    compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=int(time.time()),
    )
    row = memdb.execute(
        "SELECT confidence FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    assert row is not None
    assert 0.0 <= float(row[0]) <= 1.0


def test_compute_and_flip_regime_still_signal_only(memdb: sqlite3.Connection) -> None:
    """Synthesis returns a label string only; no side-effects beyond regime_state.
    (No positions/orders/risk writes — SIGNAL-only.)"""
    bars = _series(120, drift=5.0)
    regime = compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=int(time.time()),
    )
    assert isinstance(regime, str)
    # nothing was written to positions/orders by regime synthesis
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
