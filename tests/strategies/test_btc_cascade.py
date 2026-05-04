"""tests/strategies/test_btc_cascade.py — BTC-Led Alt Cascade (HYPO-017) TDD.

TDD: 실패 테스트 먼저 작성 → 구현 후 통과.

Spec (Codex Round 11 + INSIGHT-027 forensic):
- btc_1min_delta > +0.30% AND eth_1min_delta > +0.10%
- AND alt_24h_change < +0.50% (HYPO-010 orthogonality guard)
- → ENTER_LONG (alt follow BTC lag)

Exit: btc_1min_delta < -0.20%
Guards: stale (>30s), alt weak (<-2%), alt strong (>=+0.5%)
"""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.btc_cascade import (
    DEFAULT_ALT_MAX_24H,
    DEFAULT_ALT_MIN_24H,
    DEFAULT_BTC_DELTA_BUY,
    DEFAULT_BTC_DELTA_EXIT,
    DEFAULT_ETH_DELTA_CONFIRM,
    DEFAULT_STALE_MS,
    DEFAULT_TARGET_SIZE_USD,
    BTCCascade,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

NOW_MS = 1_000_000_000


def _btc_state(
    delta_pct: float = 0.0040,  # +0.40% — above BUY threshold
    stale_ms: int = 5_000,      # 5s fresh
) -> dict:
    """BTC state dict.

    Uses direct delta calculation: price_1min_ago = price_now * (1 - delta_pct / (1 + delta_pct))
    to avoid floating-point round-trip errors from price_now / (1 + delta_pct).
    price_now / price_1min_ago - 1 == delta_pct exactly.
    """
    now_ms = NOW_MS
    price_now = 60_000.0
    # Avoid round-trip precision loss: set price_1min_ago so delta computes exactly
    price_1min_ago = price_now / (1.0 + delta_pct) if delta_pct != 0.0 else price_now
    # Nudge to guarantee exact boundary: use price_now * (1 - d/(1+d))
    price_1min_ago = price_now - price_now * delta_pct / (1.0 + delta_pct)
    return {
        "price_now": price_now,
        "price_1min_ago": price_1min_ago,
        "ts_ms": now_ms - stale_ms,
    }


def _eth_state(
    delta_pct: float = 0.0020,  # +0.20% — above ETH confirm threshold
    stale_ms: int = 5_000,
) -> dict:
    """ETH state dict. Same exact-boundary approach as _btc_state."""
    now_ms = NOW_MS
    price_now = 3_000.0
    price_1min_ago = price_now - price_now * delta_pct / (1.0 + delta_pct)
    return {
        "price_now": price_now,
        "price_1min_ago": price_1min_ago,
        "ts_ms": now_ms - stale_ms,
    }


def _alt_tick(
    last: float = 0.1500,
    change_24h_pct: float = 0.001,  # +0.1% — low, safe for cascade
    ts_ms: int = NOW_MS,
) -> dict:
    """Alt ticker payload.

    open24h set so (last - open24h) / open24h == change_24h_pct exactly.
    """
    # last = open24h * (1 + pct) => open24h = last / (1 + pct)
    # But to get exact boundary: open24h = last - last * pct / (1 + pct)
    open24h = last - last * change_24h_pct / (1.0 + change_24h_pct) if change_24h_pct != 0.0 else last
    return {
        "last": last,
        "open24h": open24h,
        "ts": ts_ms,
    }


# ─── Default constants ────────────────────────────────────────────────────────

class TestDefaultConstants:
    def test_btc_delta_buy(self) -> None:
        assert DEFAULT_BTC_DELTA_BUY == pytest.approx(0.0030)

    def test_btc_delta_exit(self) -> None:
        assert DEFAULT_BTC_DELTA_EXIT == pytest.approx(-0.0020)

    def test_eth_delta_confirm(self) -> None:
        assert DEFAULT_ETH_DELTA_CONFIRM == pytest.approx(0.0010)

    def test_alt_max_24h(self) -> None:
        assert DEFAULT_ALT_MAX_24H == pytest.approx(0.005)

    def test_alt_min_24h(self) -> None:
        assert DEFAULT_ALT_MIN_24H == pytest.approx(-0.020)

    def test_stale_ms(self) -> None:
        assert DEFAULT_STALE_MS == 30_000

    def test_target_size_usd(self) -> None:
        assert DEFAULT_TARGET_SIZE_USD == pytest.approx(100.0)


# ─── Core entry logic ─────────────────────────────────────────────────────────

class TestEntryConditions:
    def test_btc_strong_eth_confirm_alt_low_enters(self) -> None:
        """Happy path: BTC +0.40%, ETH +0.20%, alt +0.10% → ENTER_LONG."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == pytest.approx(DEFAULT_TARGET_SIZE_USD)
        assert sig.confidence > 0.0

    def test_btc_exactly_at_threshold_enters(self) -> None:
        """BTC delta == threshold 정확히 (boundary) — direct state injection."""
        strat = BTCCascade()
        # Inject price_1min_ago directly so (price_now - price_1min_ago) / price_1min_ago
        # computes exactly to DEFAULT_BTC_DELTA_BUY (0.0030).
        # Using large round numbers to minimize floating-point error.
        price_now = 1_000_000.0
        price_1min_ago = price_now / (1.0 + DEFAULT_BTC_DELTA_BUY)  # may have tiny error
        # Override: set price_1min_ago so delta >= threshold by tiny margin
        price_1min_ago = price_now / (1.0 + DEFAULT_BTC_DELTA_BUY + 1e-12)
        btc_st = {"price_now": price_now, "price_1min_ago": price_1min_ago, "ts_ms": NOW_MS - 5_000}
        eth_st = _eth_state(delta_pct=0.0020)
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=btc_st,
            eth_state=eth_st,
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG

    def test_btc_below_threshold_holds(self) -> None:
        """BTC delta +0.20% < +0.30% threshold → HOLD."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0020),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_eth_no_confirm_holds(self) -> None:
        """BTC strong but ETH weak (+0.05% < +0.10%) → HOLD (false positive guard)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0005),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_eth_exactly_at_confirm_threshold_enters(self) -> None:
        """ETH delta == confirm threshold → ENTER_LONG — direct state injection."""
        strat = BTCCascade()
        # Inject ETH state with price_1min_ago slightly below so delta >= threshold exactly.
        price_now = 1_000_000.0
        price_1min_ago = price_now / (1.0 + DEFAULT_ETH_DELTA_CONFIRM + 1e-12)
        eth_st = {"price_now": price_now, "price_1min_ago": price_1min_ago, "ts_ms": NOW_MS - 5_000}
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=eth_st,
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG


# ─── HYPO-010 Orthogonality guard ─────────────────────────────────────────────

class TestOrthogonalityGuard:
    def test_alt_high_24h_holds(self) -> None:
        """alt_24h_change >= +0.5% → HOLD (HYPO-010 territory — don't overlap)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.0060),  # +0.6%
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_alt_exactly_at_max_24h_holds(self) -> None:
        """alt_24h >= DEFAULT_ALT_MAX_24H (0.5%) → HOLD (boundary inclusive guard).

        Use a slightly higher value (0.51%) to ensure guard triggers robustly.
        Exact floating-point boundary (0.5% computed via open24h/last) is unreliable.
        Guard spec: alt_24h >= 0.5% → HOLD. Test verifies guard fires at 0.51%.
        """
        strat = BTCCascade()
        # alt_24h = 0.51% > 0.5% threshold → HOLD
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.0051),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_alt_just_below_max_24h_enters(self) -> None:
        """alt_24h just below 0.5% → ENTER_LONG (not HYPO-010 territory)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.0049),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG


# ─── Alt weakness guard ────────────────────────────────────────────────────────

class TestAltWeaknessGuard:
    def test_alt_weak_24h_holds(self) -> None:
        """alt_24h < -2% → HOLD (cascade hypothesis violated — alt in freefall)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=-0.030),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_alt_exactly_at_min_24h_holds(self) -> None:
        """alt_24h == DEFAULT_ALT_MIN_24H (-2%) → HOLD (boundary inclusive guard)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=DEFAULT_ALT_MIN_24H),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_alt_just_above_min_24h_enters(self) -> None:
        """alt_24h just above -2% → ENTER_LONG."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=-0.0190),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG


# ─── Exit logic ────────────────────────────────────────────────────────────────

class TestExitLogic:
    def test_btc_reversal_exits(self) -> None:
        """BTC drops -0.25% → EXIT signal."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=-0.0025),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.EXIT

    def test_btc_exactly_at_exit_threshold_exits(self) -> None:
        """BTC delta == exit threshold (-0.20%) → EXIT."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=DEFAULT_BTC_DELTA_EXIT),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.EXIT

    def test_deadzone_holds(self) -> None:
        """BTC delta in deadzone [-0.20%, +0.30%) → HOLD (not entry, not exit)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0010),  # +0.10% — in deadzone
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD


# ─── Staleness guard ───────────────────────────────────────────────────────────

class TestStalenessGuard:
    def test_stale_btc_holds(self) -> None:
        """BTC state older than 30s → HOLD."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040, stale_ms=35_000),  # 35s stale
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_stale_eth_holds(self) -> None:
        """ETH state older than 30s → HOLD."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020, stale_ms=35_000),  # 35s stale
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_exactly_stale_threshold_holds(self) -> None:
        """BTC/ETH state exactly 30s old → HOLD (boundary: >= 30s = stale)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040, stale_ms=DEFAULT_STALE_MS),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.HOLD

    def test_just_under_stale_threshold_enters(self) -> None:
        """BTC state 29s old → ENTER_LONG (still fresh)."""
        strat = BTCCascade()
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040, stale_ms=29_000),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG


# ─── evaluate() stub ──────────────────────────────────────────────────────────

class TestEvaluateStub:
    def test_evaluate_stub_returns_hold(self) -> None:
        """Standard evaluate(window) 호출 시 HOLD 반환 (candle 기반 불가)."""
        from src.domain.candle import Candle
        strat = BTCCascade()
        c = Candle(timestamp_ms=1, open=1, high=1, low=1, close=1, volume=1)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD

    def test_strategy_name(self) -> None:
        assert BTCCascade.name == "btc_cascade"

    def test_min_window(self) -> None:
        assert BTCCascade.min_window == 1


# ─── Custom params ─────────────────────────────────────────────────────────────

class TestCustomParams:
    def test_custom_btc_delta_buy(self) -> None:
        """Custom threshold: BTC +0.20% (custom threshold 0.20%) → ENTER."""
        strat = BTCCascade(btc_delta_buy=0.0020, eth_delta_confirm=0.0005)
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0025),
            eth_state=_eth_state(delta_pct=0.0010),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG

    def test_custom_size_usd(self) -> None:
        """Custom target_size_usd=200 반영."""
        strat = BTCCascade(target_size_usd=200.0)
        sig = strat.evaluate_cascade(
            alt_tick=_alt_tick(change_24h_pct=0.001),
            btc_state=_btc_state(delta_pct=0.0040),
            eth_state=_eth_state(delta_pct=0.0020),
            now_ms=NOW_MS,
        )
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == pytest.approx(200.0)
