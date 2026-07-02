"""P1-11 tick↔bar parity — TDD for the 3 divergences.

DEMO/PAPER only (paper-trading bot, no real-money path touched). Aggressive
bias preserved — these are CORRECTNESS fixes (identical physical inputs must
size/exit identically on the bar vs tick path), never a size/entry cut. No
9-stack: the leverage + notional-clip fixes reuse the EXISTING single
``intent.leverage`` field / single clip slot, they add no new multiplier.

  ① tick exit horizon: ``_run_exits`` must use the SAME per-strategy horizon
     (``_horizon_seconds_for``) the bar recalc uses, not a held-time-derived
     ``max(held_seconds, 60)`` guess.
  ② tick sizing leverage: ``_sized_notional`` must derive venue-aware leverage
     (Capital FX 30 / index+commodity 20 / crypto 2; OKX spot fixed 1.0) via
     the SAME ``derive_leverage(resolve_stream(venue), asset_class)`` the bar
     path (T7) uses, instead of a hardcoded ``1.0``.
  ③ notional clip symmetry: the bar path's ``_production_run_signal.py``
     clamps ``final_notional_usd`` into ``[floor, ceiling]`` before submit;
     the tick path had no ceiling. Both now route through one shared,
     env-tunable ``clip_entry_notional_usd`` slot.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.scripts import _production_tick_engine as eng_mod

# ---------------------------------------------------------------------------
# ② leverage venue-awareness in the tick sizing path
# ---------------------------------------------------------------------------


def test_sized_notional_derives_capital_index_leverage_not_hardcoded_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capital index intent must size with leverage=20.0 (fallback table), not
    the hardcoded 1.0 the pre-fix ``_sized_notional`` always passed."""
    captured: dict[str, Any] = {}

    def _fake_compute_size(conn: Any, *, intent: Any, risk_state: Any,
                            portfolio: Any, now_ts: int) -> Any:
        captured["leverage"] = intent.leverage

        class _Sized:
            final_notional_usd = 100.0

        return _Sized()

    monkeypatch.setattr(eng_mod, "compute_size", _fake_compute_size)
    monkeypatch.setattr(
        eng_mod, "_read_strategy_risk_state", lambda *a, **k: object()
    )
    monkeypatch.setattr(eng_mod, "_read_portfolio_state", lambda *a, **k: object())

    from polaris.core.ticks.signals import TickIntent

    intent = TickIntent(
        venue="capital", symbol="GOLD", side="long", conviction=0.9,
        signal_id="micro_reversion", signal_family="reversion", ref_price=2000.0,
    )
    eng_mod._sized_notional(
        None, intent=intent, asset_class="index",
        underlying_group_id="cfd:GOLD", regime="trend", now_ts=int(time.time()),
    )

    assert captured["leverage"] == 20.0, (
        "Capital index tick intent must size with the venue-aware fallback "
        f"leverage (20.0), got {captured.get('leverage')} — the tick path is "
        "still hardcoding leverage=1.0, under-sizing every Capital CFD entry."
    )


def test_sized_notional_okx_spot_leverage_stays_fixed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OKX spot must stay leverage=1.0 (fixed-leverage stream) — no regression."""
    captured: dict[str, Any] = {}

    def _fake_compute_size(conn: Any, *, intent: Any, risk_state: Any,
                            portfolio: Any, now_ts: int) -> Any:
        captured["leverage"] = intent.leverage

        class _Sized:
            final_notional_usd = 100.0

        return _Sized()

    monkeypatch.setattr(eng_mod, "compute_size", _fake_compute_size)
    monkeypatch.setattr(
        eng_mod, "_read_strategy_risk_state", lambda *a, **k: object()
    )
    monkeypatch.setattr(eng_mod, "_read_portfolio_state", lambda *a, **k: object())

    from polaris.core.ticks.signals import TickIntent

    intent = TickIntent(
        venue="okx", symbol="BTC-USDT", side="long", conviction=0.9,
        signal_id="burst_rider", signal_family="momentum", ref_price=60000.0,
    )
    eng_mod._sized_notional(
        None, intent=intent, asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="trend", now_ts=int(time.time()),
    )

    assert captured["leverage"] == 1.0


# ---------------------------------------------------------------------------
# ③ notional clip symmetry (bar-only $5k ceiling vs tick uncapped)
# ---------------------------------------------------------------------------


def test_clip_entry_notional_usd_applies_floor_and_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Merge 2026-07-02 (P0-3 × P1-11 unification): the DEFAULT ceiling is
    # UNBOUNDED — the venue-aware notional cap is owned by T4's single
    # headroom_min() clip slot (engine.py notional_ceiling_pct, binding= log),
    # and a second silent $5k re-clip here was the exact P0-3 pathology. The
    # env knob remains as a manual ops override — clip mechanics verified with
    # it set.
    from polaris.core.sizing.schema import clip_entry_notional_usd

    assert clip_entry_notional_usd(1.0) == pytest.approx(10.0)  # floor-bump 유지
    assert clip_entry_notional_usd(50_000.0) == pytest.approx(50_000.0)  # 기본 무상한
    monkeypatch.setenv("POLARIS_ENTRY_NOTIONAL_CEILING_USD", "5000")
    assert clip_entry_notional_usd(50_000.0) == pytest.approx(5_000.0)  # 수동 오버라이드만 클립
    assert clip_entry_notional_usd(2_500.0) == pytest.approx(2_500.0)


def test_clip_entry_notional_ceiling_usd_has_no_floor_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick-path variant applies ONLY the shared ceiling — a tiny residual
    must pass through unchanged (no floor-bump) so the tick engine's existing
    sub-minimum drop-not-bump semantics stay intact. Ceiling default is
    unbounded (T4-owned cap); env override exercises the clip."""
    from polaris.core.sizing.schema import clip_entry_notional_ceiling_usd

    assert clip_entry_notional_ceiling_usd(0.0001) == pytest.approx(0.0001)
    assert clip_entry_notional_ceiling_usd(50_000.0) == pytest.approx(50_000.0)
    monkeypatch.setenv("POLARIS_ENTRY_NOTIONAL_CEILING_USD", "5000")
    assert clip_entry_notional_ceiling_usd(50_000.0) == pytest.approx(5_000.0)
    assert clip_entry_notional_ceiling_usd(2_500.0) == pytest.approx(2_500.0)


def test_tick_sized_notional_is_ceiling_clipped_same_as_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bar/tick cap SYMMETRY (P1-11 item 3, unified 2026-07-02 with P0-3): the
    venue-aware notional cap is owned by T4's headroom_min() slot, so a T4
    output passes the tick path UNCLIPPED by default (no silent second $5k
    re-clip — the exact P0-3 pathology). The shared env override still clips
    BOTH paths identically."""
    def _fake_compute_size(conn: Any, *, intent: Any, risk_state: Any,
                            portfolio: Any, now_ts: int) -> Any:
        class _Sized:
            final_notional_usd = 50_000.0

        return _Sized()

    monkeypatch.setattr(eng_mod, "compute_size", _fake_compute_size)
    monkeypatch.setattr(
        eng_mod, "_read_strategy_risk_state", lambda *a, **k: object()
    )
    monkeypatch.setattr(eng_mod, "_read_portfolio_state", lambda *a, **k: object())

    from polaris.core.ticks.signals import TickIntent

    intent = TickIntent(
        venue="okx", symbol="BTC-USDT", side="long", conviction=0.9,
        signal_id="burst_rider", signal_family="momentum", ref_price=60000.0,
    )
    kwargs = dict(
        intent=intent, asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="trend", now_ts=int(time.time()),
    )
    # 기본: T4 산출이 그대로 통과 (엔진 소유 캡 외 무음 재캡 없음)
    assert eng_mod._sized_notional(None, **kwargs) == pytest.approx(50_000.0)
    # 수동 오버라이드: 양 경로 공유 슬롯이 동일하게 클립
    monkeypatch.setenv("POLARIS_ENTRY_NOTIONAL_CEILING_USD", "5000")
    assert eng_mod._sized_notional(None, **kwargs) == pytest.approx(5_000.0)


# ---------------------------------------------------------------------------
# ① tick exit horizon must reuse the strategy's real horizon, not
# ``max(held_seconds, 60)``.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_exit_horizon_matches_strategy_expected_horizon(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.ticks.config import TickEngineConfig
    from polaris.scripts import _production_tick_exit as exit_mod
    from polaris.scripts._production_recalc import _horizon_seconds_for
    from polaris.scripts._production_state import ProdLoopState
    from polaris.scripts._production_tick_state import TickEngineState
    from polaris.scripts._smoke_fills import SimulatedTrade

    strategy_id = "burst_rider"
    expected_horizon = _horizon_seconds_for(strategy_id)
    # Sanity: the strategy-derived horizon differs from the naive
    # max(held_seconds, 60) floor at the held_seconds this test uses (30s), so
    # the assertion actually distinguishes the fix from the old behaviour.
    assert expected_horizon != 60

    now_ts = int(time.time())
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, exit_state, entry_atr_pct, mfe_r, mae_r, entry_regime) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pos-1", "okx", "BTC-USDT", "crypto:BTC", strategy_id, strategy_id,
            strategy_id, "long", 1.0, "active", now_ts - 30, "open", 0.02,
            0.0, 0.0, "trend",
        ),
    )
    memdb.commit()

    captured: dict[str, Any] = {}

    def _fake_assess_mode(**kwargs: Any) -> str | None:
        captured["horizon_seconds"] = kwargs["horizon_seconds"]
        return None

    monkeypatch.setattr(exit_mod, "assess_mode_for_position", _fake_assess_mode)

    async def _fake_run_precise_exit(**kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(exit_mod, "run_precise_exit", _fake_run_precise_exit)

    class _FakeWriter:
        def live_px(self, instrument_id: str) -> tuple[float, float]:
            return (101.0, time.monotonic())

        def feature_window(self, instrument_id: str) -> list[Any]:
            return []

    state = ProdLoopState()
    state.quote_writer = _FakeWriter()
    trade = SimulatedTrade(
        signal_id="sig-1", position_id="pos-1", venue="okx", symbol="BTC-USDT",
        side="long", entry_price=100.0, notional_usd=100.0,
        strategy_id=strategy_id, open_ts=now_ts - 30,
    )
    state.open_trades = [trade]
    eng = TickEngineState(cfg=TickEngineConfig())
    eng.family_by_position["pos-1"] = "momentum"

    await exit_mod._run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=time.monotonic(),
        phase="P0", real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=lambda *a, **k: "trend",
    )

    assert captured.get("horizon_seconds") == expected_horizon, (
        f"tick exit horizon_seconds={captured.get('horizon_seconds')} must match "
        f"the bar recalc's _horizon_seconds_for('{strategy_id}')="
        f"{expected_horizon} — a held-time floor is not the strategy's real "
        "horizon (P1-11 item 1)."
    )
