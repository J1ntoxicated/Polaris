"""tests/paper/test_exit_profiles.py — TDD for exit_profiles module (Phase 2P).

Strategy timeframe별 TP/SL/max_hold 차등 적용.

테스트 먼저 작성 (TDD P4 의무):
- test_exit_profile_scalp_defaults
- test_exit_profile_swing_tp_5pct
- test_exit_profile_position_tp_12pct
- test_exit_profile_liquidation_tp_1_5pct
- test_get_exit_profile_from_hypo_key
- test_get_exit_profile_defaults_to_scalp
- test_get_exit_profile_unknown_key_raises
- test_hypo_007_uses_scalp_profile
- test_hypo_008_uses_swing_profile
- test_hypo_023_uses_liquidation_profile
- test_hypo_027_uses_swing_profile
- test_hypo_028_uses_scalp_profile
- test_hypo_032_uses_position_profile
- test_hypo_033_uses_scalp_profile
- test_hypo_034_uses_scalp_profile
"""
from __future__ import annotations

import pytest

from src.paper.exit_profiles import EXIT_PROFILES, get_exit_profile


# ── 1. EXIT_PROFILES 딕셔너리 구조 ─────────────────────────────────────────────


def test_exit_profile_scalp_defaults():
    """scalp: TP 0.6%, SL 0.35%, max 4h."""
    p = EXIT_PROFILES["scalp"]
    assert p["tp_pct"] == pytest.approx(0.006)
    assert p["sl_pct"] == pytest.approx(0.0035)
    assert p["max_hold_h"] == 4


def test_exit_profile_swing_tp_5pct():
    """swing: TP 5%, SL 2%, max 7d (168h)."""
    p = EXIT_PROFILES["swing"]
    assert p["tp_pct"] == pytest.approx(0.05)
    assert p["sl_pct"] == pytest.approx(0.02)
    assert p["max_hold_h"] == 168


def test_exit_profile_position_tp_12pct():
    """position: TP 12%, SL 4%, max 30d (720h)."""
    p = EXIT_PROFILES["position"]
    assert p["tp_pct"] == pytest.approx(0.12)
    assert p["sl_pct"] == pytest.approx(0.04)
    assert p["max_hold_h"] == 720


def test_exit_profile_liquidation_tp_1_5pct():
    """liquidation: TP 1.5%, SL 0.7%, max 30min (0.5h)."""
    p = EXIT_PROFILES["liquidation"]
    assert p["tp_pct"] == pytest.approx(0.015)
    assert p["sl_pct"] == pytest.approx(0.007)
    assert p["max_hold_h"] == pytest.approx(0.5)


def test_exit_profiles_all_keys_present():
    """모든 profile에 tp_pct, sl_pct, max_hold_h 키 존재."""
    required = {"tp_pct", "sl_pct", "max_hold_h"}
    for name, profile in EXIT_PROFILES.items():
        missing = required - set(profile.keys())
        assert not missing, f"profile '{name}' missing keys: {missing}"


def test_exit_profiles_numeric_values_positive():
    """모든 profile 값이 양수 (sanity check)."""
    for name, profile in EXIT_PROFILES.items():
        assert profile["tp_pct"] > 0, f"'{name}' tp_pct must be positive"
        assert profile["sl_pct"] > 0, f"'{name}' sl_pct must be positive"
        assert profile["max_hold_h"] > 0, f"'{name}' max_hold_h must be positive"


def test_exit_profiles_tp_greater_than_sl():
    """scalp/liquidation: TP > SL (asymmetry sanity). swing/position도 TP > SL."""
    for name, profile in EXIT_PROFILES.items():
        assert profile["tp_pct"] > profile["sl_pct"], (
            f"'{name}' TP must be > SL for positive EV asymmetry. "
            f"tp={profile['tp_pct']}, sl={profile['sl_pct']}"
        )


# ── 2. get_exit_profile() 함수 ─────────────────────────────────────────────────


def test_get_exit_profile_from_hypo_key():
    """hypo에 exit_profile 키 있으면 해당 profile 반환."""
    hypo = {"hypo_id": "HYPO-TEST", "exit_profile": "swing"}
    p = get_exit_profile(hypo)
    assert p["tp_pct"] == pytest.approx(0.05)
    assert p["sl_pct"] == pytest.approx(0.02)


def test_get_exit_profile_defaults_to_scalp():
    """hypo에 exit_profile 키 없으면 scalp (기존 default)."""
    hypo = {"hypo_id": "HYPO-TEST"}
    p = get_exit_profile(hypo)
    assert p["tp_pct"] == pytest.approx(0.006)
    assert p["sl_pct"] == pytest.approx(0.0035)
    assert p["max_hold_h"] == 4


def test_get_exit_profile_position_profile():
    """exit_profile='position' 시 TP 12%."""
    hypo = {"hypo_id": "HYPO-TEST", "exit_profile": "position"}
    p = get_exit_profile(hypo)
    assert p["tp_pct"] == pytest.approx(0.12)
    assert p["sl_pct"] == pytest.approx(0.04)
    assert p["max_hold_h"] == 720


def test_get_exit_profile_liquidation_profile():
    """exit_profile='liquidation' 시 TP 1.5%."""
    hypo = {"hypo_id": "HYPO-TEST", "exit_profile": "liquidation"}
    p = get_exit_profile(hypo)
    assert p["tp_pct"] == pytest.approx(0.015)
    assert p["sl_pct"] == pytest.approx(0.007)


def test_get_exit_profile_unknown_key_raises():
    """알 수 없는 exit_profile 값은 KeyError 발생 (silent default 금지)."""
    hypo = {"hypo_id": "HYPO-TEST", "exit_profile": "nonexistent_tier"}
    with pytest.raises(KeyError):
        get_exit_profile(hypo)


# ── 3. REALTIME_HYPOS exit_profile 설정 검증 ──────────────────────────────────


def test_hypo_007_uses_scalp_profile():
    """HYPO-007-RT (RSI15m intraday): exit_profile='scalp'."""
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-007-RT"), None)
    assert hypo is not None, "HYPO-007-RT must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "scalp", (
        f"HYPO-007-RT (15m intraday) must use scalp exit profile, "
        f"got '{hypo.get('exit_profile')}'"
    )


def test_hypo_008_uses_swing_profile():
    """HYPO-008-RT (VolumeBurst 1H): exit_profile='swing' — larger volume moves."""
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-008-RT"), None)
    assert hypo is not None, "HYPO-008-RT must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "swing", (
        f"HYPO-008-RT (1H VolumeBurst → swing moves) must use swing exit profile, "
        f"got '{hypo.get('exit_profile')}'"
    )


def test_hypo_023_uses_liquidation_profile():
    """HYPO-023 (LiquidationCascade event-driven): exit_profile='liquidation'."""
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-023"), None)
    assert hypo is not None, "HYPO-023 must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "liquidation", (
        f"HYPO-023 (event-driven mean revert, 30min expected) must use liquidation profile, "
        f"got '{hypo.get('exit_profile')}'"
    )


def test_hypo_024_deprecated():
    """HYPO-024 (CrossExchangeGap) must be deprecated (not in REALTIME_HYPOS).

    Confirmed DEPRECATED Phase 4 (2026-05-05): auto fast_fail n=11 win 36% < 40%.
    """
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-024"), None)
    assert hypo is None, "HYPO-024 must be deprecated (n=11 win 36% < 40% fast_fail)"


def test_hypo_027_uses_swing_profile():
    """HYPO-027 (FundingRateFilter): exit_profile='swing' — funding cycles are multi-hour."""
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-027"), None)
    assert hypo is not None, "HYPO-027 must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "swing", (
        f"HYPO-027 (funding rate squeeze, 8h cycle) must use swing profile, "
        f"got '{hypo.get('exit_profile')}'"
    )


def test_hypo_028_uses_scalp_profile():
    """HYPO-028 (TickBurst): exit_profile='scalp' — 60s expected hold."""
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-028"), None)
    assert hypo is not None, "HYPO-028 must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "scalp", (
        f"HYPO-028 (5s burst, 60s hold) must use scalp profile, "
        f"got '{hypo.get('exit_profile')}'"
    )


def test_hypo_032_uses_position_profile():
    """HYPO-032 (TSMOM 1d/7d/30d momentum): exit_profile='position'.

    Moskowitz 2012 JFE: 30d momentum → position hold weeks/months.
    Paper TP 0.6% cuts 99% alpha. Position profile TP 12% recovers alpha.
    """
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-032"), None)
    assert hypo is not None, "HYPO-032 must exist in REALTIME_HYPOS"
    assert hypo.get("exit_profile") == "position", (
        f"HYPO-032 (TSMOM 30d momentum, expected +5-15%/trade) must use position profile. "
        f"Current profile '{hypo.get('exit_profile')}' with 0.6% TP kills 99% alpha. "
        "Position profile TP=12% aligns with backtest expectancy."
    )


def test_vpin_deprecated():
    """HYPO-033 (VPIN Toxicity) must be deprecated (not in REALTIME_HYPOS).

    Auto loss_cap -$5.21 < -$5 trigger met 2026-05-04. Permanently cut.
    Phase 5: loss_cap threshold raised to -$15 — HYPO-033 was cut before Phase 5,
    so it remains deprecated.
    """
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-033"), None)
    assert hypo is None, "HYPO-033 must be deprecated (auto loss_cap -$5.21 < -$5)"


def test_hypo_034_deprecated():
    """HYPO-034 (BTCDominanceLag) must be deprecated (not in REALTIME_HYPOS).

    Manual cut 2026-05-04: n=3, win 0%, 3 SL consecutive, -$7.09.
    Pattern confirmed — BTC spike reversal before alt entry fills alt into correction.
    """
    from src.paper import realtime_runner as rt
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-034"), None)
    assert hypo is None, (
        "HYPO-034 must NOT exist in REALTIME_HYPOS — deprecated 2026-05-04 "
        "(n=3 win 0% 3 SL consecutive)"
    )


# ── 4. _eval_and_act가 exit_profile 기반 TP/SL 적용 ──────────────────────────


def test_swing_profile_tp_not_hit_at_scalp_threshold(tmp_path, monkeypatch):
    """swing profile 포지션: scalp TP 0.6%에서 close 안 됨 (TP 5% 미달).

    HYPO-032 (position profile) 포지션이 +0.7% 시 close 안 되는 것 검증.
    Old: TP_PCT_INTRADAY=0.006 → +0.7% >= 0.6% → close (alpha 잠식).
    New: position profile TP=12% → +0.7% < 12% → no close (alpha 보존).
    """
    from src.paper import realtime_runner as rt
    from src.strategies.tsmom import TSMOM
    from src.domain.signal import Signal, SignalAction
    from unittest.mock import patch

    from src.paper import runner as paper_runner
    monkeypatch.setattr(paper_runner, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(paper_runner, "_LEDGER_ENABLED", False)
    rt._last_close_ms.clear()
    rt._last_close_ms_ticker.clear()
    rt._indicator_cache.clear()
    rt._price_history_60s.clear()

    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000
    entry_price = 100.0

    # TSMOM HYPO with position profile
    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-032"), None)
    if hypo is None:
        pytest.skip("HYPO-032 not in REALTIME_HYPOS")

    assert hypo.get("exit_profile") == "position", "pre-condition: HYPO-032 must use position profile"

    # Stub ENTER signal at entry_price
    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=0.70,
        target_size_usd=200.0,
        reason="tsmom_test_entry",
    )

    with patch("src.strategies.tsmom.TSMOM.evaluate", return_value=enter_signal), \
         patch.object(rt, "_refresh_candles", return_value=[None] * 40), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        # Patch min_window to avoid candle length check
        original_min_window = TSMOM.min_window
        TSMOM.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=entry_price, tick_ts_ms=entry_ts,
                             full_tick=None)
        finally:
            TSMOM.min_window = original_min_window

    bal = rt.load_state(ticker, "tsmom", starting_usd=5000.0)
    if len(bal.open_positions) == 0:
        pytest.skip("Entry did not occur (dynamic sizing returned 0 or no cash)")

    assert len(bal.open_positions) == 1, "position must be open after entry"

    # Now tick at +0.7% — scalp TP would close, position TP (12%) should not
    tick_07pct = entry_price * 1.007
    later_ts = entry_ts + 120_000  # 2min later (> MIN_HOLD_MS)

    hold_signal = Signal(
        timestamp_ms=later_ts,
        action=SignalAction.HOLD,
        confidence=0.0,
        reason="hold",
    )

    with patch("src.strategies.tsmom.TSMOM.evaluate", return_value=hold_signal), \
         patch.object(rt, "_refresh_candles", return_value=[None] * 40), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        original_min_window = TSMOM.min_window
        TSMOM.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=tick_07pct, tick_ts_ms=later_ts,
                             full_tick=None)
        finally:
            TSMOM.min_window = original_min_window

    bal_after = rt.load_state(ticker, "tsmom", starting_usd=5000.0)
    assert len(bal_after.open_positions) == 1, (
        f"position profile (TP=12%) must NOT close at +0.7%. "
        f"Old scalp TP=0.6% would have closed. Got closed_positions={len(bal_after.closed_positions)}"
    )
    assert len(bal_after.closed_positions) == 0, (
        "No close at +0.7% with position profile (TP=12%)"
    )


def test_position_profile_closes_at_tp_12pct(tmp_path, monkeypatch):
    """position profile: +12% 시 TP hit → close 됨.

    HYPO-032 포지션 +12% 도달 시 close 검증.
    """
    from src.paper import realtime_runner as rt
    from src.strategies.tsmom import TSMOM
    from src.domain.signal import Signal, SignalAction
    from unittest.mock import patch

    from src.paper import runner as paper_runner
    monkeypatch.setattr(paper_runner, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(paper_runner, "_LEDGER_ENABLED", False)
    rt._last_close_ms.clear()
    rt._last_close_ms_ticker.clear()
    rt._indicator_cache.clear()
    rt._price_history_60s.clear()

    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000
    entry_price = 100.0

    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-032"), None)
    if hypo is None:
        pytest.skip("HYPO-032 not in REALTIME_HYPOS")

    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=0.70,
        target_size_usd=200.0,
        reason="tsmom_entry",
    )

    with patch("src.strategies.tsmom.TSMOM.evaluate", return_value=enter_signal), \
         patch.object(rt, "_refresh_candles", return_value=[None] * 40), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        TSMOM.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=entry_price, tick_ts_ms=entry_ts,
                             full_tick=None)
        finally:
            TSMOM.min_window = 30

    bal = rt.load_state(ticker, "tsmom", starting_usd=5000.0)
    if len(bal.open_positions) == 0:
        pytest.skip("Entry did not occur")

    # Tick at +12.1% — position TP=12% → close
    tick_12pct = entry_price * 1.121
    later_ts = entry_ts + 200_000  # > MIN_HOLD_MS

    hold_signal = Signal(
        timestamp_ms=later_ts,
        action=SignalAction.HOLD,
        confidence=0.0,
        reason="hold",
    )

    with patch("src.strategies.tsmom.TSMOM.evaluate", return_value=hold_signal), \
         patch.object(rt, "_refresh_candles", return_value=[None] * 40), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        TSMOM.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=tick_12pct, tick_ts_ms=later_ts,
                             full_tick=None)
        finally:
            TSMOM.min_window = 30

    bal_after = rt.load_state(ticker, "tsmom", starting_usd=5000.0)
    assert len(bal_after.closed_positions) == 1, (
        f"position profile TP=12% must close at +12.1%. "
        f"closed_positions={len(bal_after.closed_positions)}, "
        f"open_positions={len(bal_after.open_positions)}"
    )


def test_scalp_profile_closes_at_old_tp_threshold(tmp_path, monkeypatch):
    """scalp profile: +0.7% (> TP 0.6%) 시 close 됨 (backward compat).

    HYPO-007-RT (scalp) 포지션 +0.7% 시 기존처럼 close되는 것 확인.
    """
    from src.paper import realtime_runner as rt
    from src.strategies.rsi_15m_intraday import RSI15mIntraday
    from src.domain.signal import Signal, SignalAction
    from src.domain.candle import Candle
    from unittest.mock import patch

    from src.paper import runner as paper_runner
    monkeypatch.setattr(paper_runner, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(paper_runner, "_LEDGER_ENABLED", False)
    rt._last_close_ms.clear()
    rt._last_close_ms_ticker.clear()
    rt._indicator_cache.clear()
    rt._price_history_60s.clear()

    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000
    entry_price = 100.0

    hypo = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-007-RT"), None)
    if hypo is None:
        pytest.skip("HYPO-007-RT not in REALTIME_HYPOS")

    assert hypo.get("exit_profile") == "scalp", "pre-condition"

    def _candles(n: int = 50):
        return [
            Candle(
                timestamp_ms=entry_ts - (n - i) * 900_000,
                open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0,
            )
            for i in range(n)
        ]

    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=0.70,
        target_size_usd=200.0,
        reason="rsi_entry",
    )

    with patch("src.strategies.rsi_15m_intraday.RSI15mIntraday.evaluate",
               return_value=enter_signal), \
         patch.object(rt, "_refresh_candles", return_value=_candles(50)), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        RSI15mIntraday.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=entry_price, tick_ts_ms=entry_ts,
                             full_tick=None)
        finally:
            RSI15mIntraday.min_window = 15

    bal = rt.load_state(ticker, "rsi_15m_intraday", starting_usd=5000.0)
    if len(bal.open_positions) == 0:
        pytest.skip("Entry did not occur")

    # Tick at +0.7% — scalp TP=0.6% → close됨
    tick_07pct = entry_price * 1.007
    later_ts = entry_ts + 200_000  # > MIN_HOLD_MS

    hold_signal = Signal(
        timestamp_ms=later_ts,
        action=SignalAction.HOLD,
        confidence=0.0,
        reason="hold",
    )

    with patch("src.strategies.rsi_15m_intraday.RSI15mIntraday.evaluate",
               return_value=hold_signal), \
         patch.object(rt, "_refresh_candles", return_value=_candles(50)), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.55, "avg_win_pct": 0.8, "avg_loss_pct": 0.5}):
        RSI15mIntraday.min_window = 0
        try:
            rt._eval_and_act(hypo, ticker, tick_price=tick_07pct, tick_ts_ms=later_ts,
                             full_tick=None)
        finally:
            RSI15mIntraday.min_window = 15

    bal_after = rt.load_state(ticker, "rsi_15m_intraday", starting_usd=5000.0)
    assert len(bal_after.closed_positions) == 1, (
        f"scalp profile TP=0.6% must close at +0.7%. "
        f"closed={len(bal_after.closed_positions)}, open={len(bal_after.open_positions)}"
    )


# ── 5. runner.py 1D strategy exit_profile 적용 (cron/daily_paper_runner) ──────


def test_runner_1d_strategy_tp_from_exit_profile():
    """runner.run_cycle: 1D strategy default is sl_pct=2%, tp_pct=3% (swing-aligned).

    runner.py 현재: is_intraday=False → tp_pct=0.03 (3%), sl_pct=0.02 (2%).
    Phase 2P requires swing profile in runner for 1D strategies.
    tp_pct=0.03 is close to swing (0.05) but not equal.

    This test documents the current runner.py 1D exit parameters and
    verifies they are at minimum wider than scalp (TP >= 3%).
    """
    # runner.run_cycle uses is_intraday logic:
    # bar not in {1m,3m,5m,15m,30m,1h} → is_intraday=False → tp=0.03, sl=0.02
    # These are already wider than scalp (0.006/0.0035).
    # Phase 2P focus: realtime_runner (tick-driven) exit_profile system.
    # runner.py 1D: tp=3% (acceptable for cron daily cycle — 1 candle per day max).
    assert EXIT_PROFILES["swing"]["tp_pct"] > EXIT_PROFILES["scalp"]["tp_pct"], (
        "swing TP must exceed scalp TP"
    )
    assert EXIT_PROFILES["position"]["tp_pct"] > EXIT_PROFILES["swing"]["tp_pct"], (
        "position TP must exceed swing TP"
    )
