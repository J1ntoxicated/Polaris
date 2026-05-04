"""tests/strategies/test_binance_lead.py — BinanceLeadSignal cross-exchange (Phase 2g Round 3/13)."""
from __future__ import annotations

from src.domain.signal import SignalAction
from src.strategies.binance_lead import DEFAULT_MIN_VOLATILITY_BPS, BinanceLeadSignal


# ---------------------------------------------------------------------------
# Round 13 regression — DEFAULT_MIN_VOLATILITY_BPS == 5.0
# ---------------------------------------------------------------------------

class TestRound13VolThreshold:
    def test_default_min_volatility_bps_5_round13(self) -> None:
        """HYPO-014 Round 13: vol threshold 8 → 5 bps (BLEAD-HOLD 분포 완화)."""
        assert DEFAULT_MIN_VOLATILITY_BPS == 5.0

    def test_instance_min_volatility_bps_5_round13(self) -> None:
        """Instance default matches module constant."""
        s = BinanceLeadSignal()
        assert s.min_volatility_bps == 5.0

    def test_vol_between_5_and_8_now_enters(self) -> None:
        """vol=6.0 bps: was rejected at threshold 8, now passes at threshold 5."""
        s = BinanceLeadSignal()
        # vol=6.0 (between old 8 and new 5 threshold) + strong buy → ENTER_LONG
        sig = s.evaluate_cross(
            _okx_tick(),
            _bs(taker=0.7, n=100, vol=6.0, price=79050.0),
        )
        assert sig.action == SignalAction.ENTER_LONG

    def test_vol_below_5_still_holds(self) -> None:
        """vol=3.0 bps: below new threshold 5 → HOLD."""
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.7, n=100, vol=3.0))
        assert sig.action == SignalAction.HOLD
        assert "vol" in sig.reason


def _okx_tick(price: float = 79000.0, ts: int = 1_700_000_000_000) -> dict:
    return {"ts": ts, "last": price, "bid": price * 0.999, "ask": price * 1.001}


def _bs(taker=0.7, n=100, vol=12.0, price=79000.0, ts_offset_ms=0,
        base_ts=1_700_000_000_000) -> dict:
    return {
        "taker_buy_ratio": taker,
        "n_trades": n,
        "volatility_bps": vol,
        "last_price": price,
        "last_trade_ts_ms": base_ts - ts_offset_ms,
    }


class TestEntry:
    def test_strong_buy_active_market_enters(self) -> None:
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.7, n=100, vol=12.0, price=79050.0))
        assert sig.action == SignalAction.ENTER_LONG

    def test_low_volatility_holds(self) -> None:
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.7, n=100, vol=2.0))
        assert sig.action == SignalAction.HOLD
        assert "vol" in sig.reason


class TestExit:
    def test_sell_pressure_exits(self) -> None:
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.30, n=100))
        assert sig.action == SignalAction.EXIT


class TestDeadzone:
    def test_neutral_ratio_holds(self) -> None:
        s = BinanceLeadSignal()
        for r in (0.45, 0.50, 0.55, 0.60, 0.64):
            sig = s.evaluate_cross(_okx_tick(), _bs(taker=r))
            assert sig.action == SignalAction.HOLD


class TestGuards:
    def test_insufficient_trades_holds(self) -> None:
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.7, n=10))
        assert sig.action == SignalAction.HOLD

    def test_stale_binance_holds(self) -> None:
        s = BinanceLeadSignal()
        # binance last trade 60s 전 (stale_ms 30s 초과)
        sig = s.evaluate_cross(_okx_tick(), _bs(taker=0.7, ts_offset_ms=60_000))
        assert sig.action == SignalAction.HOLD
        assert "stale" in sig.reason

    def test_stale_uses_now_ms_not_okx_clock(self) -> None:
        """Fix 2: stale check uses now_ms from binance_state (runner-injected local clock).

        Cross-exchange clock skew: OKX ts and Binance ts use different exchange clocks.
        now_ms = int(time.time()*1000) injected by runner prevents false stale detection.
        """
        s = BinanceLeadSignal()
        base_ts = 1_700_000_000_000  # OKX clock
        # OKX ts == base_ts, Binance last trade == base_ts - 60_000 (60s old by OKX clock)
        # BUT now_ms == base_ts + 1000 (runner local clock, only 1s since last trade)
        # With now_ms present: should NOT be stale (1s < stale_ms=30s)
        binance_state = {
            "taker_buy_ratio": 0.7,
            "n_trades": 100,
            "volatility_bps": 12.0,
            "last_price": 79000.0,
            "last_trade_ts_ms": base_ts - 60_000,  # Binance exchange clock (diverged from OKX)
            "now_ms": base_ts - 60_000 + 1_000,    # local clock: only 1s since last trade
        }
        sig = s.evaluate_cross(_okx_tick(ts=base_ts), binance_state)
        # With now_ms override: 1s gap → NOT stale → can enter
        assert sig.action != SignalAction.HOLD or "stale" not in (sig.reason or ""), (
            f"Expected non-stale signal but got HOLD: {sig.reason}"
        )

    def test_stale_detected_via_now_ms(self) -> None:
        """Fix 2: when now_ms shows 60s elapsed (stale_ms=30s), must HOLD."""
        s = BinanceLeadSignal()
        base_ts = 1_700_000_000_000
        b_ts = base_ts - 1_000  # Binance last trade 1s before OKX (close in Binance clock)
        now_ms = base_ts + 60_000  # but local clock says 60s have passed
        binance_state = {
            "taker_buy_ratio": 0.7,
            "n_trades": 100,
            "volatility_bps": 12.0,
            "last_price": 79000.0,
            "last_trade_ts_ms": b_ts,
            "now_ms": now_ms,
        }
        sig = s.evaluate_cross(_okx_tick(ts=base_ts), binance_state)
        assert sig.action == SignalAction.HOLD
        assert "stale" in sig.reason

    def test_abnormal_gap_holds(self) -> None:
        # OKX 79000 vs Binance 79500 = 63bps gap (> max 30bps)
        s = BinanceLeadSignal()
        sig = s.evaluate_cross(_okx_tick(price=79000.0), _bs(taker=0.7, price=79500.0))
        assert sig.action == SignalAction.HOLD
        assert "gap" in sig.reason
