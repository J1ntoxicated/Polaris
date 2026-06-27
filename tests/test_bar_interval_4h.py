"""② 4H bar_interval — additive timeframe support (OKX-native, data layer only).

DEMO/PAPER only. Aggressive bias preserved (flow_not_block: 4H is additive, no
entry/size/exit gated). 4H is OKX-native (yfinance has no 4h token) so it rides
the exchange fallback path; the only risk is rate-limit storm if cadence is
unmapped — these tests pin the cadence so the #21 storm / #74 STALL cannot recur.

Verifies:
1. ``"4H"`` is in ``BAR_INTERVALS`` (the single hard gate for convert/persist/read).
2. ``okx_candle_to_bar`` accepts a 4H row (no ValueError) → canonical Bar.
3. The four ``_production_bars`` maps carry a 4H entry — CRUCIALLY the fetch
   cadence is 3600s+, NOT the 5s ``.get(tf, 5.0)`` fallback (storm guard).
4. Existing 1m/15m/1H/1D entries are byte-identical (no regression).
"""

from __future__ import annotations

from polaris.core.data.canonical import compute_underlying_group_id, okx_candle_to_bar
from polaris.core.data.schema import BAR_INTERVALS
from polaris.scripts._production_bars import (
    _BAR_STALENESS_BY_INTERVAL,
    _PERIOD_SECONDS_BY_INTERVAL,
    TIMEFRAME_FETCH_CADENCE_SEC,
    current_period_open_ts,
    staleness_threshold_for,
)


def test_4h_in_bar_intervals() -> None:
    """The single hard gate accepts 4H (convert/persist/read all key off this)."""
    assert "4H" in BAR_INTERVALS
    # Existing intervals untouched (additive only).
    for tf in ("1m", "5m", "15m", "1H", "1D"):
        assert tf in BAR_INTERVALS


def test_okx_candle_to_bar_accepts_4h() -> None:
    """A 4H OKX candle row converts to a canonical Bar (was ValueError before)."""
    underlying = compute_underlying_group_id("okx", "BTC-USDT", asset_class="crypto")
    # OKX row: [ts_ms, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    row = ["1719460800000", "100", "110", "95", "105", "1000", "1000", "105000", "1"]
    bar = okx_candle_to_bar(
        row, inst_id="BTC-USDT", bar_interval="4H", underlying_group_id=underlying
    )
    assert bar.bar_interval == "4H"
    assert bar.venue == "okx"
    assert bar.close == 105.0
    assert bar.source == "okx_rest"


def test_4h_fetch_cadence_is_not_storm() -> None:
    """🚨 cadence MUST be explicit 3600s+ — NOT the 5s .get(tf, 5.0) fallback.

    Without an explicit entry, 4H would re-fetch every 5s = 157÷5 ≈ 31 req/s OKX
    storm + tick-engine STALL (#21 / #74). This is the single most important
    guard for 4H safety.
    """
    cadence = TIMEFRAME_FETCH_CADENCE_SEC.get("4H")
    assert cadence is not None, "4H cadence unmapped → 5s storm fallback"
    assert cadence >= 3600.0, f"4H cadence {cadence}s too aggressive (storm risk)"


def test_4h_staleness_threshold_explicit() -> None:
    """4H staleness is a generous multiple of the 4h cadence (not the 36h default)."""
    assert "4H" in _BAR_STALENESS_BY_INTERVAL
    thr = staleness_threshold_for("4H")
    # 4h cadence → 6-8h dead-feed window (between the 1H 6h and the 1D 36h).
    assert 6 * 3600.0 <= thr <= 12 * 3600.0


def test_4h_period_seconds_mapped() -> None:
    """4H period = 14400s so current_period_open_ts floors correctly (no now-fallback)."""
    assert _PERIOD_SECONDS_BY_INTERVAL.get("4H") == 14400
    # 1719460800 is a 4h-aligned epoch (2024-06-27 04:00 UTC); inside the period
    # the open floors to the same boundary.
    inside = 1719460800 + 7200  # +2h, still in the same 4h period
    assert current_period_open_ts("4H", inside) == 1719460800


def test_existing_cadence_unchanged() -> None:
    """No regression: the existing fetch cadences are byte-identical."""
    assert TIMEFRAME_FETCH_CADENCE_SEC["1m"] == 5.0
    assert TIMEFRAME_FETCH_CADENCE_SEC["15m"] == 60.0
    assert TIMEFRAME_FETCH_CADENCE_SEC["1H"] == 300.0
    assert TIMEFRAME_FETCH_CADENCE_SEC["1D"] == 3600.0
