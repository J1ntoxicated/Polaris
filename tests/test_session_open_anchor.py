"""Wave 1 — session_open anchors to the TRUE session-start bar, not bar_views[-60].

DEMO/PAPER. session_breakout runs on a 5m feed; ``bar_views[-60]`` is ~5h stale
(the code was written for a 1m feed where 60 bars ≈ 1h). The open±1.5×ATR
breakout band must be measured from the ACTUAL session open (the first bar at/
after the most-recent UTC session-open hour 00/07/13). This is measurement
correctness — no throttle (atr_mult=1.5 is UNCHANGED).
"""

from __future__ import annotations

from polaris.core.data.schema import Bar
from polaris.scripts._production_indicators import (
    build_real_market_view,
    session_anchor_index,
)
from polaris.strategies.base import BarView

SESSION_HOUR = 13  # UTC NY open
BAR_SEC = 300  # 5m feed


def _bar(ts: int, price: float) -> Bar:
    return Bar(
        instrument_id="capital:US500", underlying_group_id="idx_us500",
        venue="capital", symbol="US500", bar_interval="5m", ts=ts,
        open=price, high=price + 1.0, low=price - 1.0, close=price,
        volume=1000.0,
    )


def _bv(ts: int, price: float) -> BarView:
    return BarView(
        ts=ts, open=price, high=price + 1.0, low=price - 1.0, close=price,
        volume=1000.0,
    )


def _five_min_session_bars(open_price: float, n_after_open: int) -> list[Bar]:
    """Bars: 1h of PRE-session history then the session-open bar + ``n_after_open``.

    The session-open bar sits exactly on the UTC hour-13 boundary. Pre-session
    bars are at a clearly different price so a wrong anchor is detectable.
    """
    session_open_ts = SESSION_HOUR * 3600  # 13:00:00 UTC on day 0
    bars: list[Bar] = []
    # 12 pre-session 5m bars (1h) at price 50.0 — must NOT be the anchor.
    for i in range(12, 0, -1):
        bars.append(_bar(session_open_ts - i * BAR_SEC, 50.0))
    # The session-open bar (the TRUE anchor) opens at ``open_price``.
    bars.append(_bar(session_open_ts, open_price))
    # Bars after the open, rising.
    for i in range(1, n_after_open + 1):
        bars.append(_bar(session_open_ts + i * BAR_SEC, open_price + i))
    return bars


def test_session_anchor_index_is_session_open_bar() -> None:
    bvs = [
        _bv(b.ts, b.open) for b in _five_min_session_bars(open_price=100.0, n_after_open=4)
    ]
    now_ts = SESSION_HOUR * 3600 + 5 * BAR_SEC  # inside the open window
    idx = session_anchor_index(bvs, now_ts=now_ts)
    assert idx is not None
    # The anchor is the session-open bar (index 12 here), NOT bar_views[-60].
    assert bvs[idx].ts == SESSION_HOUR * 3600
    assert bvs[idx].open == 100.0


def test_market_view_session_open_price_is_true_open() -> None:
    bars = _five_min_session_bars(open_price=100.0, n_after_open=4)
    mv = build_real_market_view(
        venue="capital", symbol="US500", timeframe="5m", bars=bars,
        spread_bps=2.0, session_open_window=True, asset_class="index",
    )
    # The anchored open is the TRUE session-open bar's open (100.0), not the
    # stale bar_views[-60].open (which on this short window would be a
    # PRE-session bar at 50.0).
    assert mv.session_open_price == 100.0
    assert mv.session_open_ts == SESSION_HOUR * 3600


def test_no_session_open_window_leaves_anchor_none() -> None:
    bars = _five_min_session_bars(open_price=100.0, n_after_open=4)
    mv = build_real_market_view(
        venue="capital", symbol="US500", timeframe="5m", bars=bars,
        spread_bps=2.0, session_open_window=False, asset_class="index",
    )
    assert mv.session_open_price is None
    assert mv.session_open_ts is None
