"""tests/paper/test_cron.py — ACTIVE_HYPOS 구성 검증 (Round 15 + Phase 2h Codex R15 fix).

Round 15 decision (Jin 2026-05-04):
- tick-driven scalp 전략 비활성화 → 1d trend portfolio 강화
- HYPO-003 SMA: 8 ticker (기존 3 → 확대)
- HYPO-004 Donchian: 2 variants (40/15 BTC+ETH + 20/10 BTC+ETH+SOL)

Phase 2h + Codex Round 15 ADR-010 compliance fix (2026-05-04):
- HYPO-020 VB-DONCH: cron ACTIVE_HYPOS에서 제거 (ADR-010 paper 단계 건너뜀 위반).
  cron = production trigger. HYPO-020은 DAILY_PAPER_HYPOS에서 paper 단계 검증 후 진입.
- ADR-010: BACKTEST → PAPER → Promotion Gate → cron 순서 의무.
"""
from __future__ import annotations

import pytest

from src.paper.cron import ACTIVE_HYPOS
from src.paper.daily_paper_runner import DAILY_PAPER_HYPOS
from src.strategies.confluence_signal import ConfluenceSignal
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.sma_crossover import SMACrossover
from src.strategies.volume_burst import VolumeBurst


# ── HYPO-003 SMA ──────────────────────────────────────────────────────────────


def test_active_hypos_sma_8_tickers():
    """Round 15: HYPO-003-SMA50-200 은 8 ticker 커버해야 함.

    기존 3 (BTC/ETH/SOL) → 8 (BTC/ETH/SOL/DOGE/ADA/XRP/ORDI/SUI)
    1d trend portfolio 강화 — 더 넓은 basket으로 edge 누적.
    """
    sma_hypos = [h for h in ACTIVE_HYPOS if "SMA" in h.get("hypo_id", "") or
                 h.get("strategy") is SMACrossover]
    assert sma_hypos, "ACTIVE_HYPOS must contain at least one SMACrossover HYPO"

    # 모든 SMA hypo ticker 합집합
    all_sma_tickers: set[str] = set()
    for h in sma_hypos:
        all_sma_tickers.update(h["tickers"])

    required_tickers = {
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT",
        "ADA-USDT", "XRP-USDT", "ORDI-USDT", "SUI-USDT",
    }
    missing = required_tickers - all_sma_tickers
    assert not missing, (
        f"SMA HYPO must cover 8 tickers. Missing: {missing}. "
        f"Current SMA tickers: {all_sma_tickers}"
    )


def test_active_hypos_sma_params_50_200():
    """HYPO-003-SMA50-200: fast=50, slow=200 확인."""
    sma_hypos = [h for h in ACTIVE_HYPOS if h.get("strategy") is SMACrossover]
    assert sma_hypos, "Must have at least one SMACrossover HYPO"

    # 50/200 variant 존재
    sma_50_200 = [
        h for h in sma_hypos
        if h.get("strategy_params", {}).get("fast") == 50
        and h.get("strategy_params", {}).get("slow") == 200
    ]
    assert sma_50_200, (
        "Must have SMA HYPO with fast=50, slow=200. "
        f"Existing SMA params: {[h.get('strategy_params') for h in sma_hypos]}"
    )


def test_active_hypos_sma_bar_1d():
    """SMA HYPO bar == '1D' (daily timeframe)."""
    sma_hypos = [h for h in ACTIVE_HYPOS if h.get("strategy") is SMACrossover]
    for h in sma_hypos:
        assert h.get("bar") == "1D", (
            f"{h['hypo_id']} bar must be '1D', got {h.get('bar')}"
        )


# ── HYPO-004 Donchian ─────────────────────────────────────────────────────────


def test_active_hypos_donchian_variants():
    """Round 15: Donchian 2 variants — (40,15) BTC+ETH + (20,10) BTC+ETH+SOL."""
    donch_hypos = [h for h in ACTIVE_HYPOS if h.get("strategy") is DonchianBreakout]
    assert len(donch_hypos) >= 2, (
        f"Must have at least 2 Donchian variants, got {len(donch_hypos)}"
    )

    # Variant 1: entry_period=40, exit_period=15 → BTC+ETH (기존 HYPO-004-BTC 계승)
    variant_40_15 = [
        h for h in donch_hypos
        if h.get("strategy_params", {}).get("entry_period") == 40
        and h.get("strategy_params", {}).get("exit_period") == 15
    ]
    assert variant_40_15, "Must have Donchian variant entry=40, exit=15"
    v40_tickers = set(variant_40_15[0]["tickers"])
    assert "BTC-USDT" in v40_tickers, "Donchian 40/15 must include BTC-USDT"
    assert "ETH-USDT" in v40_tickers, "Donchian 40/15 must include ETH-USDT"

    # Variant 2: entry_period=20, exit_period=10 → BTC+ETH+SOL (기존 HYPO-004-ETH 계승 + SOL 추가)
    variant_20_10 = [
        h for h in donch_hypos
        if h.get("strategy_params", {}).get("entry_period") == 20
        and h.get("strategy_params", {}).get("exit_period") == 10
    ]
    assert variant_20_10, "Must have Donchian variant entry=20, exit=10"
    v20_tickers = set(variant_20_10[0]["tickers"])
    assert "BTC-USDT" in v20_tickers, "Donchian 20/10 must include BTC-USDT"
    assert "ETH-USDT" in v20_tickers, "Donchian 20/10 must include ETH-USDT"
    assert "SOL-USDT" in v20_tickers, "Donchian 20/10 must include SOL-USDT"


def test_active_hypos_donchian_bar_1d():
    """Donchian HYPO bar == '1D'."""
    donch_hypos = [h for h in ACTIVE_HYPOS if h.get("strategy") is DonchianBreakout]
    for h in donch_hypos:
        assert h.get("bar") == "1D", (
            f"{h['hypo_id']} bar must be '1D', got {h.get('bar')}"
        )


# ── 전체 구조 ──────────────────────────────────────────────────────────────────


def test_active_hypos_strategy_classes_importable():
    """ACTIVE_HYPOS 내 모든 strategy class가 instantiate 가능해야 함 (runtime verify)."""
    for h in ACTIVE_HYPOS:
        cls = h["strategy"]
        params = h.get("strategy_params", {})
        try:
            instance = cls(**params)
        except Exception as e:
            pytest.fail(
                f"{h['hypo_id']} strategy {cls.__name__}(**{params}) failed: {e}"
            )
        assert hasattr(instance, "evaluate"), (
            f"{cls.__name__} must have evaluate() method"
        )


def test_active_hypos_required_fields():
    """모든 ACTIVE_HYPOS entry에 필수 필드 존재."""
    required = {"hypo_id", "strategy", "strategy_params", "tickers", "bar",
                "starting_usd", "max_position_pct"}
    for h in ACTIVE_HYPOS:
        missing = required - set(h.keys())
        assert not missing, (
            f"{h.get('hypo_id', '?')} missing required fields: {missing}"
        )
        assert isinstance(h["tickers"], list) and len(h["tickers"]) >= 1, (
            f"{h['hypo_id']} tickers must be non-empty list"
        )
        assert h["starting_usd"] > 0, f"{h['hypo_id']} starting_usd must be > 0"
        assert 0 < h["max_position_pct"] <= 1.0, (
            f"{h['hypo_id']} max_position_pct out of range"
        )


# ── HYPO-020 VB-DONCH Confluence ──────────────────────────────────────────────


def test_hypo_020_not_in_active_hypos():
    """Codex Round 15 ADR-010 fix: HYPO-020 은 cron ACTIVE_HYPOS 에 없어야 함.

    ADR-010: BACKTEST → PAPER → Promotion Gate → cron 순서 의무.
    cron = production trigger. HYPO-020 는 paper 단계 미검증 → cron 진입 불가.
    올바른 위치: DAILY_PAPER_HYPOS (daily_paper_runner.py).
    """
    ids = [h["hypo_id"] for h in ACTIVE_HYPOS]
    assert "HYPO-020-VB-DONCH-DOGE" not in ids, (
        "HYPO-020-VB-DONCH-DOGE must NOT be in cron ACTIVE_HYPOS (ADR-010 violation). "
        "Paper validation required before cron promotion. Current cron hypos: "
        + str(ids)
    )


def test_hypo_020_in_daily_paper_hypos():
    """Codex Round 15 ADR-010 fix: HYPO-020 은 DAILY_PAPER_HYPOS 에 존재해야 함.

    BACKTEST IS Sharpe 0.42 > ADR-011 기준 0.3 → paper 단계 시작.
    paper 60일 실측 후 Promotion Gate 통과 시 cron 진입 가능.
    """
    ids = [h["hypo_id"] for h in DAILY_PAPER_HYPOS]
    assert "HYPO-020-VB-DONCH-DOGE" in ids, (
        "HYPO-020-VB-DONCH-DOGE must be in DAILY_PAPER_HYPOS for paper validation. "
        f"Current DAILY_PAPER_HYPOS ids: {ids}"
    )


def test_hypo_020_vb_donch_deprecated_from_cron():
    """Phase 2h Codex R15: HYPO-020 이 cron에서 제거되었음을 명시적으로 확인.

    test_hypo_020_vb_donch_present (구 테스트) 는 삭제됨 — 역할 역전:
    ACTIVE_HYPOS에 없는 것이 정합 (ADR-010 위반 방지).
    """
    cron_ids = [h["hypo_id"] for h in ACTIVE_HYPOS]
    paper_ids = [h["hypo_id"] for h in DAILY_PAPER_HYPOS]
    assert "HYPO-020-VB-DONCH-DOGE" not in cron_ids, "Not in cron (ADR-010)"
    assert "HYPO-020-VB-DONCH-DOGE" in paper_ids, "But in paper runner"


