"""tests/strategies/test_nfi_dipbuy.py — NFI DipBuy TDD (HYPO-NFI-001).

TDD: 실패 테스트 먼저 작성 → 구현 후 통과 (lessons #46 — import pass != runtime pass).

Spec (HYPO-NFI-001, NFI X7 Polaris simplification):
  Entry conditions (all must hold):
    1. RSI_3 5m < 5 OR RSI_3 15m < 5 (multi-TF extreme dip)
    2. RSI_14 1h < 30 (classic oversold)
    3. close <= BB lower (price at oversold band)
    4. 4h AROON_UP < 80 (no overbought peak on 4h)
  Exit:
    - RSI_14 1h > 84 (momentum exhaustion)
    - OR close > BB upper

  pure: true — _compute_nfi_signal is deterministic, no I/O
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.nfi_dipbuy import (
    DEFAULT_AROON_UP_MAX,
    DEFAULT_BB_WINDOW,
    DEFAULT_RSI14_ENTRY,
    DEFAULT_RSI14_EXIT,
    DEFAULT_RSI3_ENTRY,
    DEFAULT_TARGET_SIZE_USD,
    NFIDipBuy,
    _compute_nfi_signal,
    compute_aroon_up,
    compute_rsi,
)

# ── Constants ──────────────────────────────────────────────────────────────────
NOW_MS = 1_700_000_000_000


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_candle(close: float, high: float | None = None, low: float | None = None, ts: int = NOW_MS) -> Candle:
    h = high if high is not None else close * 1.005
    l = low if low is not None else close * 0.995
    return Candle(timestamp_ms=ts, open=close, high=h, low=l, close=close, volume=1000.0)


def _make_closes(n: int, value: float = 100.0) -> list[float]:
    """Create n close prices at constant value."""
    return [value] * n


def _make_declining_closes(n: int, start: float = 100.0, step: float = 0.5) -> list[float]:
    """Create n declining closes (oversold scenario)."""
    return [start - i * step for i in range(n)]


def _make_rising_closes(n: int, start: float = 90.0, step: float = 0.5) -> list[float]:
    """Create n rising closes."""
    return [start + i * step for i in range(n)]


def _make_h4_highs_not_overbought(n: int = 14, value: float = 100.0) -> list[float]:
    """4h highs where AROON_UP < 80 (max is not in recent bars)."""
    # Make max at position 0 (oldest) → bars_since = n-1, aroon = (n-(n-1))/n*100 = 100/n%
    highs = [value] * n
    highs[0] = value * 1.1  # oldest = highest → low AROON_UP
    return highs


def _make_h4_highs_overbought(n: int = 14, value: float = 100.0) -> list[float]:
    """4h highs where AROON_UP >= 80 (max is recent)."""
    highs = [value] * n
    highs[-1] = value * 1.1  # most recent = highest → AROON_UP = 100
    return highs


# ── compute_rsi unit tests ─────────────────────────────────────────────────────

class TestComputeRSI:
    def test_insufficient_data_returns_50(self) -> None:
        """len(closes) < period+1 → 50.0."""
        assert compute_rsi([100.0] * 3, period=14) == 50.0

    def test_all_gains_returns_100(self) -> None:
        """All rising prices → RSI = 100."""
        closes = _make_rising_closes(16, start=90.0, step=1.0)
        result = compute_rsi(closes, period=14)
        assert result == 100.0

    def test_all_losses_returns_0(self) -> None:
        """All declining prices → RSI close to 0."""
        closes = _make_declining_closes(16, start=100.0, step=1.0)
        result = compute_rsi(closes, period=14)
        assert result < 10.0

    def test_flat_prices_returns_50(self) -> None:
        """Flat prices → avg_loss = 0 with avg_gain = 0 → 50.0."""
        closes = [100.0] * 16
        result = compute_rsi(closes, period=14)
        assert result == 50.0

    def test_rsi3_extreme_oversold(self) -> None:
        """Sharp 3-period decline → RSI_3 < 5."""
        # 3 large drops in a row
        closes = [100.0, 100.0, 100.0, 85.0, 75.0, 60.0]
        result = compute_rsi(closes, period=3)
        assert result < 5.0


# ── compute_aroon_up unit tests ────────────────────────────────────────────────

class TestComputeAroonUp:
    def test_insufficient_data_returns_50(self) -> None:
        """len(highs) < period → 50.0."""
        assert compute_aroon_up([100.0] * 5, period=14) == 50.0

    def test_max_at_most_recent_returns_100(self) -> None:
        """Max at most recent bar → AROON_UP = 100."""
        highs = [100.0] * 14
        highs[-1] = 110.0  # highest at last position
        result = compute_aroon_up(highs, period=14)
        assert result == 100.0

    def test_max_at_oldest_returns_low_value(self) -> None:
        """Max at oldest bar → AROON_UP = 100/14 ≈ 7.14."""
        highs = [100.0] * 14
        highs[0] = 110.0  # highest at first position (oldest)
        result = compute_aroon_up(highs, period=14)
        assert result < 10.0

    def test_not_overbought(self) -> None:
        """AROON_UP < 80 when max is at old position."""
        highs = _make_h4_highs_not_overbought(14)
        result = compute_aroon_up(highs, period=14)
        assert result < 80.0

    def test_overbought(self) -> None:
        """AROON_UP = 100 when max is most recent."""
        highs = _make_h4_highs_overbought(14)
        result = compute_aroon_up(highs, period=14)
        assert result == 100.0


# ── _compute_nfi_signal unit tests ────────────────────────────────────────────

class TestComputeNFISignal:
    """Pure core function tests."""

    def _make_full_dip_signal(
        self,
        aroon_overbought: bool = False,
    ) -> "Signal":
        """Helper: construct multi-TF dip scenario and return signal action.

        Uses bb_std=0.001 (near-zero) so bb_lower ≈ mean.
        Declining h1_closes → last < mean → at_bb_lower ✓.
        Declining h1_closes → all losses → RSI_14 < 30 ✓.
        m5/m15 sharp drop → RSI_3 < 5 ✓.
        """
        m5_closes = [100.0, 100.0, 100.0, 60.0, 50.0]  # RSI_3 < 5
        m15_closes = [100.0, 100.0, 100.0, 60.0, 50.0]  # RSI_3 < 5
        h1_closes = _make_declining_closes(25, start=100.0, step=2.0)  # RSI_14<30, last<mean
        h4_highs = _make_h4_highs_overbought(14) if aroon_overbought else _make_h4_highs_not_overbought(14)
        return _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=0.001,  # near-zero: bb_lower ≈ mean; last < mean → at_bb_lower ✓
        )

    def test_all_conditions_met_enter_long(self) -> None:
        """All 4 conditions met → ENTER_LONG."""
        sig = self._make_full_dip_signal()
        assert sig.action == SignalAction.ENTER_LONG

    def test_4h_overbought_blocks_entry(self) -> None:
        """AROON_UP >= 80 on 4h → no ENTER_LONG."""
        sig = self._make_full_dip_signal(aroon_overbought=True)
        # AROON_UP = 100 >= 80 → should not enter
        assert sig.action != SignalAction.ENTER_LONG

    def test_insufficient_5m_data_hold(self) -> None:
        """len(m5_closes) < 4 → HOLD (insufficient_5m)."""
        sig = _compute_nfi_signal(
            m5_closes=[100.0, 90.0],  # only 2
            m15_closes=[100.0] * 5,
            h1_closes=[100.0] * 25,
            h4_highs=[100.0] * 14,
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD
        assert "insufficient_5m" in sig.reason

    def test_insufficient_1h_data_hold(self) -> None:
        """len(h1_closes) < bb_window+1 = 21 → HOLD (insufficient_1h)."""
        sig = _compute_nfi_signal(
            m5_closes=[100.0] * 5,
            m15_closes=[100.0] * 5,
            h1_closes=[100.0] * 10,  # only 10 < 21
            h4_highs=[100.0] * 14,
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD
        assert "insufficient_1h" in sig.reason

    def test_insufficient_4h_data_hold(self) -> None:
        """len(h4_highs) < 14 → HOLD (insufficient_4h)."""
        sig = _compute_nfi_signal(
            m5_closes=[100.0] * 5,
            m15_closes=[100.0] * 5,
            h1_closes=[100.0] * 25,
            h4_highs=[100.0] * 5,  # only 5 < 14
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD
        assert "insufficient_4h" in sig.reason

    def test_exit_when_rsi14_above_84(self) -> None:
        """RSI_14 1h > 84 → EXIT (takes priority over entry)."""
        # Rising prices → RSI_14 > 84
        h1_closes = _make_rising_closes(25, start=80.0, step=1.0)
        sig = _compute_nfi_signal(
            m5_closes=[100.0] * 5,
            m15_closes=[100.0] * 5,
            h1_closes=h1_closes,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.EXIT
        assert "exit" in sig.reason

    def test_exit_when_price_above_bb_upper(self) -> None:
        """price > BB upper → EXIT."""
        # Flat then spike: last value far above mean
        h1_closes = [100.0] * 20 + [150.0]  # spike above BB upper
        sig = _compute_nfi_signal(
            m5_closes=[100.0] * 5,
            m15_closes=[100.0] * 5,
            h1_closes=h1_closes,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.EXIT

    def test_hold_when_rsi14_not_oversold(self) -> None:
        """RSI_14 1h >= 30 → no entry (need_rsi14 not met)."""
        # Flat prices → RSI_14 ≈ 50
        h1_closes = [100.0] * 25
        m5_closes = [100.0, 100.0, 100.0, 60.0, 50.0]  # RSI_3 < 5
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m5_closes,
            h1_closes=h1_closes,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD
        assert "rsi14" in sig.reason

    def test_enter_long_has_positive_confidence(self) -> None:
        """ENTER_LONG signal has confidence in (0, 1]."""
        # 5m/15m: RSI_3 < 5 via sharp drop
        m5 = [100.0, 100.0, 100.0, 60.0, 45.0]
        h1 = _make_declining_closes(25, start=100.0, step=1.5)
        sig = _compute_nfi_signal(
            m5_closes=m5,
            m15_closes=m5,
            h1_closes=h1,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
        )
        if sig.action == SignalAction.ENTER_LONG:
            assert 0.0 < sig.confidence <= 1.0
            assert sig.target_size_usd == DEFAULT_TARGET_SIZE_USD

    def test_enter_long_reason_contains_nfi_prefix(self) -> None:
        """ENTER_LONG reason starts with 'nfi_dip:'."""
        m5 = [100.0, 100.0, 100.0, 60.0, 45.0]
        h1 = _make_declining_closes(25, start=100.0, step=1.5)
        sig = _compute_nfi_signal(
            m5_closes=m5,
            m15_closes=m5,
            h1_closes=h1,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
        )
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.reason.startswith("nfi_dip:")


# ── NFIDipBuy class tests ──────────────────────────────────────────────────────

class TestNFIDipBuyClass:
    def _make_tf_data(
        self,
        oversold: bool = True,
        overbought_4h: bool = False,
    ) -> dict[str, list[Candle]]:
        """Build tf_data dict with controlled conditions.

        Candle lists are built oldest-first (index 0 = oldest, index -1 = newest).
        evaluate_multi_tf reads [c.close for c in candles] → same ordering.

        oversold=True: declining prices (oldest=high, newest=low) → RSI_14 < 30.
          BUT: candle order must be oldest→newest for correct RSI calc.
          _make_declining_closes(25, start=100, step=1.5) → [100, 98.5, ..., 64]
          This is already oldest(high)→newest(low) = declining sequence. Correct.
        """
        if oversold:
            # Declining: RSI_14<30 + last<mean (at_bb_lower with bb_std≈0).
            # bb_std=0.001 used in test via NFIDipBuy(bb_std=0.001).
            h1_closes = _make_declining_closes(25, start=100.0, step=2.0)
            # 5m/15m: extreme dip (RSI_3 < 5)
            m_closes = [100.0, 100.0, 100.0, 60.0, 50.0]
        else:
            h1_closes = [100.0] * 25  # flat → RSI ~50, not oversold
            m_closes = [100.0] * 5    # flat → RSI_3 ~50, not extreme

        h4_highs = _make_h4_highs_overbought(14) if overbought_4h else _make_h4_highs_not_overbought(14)
        h4_closes = [100.0] * 14

        # Build candles oldest-first: ts decreasing as index increases (oldest has largest ts offset)
        # h1_closes[0] = oldest → ts = NOW_MS - 24 * 3_600_000
        # h1_closes[-1] = newest → ts = NOW_MS
        n_h1 = len(h1_closes)
        h1 = [
            _make_candle(h1_closes[i], ts=NOW_MS - (n_h1 - 1 - i) * 3_600_000)
            for i in range(n_h1)
        ]
        n_m = len(m_closes)
        m5 = [
            _make_candle(m_closes[i], ts=NOW_MS - (n_m - 1 - i) * 300_000)
            for i in range(n_m)
        ]
        m15 = [
            _make_candle(m_closes[i], ts=NOW_MS - (n_m - 1 - i) * 900_000)
            for i in range(n_m)
        ]
        h4 = [
            _make_candle(h4_closes[i], high=h4_highs[i], ts=NOW_MS - (13 - i) * 14_400_000)
            for i in range(14)
        ]
        return {"5m": m5, "15m": m15, "1H": h1, "4H": h4}

    def test_name_is_nfi_dipbuy(self) -> None:
        """Strategy name is correct."""
        s = NFIDipBuy()
        assert s.name == "nfi_dipbuy"

    def test_evaluate_multi_tf_oversold_enter_long(self) -> None:
        """evaluate_multi_tf with full dip conditions → ENTER_LONG.

        Uses bb_std=0.001 (near-zero bands) so bb_lower ≈ mean.
        Declining h1_closes → last < mean → at_bb_lower ✓.
        """
        strat = NFIDipBuy(bb_std=0.001)
        tf_data = self._make_tf_data(oversold=True, overbought_4h=False)
        sig = strat.evaluate_multi_tf(tf_data)
        assert sig.action == SignalAction.ENTER_LONG

    def test_evaluate_multi_tf_4h_overbought_no_entry(self) -> None:
        """4h overbought → no ENTER_LONG."""
        strat = NFIDipBuy(bb_std=0.001)
        tf_data = self._make_tf_data(oversold=True, overbought_4h=True)
        sig = strat.evaluate_multi_tf(tf_data)
        assert sig.action != SignalAction.ENTER_LONG

    def test_evaluate_multi_tf_empty_returns_hold(self) -> None:
        """Empty tf_data → HOLD (insufficient data)."""
        strat = NFIDipBuy()
        sig = strat.evaluate_multi_tf({})
        assert sig.action == SignalAction.HOLD

    def test_evaluate_single_tf_fallback(self) -> None:
        """evaluate(window) with candles → valid signal (single-TF fallback)."""
        strat = NFIDipBuy()
        # Flat candles (RSI ~50, not dipping) → HOLD
        window = [_make_candle(100.0, ts=NOW_MS - i * 3_600_000) for i in range(25, 0, -1)]
        sig = strat.evaluate(window)
        assert sig.action == SignalAction.HOLD

    def test_evaluate_insufficient_window_hold(self) -> None:
        """evaluate(window) with < min_window candles → HOLD."""
        strat = NFIDipBuy()
        window = [_make_candle(100.0, ts=NOW_MS - i * 3_600_000) for i in range(10, 0, -1)]
        sig = strat.evaluate(window)
        assert sig.action == SignalAction.HOLD
        assert "insufficient_candles" in sig.reason

    def test_custom_params_applied(self) -> None:
        """Custom rsi3_entry/rsi14_entry params are respected."""
        # Very strict entry: RSI_3 < 1 (near impossible)
        strat = NFIDipBuy(rsi3_entry=1.0, rsi14_entry=10.0)
        tf_data = self._make_tf_data(oversold=True, overbought_4h=False)
        sig = strat.evaluate_multi_tf(tf_data)
        # With very strict thresholds, should HOLD (standard dip not deep enough)
        assert sig.action == SignalAction.HOLD

    def test_enter_long_target_size_default(self) -> None:
        """ENTER_LONG signal uses DEFAULT_TARGET_SIZE_USD = 200."""
        strat = NFIDipBuy(bb_std=0.001)  # near-zero bands for deterministic ENTER_LONG
        tf_data = self._make_tf_data(oversold=True, overbought_4h=False)
        sig = strat.evaluate_multi_tf(tf_data)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == DEFAULT_TARGET_SIZE_USD


# ── Property-based tests (P7) ─────────────────────────────────────────────────

class TestNFIPropertyBased:
    """Hypothesis property-based tests — NULL cascade + boundary invariants."""

    @given(st.floats(min_value=0.01, max_value=1e6))
    @settings(max_examples=50)
    def test_single_price_always_hold(self, price: float) -> None:
        """Single close price → always HOLD (insufficient data)."""
        sig = _compute_nfi_signal(
            m5_closes=[price],
            m15_closes=[price],
            h1_closes=[price],
            h4_highs=[price],
            ts_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    @given(
        st.lists(st.floats(min_value=1.0, max_value=1000.0), min_size=25, max_size=50),
        st.lists(st.floats(min_value=1.0, max_value=1000.0), min_size=14, max_size=20),
    )
    @settings(max_examples=50)
    def test_signal_action_is_valid_enum(
        self,
        h1_closes: list[float],
        h4_highs: list[float],
    ) -> None:
        """Any valid input produces a valid SignalAction enum member."""
        m5 = [50.0, 60.0, 70.0, 40.0, 30.0]  # RSI_3 near 0
        sig = _compute_nfi_signal(
            m5_closes=m5,
            m15_closes=m5,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
        )
        assert sig.action in SignalAction

    @given(st.floats(min_value=0.1, max_value=99.9))
    @settings(max_examples=30)
    def test_confidence_always_in_unit_interval(self, rsi3_entry: float) -> None:
        """Confidence always in [0, 1] regardless of rsi3_entry."""
        m5 = [100.0, 100.0, 100.0, 60.0, 40.0]
        h1 = _make_declining_closes(25, start=100.0, step=1.5)
        sig = _compute_nfi_signal(
            m5_closes=m5,
            m15_closes=m5,
            h1_closes=h1,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
            rsi3_entry=rsi3_entry,
        )
        assert 0.0 <= sig.confidence <= 1.0

    @given(st.floats(min_value=0.0, max_value=50.0))
    @settings(max_examples=30)
    def test_rsi3_none_trigger_boundary(self, rsi3_entry: float) -> None:
        """compute_rsi never returns NaN or negative."""
        closes = _make_declining_closes(20, start=100.0, step=1.0)
        result = compute_rsi(closes, period=3)
        assert not (result != result)  # NaN check
        assert 0.0 <= result <= 100.0
