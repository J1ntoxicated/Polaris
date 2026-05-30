"""Alt-data → regime EVIDENCE wire (#6) — TDD test surface.

DEMO/PAPER. Verifies the integration of the alt-data fuser into the Layer 6
regime engine as SIGNAL/EVIDENCE only:

  * ``detect_regime_flip`` persists ``evidence_json`` + ``confidence`` on ALL
    three write paths (initial INSERT, confirmed-flip UPDATE, pending UPDATE).
  * The existing 2-consecutive-close confirm gate is UNCHANGED — a regime_hint
    does NOT skip the gate.
  * ``compute_and_flip_regime`` override is conservative: a price-derived
    ``crisis`` is NEVER downgraded by evidence; a hint still needs the confirm
    gate; no evidence → price-only regime stands (no throttle).
  * ``altdata_snapshot`` persistence is additive and idempotent.
  * The ``_altdata_producer`` loop is interruptible (stop_evt) and survives a
    collector raising (logs + keeps last cache, no throttle / no halt).
  * NO size/skip/block/learner-write is introduced anywhere on this path.

No live network: collectors are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any

import pytest

from polaris.core.altdata.cache import AltDataCache
from polaris.core.live_recalc.regime_flip import detect_regime_flip
from polaris.scripts._production_layers import compute_and_flip_regime
from polaris.scripts.production_paper_loop import (
    ProdLoopState,
    _altdata_producer,
    persist_altdata_snapshot,
)

# ---------------------------------------------------------------------------
# detect_regime_flip — evidence/confidence persisted on all 3 write paths
# ---------------------------------------------------------------------------


def _regime_row(conn: sqlite3.Connection, venue: str, group: str) -> Any:
    return conn.execute(
        "SELECT regime, confidence, evidence_json, consecutive_candidate, "
        "consecutive_count FROM regime_state "
        "WHERE venue = ? AND underlying_group_id = ?",
        (venue, group),
    ).fetchone()


def test_initial_insert_persists_evidence(memdb: sqlite3.Connection) -> None:
    """First observation (INSERT path) writes evidence_json + confidence."""
    ev = {"vix": 14.0, "crypto_fg": 80}
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
        evidence=ev, confidence=0.7,
    )
    row = _regime_row(memdb, "okx", "crypto:BTC")
    assert row is not None
    assert row[0] == "bull_trend"
    assert row[1] == pytest.approx(0.7)
    assert json.loads(row[2]) == ev


def test_pending_update_persists_evidence(memdb: sqlite3.Connection) -> None:
    """Pending (1x, below confirm) UPDATE path persists evidence + confidence."""
    # Seed at bull_trend.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
    )
    ev = {"avg_funding": -0.002}
    dec = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1060,
        evidence=ev, confidence=0.55,
    )
    assert dec.reason == "pending_1x"
    assert dec.confirmed is False
    row = _regime_row(memdb, "okx", "crypto:BTC")
    # SSOT regime UNCHANGED (still bull) — pending, not confirmed.
    assert row[0] == "bull_trend"
    assert row[1] == pytest.approx(0.55)
    assert json.loads(row[2]) == ev
    assert row[3] == "bear_trend"
    assert row[4] == 1


def test_confirmed_flip_persists_evidence(memdb: sqlite3.Connection) -> None:
    """Confirmed-flip (2x) UPDATE path persists evidence + confidence."""
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
    )
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1060,
        evidence={"avg_funding": -0.002}, confidence=0.55,
    )
    ev2 = {"avg_funding": -0.003, "crypto_fg": 12}
    dec = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1120,
        evidence=ev2, confidence=0.8,
    )
    assert dec.reason == "confirmed_2x"
    assert dec.confirmed is True
    row = _regime_row(memdb, "okx", "crypto:BTC")
    assert row[0] == "bear_trend"
    assert row[1] == pytest.approx(0.8)
    assert json.loads(row[2]) == ev2


def test_hint_does_not_skip_two_close_gate(memdb: sqlite3.Connection) -> None:
    """A regime_hint candidate STILL needs 2 consecutive closes (gate intact)."""
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
    )
    # One close of a hinted bear candidate: must NOT flip yet.
    dec1 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1060,
        evidence={"crypto_fg": 12}, confidence=0.9,
    )
    assert dec1.confirmed is False
    assert _regime_row(memdb, "okx", "crypto:BTC")[0] == "bull_trend"
    # Second consecutive close → now it flips.
    dec2 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1120,
        evidence={"crypto_fg": 11}, confidence=0.9,
    )
    assert dec2.confirmed is True
    assert _regime_row(memdb, "okx", "crypto:BTC")[0] == "bear_trend"


def test_no_change_path_unaffected(memdb: sqlite3.Connection) -> None:
    """Same-regime no-change path still works (no flip, counter reset)."""
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
    )
    dec = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1060,
        evidence={"crypto_fg": 80}, confidence=0.6,
    )
    assert dec.reason == "no_change"
    assert dec.confirmed is False


def test_no_change_evidence_less_tick_preserves_prior_evidence(
    memdb: sqlite3.Connection,
) -> None:
    """N1: a same-regime tick WITHOUT evidence must NOT wipe evidence written
    on a prior evidence-bearing tick (durable G3/G7 context)."""
    # Evidence-bearing tick first (seeds + writes real evidence).
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
        evidence={"crypto_fg": 80, "vix": 13.0}, confidence=0.72,
    )
    # Evidence-less same-regime tick (no evidence / default confidence 0.5).
    dec = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1060,
    )
    assert dec.reason == "no_change"
    row = _regime_row(memdb, "okx", "crypto:BTC")
    # Prior evidence + confidence PRESERVED (not overwritten by {} / 0.5).
    assert json.loads(row[2]) == {"crypto_fg": 80, "vix": 13.0}
    assert row[1] == pytest.approx(0.72)
    # A LATER evidence-bearing tick still overwrites with the new evidence.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1120,
        evidence={"crypto_fg": 60}, confidence=0.6,
    )
    row2 = _regime_row(memdb, "okx", "crypto:BTC")
    assert json.loads(row2[2]) == {"crypto_fg": 60}
    assert row2[1] == pytest.approx(0.6)


def test_pending_evidence_less_tick_preserves_prior_evidence(
    memdb: sqlite3.Connection,
) -> None:
    """N1: a pending (1x) UPDATE WITHOUT evidence must NOT wipe evidence from a
    prior evidence-bearing pending tick.

    Seed bull → pending bear WITH evidence → pending chop WITHOUT evidence
    (a different candidate resets the counter via the SAME pending UPDATE path).
    The evidence-less chop pend must preserve the bear-tick evidence.
    """
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=1000,
    )
    dec1 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=1060,
        evidence={"avg_funding": -0.003}, confidence=0.58,
    )
    assert dec1.reason == "pending_1x"
    row1 = _regime_row(memdb, "okx", "crypto:BTC")
    assert json.loads(row1[2]) == {"avg_funding": -0.003}
    # Evidence-less pending chop (different candidate → pending UPDATE, count→1).
    dec2 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="chop", now_ts=1120,
    )
    assert dec2.reason == "pending_1x"
    row2 = _regime_row(memdb, "okx", "crypto:BTC")
    # Candidate switched to chop but the PRIOR evidence/confidence survive.
    assert row2[3] == "chop"
    assert json.loads(row2[2]) == {"avg_funding": -0.003}
    assert row2[1] == pytest.approx(0.58)


# ---------------------------------------------------------------------------
# compute_and_flip_regime — conservative override + price-only fallback
# ---------------------------------------------------------------------------


class _StubCache:
    """Minimal cache stand-in exposing ``get_for_group`` for the fuser."""

    def __init__(self, sources: dict[str, dict[str, Any]]) -> None:
        self._sources = sources

    def get_for_group(
        self, underlying_group_id: str, *, now_ts: float | None = None
    ) -> dict[str, dict[str, Any]]:
        return self._sources


def test_no_cache_uses_price_only(memdb: sqlite3.Connection) -> None:
    """No cache → behaves exactly like the price-only path (no evidence)."""
    bars = _bars_series_chop()
    out = compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=1000,
    )
    from polaris.scripts._production_indicators import compute_real_regime

    assert out == compute_real_regime(bars)
    row = _regime_row(memdb, "okx", "crypto:BTC")
    # No evidence supplied → evidence_json stays the empty-object default.
    assert json.loads(row[2]) == {}


def test_empty_cache_is_price_only_no_throttle(memdb: sqlite3.Connection) -> None:
    """Empty/keyless cache → price-only regime stands (fallback, not throttle)."""
    bars = _bars_series_chop()
    cache = _StubCache({})  # no fresh sources at all
    out = compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=1000, altdata_cache=cache,
    )
    from polaris.scripts._production_indicators import compute_real_regime

    assert out == compute_real_regime(bars)


def test_evidence_never_downgrades_price_crisis(memdb: sqlite3.Connection) -> None:
    """Price-derived crisis is NEVER overridden DOWN by a calmer alt-data hint."""
    # Seed regime_state at crisis so a crisis candidate is the steady SSOT.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="crisis", now_ts=900,
    )
    # Force price candidate = crisis via monkeypatched compute_real_regime.
    bars = _bars_series_chop()
    # Bullish alt-data (would suggest bull_trend if it could override).
    cache = _StubCache({"crypto_fg": {"value": 85}, "okx_funding": {
        "BTC-USDT-SWAP": {"fundingRate": 0.004}}})
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "crisis"  # type: ignore[assignment]
    try:
        out = compute_and_flip_regime(
            memdb, venue="okx", underlying_group_id="crypto:BTC",
            bars=bars, now_ts=1000, altdata_cache=cache,
        )
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]
    assert out == "crisis"


def test_evidence_tilts_borderline_then_confirms(memdb: sqlite3.Connection) -> None:
    """A strong alt-data hint becomes the candidate and flips after 2 closes."""
    # Price says chop; alt-data strongly says bear (extreme fear + bearish funding).
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=900,
    )
    cache = _StubCache({
        "crypto_fg": {"value": 12},
        "okx_funding": {"BTC-USDT-SWAP": {"fundingRate": -0.002}},
    })
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        bars = _bars_series_chop()
        # First close: candidate becomes bear (hint), but gate not yet met.
        out1 = compute_and_flip_regime(
            memdb, venue="okx", underlying_group_id="crypto:BTC",
            bars=bars, now_ts=1000, altdata_cache=cache,
        )
        assert out1 == "bull_trend"  # still the prior SSOT (pending)
        row = _regime_row(memdb, "okx", "crypto:BTC")
        assert row[3] == "bear_trend"  # candidate tracked
        assert json.loads(row[2])  # evidence non-empty after the write
        # Second close confirms.
        out2 = compute_and_flip_regime(
            memdb, venue="okx", underlying_group_id="crypto:BTC",
            bars=bars, now_ts=1060, altdata_cache=cache,
        )
        assert out2 == "bear_trend"
        row2 = _regime_row(memdb, "okx", "crypto:BTC")
        assert json.loads(row2[2])  # evidence persisted on confirmed flip
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]


def test_weak_evidence_below_floor_is_price_only(memdb: sqlite3.Connection) -> None:
    """Evidence below the conviction floor → no hint, price regime stands."""
    cache = _StubCache({"fred_macro": {"vix": 26.0}})  # single mild bear < floor
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        bars = _bars_series_chop()
        out = compute_and_flip_regime(
            memdb, venue="okx", underlying_group_id="crypto:BTC",
            bars=bars, now_ts=1000, altdata_cache=cache,
        )
        assert out == "chop"
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Equity (Stream C / Alpaca) — macro evidence populates evidence_json
# ---------------------------------------------------------------------------


def test_equity_macro_populates_evidence_json(memdb: sqlite3.Connection) -> None:
    """An alpaca/equity group now gets macro regime EVIDENCE: a crisis VIX
    populates evidence_json for the equity:* group (was price-only before)."""
    cache = _StubCache({"fred_macro": {"vix": 45.0, "hy_spread": 620.0}})
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        compute_and_flip_regime(
            memdb, venue="alpaca", underlying_group_id="equity:AAPL",
            bars=_bars_series_chop(), now_ts=1000, altdata_cache=cache,
        )
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]
    row = _regime_row(memdb, "alpaca", "equity:AAPL")
    assert row is not None
    ev = json.loads(row[2])
    assert ev.get("vix") == 45.0  # equity now carries macro evidence
    assert ev.get("hy_spread") == 620.0


def test_equity_neutral_macro_no_override(memdb: sqlite3.Connection) -> None:
    """Neutral macro → None hint → price-only equity regime stands (no throttle)."""
    cache = _StubCache({"fred_macro": {"vix": 18.0, "hy_spread": 350.0}})
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        out = compute_and_flip_regime(
            memdb, venue="alpaca", underlying_group_id="equity:SPY",
            bars=_bars_series_chop(), now_ts=1000, altdata_cache=cache,
        )
        assert out == "chop"  # price candidate, not overridden
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]


def test_equity_missing_fred_is_price_only(memdb: sqlite3.Connection) -> None:
    """Missing FRED source → {} → price-only equity regime (correct fallback)."""
    cache = _StubCache({})  # no fresh macro at all (keyless FRED)
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "bull_trend"  # type: ignore[assignment]
    try:
        out = compute_and_flip_regime(
            memdb, venue="alpaca", underlying_group_id="equity:MSFT",
            bars=_bars_series_chop(), now_ts=1000, altdata_cache=cache,
        )
        assert out == "bull_trend"
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]
    row = _regime_row(memdb, "alpaca", "equity:MSFT")
    assert json.loads(row[2]) == {}  # no evidence → empty-object default


def test_equity_hint_does_not_skip_two_close_gate(memdb: sqlite3.Connection) -> None:
    """A NON-crisis equity macro hint STILL needs 2 consecutive closes.

    Uses a calm-macro bull hint (not crisis — crisis has a pre-existing
    immediate-flip rule that is unchanged here). Price says chop; the bull
    hint becomes the candidate but only flips after the 2nd consecutive close.
    """
    detect_regime_flip(
        memdb, venue="alpaca", underlying_group_id="equity:AAPL",
        candidate="bear_trend", now_ts=900,
    )
    cache = _StubCache({"fred_macro": {"vix": 12.0, "hy_spread": 250.0}})  # → bull
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        out1 = compute_and_flip_regime(
            memdb, venue="alpaca", underlying_group_id="equity:AAPL",
            bars=_bars_series_chop(), now_ts=1000, altdata_cache=cache,
        )
        # First close of the hinted bull candidate: NOT yet flipped (gate holds).
        assert out1 == "bear_trend"  # prior SSOT (pending)
        row = _regime_row(memdb, "alpaca", "equity:AAPL")
        assert row[3] == "bull_trend"  # candidate tracked
        assert json.loads(row[2]).get("vix") == 12.0  # evidence written
        # Second consecutive close confirms (gate honored, not bypassed).
        out2 = compute_and_flip_regime(
            memdb, venue="alpaca", underlying_group_id="equity:AAPL",
            bars=_bars_series_chop(), now_ts=1060, altdata_cache=cache,
        )
        assert out2 == "bull_trend"
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# altdata_snapshot persistence
# ---------------------------------------------------------------------------


def test_persist_altdata_snapshot(memdb: sqlite3.Connection) -> None:
    persist_altdata_snapshot(
        memdb, ts=1000, source="fred_macro", asset_class="forex",
        payload={"vix": 15.7, "hy_spread": 272.0},
    )
    row = memdb.execute(
        "SELECT ts, source, asset_class, payload_json FROM altdata_snapshot"
    ).fetchone()
    assert row[0] == 1000
    assert row[1] == "fred_macro"
    assert row[2] == "forex"
    assert json.loads(row[3]) == {"vix": 15.7, "hy_spread": 272.0}


def test_persist_altdata_snapshot_idempotent(memdb: sqlite3.Connection) -> None:
    """Re-writing the same (ts, source) overwrites — no PK violation raised."""
    persist_altdata_snapshot(
        memdb, ts=1000, source="crypto_fg", asset_class="crypto",
        payload={"value": 23},
    )
    persist_altdata_snapshot(
        memdb, ts=1000, source="crypto_fg", asset_class="crypto",
        payload={"value": 24},
    )
    rows = memdb.execute(
        "SELECT payload_json FROM altdata_snapshot WHERE source='crypto_fg'"
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == {"value": 24}


# ---------------------------------------------------------------------------
# _altdata_producer — background loop interruptible + collector-error resilient
# ---------------------------------------------------------------------------


class _StubCollector:
    def __init__(self, name: str, payload: dict[str, Any], *, asset_class: str = "crypto") -> None:
        self.name = name
        self.ttl_sec = 1
        self.asset_classes = (asset_class,)
        self.payload = payload
        self.calls = 0

    async def fetch(self, *, client: Any = None) -> dict[str, Any]:
        self.calls += 1
        return dict(self.payload)


class _ExplodingCollector:
    name = "boom"
    ttl_sec = 1
    asset_classes = ("crypto",)

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, *, client: Any = None) -> dict[str, Any]:
        self.calls += 1
        raise RuntimeError("collector blew up")


@pytest.mark.asyncio
async def test_producer_interruptible_and_persists(memdb: sqlite3.Connection) -> None:
    """One pass populates the cache + persists a snapshot, then stop_evt halts."""
    cache = AltDataCache()
    state = ProdLoopState()
    stop_evt = asyncio.Event()
    coll = _StubCollector("okx_funding", {"BTC-USDT-SWAP": {"fundingRate": 0.001}})
    task = asyncio.create_task(
        _altdata_producer(
            memdb, cache=cache, state=state, stop_evt=stop_evt,
            collectors=[coll], poll_sec=0.01,
        )
    )
    # Let it run a few passes.
    await asyncio.sleep(0.05)
    stop_evt.set()
    await asyncio.wait_for(task, timeout=2.0)
    # Cache populated.
    assert cache.get("okx_funding", now_ts=time.time()) is not None
    # Snapshot persisted.
    n = memdb.execute(
        "SELECT COUNT(*) FROM altdata_snapshot WHERE source='okx_funding'"
    ).fetchone()[0]
    assert n >= 1
    assert coll.calls >= 1


@pytest.mark.asyncio
async def test_producer_survives_collector_error(memdb: sqlite3.Connection) -> None:
    """A raising collector is caught (log + keep last cache); loop keeps running."""
    cache = AltDataCache()
    state = ProdLoopState()
    stop_evt = asyncio.Event()
    boom = _ExplodingCollector()
    good = _StubCollector("crypto_fg", {"value": 50})
    task = asyncio.create_task(
        _altdata_producer(
            memdb, cache=cache, state=state, stop_evt=stop_evt,
            collectors=[boom, good], poll_sec=0.01,
        )
    )
    await asyncio.sleep(0.05)
    stop_evt.set()
    await asyncio.wait_for(task, timeout=2.0)
    # The exploding collector did not crash the loop; the good one still ran.
    assert boom.calls >= 1
    assert good.calls >= 1
    assert cache.get("crypto_fg", now_ts=time.time()) is not None
    # The exploding source contributed NO cache entry (graceful, no throttle).
    assert cache.get("boom", now_ts=time.time()) is None


@pytest.mark.asyncio
async def test_producer_respects_per_source_ttl_cadence(memdb: sqlite3.Connection) -> None:
    """A collector is not re-fetched before its own ttl_sec elapses."""
    cache = AltDataCache()
    state = ProdLoopState()
    stop_evt = asyncio.Event()
    slow = _StubCollector("fred_macro", {"vix": 15.0}, asset_class="forex")
    slow.ttl_sec = 100  # far longer than the test window
    task = asyncio.create_task(
        _altdata_producer(
            memdb, cache=cache, state=state, stop_evt=stop_evt,
            collectors=[slow], poll_sec=0.01,
        )
    )
    await asyncio.sleep(0.06)
    stop_evt.set()
    await asyncio.wait_for(task, timeout=2.0)
    # Despite many poll iterations, the long-TTL source fetched only once.
    assert slow.calls == 1


# ---------------------------------------------------------------------------
# Negative guard — NO action path introduced on the wire
# ---------------------------------------------------------------------------


def test_wire_writes_no_learner_or_risk_state(memdb: sqlite3.Connection) -> None:
    """compute_and_flip_regime with evidence writes ONLY regime_state — no
    learner_blocks / strategy_risk_state / positions rows."""
    cache = _StubCache({
        "crypto_fg": {"value": 12},
        "okx_funding": {"BTC-USDT-SWAP": {"fundingRate": -0.002}},
    })
    import polaris.scripts._production_layers as pl

    orig = pl.compute_real_regime
    pl.compute_real_regime = lambda _bars: "chop"  # type: ignore[assignment]
    try:
        for ts in (1000, 1060):
            compute_and_flip_regime(
                memdb, venue="okx", underlying_group_id="crypto:BTC",
                bars=_bars_series_chop(), now_ts=ts, altdata_cache=cache,
            )
    finally:
        pl.compute_real_regime = orig  # type: ignore[assignment]
    assert memdb.execute("SELECT COUNT(*) FROM learner_blocks").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM strategy_risk_state").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# local helpers
# ---------------------------------------------------------------------------


def _bars_series_chop() -> list[Any]:
    from tests.test_production_paper_loop import _bars_series

    return _bars_series(n=120, base=60_000.0, drift=0.0)
