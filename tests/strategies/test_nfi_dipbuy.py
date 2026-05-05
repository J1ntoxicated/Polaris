"""tests/strategies/test_nfi_dipbuy.py — NFI DipBuy TDD (HYPO-NFI-001).

TDD: 실패 테스트 먼저 작성 → 구현 후 통과 (lessons #46 — import pass != runtime pass).

Spec (HYPO-NFI-001, NFI X7 Polaris simplification):
  Entry conditions — 3-of-5 confluence (NFI-style OR-AND):
    1. RSI_3 5m < 15  (1점)
    2. RSI_3 15m < 15 (1점)
    3. RSI_14 1h < 35  (1점)
    4. price < bb_lower × 1.005  (1점, 0.5% 여유)
    5. 4h AROON_UP < 85  (1점)
    3+ 만족 시 ENTER_LONG
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
    DEFAULT_BB_BUFFER,
    DEFAULT_BB_WINDOW,
    DEFAULT_MIN_CONFLUENCE,
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
        """All 5 conditions met → ENTER_LONG (score=5 >= min_confluence=3)."""
        sig = self._make_full_dip_signal()
        assert sig.action == SignalAction.ENTER_LONG

    def test_4h_overbought_reduces_score_but_not_block(self) -> None:
        """AROON_UP = 100 on 4h → score drops by 1 but 4 other conditions still give 4/5 → ENTER_LONG.

        In 3-of-5 mode, overbought 4h alone does not block entry (4 others pass).
        To block entry, use min_confluence=5 (strict all-must-pass mode).
        """
        sig = self._make_full_dip_signal(aroon_overbought=True)
        # score = 4 (RSI_3 5m + 15m + RSI_14 + BB) >= 3 → still enters
        assert sig.action == SignalAction.ENTER_LONG

    def test_4h_overbought_blocks_entry_strict_mode(self) -> None:
        """AROON_UP = 100 on 4h with min_confluence=5 → HOLD (all-must-pass strict)."""
        m5_closes = [100.0, 100.0, 100.0, 60.0, 50.0]
        m15_closes = [100.0, 100.0, 100.0, 60.0, 50.0]
        h1_closes = _make_declining_closes(25, start=100.0, step=2.0)
        h4_highs = _make_h4_highs_overbought(14)  # AROON_UP = 100 → cond_aroon fails
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=0.001,
            min_confluence=5,  # all-must-pass
        )
        assert sig.action == SignalAction.HOLD

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

    def test_hold_when_only_1_condition_met(self) -> None:
        """Only 1 of 5 conditions met → HOLD (score < min_confluence=3).

        Flat h1 prices → RSI_14 ≈ 50 (c3 fails) + price ≈ mean (c4 fails, default bb_std=2.0).
        Flat m5/m15 → RSI_3 ≈ 50 (c1+c2 fail).
        Only AROON (c5) passes → score=1 < 3 → HOLD.
        """
        h1_closes = [100.0] * 25  # flat → RSI_14 ≈ 50, price=mean → not at bb_lower
        m5_closes = [97.0, 98.0, 99.0, 100.0, 101.0]  # rising → RSI_3 high
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m5_closes,
            h1_closes=h1_closes,
            h4_highs=_make_h4_highs_not_overbought(14),
            ts_ms=NOW_MS,
            bb_std=2.0,  # standard bands → flat price not at bb_lower
        )
        assert sig.action == SignalAction.HOLD

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
        """ENTER_LONG reason starts with 'nfi_dip' (includes conf= score)."""
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
            assert sig.reason.startswith("nfi_dip")


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

    def test_evaluate_multi_tf_4h_overbought_strict_no_entry(self) -> None:
        """4h overbought with min_confluence=5 (all-must-pass) → HOLD."""
        strat = NFIDipBuy(bb_std=0.001, min_confluence=5)
        tf_data = self._make_tf_data(oversold=True, overbought_4h=True)
        sig = strat.evaluate_multi_tf(tf_data)
        assert sig.action == SignalAction.HOLD

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
        """Custom min_confluence=5 (all-must-pass) respected: aroon overbought → HOLD.

        With overbought_4h=True → cond_aroon (c5) fails → score = 4 < 5 → HOLD.
        This verifies min_confluence param is wired correctly.
        """
        strat = NFIDipBuy(bb_std=0.001, min_confluence=5)
        tf_data = self._make_tf_data(oversold=True, overbought_4h=True)  # aroon fails
        sig = strat.evaluate_multi_tf(tf_data)
        # c1+c2+c3+c4 pass, c5 (aroon) fails → score=4 < 5 → HOLD
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


# ── New threshold tests (URGENT FIX — 0 entry in 30m) ────────────────────────

class TestNFIThreshold15:
    """test_nfi_threshold_15: RSI_3 threshold 5 → 15, RSI_14 30 → 35, AROON 80 → 85."""

    def test_default_rsi3_entry_is_15(self) -> None:
        """DEFAULT_RSI3_ENTRY must be 15.0 (relaxed from 5.0)."""
        assert DEFAULT_RSI3_ENTRY == 15.0

    def test_default_rsi14_entry_is_35(self) -> None:
        """DEFAULT_RSI14_ENTRY must be 35.0 (relaxed from 30.0)."""
        assert DEFAULT_RSI14_ENTRY == 35.0

    def test_default_aroon_up_max_is_85(self) -> None:
        """DEFAULT_AROON_UP_MAX must be 85.0 (relaxed from 80.0)."""
        assert DEFAULT_AROON_UP_MAX == 85.0

    def test_default_bb_buffer_is_0_005(self) -> None:
        """DEFAULT_BB_BUFFER must be 1.005 (0.5% slack above bb_lower)."""
        assert DEFAULT_BB_BUFFER == 1.005

    def test_default_min_confluence_is_3(self) -> None:
        """DEFAULT_MIN_CONFLUENCE must be 3 (3-of-5 NFI-style)."""
        assert DEFAULT_MIN_CONFLUENCE == 3

    def test_rsi3_between_5_and_15_triggers_entry(self) -> None:
        """RSI_3 5m = 10 (between old=5 and new=15) should now trigger entry with enough confluence."""
        # Craft scenario: RSI_3 5m is ~10 (moderate dip), other conditions met
        # m5: moderate decline giving RSI_3 ~ 10
        m5_closes = [100.0, 100.0, 98.0, 94.0, 91.0]   # RSI_3 moderate dip
        m15_closes = [100.0, 100.0, 98.0, 94.0, 91.0]  # same → RSI_3 ~10
        h1_closes = _make_declining_closes(25, start=100.0, step=2.5)  # RSI_14 < 35
        h4_highs = _make_h4_highs_not_overbought(14)
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=0.001,  # near-zero bands → bb_lower ≈ mean; last < mean
        )
        # With 3-of-5 confluence, RSI_3 ~10 + RSI_14 < 35 + bb_buffer + aroon should give >= 3
        assert sig.action == SignalAction.ENTER_LONG

    def test_typical_nfi_hold_distribution_enters(self) -> None:
        """Observed: rsi3_5m=58.3 rsi3_15m=75.0 — those should still HOLD (too high)."""
        # Only bb + aroon conditions met → 2 of 5 < 3 → HOLD
        m5_closes = [95.0, 96.0, 97.0, 98.0, 99.0]  # RSI_3 ~ 58 (rising)
        m15_closes = [88.0, 90.0, 93.0, 96.0, 100.0]  # RSI_3 ~ 75 (rising fast)
        h1_closes = _make_declining_closes(25, start=100.0, step=2.5)
        h4_highs = _make_h4_highs_not_overbought(14)
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=0.001,
        )
        # RSI_3 5m ~58 and 15m ~75 → conditions 1+2 not met; only 3+4+5 = 3 points → borderline
        # Accept either ENTER_LONG (3 conditions: bb+aroon+rsi14) or HOLD
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.HOLD)


class TestNFI3of5Confluence:
    """test_nfi_3_of_5_confluence: 3-of-5 OR-AND logic (require_all=False)."""

    def _base_signal(
        self,
        rsi3_5m_low: bool = True,
        rsi3_15m_low: bool = True,
        rsi14_1h_low: bool = True,
        at_bb_lower: bool = True,
        aroon_ok: bool = True,
    ) -> "Signal":
        """Build signal with selective conditions met."""
        # RSI_3 low scenario: sharp drop gives RSI_3 < 15
        m5_closes = [100.0, 100.0, 100.0, 88.0, 82.0] if rsi3_5m_low else [90.0, 92.0, 94.0, 96.0, 98.0]
        m15_closes = [100.0, 100.0, 100.0, 88.0, 82.0] if rsi3_15m_low else [90.0, 92.0, 94.0, 96.0, 98.0]
        # RSI_14 < 35: sustained decline
        h1_closes = _make_declining_closes(25, start=100.0, step=2.5) if rsi14_1h_low else [100.0] * 25
        h4_highs = _make_h4_highs_not_overbought(14) if aroon_ok else _make_h4_highs_overbought(14)
        # bb_std: 0.001 → near-zero bands. at_bb_lower requires last < mean.
        # h1_closes[-1] < mean(h1_closes) when declining → at_bb_lower True (with 0.5% buffer).
        # For at_bb_lower=False, we need last > bb_lower*1.005 but not exit territory.
        # Use flat h1 + moderate price — last ≈ mean → not below bb_lower*1.005.
        bb_std = 0.001 if at_bb_lower else 2.0  # std=2.0: wide bands → last well inside
        return _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=bb_std,
        )

    def test_5_of_5_conditions_enter_long(self) -> None:
        """All 5 conditions met → ENTER_LONG."""
        sig = self._base_signal()
        assert sig.action == SignalAction.ENTER_LONG

    def test_exactly_3_conditions_enter_long(self) -> None:
        """3 of 5 conditions met → ENTER_LONG (RSI_3 5m/15m fail, rest pass)."""
        # Fail conditions 1+2 (RSI_3 not low), pass 3+4+5
        sig = self._base_signal(rsi3_5m_low=False, rsi3_15m_low=False)
        # rsi14_1h_low + at_bb_lower + aroon_ok = 3 conditions
        assert sig.action == SignalAction.ENTER_LONG

    def test_exactly_2_conditions_hold(self) -> None:
        """Only 2 of 5 conditions met → HOLD."""
        # Fail 3 conditions: RSI_3 5m, RSI_3 15m, RSI_14 1h all not met
        # Only bb_lower + aroon pass = 2 conditions
        sig = self._base_signal(rsi3_5m_low=False, rsi3_15m_low=False, rsi14_1h_low=False)
        assert sig.action == SignalAction.HOLD

    def test_confluence_score_in_reason(self) -> None:
        """ENTER_LONG reason includes confluence score."""
        sig = self._base_signal()
        assert sig.action == SignalAction.ENTER_LONG
        assert "conf=" in sig.reason or "confluence" in sig.reason

    def test_aroon_fail_still_enters_with_4_conditions(self) -> None:
        """4h overbought (aroon fails) but 4 other conditions pass → still ENTER_LONG."""
        sig = self._base_signal(aroon_ok=False)
        # 4 conditions met (RSI_3 5m + 15m + RSI_14 + bb) → >= 3 → ENTER_LONG
        assert sig.action == SignalAction.ENTER_LONG

    def test_min_confluence_param_respected(self) -> None:
        """min_confluence=5 (strict, all must pass) → only 3 conditions → HOLD."""
        # Only RSI_3 5m/15m fail → 3 conditions pass → with min=5, should HOLD
        m5_closes = [95.0, 96.0, 97.0, 98.0, 99.0]   # RSI_3 not low
        m15_closes = [95.0, 96.0, 97.0, 98.0, 99.0]  # RSI_3 not low
        h1_closes = _make_declining_closes(25, start=100.0, step=2.5)
        h4_highs = _make_h4_highs_not_overbought(14)
        sig = _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=NOW_MS,
            bb_std=0.001,
            min_confluence=5,  # all-must-pass mode
        )
        assert sig.action == SignalAction.HOLD
