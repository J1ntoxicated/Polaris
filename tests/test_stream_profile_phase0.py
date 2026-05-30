"""Gate architecture Phase 0 — StreamProfile structural-enabler parity tests.

Option A (Jin CONFIRMED): the UNIFIED 8-gate pipeline stays unified; A/B/C vary
via a SINGLE first-class ``StreamProfile`` injected into ``GateContext``, NOT via
scattered ``if product_class ==`` branches or forked gates.

Phase 0 is a STRUCTURAL ENABLER ONLY — it MUST be behavior-100%-identical:
threading the profile through must NOT change any gate decision or payload byte.
These tests assert exactly that:

1. ``resolve_stream_profile`` projects the StreamConfig fields the pipeline uses.
2. The reserved hook fields are EMPTY in P0 (P1+ enrichment lands later).
3. Each of the 4 content-bearing payload builders produces a BYTE-IDENTICAL
   output for A(okx)/B(capital)/C(alpaca) WITH vs WITHOUT the profile threaded.
4. ``GateContext`` accepts the profile (optional, default None — back-compat).
"""

from __future__ import annotations

from pathlib import Path

from polaris.core.pipeline import (
    GateContext,
    SignalLifecycle,
    build_exit_payload,
    build_monitor_payload,
    build_validator_payload,
    build_watcher_payload,
)
from polaris.core.streams import (
    StreamProfile,
    resolve_stream,
    resolve_stream_profile,
)
from polaris.storage.schema import init_db
from polaris.strategies.base import RawSignal

# venue -> (symbol, instrument_id, strategy_id, asset_class)
_VENUES = {
    "okx": ("BTC-USDT", "okx:BTC-USDT", "volume_burst"),
    "capital": ("XAUUSD", "capital:XAUUSD", "xau_indices_trend"),
    "alpaca": ("AAPL", "alpaca:AAPL", "equity_tsmom"),
}


def _raw_signal(strategy_id: str, symbol: str) -> RawSignal:
    return RawSignal(
        signal_id="sig_phase0",
        strategy_id=strategy_id,
        symbol=symbol,
        side="long",
        strength=1.2,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="phase0",
        correlation_group="grp",
    )


# ---------------------------------------------------------------------------
# 1/2 — StreamProfile resolution + empty hooks (P0)
# ---------------------------------------------------------------------------


def test_resolve_stream_profile_mirrors_streamconfig() -> None:
    """The P0 profile projects exactly the StreamConfig fields the gates use."""
    for venue in _VENUES:
        cfg = resolve_stream(venue)
        prof = resolve_stream_profile(venue)
        assert isinstance(prof, StreamProfile)
        assert prof.stream_id == cfg.stream_id
        assert prof.venue == cfg.venue
        assert prof.product_class == cfg.product_class
        assert prof.asset_classes == cfg.asset_classes
        assert prof.session_calendar == cfg.session_calendar
        assert prof.cost_model == cfg.cost_model
        assert prof.allow_short == cfg.allow_short
        assert prof.leverage_source == cfg.sizing_profile.leverage_source
        assert prof.external_reject_codes == cfg.external_reject_codes


def test_stream_profile_hooks_seam() -> None:
    """``regime_evidence`` stays the empty P1+ regime seam. ``guard_hooks`` is
    now FILLED (gate architecture Phase 2) with the per-stream G4 pre-entry guard
    token — this is the documented enrichment point, not a P0 decision input."""
    for venue in _VENUES:
        prof = resolve_stream_profile(venue)
        assert prof.regime_evidence == frozenset()
        assert prof.guard_hooks  # Phase 2: populated per product_class
        assert len(prof.guard_hooks) == 1


def test_stream_profile_is_frozen() -> None:
    prof = resolve_stream_profile("okx")
    try:
        prof.venue = "capital"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:  # pragma: no cover - frozen dataclass must raise
        raise AssertionError("StreamProfile must be frozen")


def test_resolve_stream_profile_propagates_resolve_errors() -> None:
    import pytest

    with pytest.raises(KeyError):
        resolve_stream_profile("kraken")
    with pytest.raises(ValueError):
        resolve_stream_profile("okx", "cfd")


# ---------------------------------------------------------------------------
# 3 — payload-builder byte-identity WITH vs WITHOUT the profile (A/B/C)
# ---------------------------------------------------------------------------


def test_validator_payload_identical_with_without_profile(tmp_path: Path) -> None:
    """G3 validator payload is byte-identical with vs without the profile for
    every stream (A/B/C) — P0 reads it for NO output."""
    conn = init_db(tmp_path / "polaris.sqlite")
    try:
        for venue, (symbol, instrument_id, strategy_id) in _VENUES.items():
            sig = _raw_signal(strategy_id, symbol)
            prof = resolve_stream_profile(venue)
            base = build_validator_payload(
                raw_signal=sig, venue=venue, symbol=symbol,
                instrument_id=instrument_id, regime="bull_trend",
                conn=conn, now_ts=1_780_000_000,
            )
            with_prof = build_validator_payload(
                raw_signal=sig, venue=venue, symbol=symbol,
                instrument_id=instrument_id, regime="bull_trend",
                conn=conn, now_ts=1_780_000_000, stream_profile=prof,
            )
            assert with_prof == base, f"validator parity broke for {venue}"
    finally:
        conn.close()


def test_watcher_payload_identical_with_without_profile() -> None:
    """G4 watcher payload (incl. the T14 net-edge keys) is byte-identical with
    vs without the profile for every stream."""
    for venue in _VENUES:
        prof = resolve_stream_profile(venue)
        kwargs = dict(
            spread_bps=1.5, baseline_p50_spread_bps=2.0,
            listing_age_hours=8760.0, recent_reject_in_6h=False,
            session_open_shock_window=False, venue=venue,
            signal_strength=1.2, atr_pct=0.01,
        )
        base = build_watcher_payload(**kwargs)  # type: ignore[arg-type]
        with_prof = build_watcher_payload(**kwargs, stream_profile=prof)  # type: ignore[arg-type]
        assert with_prof == base, f"watcher parity broke for {venue}"


def test_monitor_payload_identical_with_without_profile() -> None:
    for venue in _VENUES:
        prof = resolve_stream_profile(venue)
        position = {"venue": venue, "symbol": "X", "side": "long", "qty": 1.0}
        base = build_monitor_payload(
            position=position, unrealized_pnl_r=0.5, max_loss_r=1.0,
        )
        with_prof = build_monitor_payload(
            position=position, unrealized_pnl_r=0.5, max_loss_r=1.0,
            stream_profile=prof,
        )
        assert with_prof == base, f"monitor parity broke for {venue}"


def test_exit_payload_identical_with_without_profile() -> None:
    for venue in _VENUES:
        prof = resolve_stream_profile(venue)
        kwargs = dict(
            side="long", current_stop_price=99.0, proposed_stop_price=98.5,
            entry_price=100.0, unrealized_pnl_r=0.5, max_loss_r=1.0,
            overrides_used=0, seconds_since_last_override=60,
            regime="bull_trend", atr_pct=0.01, held_seconds=120,
        )
        base = build_exit_payload(**kwargs)  # type: ignore[arg-type]
        with_prof = build_exit_payload(**kwargs, stream_profile=prof)  # type: ignore[arg-type]
        assert with_prof == base, f"exit parity broke for {venue}"


# ---------------------------------------------------------------------------
# 4 — GateContext accepts the profile (optional, default None)
# ---------------------------------------------------------------------------


def test_gate_context_default_profile_is_none() -> None:
    ctx = GateContext(
        run_id="r", signal_id="s", position_id=None, gate_id=1,
        venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        payload={}, started_ts=0, state=SignalLifecycle.RAW,
    )
    assert ctx.stream_profile is None


def test_gate_context_carries_stream_profile() -> None:
    prof = resolve_stream_profile("capital")
    ctx = GateContext(
        run_id="r", signal_id="s", position_id=None, gate_id=1,
        venue="capital", symbol="XAUUSD", strategy_id="xau_indices_trend",
        payload={}, started_ts=0, state=SignalLifecycle.RAW,
        stream_profile=prof,
    )
    assert ctx.stream_profile is prof
    assert ctx.stream_profile.product_class == "cfd"
