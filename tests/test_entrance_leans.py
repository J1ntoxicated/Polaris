"""Entrance-judge lean wiring — pure per-instrument lean builders.

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack ban · in-loop GPT=0.

The audit (``code_review_2026-06-24`` / dead-code sweep ``w4wb7123o``) found the
EntranceJudge 5-lens design wired only 2 lenses in production (liquidity + ATR);
``technical`` / ``regime`` / ``altdata`` were never fed → permanently neutral. The
three pure builders here turn data the loop already holds into per-``instrument_id``
signed leans ∈ [-1, +1] (signal-only — they feed ``opportunity_score`` rank, NEVER
a sizing multiplier). These tests prove each builder is pure, signed, and degrades
to neutral (omitted entry) on absent/thin data — flow_not_block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from polaris.core.probes.entrance_leans import (
    build_altdata_lean,
    build_regime_lean,
    build_technical_lean,
)
from polaris.core.universe.schema import UniverseInstrument


def _ins(
    symbol: str = "BTC-USDT",
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    group: str | None = None,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=group if group is not None else f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=1e6,
    )


@dataclass
class _FakeTick:
    mid: float


class _FakeWriter:
    """Minimal quote-writer stub exposing ``feature_window``."""

    def __init__(self, windows: dict[str, list[_FakeTick]]) -> None:
        self._windows = windows

    def feature_window(self, instrument_id: str) -> list[_FakeTick]:
        return self._windows.get(instrument_id, [])


# --------------------------------------------------------------------------
# technical_lean — signed mid-drift over the live tick window → tanh squash
# --------------------------------------------------------------------------


def test_technical_lean_rising_window_positive() -> None:
    # A monotonically rising window → positive drift → positive signed lean.
    writer = _FakeWriter(
        {"okx:BTC-USDT": [_FakeTick(100.0), _FakeTick(102.0), _FakeTick(105.0)]}
    )
    out = build_technical_lean([_ins("BTC-USDT")], writer)
    assert out["okx:BTC-USDT"] > 0.0
    assert -1.0 <= out["okx:BTC-USDT"] <= 1.0


def test_technical_lean_falling_window_negative() -> None:
    writer = _FakeWriter(
        {"okx:BTC-USDT": [_FakeTick(105.0), _FakeTick(101.0), _FakeTick(98.0)]}
    )
    out = build_technical_lean([_ins("BTC-USDT")], writer)
    assert out["okx:BTC-USDT"] < 0.0


def test_technical_lean_empty_window_omitted_neutral() -> None:
    # Empty / single-tick window → no entry (judge treats absent as neutral 0).
    writer = _FakeWriter({"okx:BTC-USDT": [_FakeTick(100.0)]})
    out = build_technical_lean(
        [_ins("BTC-USDT"), _ins("ETH-USDT")], writer
    )
    assert "okx:BTC-USDT" not in out
    assert "okx:ETH-USDT" not in out


def test_technical_lean_none_writer_empty_map() -> None:
    assert build_technical_lean([_ins("BTC-USDT")], None) == {}


def test_technical_lean_signed_and_bounded() -> None:
    # A huge move still clamps into [-1, +1] (tanh squash, never unbounded).
    writer = _FakeWriter(
        {"okx:BTC-USDT": [_FakeTick(1.0), _FakeTick(1000.0)]}
    )
    out = build_technical_lean([_ins("BTC-USDT")], writer)
    assert out["okx:BTC-USDT"] == math.tanh(999.0)
    assert out["okx:BTC-USDT"] <= 1.0


# --------------------------------------------------------------------------
# regime_lean — directional bias from the regime_state SSOT (signal-family-free)
# --------------------------------------------------------------------------


def test_regime_lean_bull_positive_bear_negative() -> None:
    regimes = {("okx", "crypto:BTC"): "bull_trend", ("okx", "crypto:ETH"): "bear_trend"}

    def reader(venue: str, group: str) -> str | None:
        return regimes.get((venue, group))

    universe = [
        _ins("BTC-USDT", group="crypto:BTC"),
        _ins("ETH-USDT", group="crypto:ETH"),
    ]
    out = build_regime_lean(universe, reader)
    assert out["okx:BTC-USDT"] == 1.0
    assert out["okx:ETH-USDT"] == -1.0


def test_regime_lean_chop_crisis_unknown_omitted() -> None:
    # chop / crisis / unknown / missing → direction-neutral → omitted (neutral 0).
    def reader(venue: str, group: str) -> str | None:
        return {"crypto:A": "chop", "crypto:B": "crisis"}.get(group)

    universe = [
        _ins("A-USDT", group="crypto:A"),
        _ins("B-USDT", group="crypto:B"),
        _ins("C-USDT", group="crypto:C"),  # no regime row at all
    ]
    out = build_regime_lean(universe, reader)
    assert out == {}


def test_regime_lean_groups_share_one_read() -> None:
    # Two instruments sharing one underlying_group_id resolve to the same lean
    # and the reader is consulted once per distinct group (cheap).
    calls: list[tuple[str, str]] = []

    def reader(venue: str, group: str) -> str | None:
        calls.append((venue, group))
        return "bull_trend"

    universe = [
        _ins("BTC-USDT", group="crypto:BTC"),
        _ins("BTC-USDC", group="crypto:BTC"),
    ]
    out = build_regime_lean(universe, reader)
    assert out["okx:BTC-USDT"] == 1.0
    assert out["okx:BTC-USDC"] == 1.0
    assert calls == [("okx", "crypto:BTC")]


# --------------------------------------------------------------------------
# altdata_lean — signed tilt from the ALREADY-FUSED fuse_evidence scores
# --------------------------------------------------------------------------


def test_altdata_lean_bull_dominant_positive() -> None:
    def fuser(group: str) -> dict[str, float] | None:
        return {"bull_trend": 4.0, "bear_trend": 1.0}

    out = build_altdata_lean([_ins("BTC-USDT", group="crypto:BTC")], fuser)
    assert out["okx:BTC-USDT"] > 0.0
    assert out["okx:BTC-USDT"] <= 1.0


def test_altdata_lean_bear_dominant_negative() -> None:
    def fuser(group: str) -> dict[str, float] | None:
        return {"bull_trend": 1.0, "bear_trend": 4.0}

    out = build_altdata_lean([_ins("BTC-USDT", group="crypto:BTC")], fuser)
    assert out["okx:BTC-USDT"] < 0.0


def test_altdata_lean_crisis_dominant_negative() -> None:
    # A dominant crisis score is a strong adverse (defensive-context) tilt.
    def fuser(group: str) -> dict[str, float] | None:
        return {"bull_trend": 0.5, "bear_trend": 0.5, "crisis": 5.0}

    out = build_altdata_lean([_ins("BTC-USDT", group="crypto:BTC")], fuser)
    assert out["okx:BTC-USDT"] == -1.0


def test_altdata_lean_no_evidence_omitted() -> None:
    # No fused evidence (keyless / failing collectors) → omitted (neutral 0).
    def fuser(group: str) -> dict[str, float] | None:
        return None

    out = build_altdata_lean([_ins("BTC-USDT", group="crypto:BTC")], fuser)
    assert out == {}


def test_altdata_lean_balanced_scores_neutral_omitted() -> None:
    # Equal bull/bear and no crisis → zero tilt → omitted (no spurious lean).
    def fuser(group: str) -> dict[str, float] | None:
        return {"bull_trend": 2.0, "bear_trend": 2.0}

    out = build_altdata_lean([_ins("BTC-USDT", group="crypto:BTC")], fuser)
    assert out == {}


def test_altdata_lean_one_fuse_per_group() -> None:
    calls: list[str] = []

    def fuser(group: str) -> dict[str, float] | None:
        calls.append(group)
        return {"bull_trend": 3.0, "bear_trend": 1.0}

    universe = [
        _ins("BTC-USDT", group="crypto:BTC"),
        _ins("BTC-USDC", group="crypto:BTC"),
    ]
    out = build_altdata_lean(universe, fuser)
    assert out["okx:BTC-USDT"] == out["okx:BTC-USDC"]
    assert calls == ["crypto:BTC"]
