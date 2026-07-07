"""Re-base ``pnl_r`` onto per-trade staked risk (``risk_usd``) — 2026-07-07.

DEMO/PAPER only; virtual funds. MANDATE-SAFE, /debate-cleared: ``sign(pnl_r) ==
sign(pnl_usd)`` preserved, the dollar (``fills.pnl_usd``) truth untouched, this
UN-CRUSHES measurement (never throttles/blocks/rejects), T4 sizing stays
byte-identical (sizing keys off the stop-distance SSOT + a SEPARATE
``r_budget_for_venue`` constant, never ``pnl_r``).

Canonical fix: ``pnl_r ≡ realized_usd / risk_usd_at_entry`` (the per-trade
dollar staked risk persisted on ``positions.risk_usd``) REPLACES the
per-stream ``R_budget`` ledger (``realised_r_stream``) as the CLOSE-PATH
realised-R denominator. The live DECISION path (``compute_unrealized_pnl_r`` /
G6/G7) is a STAGED FOLLOW-UP — untouched here.

Covers:
  1. donchian NEAR repro (realized/risk -> pnl_r, was crushed under the old
     per-stream ruler).
  2. FX fix — a Capital adopt-path position with a non-USD quote_ccy gets a
     correct (not raw-FX-inflated) risk_usd when a rate resolver is injected.
  3. floor — a micro-notional risk_usd is bounded (RISK_USD_ABS_FLOOR), no
     re-explosion.
  4. partial-close linearity — sum of slice pnl_r == whole-position pnl_r.
  5. sign — sign(pnl_r) == sign(pnl_usd) at every close chokepoint.
  6. sizing isolation regression — T4 ``compute_size`` output is byte-identical
     regardless of ``positions.pnl_r`` / ``risk_usd`` values in the DB.
  7. ruler unification — positions.pnl_r == segments.pnl_r == probe.
     realized_pnl_r for a fixture.
  8. migration — a DB fixture with old-ruler rows backfills correctly, AND
     skipping the FX re-derive step yields a WRONG (still FX-crushed) pnl_r
     for the affected row (order is load-bearing).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from polaris.core.lifecycle.recover import _import_one
from polaris.core.metrics.risk_unit import (
    RISK_USD_ABS_FLOOR,
    realised_r,
    risk_usd_at_entry,
)
from polaris.core.probes.tuning_log import log_probe_decisions, open_probe_db
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState
from polaris.core.sizing.engine import compute_size
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._production_close_helpers import (
    _position_risk_usd,
    real_pnl_r_from_fills,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState
from tools.rebase_pnl_r_to_risk_usd import (
    apply_backfill,
    apply_fx_rederive,
    compute_backfill,
    compute_fx_rederive,
    reset_learned_aggregates,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_position(
    conn: sqlite3.Connection, *, position_id: str, venue: str, symbol: str,
    entry_price: float, exit_price: float, base_qty: float, risk_usd: float | None,
    side: str = "long", status: str = "closed",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts, swap_count, risk_usd) "
        "VALUES (?, ?, ?, '', 'tsmom', 'tsmom', 'tsmom', ?, ?, ?, ?, ?, 0, ?)",
        (position_id, venue, symbol, side, base_qty, status, NOW, NOW + 60, risk_usd),
    )
    pnl_abs = (exit_price - entry_price) if side == "long" else (entry_price - exit_price)
    pnl_usd = pnl_abs * base_qty
    conn.execute(
        "INSERT INTO fills (fill_id, ts_ms, strategy_id, instrument_id, venue, "
        "side, base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        "is_close, contribution_id, order_id, state) VALUES "
        "(?, ?, 'tsmom', ?, ?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, f"{venue}:{symbol}", venue, side,
            base_qty, entry_price, base_qty * entry_price, position_id,
            uuid.uuid4().hex,
        ),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, ts_ms, strategy_id, instrument_id, venue, "
        "side, base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        "is_close, contribution_id, order_id, state) VALUES "
        "(?, ?, 'tsmom', ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, 1, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, (NOW + 60) * 1000, f"{venue}:{symbol}", venue,
            "sell" if side == "long" else "buy", base_qty, exit_price,
            base_qty * exit_price, pnl_usd, position_id, uuid.uuid4().hex,
        ),
    )
    conn.execute(
        "INSERT INTO position_strategy_segments "
        "(position_id, segment_id, strategy_id, started_ts, pnl_r, pnl_usd, "
        " venue, ticker) VALUES (?, ?, 'tsmom', ?, 0.0, 0.0, ?, ?)",
        (position_id, uuid.uuid4().hex, NOW, venue, symbol),
    )


# ---------------------------------------------------------------------------
# 1. donchian NEAR repro
# ---------------------------------------------------------------------------


def test_donchian_near_repro_pnl_r() -> None:
    """realized=-306.23 / risk=631.12 -> pnl_r ~= -0.485 (was -0.194 under the
    old per-stream R_budget ruler — the fix un-crushes the measured loss)."""
    pnl_r = realised_r(pnl_usd=-306.23, risk_usd=631.12)
    assert pnl_r == pytest.approx(-0.485, abs=0.001)


# ---------------------------------------------------------------------------
# 2. FX fix — recover.py adopt path threads quote_usd_rate
# ---------------------------------------------------------------------------


def test_fx_fix_j225_adopt_risk_usd_not_150x_crushed(memdb: sqlite3.Connection) -> None:
    """A J225 (JPY-quoted) adopt-import WITHOUT a rate resolver stamps a
    raw-JPY-level risk_usd (the pre-fix defect); WITH one, risk_usd converts to
    real USD (~$300 order of magnitude, not $47k+)."""

    def _anchor(conn: sqlite3.Connection, instrument_id: str, now_ts: int) -> tuple[float, str]:
        return (0.01, "1m")  # 1% ATR anchor

    def _rate_fn(conn: sqlite3.Connection, venue: str, symbol: str) -> float | None:
        return 0.0067  # illustrative JPY->USD

    trade_no_rate = _import_one(
        memdb, venue="capital", symbol="J225", side="long", mark=38000.0,
        base_qty=1.0, now_ts=NOW, deal_id="d1", atr_anchor_fn=_anchor,
    )
    assert trade_no_rate is not None
    risk_usd_no_rate = memdb.execute(
        "SELECT risk_usd FROM positions WHERE position_id = ?",
        (trade_no_rate.position_id,),
    ).fetchone()[0]
    # No rate injected -> defaults to quote_usd_rate=1.0 -> raw JPY level.
    assert risk_usd_no_rate == pytest.approx(
        risk_usd_at_entry(entry_price=38000.0, entry_atr_pct=0.01, base_qty=1.0)
    )
    assert risk_usd_no_rate > 500.0  # the raw-JPY-level defect magnitude

    trade_with_rate = _import_one(
        memdb, venue="capital", symbol="J225", side="long", mark=38000.0,
        base_qty=1.0, now_ts=NOW + 1, deal_id="d2", atr_anchor_fn=_anchor,
        quote_usd_rate_fn=_rate_fn,
    )
    assert trade_with_rate is not None
    risk_usd_with_rate = memdb.execute(
        "SELECT risk_usd FROM positions WHERE position_id = ?",
        (trade_with_rate.position_id,),
    ).fetchone()[0]
    expected = risk_usd_at_entry(
        entry_price=38000.0, entry_atr_pct=0.01, base_qty=1.0, quote_usd_rate=0.0067,
    )
    assert risk_usd_with_rate == pytest.approx(expected)
    # FX-corrected risk_usd is NOT the 150x-inflated raw-JPY figure.
    assert risk_usd_with_rate < risk_usd_no_rate / 10.0


def test_fx_fix_none_resolver_result_degrades_to_rate_1(memdb: sqlite3.Connection) -> None:
    """A resolver that returns ``None`` (unresolvable) degrades to rate=1.0,
    never crashes, never fabricates."""

    def _anchor(conn: sqlite3.Connection, instrument_id: str, now_ts: int) -> tuple[float, str]:
        return (0.01, "1m")

    def _rate_fn(conn: sqlite3.Connection, venue: str, symbol: str) -> float | None:
        return None

    trade = _import_one(
        memdb, venue="capital", symbol="J225", side="long", mark=38000.0,
        base_qty=1.0, now_ts=NOW, deal_id="d3", atr_anchor_fn=_anchor,
        quote_usd_rate_fn=_rate_fn,
    )
    assert trade is not None
    risk_usd = memdb.execute(
        "SELECT risk_usd FROM positions WHERE position_id = ?", (trade.position_id,),
    ).fetchone()[0]
    assert risk_usd == pytest.approx(
        risk_usd_at_entry(entry_price=38000.0, entry_atr_pct=0.01, base_qty=1.0)
    )


# ---------------------------------------------------------------------------
# 3. floor — micro-notional risk_usd bounded, no re-explosion
# ---------------------------------------------------------------------------


def test_floor_micro_notional_risk_usd_bounds_pnl_r() -> None:
    """A degenerate-small risk_usd is floored (RISK_USD_ABS_FLOOR) upstream by
    risk_usd_at_entry; realised_r on the FLOORED value stays bounded (no
    phantom multi-hundred-R explosion on a tiny -$2 loss)."""
    risk_usd = risk_usd_at_entry(entry_price=0.001, entry_atr_pct=1e-6, base_qty=1.0)
    assert risk_usd >= RISK_USD_ABS_FLOOR
    pnl_r = realised_r(pnl_usd=-2.0, risk_usd=risk_usd)
    assert abs(pnl_r) <= 100.0  # R_CLAMP bound, never explodes past the clamp
    assert pnl_r < 0.0


# ---------------------------------------------------------------------------
# 4. partial-close linearity
# ---------------------------------------------------------------------------


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


class _FixedFillOKX:
    def __init__(self, *, filled_base: float, price: float) -> None:
        self._filled = filled_base
        self._price = price
        self._used = False
        self._ord_prefix = f"sell_{uuid.uuid4().hex[:6]}"

    async def fetch_balance(self, ccy: str | None = None) -> dict:  # type: ignore[type-arg]
        return {"data": []}

    async def place_market_order(self, **kwargs: object) -> object:
        from polaris.venues.okx.adapter import OKXOrderResponse

        return OKXOrderResponse(
            ok=True, venue_order_id=f"{self._ord_prefix}_1",
            client_order_id="cl", code="0", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict:  # type: ignore[type-arg]
        if self._used:
            return {"data": []}
        self._used = True
        return {
            "data": [{
                "ordId": ord_id, "clOrdId": "cl", "instId": inst_id,
                "side": "sell", "tgtCcy": "base_ccy",
                "accFillSz": f"{self._filled:.10f}", "avgPx": str(self._price),
                "fee": "-0.02", "feeCcy": "USDT", "state": "filled",
                "uTime": str(NOW * 1000),
            }]
        }


def _seed_open_okx(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float, risk_usd: float,
    entry_price: float = 100.0, bar_close: float = 130.0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0, ?)",
        (position_id, base_qty, NOW, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, ts_ms, strategy_id, instrument_id, venue, "
        "side, base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        "is_close, contribution_id, order_id, state) VALUES "
        "(?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', 'long', ?, ?, ?, 0.05, 1.0, 0.0, "
        "0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
            base_qty * entry_price, position_id, uuid.uuid4().hex,
        ),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:SOL-USDT', 'crypto:SOL', 'okx', 'SOL-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0, 13000.0, 1, ?, ?, ?, 1.0, 'rest')",
            (ts, bar_close, bar_close + 1.0, bar_close - 1.0, bar_close,
             bar_close, bar_close, bar_close),
        )
    conn.execute(
        "INSERT INTO position_strategy_segments "
        "(position_id, segment_id, strategy_id, started_ts, pnl_r, pnl_usd, "
        " venue, ticker) VALUES (?, ?, 'tsmom', ?, 0.0, 0.0, 'okx', 'SOL-USDT')",
        (position_id, uuid.uuid4().hex, NOW),
    )


def _trade(position_id: str, base_qty: float) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=base_qty * 100.0, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


@pytest.mark.asyncio
async def test_partial_close_linearity_sum_equals_whole(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sum of the two partial-slice pnl_r stamps == the whole-position
    realised_r(realized_usd_total, risk_usd) — risk_usd-based R is linear in
    pnl_usd exactly like the old per-stream ruler was."""
    risk_usd = 500.0
    _seed_open_okx(memdb, position_id="pos-lin", base_qty=100.0, risk_usd=risk_usd)
    state = ProdLoopState()
    trade = _trade("pos-lin", 100.0)
    state.open_trades = [trade]

    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=60.0, price=130.0),
    )
    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW + 60,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=40.0, price=130.0),
    )
    assert trade.closed is True

    realized_usd_total = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = 'pos-lin' AND is_close = 1"
    ).fetchone()[0]
    expected_whole_r = realised_r(pnl_usd=realized_usd_total, risk_usd=risk_usd)
    stamped_pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-lin'"
    ).fetchone()[0]
    assert stamped_pnl_r == pytest.approx(expected_whole_r, rel=1e-6)


@pytest.mark.asyncio
async def test_single_shot_full_close_byte_identical_pnl_r(memdb: sqlite3.Connection) -> None:
    """REGRESSION GUARD: a single full close (no prior partial) — pnl_r ==
    realised_r(realized_usd, risk_usd) exactly (the slice IS the whole)."""
    risk_usd = 500.0
    _seed_open_okx(memdb, position_id="pos-1shot", base_qty=100.0, risk_usd=risk_usd)
    state = ProdLoopState()
    trade = _trade("pos-1shot", 100.0)
    state.open_trades = [trade]

    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=99.8, price=130.0),
    )
    assert trade.closed is True
    realized_usd = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = 'pos-1shot' AND is_close = 1"
    ).fetchone()[0]
    expected_r = realised_r(pnl_usd=realized_usd, risk_usd=risk_usd)
    pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-1shot'"
    ).fetchone()[0]
    assert pnl_r == pytest.approx(expected_r, rel=1e-9)


# ---------------------------------------------------------------------------
# 5. sign(pnl_r) == sign(pnl_usd)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pnl_usd", [-500.0, -1.0, 0.0, 1.0, 500.0])
def test_sign_matches_dollar_truth(pnl_usd: float) -> None:
    r = realised_r(pnl_usd=pnl_usd, risk_usd=250.0)
    if pnl_usd > 0.0:
        assert r > 0.0
    elif pnl_usd < 0.0:
        assert r < 0.0
    else:
        assert r == 0.0


@pytest.mark.asyncio
async def test_sign_matches_dollar_truth_at_close_chokepoint(memdb: sqlite3.Connection) -> None:
    _seed_open_okx(
        memdb, position_id="pos-sign-loss", base_qty=100.0, risk_usd=500.0,
        entry_price=100.0, bar_close=70.0,  # loss
    )
    state = ProdLoopState()
    trade = _trade("pos-sign-loss", 100.0)
    state.open_trades = [trade]
    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=99.8, price=70.0),
    )
    pnl_r, pnl_usd = memdb.execute(
        "SELECT p.pnl_r, (SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = p.position_id AND is_close = 1) "
        "FROM positions p WHERE position_id = 'pos-sign-loss'"
    ).fetchone()
    assert pnl_usd < 0.0
    assert pnl_r < 0.0


# ---------------------------------------------------------------------------
# 6. SIZING ISOLATION REGRESSION — T4 qty byte-identical before/after
# ---------------------------------------------------------------------------


def _intent() -> SignalIntent:
    return SignalIntent(
        signal_id="sig-iso", venue="okx", symbol="ISO-USDT",
        instrument_id="okx:ISO-USDT", underlying_group_id="crypto:ISO",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst", closed_trades=25,
        kelly_p=0.55, kelly_q=0.45, kelly_fraction=0.05, win_streak=2,
        hit_rate_10=0.55, updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


def test_t4_sizing_byte_identical_regardless_of_pnl_r_risk_usd(
    memdb: sqlite3.Connection,
) -> None:
    """PROOF sizing is untouched: compute_size output is IDENTICAL whether the
    DB's positions.pnl_r/risk_usd hold the OLD-ruler values, the NEW-ruler
    values, or nothing at all — T4 never reads either column."""
    sized_empty = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(),
        now_ts=NOW + 100,
    )

    _seed_position(
        memdb, position_id="pos-t4-a", venue="okx", symbol="ISO-USDT",
        entry_price=100.0, exit_price=70.0, base_qty=10.0, risk_usd=50.0,
    )
    memdb.execute(
        "UPDATE positions SET pnl_r = -999.0 WHERE position_id = 'pos-t4-a'"
    )
    sized_old_ruler = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(),
        now_ts=NOW + 100,
    )

    memdb.execute(
        "UPDATE positions SET pnl_r = 12345.0, risk_usd = 0.01 "
        "WHERE position_id = 'pos-t4-a'"
    )
    sized_new_ruler = compute_size(
        memdb, intent=_intent(), risk_state=_risk_state(), portfolio=_portfolio(),
        now_ts=NOW + 100,
    )

    assert sized_empty.final_risk_pct == sized_old_ruler.final_risk_pct == sized_new_ruler.final_risk_pct
    assert sized_empty.final_notional_usd == sized_old_ruler.final_notional_usd == sized_new_ruler.final_notional_usd


# ---------------------------------------------------------------------------
# 7. ruler unification — positions.pnl_r == segments.pnl_r == probe.realized_pnl_r
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ruler_unification_positions_segments_probe_agree(
    memdb: sqlite3.Connection, tmp_path: Path,
) -> None:
    """A single-shot full close: positions.pnl_r == segments.pnl_r (already
    proven live) == probe.realized_pnl_r (the unified basis, item 3)."""
    probe_db = open_probe_db(tmp_path / "probes.sqlite")
    try:
        risk_usd = 400.0
        _seed_open_okx(memdb, position_id="pos-unify", base_qty=100.0, risk_usd=risk_usd)
        state = ProdLoopState()
        state.probe_conn = probe_db
        trade = _trade("pos-unify", 100.0)
        state.open_trades = [trade]
        from polaris.core.probes import EngineDecision

        log_probe_decisions(
            probe_db, ts=NOW, run_id="r1", position_id="pos-unify", mode="observe",
            decision=EngineDecision(action="HOLD", composite_lean=0.0, applied=False),
            pnl_r_at_decision=0.0, pnl_r_truth=0.0, mark_source="bar", mark_age_ms=0,
            exit_state="open",
        )

        await _close_trade_with_real_pnl(
            memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
            lookup_regime=_regime_stub, gpt_client=None, phase="P0",
            real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=99.8, price=130.0),
        )

        positions_pnl_r = memdb.execute(
            "SELECT pnl_r FROM positions WHERE position_id = 'pos-unify'"
        ).fetchone()[0]
        segments_pnl_r = memdb.execute(
            "SELECT pnl_r FROM position_strategy_segments WHERE position_id = 'pos-unify'"
        ).fetchone()[0]
        probe_realized = probe_db.execute(
            "SELECT realized_pnl_r FROM probe_decisions WHERE position_id = 'pos-unify'"
        ).fetchone()[0]

        assert positions_pnl_r == pytest.approx(segments_pnl_r, rel=1e-6)
        assert positions_pnl_r == pytest.approx(probe_realized, rel=1e-6)
    finally:
        probe_db.close()


# ---------------------------------------------------------------------------
# 8. migration — order enforced (FX re-derive BEFORE backfill)
# ---------------------------------------------------------------------------


def test_migration_backfill_matches_realized_over_risk(memdb: sqlite3.Connection) -> None:
    _seed_position(
        memdb, position_id="pos-mig-1", venue="okx", symbol="LTC-USDT",
        entry_price=100.0, exit_price=70.0, base_qty=10.0, risk_usd=200.0,
    )
    memdb.execute("UPDATE positions SET pnl_r = -0.05 WHERE position_id = 'pos-mig-1'")  # old-ruler value

    results, excluded = compute_backfill(memdb)
    assert excluded == []
    result = next(r for r in results if r.position_id == "pos-mig-1")
    assert result.new_pnl_r == pytest.approx(realised_r(pnl_usd=-300.0, risk_usd=200.0))
    apply_backfill(memdb, results)
    new_pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-mig-1'"
    ).fetchone()[0]
    assert new_pnl_r == pytest.approx(result.new_pnl_r)


def test_migration_excludes_null_risk_usd_zombie_rows(memdb: sqlite3.Connection) -> None:
    _seed_position(
        memdb, position_id="pos-mig-zombie", venue="capital", symbol="EURUSD",
        entry_price=1.1, exit_price=1.1, base_qty=1000.0, risk_usd=None,
    )
    results, excluded = compute_backfill(memdb)
    assert excluded == ["pos-mig-zombie"]
    assert all(r.position_id != "pos-mig-zombie" for r in results)


def test_migration_order_enforced_skipping_fx_rederive_yields_wrong_pnl_r(
    memdb: sqlite3.Connection,
) -> None:
    """A Capital JPY-quoted position stamped WITHOUT the FX fix (raw-JPY-level
    risk_usd, the pre-fix defect) backfills a WRONG (still-crushed) pnl_r if
    the FX re-derive step is skipped — proving step order is load-bearing."""
    defective_risk_usd = 47_615.89  # raw-JPY-level (undivided) defect magnitude
    _seed_position(
        memdb, position_id="pos-mig-j225", venue="capital", symbol="J225",
        entry_price=38000.0, exit_price=37800.0, base_qty=1.0,
        risk_usd=defective_risk_usd,
    )
    # (b) WITHOUT running (a) first: backfill directly off the defective risk_usd.
    results_skipped, _ = compute_backfill(memdb)
    wrong = next(r for r in results_skipped if r.position_id == "pos-mig-j225").new_pnl_r

    # (a) THEN (b): FX re-derive corrects risk_usd to a real-USD order of
    # magnitude first (bars unavailable in this fixture -> supply an override).
    fx_results = compute_fx_rederive(memdb, rate_override={"JPY": 0.0067})
    apply_fx_rederive(memdb, fx_results)
    results_ordered, _ = compute_backfill(memdb)
    corrected = next(r for r in results_ordered if r.position_id == "pos-mig-j225").new_pnl_r

    # Skipping (a) produces a materially DIFFERENT (wrong) pnl_r than running
    # it first — the order is load-bearing, not cosmetic. Bound the error: the
    # undivided-JPY denominator is ~150x too large, so ``wrong`` is crushed to
    # near-zero (realised_r(-200, 47_615.89) ≈ -0.0042) while ``corrected``
    # (denominator FX-rescaled to real-USD order of magnitude, ~$317) is a
    # material fraction of a full R — not just "different", but wrong by
    # roughly the FX-crush ratio.
    assert abs(wrong) < 0.01
    assert abs(corrected) > 0.1
    assert wrong != pytest.approx(corrected, rel=1e-3)


def test_migration_idempotent_second_run_byte_identical(memdb: sqlite3.Connection) -> None:
    _seed_position(
        memdb, position_id="pos-mig-idem", venue="okx", symbol="ADA-USDT",
        entry_price=50.0, exit_price=55.0, base_qty=20.0, risk_usd=100.0,
    )
    results1, _ = compute_backfill(memdb)
    apply_backfill(memdb, results1)
    first = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-mig-idem'"
    ).fetchone()[0]
    results2, _ = compute_backfill(memdb)
    apply_backfill(memdb, results2)
    second = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-mig-idem'"
    ).fetchone()[0]
    assert first == second


def test_migration_reset_clears_cell_matrix_and_learner_tables(memdb: sqlite3.Connection) -> None:
    from polaris.core.cell_matrix import CellContext, CellKeyP0, TradeClose, update_on_trade_close

    update_on_trade_close(
        memdb,
        trade=TradeClose(
            key=CellKeyP0(exchange="okx", strategy="tsmom", ticker="X-USDT", regime="bull_trend"),
            context=CellContext(group="spot", session="asia", direction="long", liquidity_tier="high"),
            pnl_r=1.0, won=True, closed_ts=NOW,
        ),
    )
    assert memdb.execute("SELECT COUNT(*) FROM cell_matrix_p0").fetchone()[0] > 0
    cell_n, learner_n = reset_learned_aggregates(memdb)
    assert cell_n > 0
    assert memdb.execute("SELECT COUNT(*) FROM cell_matrix_p0").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM cell_matrix_parent3").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM cell_matrix_parent2").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM learner_state").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM learner_posterior").fetchone()[0] == 0


def test_migration_refuses_when_bot_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.ops.ops_config import OpsConfig
    from tools.rebase_pnl_r_to_risk_usd import BotLockHeldError, assert_bot_not_running

    pidfile = tmp_path / "production.pid"
    pidfile.write_text(str(__import__("os").getpid()) + "\n", encoding="utf-8")
    cfg = OpsConfig(
        project_dir=tmp_path, db_path=tmp_path / "db.sqlite",
        bot_log=tmp_path / "log", pidfile=pidfile, sentinel=tmp_path / "MANUAL_STOP",
        ops_dir=tmp_path / "ops", vault_dir=tmp_path / "vault",
        python_bin=tmp_path / "python",
    )
    with pytest.raises(BotLockHeldError):
        assert_bot_not_running(cfg)


# ---------------------------------------------------------------------------
# _position_risk_usd helper unit tests
# ---------------------------------------------------------------------------


def test_position_risk_usd_reads_persisted_column(memdb: sqlite3.Connection) -> None:
    _seed_position(
        memdb, position_id="pos-helper", venue="okx", symbol="BTC-USDT",
        entry_price=100.0, exit_price=110.0, base_qty=1.0, risk_usd=42.0,
    )
    assert _position_risk_usd(memdb, "pos-helper") == pytest.approx(42.0)


def test_position_risk_usd_none_position_id_returns_zero(memdb: sqlite3.Connection) -> None:
    assert _position_risk_usd(memdb, None) == 0.0


def test_position_risk_usd_missing_row_returns_zero(memdb: sqlite3.Connection) -> None:
    assert _position_risk_usd(memdb, "does-not-exist") == 0.0


def test_position_risk_usd_null_column_returns_zero(memdb: sqlite3.Connection) -> None:
    _seed_position(
        memdb, position_id="pos-null-risk", venue="okx", symbol="ETH-USDT",
        entry_price=100.0, exit_price=90.0, base_qty=1.0, risk_usd=None,
    )
    assert _position_risk_usd(memdb, "pos-null-risk") == 0.0


# ---------------------------------------------------------------------------
# real_pnl_r_from_fills uses risk_usd (not R_budget) — direct unit coverage
# ---------------------------------------------------------------------------


def test_real_pnl_r_from_fills_uses_position_risk_usd(memdb: sqlite3.Connection) -> None:
    _seed_open_okx(memdb, position_id="pos-direct", base_qty=100.0, risk_usd=500.0)
    trade = _trade("pos-direct", 100.0)
    pnl_r, pnl_usd, _exit_price = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=130.0,
    )
    expected_pnl_usd = (130.0 - 100.0) / 100.0 * (100.0 * 100.0)
    assert pnl_usd == pytest.approx(expected_pnl_usd)
    assert pnl_r == pytest.approx(realised_r(pnl_usd=expected_pnl_usd, risk_usd=500.0))


def test_real_pnl_r_from_fills_zero_risk_usd_yields_zero_r(memdb: sqlite3.Connection) -> None:
    _seed_open_okx(memdb, position_id="pos-zero-risk", base_qty=100.0, risk_usd=0.0)
    trade = _trade("pos-zero-risk", 100.0)
    pnl_r, _pnl_usd, _exit_price = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=130.0,
    )
    assert pnl_r == 0.0
