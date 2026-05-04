"""tests/paper/test_daily_paper_runner.py — DAILY_PAPER_HYPOS 검증.

ADR-010 paper stage: HYPO-020-VB-DONCH-DOGE paper validation runner.
Codex Round 15 compliance: cron 에서 이 모듈로 이동.

Phase 2O (2026-05-04): 7 backtest-viable strategies added to DAILY_PAPER_HYPOS.
Multi-resolution backtest grid (171s) identified viable strategies (Sharpe>=0.3, IS+OOS EV>0).

Paper stage 원칙:
- DAILY_PAPER_HYPOS = 60일 실측 누적 중 (Promotion Gate 미통과)
- cron ACTIVE_HYPOS 에 없어야 함 (ADR-010)
- strategy 구성 + param 검증 (lessons #46 runtime verify)
"""
from __future__ import annotations

import pytest

from src.paper.cron import ACTIVE_HYPOS
from src.paper.daily_paper_runner import DAILY_PAPER_HYPOS
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.volume_burst import VolumeBurst


# ── HYPO-020 paper stage 검증 ──────────────────────────────────────────────────


def test_hypo_020_in_daily_paper_hypos():
    """HYPO-020-VB-DONCH-DOGE 는 DAILY_PAPER_HYPOS 에 존재해야 함."""
    ids = [h["hypo_id"] for h in DAILY_PAPER_HYPOS]
    assert "HYPO-020-VB-DONCH-DOGE" in ids, (
        f"HYPO-020-VB-DONCH-DOGE must be in DAILY_PAPER_HYPOS. Current: {ids}"
    )


def test_hypo_020_strategy_is_confluence_signal():
    """HYPO-020 strategy class == ConfluenceSignal (not raw VB or Donchian)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None, "HYPO-020-VB-DONCH-DOGE not found in DAILY_PAPER_HYPOS"
    assert hypo["strategy"] is ConfluenceSignal, (
        f"Expected ConfluenceSignal, got {hypo['strategy']}"
    )


def test_hypo_020_sub_strategies_correct():
    """HYPO-020: sub_strategies = [VolumeBurst, DonchianBreakout(40, 15)]."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    params = hypo["strategy_params"]
    subs = params.get("sub_strategies", [])
    assert len(subs) == 2, f"Expected 2 sub_strategies, got {len(subs)}"
    assert isinstance(subs[0], VolumeBurst), (
        f"sub_strategies[0] must be VolumeBurst, got {type(subs[0])}"
    )
    assert isinstance(subs[1], DonchianBreakout), (
        f"sub_strategies[1] must be DonchianBreakout, got {type(subs[1])}"
    )
    assert subs[1].entry_period == 40, (
        f"DonchianBreakout entry_period must be 40, got {subs[1].entry_period}"
    )
    assert subs[1].exit_period == 15, (
        f"DonchianBreakout exit_period must be 15, got {subs[1].exit_period}"
    )


def test_hypo_020_require_all_true():
    """HYPO-020: require_all=True (AND confluence — false positive 감소)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    params = hypo["strategy_params"]
    assert params.get("require_all") is True, (
        f"require_all must be True (AND), got {params.get('require_all')}"
    )


def test_hypo_020_target_size_usd():
    """HYPO-020: target_size_usd == 200.0 (보수적 신규 paper)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    params = hypo["strategy_params"]
    assert params.get("target_size_usd") == 200.0, (
        f"target_size_usd must be 200.0, got {params.get('target_size_usd')}"
    )


def test_hypo_020_ticker_doge_only():
    """HYPO-020: DOGE-USDT only (ORDI archived — outlier-driven, INSIGHT-031)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    assert hypo["tickers"] == ["DOGE-USDT"], (
        f"HYPO-020 must track DOGE-USDT only. ORDI archived (outlier). "
        f"Got: {hypo['tickers']}"
    )


def test_hypo_020_bar_1d():
    """HYPO-020: bar == '1D' (daily candle — fee efficiency, INSIGHT-031)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    assert hypo["bar"] == "1D", f"bar must be '1D', got {hypo['bar']}"


def test_hypo_020_has_paper_since():
    """HYPO-020: paper_since 필드 존재 — 60일 추적 시작일 (Promotion Gate 기준)."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    assert "paper_since" in hypo, "HYPO-020 must have 'paper_since' field for 60-day tracking"
    assert hypo["paper_since"] == "2026-05-04", (
        f"paper_since must be '2026-05-04' (ADR-010 paper start date), "
        f"got {hypo['paper_since']}"
    )


def test_hypo_020_has_promotion_criteria():
    """HYPO-020: promotion_criteria 필드 존재 — Promotion Gate 기준 명시."""
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    assert "promotion_criteria" in hypo, (
        "HYPO-020 must have 'promotion_criteria' dict (ADR-011 gate)"
    )
    crit = hypo["promotion_criteria"]
    assert crit.get("min_sharpe", 0) >= 0.3, (
        f"min_sharpe must be >= 0.3 (ADR-011 swing), got {crit.get('min_sharpe')}"
    )
    assert "min_trades" in crit, "promotion_criteria must include min_trades"
    assert "min_calendar_days" in crit, "promotion_criteria must include min_calendar_days"


def test_hypo_020_runtime_instantiation():
    """HYPO-020 ConfluenceSignal(**params) runtime 생성 + evaluate 메서드 확인.

    lessons #46: import 통과 != runtime 통과. 실제 인스턴스 생성까지 검증.
    """
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-020-VB-DONCH-DOGE"),
        None,
    )
    assert hypo is not None
    cls = hypo["strategy"]
    params = hypo["strategy_params"]
    try:
        instance = cls(**params)
    except Exception as e:
        pytest.fail(
            f"ConfluenceSignal(**params) runtime instantiation failed: {e}\nparams={params}"
        )
    assert hasattr(instance, "evaluate"), "ConfluenceSignal must have evaluate() method"
    assert instance.min_window >= 40, (
        f"min_window should be >= 40 (DonchianBreakout(40,15) requires 40+ candles), "
        f"got {instance.min_window}"
    )


# ── DAILY_PAPER_HYPOS 구조 ─────────────────────────────────────────────────────


def test_daily_paper_hypos_required_fields():
    """DAILY_PAPER_HYPOS 모든 entry에 필수 필드 존재.

    Updated: strategy key is "strategy" OR "strategy_cls" (new pattern).
    tickers OR universe (for cross-sectional).
    """
    base_required = {
        "hypo_id", "strategy_params", "bar", "starting_usd",
        "max_position_pct", "paper_since", "promotion_criteria",
    }
    for h in DAILY_PAPER_HYPOS:
        missing = base_required - set(h.keys())
        assert not missing, (
            f"{h.get('hypo_id', '?')} missing required fields: {missing}"
        )
        # Must have either "strategy" or "strategy_cls"
        has_strategy_key = "strategy" in h or "strategy_cls" in h
        assert has_strategy_key, (
            f"{h.get('hypo_id', '?')} must have 'strategy' or 'strategy_cls' key"
        )
        # Must have either "tickers" or "universe" (cross-sectional)
        has_tickers_key = "tickers" in h or "universe" in h
        assert has_tickers_key, (
            f"{h.get('hypo_id', '?')} must have 'tickers' or 'universe' key"
        )


def test_daily_paper_hypos_all_instantiable():
    """DAILY_PAPER_HYPOS 모든 strategy runtime instantiation (lessons #46)."""
    for h in DAILY_PAPER_HYPOS:
        # Support both "strategy" (legacy) and "strategy_cls" (new)
        cls = h.get("strategy_cls") or h.get("strategy")
        assert cls is not None, f"{h['hypo_id']}: no strategy class found"
        params = h.get("strategy_params", {})
        # Skip sub_strategies-containing params that have pre-built instances
        # (ConfluenceSignal with sub_strategies already instantiated)
        try:
            instance = cls(**params)
        except Exception as e:
            pytest.fail(
                f"{h['hypo_id']} strategy {cls.__name__}(**params) failed: {e}"
            )
        assert hasattr(instance, "evaluate"), (
            f"{cls.__name__} must have evaluate() method"
        )


# ── Phase 2O: 7 backtest-viable strategies ────────────────────────────────────
# 2026-05-04 multi-resolution backtest grid. ADR-010: all in paper stage.


PHASE_2O_HYPO_IDS = [
    "HYPO-004-DONCH-DOGE-1D",
    "HYPO-020-VB-DONCH-BTC-1D",
    "HYPO-020-VB-DONCH-ETH-1D",
    "HYPO-008-VB-1D-BTC",
    "HYPO-008-VB-1D-ETH",
    "HYPO-008-VB-1D-DOGE",
    "HYPO-008-VB-4H-ETH",
]


def test_active_hypos_phase2o_count():
    """Phase 2O: 7 신규 HYPO가 DAILY_PAPER_HYPOS 에 존재해야 함.

    backtest viable (Sharpe>=0.3 + IS+OOS EV>0) 전략 → paper 단계 진입.
    ADR-010: cron 진입 전 60일 paper 추적 의무.
    """
    paper_ids = [h["hypo_id"] for h in DAILY_PAPER_HYPOS]
    missing = [hid for hid in PHASE_2O_HYPO_IDS if hid not in paper_ids]
    assert not missing, (
        f"Phase 2O: {len(missing)} HYPO(s) missing from DAILY_PAPER_HYPOS: {missing}. "
        f"Current DAILY_PAPER_HYPOS ids: {paper_ids}"
    )


def test_phase2o_hypos_not_in_cron():
    """Phase 2O HYPO들은 cron ACTIVE_HYPOS에 없어야 함 (ADR-010 — paper 단계 미검증).

    paper 60일 + Promotion Gate 통과 후에만 cron 진입 가능.
    """
    cron_ids = [h["hypo_id"] for h in ACTIVE_HYPOS]
    premature = [hid for hid in PHASE_2O_HYPO_IDS if hid in cron_ids]
    assert not premature, (
        f"ADR-010 violation: Phase 2O HYPOs must not be in cron ACTIVE_HYPOS "
        f"before paper validation. Premature entries: {premature}"
    )


def test_phase2o_hypo_004_doge_params():
    """HYPO-004-DONCH-DOGE-1D: DonchianBreakout(40,15) DOGE 1D.

    IS exp +63%, OOS exp +67%, Sharpe 1.16 — highest Phase 2O signal.
    Same params as cron HYPO-004-DONCH-40-15 (BTC+ETH) — extending to DOGE.
    """
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-004-DONCH-DOGE-1D"),
        None,
    )
    assert hypo is not None, "HYPO-004-DONCH-DOGE-1D must be in DAILY_PAPER_HYPOS"
    assert hypo["strategy"] is DonchianBreakout
    assert hypo["strategy_params"]["entry_period"] == 40
    assert hypo["strategy_params"]["exit_period"] == 15
    assert hypo["tickers"] == ["DOGE-USDT"]
    assert hypo["bar"] == "1D"


def test_phase2o_hypo_020_btc_eth_params():
    """HYPO-020-VB-DONCH-BTC-1D + ETH-1D: ConfluenceSignal(VB AND Donchian 40/15).

    BTC IS Sharpe 0.57, ETH IS Sharpe 0.45. Extends existing DOGE HYPO-020 to BTC+ETH.
    """
    for hypo_id, ticker in [
        ("HYPO-020-VB-DONCH-BTC-1D", "BTC-USDT"),
        ("HYPO-020-VB-DONCH-ETH-1D", "ETH-USDT"),
    ]:
        hypo = next(
            (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == hypo_id),
            None,
        )
        assert hypo is not None, f"{hypo_id} must be in DAILY_PAPER_HYPOS"
        assert hypo["strategy"] is ConfluenceSignal, (
            f"{hypo_id} strategy must be ConfluenceSignal"
        )
        params = hypo["strategy_params"]
        assert params.get("require_all") is True, f"{hypo_id} require_all must be True"
        subs = params.get("sub_strategies", [])
        assert len(subs) == 2, f"{hypo_id} must have 2 sub_strategies"
        assert isinstance(subs[0], VolumeBurst), f"{hypo_id} sub[0] must be VolumeBurst"
        assert isinstance(subs[1], DonchianBreakout), f"{hypo_id} sub[1] must be DonchianBreakout"
        assert subs[1].entry_period == 40
        assert subs[1].exit_period == 15
        assert hypo["tickers"] == [ticker], f"{hypo_id} ticker must be [{ticker}]"
        assert hypo["bar"] == "1D"


def test_phase2o_hypo_008_1d_trio_params():
    """HYPO-008-VB-1D-BTC/ETH/DOGE: VolumeBurst 1D — default params.

    IS Sharpe: BTC 0.38, ETH 0.41, DOGE 0.36. All OOS EV>0.
    1D timeframe new — currently runs 1H realtime (separate entry).
    """
    vb_1d_hypos = {
        "HYPO-008-VB-1D-BTC": "BTC-USDT",
        "HYPO-008-VB-1D-ETH": "ETH-USDT",
        "HYPO-008-VB-1D-DOGE": "DOGE-USDT",
    }
    for hypo_id, expected_ticker in vb_1d_hypos.items():
        hypo = next(
            (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == hypo_id),
            None,
        )
        assert hypo is not None, f"{hypo_id} must be in DAILY_PAPER_HYPOS"
        assert hypo["strategy"] is VolumeBurst, f"{hypo_id} strategy must be VolumeBurst"
        assert hypo["tickers"] == [expected_ticker]
        assert hypo["bar"] == "1D"


def test_phase2o_hypo_008_4h_eth_params():
    """HYPO-008-VB-4H-ETH: VolumeBurst 4H — first viable sub-daily entry.

    IS Sharpe 0.33, n=53 trades (9x faster trade frequency than 1D).
    runner.py bar="4H" → OKX 4H candles natively supported.
    """
    hypo = next(
        (h for h in DAILY_PAPER_HYPOS if h["hypo_id"] == "HYPO-008-VB-4H-ETH"),
        None,
    )
    assert hypo is not None, "HYPO-008-VB-4H-ETH must be in DAILY_PAPER_HYPOS"
    assert hypo["strategy"] is VolumeBurst
    assert hypo["tickers"] == ["ETH-USDT"]
    assert hypo["bar"] == "4H"


def test_phase2o_all_runtime_instantiable():
    """Phase 2O 7 HYPO 모두 runtime instantiation 성공 (lessons #46).

    import 통과 != runtime 통과. 실제 생성 + evaluate 메서드 확인.
    """
    phase2o_hypos = [
        h for h in DAILY_PAPER_HYPOS if h["hypo_id"] in PHASE_2O_HYPO_IDS
    ]
    assert len(phase2o_hypos) == 7, (
        f"Expected 7 Phase 2O hypos, found {len(phase2o_hypos)}"
    )
    for h in phase2o_hypos:
        cls = h.get("strategy_cls") or h.get("strategy")
        params = h.get("strategy_params", {})
        try:
            instance = cls(**params)
        except Exception as e:
            pytest.fail(
                f"{h['hypo_id']}: {cls.__name__}(**params) instantiation failed: {e}"
            )
        assert hasattr(instance, "evaluate"), (
            f"{h['hypo_id']}: {cls.__name__} must have evaluate() method"
        )
        assert hasattr(instance, "min_window"), (
            f"{h['hypo_id']}: {cls.__name__} must have min_window attribute"
        )


def test_phase2o_all_have_paper_since_and_promotion_criteria():
    """Phase 2O: 모든 7 HYPO에 paper_since + promotion_criteria 필드 존재."""
    phase2o_hypos = [
        h for h in DAILY_PAPER_HYPOS if h["hypo_id"] in PHASE_2O_HYPO_IDS
    ]
    for h in phase2o_hypos:
        assert "paper_since" in h, (
            f"{h['hypo_id']} must have 'paper_since' field (ADR-010 60-day tracking)"
        )
        assert h["paper_since"] == "2026-05-04", (
            f"{h['hypo_id']} paper_since must be '2026-05-04', got {h['paper_since']}"
        )
        assert "promotion_criteria" in h, (
            f"{h['hypo_id']} must have 'promotion_criteria' dict (ADR-011 gate)"
        )
        crit = h["promotion_criteria"]
        assert crit.get("min_sharpe", 0) >= 0.3, (
            f"{h['hypo_id']} min_sharpe must be >= 0.3 (ADR-011 swing threshold)"
        )
        assert crit.get("min_calendar_days", 0) == 60, (
            f"{h['hypo_id']} min_calendar_days must be 60 (ADR-010)"
        )
