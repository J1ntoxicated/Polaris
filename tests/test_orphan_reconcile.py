"""FIX 2 — true-orphan (available~0) close → status='reconciled' (state-drift
recovery), NOT an endless retry.

After the close-chunk fix some positions carry an internal base_qty > the demo
wallet balance (over-count). The OKX close balance-clamp finds available~0 and
returns gracefully, but the position stayed status='open' forever — the exit
engine + hydrate retried it every tick. No bleed, but stuck stale state.

This pins:
* ``real_okx_close_fill`` THREADS a distinguishable orphan signal back
  (CloseOrphan sentinel) when available~0 — a TRANSIENT reject (no-fill / clamp
  skipped) still returns ``None`` (retry next tick).
* ``_close_trade_with_real_pnl`` on the orphan signal marks the position
  ``status='reconciled'`` (NO close fill persisted, 0 realized pnl), pops from
  ``state.open_trades``, and writes an ``orphan_reconciled`` audit row.
* hydrate + load_active_position_rows EXCLUDE 'reconciled' (the stuck rows stop
  being retried once this code is live).

DEMO/PAPER only; virtual funds. State-drift recovery (flow_not_block) — no
defensive throttle, no size dampen, no chain multiplier, no P&L halt.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.scripts._production_close import (
    _close_trade_with_real_pnl,
    _real_close_fill,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_real_roundtrip import (
    CloseOrphan,
    real_okx_close_fill,
)
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Layer 1 — real_okx_close_fill orphan signal vs transient reject
# ---------------------------------------------------------------------------


class _OrphanOKX:
    """OKX adapter mock. ``avail`` controls the balance-clamp branch."""

    def __init__(self, *, avail: float | None = None, price: float = 10.0) -> None:
        self._avail = avail
        self._price = price
        self.sell_base_qtys: list[float] = []

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        if self._avail is None:
            return {"data": []}
        return {
            "data": [
                {"details": [{"ccy": ccy or "BTC", "availBal": str(self._avail)}]}
            ]
        }

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        self.sell_base_qtys.append(float(kwargs["notional_usd"]))
        # Transient: accepted but never fills.
        return OKXOrderResponse(
            ok=False, venue_order_id=None, client_order_id="cl",
            code="51201", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {"data": []}


@pytest.mark.asyncio
async def test_available_zero_returns_orphan_sentinel() -> None:
    """available ~0 → CloseOrphan sentinel (NOT bare None), nothing submitted."""
    adapter = _OrphanOKX(avail=0.0)
    out = await real_okx_close_fill(
        adapter, inst_id="FIL-USDT", base_qty=146.8, strategy_id="tsmom",
        mark_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(0.0)
    assert adapter.sell_base_qtys == []  # nothing submitted


@pytest.mark.asyncio
async def test_transient_reject_returns_none_not_orphan() -> None:
    """A transient reject (balance UNKNOWN, venue no-fill) returns None — a retry
    next tick, NOT an orphan reconcile (must be distinguishable)."""
    adapter = _OrphanOKX(avail=None)  # unknown balance → clamp skipped
    out = await real_okx_close_fill(
        adapter, inst_id="ALGO-USDT", base_qty=5.0, strategy_id="tsmom",
        mark_price=10.0, poll_delay_sec=0.0,
    )
    assert out is None
    assert not isinstance(out, CloseOrphan)


# ---------------------------------------------------------------------------
# Layer 2 — _close_trade_with_real_pnl reconciles on the orphan signal
# ---------------------------------------------------------------------------


def _seed_open(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float,
    entry_price: float = 0.12,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'FIL-USDT', 'crypto:FIL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0)",
        (position_id, base_qty, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:FIL-USDT', 'okx', 'long', ?, ?, ?, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
            base_qty * entry_price, position_id, uuid.uuid4().hex,
        ),
    )


def _trade(position_id: str, base_qty: float) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="FIL-USDT",
        strategy_id="tsmom", side="long", entry_price=0.12,
        notional_usd=base_qty * 0.12, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "chop"


@pytest.mark.asyncio
async def test_orphan_close_reconciles_position(memdb: sqlite3.Connection) -> None:
    """available~0 orphan → status='reconciled', NO close fill row, popped from
    open_trades, audit event written, 0 realized pnl."""
    base_qty = 146.8
    _seed_open(memdb, position_id="pos-orphan", base_qty=base_qty)
    state = ProdLoopState()
    trade = _trade("pos-orphan", base_qty)
    state.open_trades = [trade]
    adapter = _OrphanOKX(avail=0.0)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    # The position is terminal (reconciled), so the caller reports a handled
    # close (True) — the exit engine + hydrate must STOP retrying it.
    assert ok is True
    # status='reconciled', closed_ts stamped.
    row = memdb.execute(
        "SELECT status, closed_ts FROM positions WHERE position_id = 'pos-orphan'"
    ).fetchone()
    assert row[0] == "reconciled"
    assert row[1] == NOW
    # NO close fill persisted (no fabricated fill / pnl).
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id = 'pos-orphan' "
        "AND is_close = 1"
    ).fetchone()
    assert close_rows[0] == 0
    # Popped from open_trades; NOT recorded as a closed trade (no outcome).
    assert state.open_trades == []
    assert state.closed_trades == []
    # Audit event written (orphan_reconciled) carrying the position_id.
    audit = memdb.execute(
        "SELECT event_type, payload_json FROM risk_events "
        "WHERE event_type = 'orphan_reconciled'"
    ).fetchall()
    assert len(audit) == 1
    assert "pos-orphan" in audit[0][1]


@pytest.mark.asyncio
async def test_orphan_reconcile_leaves_pnl_r_null_records_drift_usd(
    memdb: sqlite3.Connection,
) -> None:
    """Step M (2026-06-22, Jin locked): a reconcile is a TRACKING FAILURE, not a
    trade. Its ``pnl_r`` is LEFT NULL so it is EXCLUDED from every R aggregation
    (PF/WR/avg_r/ticker R) — reverting the earlier mae→pnl_r stamp the design
    audit found INFLATED the R ledger (−211R artefact). The drift is instead
    surfaced as a SEPARATE rough DOLLAR estimate (mae_r × risk_usd) in the audit
    row. Still no fabricated close fill, no learner feed."""
    base_qty = 146.8
    _seed_open(memdb, position_id="pos-mae", base_qty=base_qty)
    # recalc had recorded a -5.21R worst adverse excursion + a $300 1R unit
    # before the venue drifted → rough drift estimate ≈ -5.21 × 300 = -$1563.
    memdb.execute(
        "UPDATE positions SET mae_r = -5.21, mfe_r = 0.3, risk_usd = 300.0 "
        "WHERE position_id = 'pos-mae'"
    )
    state = ProdLoopState()
    trade = _trade("pos-mae", base_qty)
    state.open_trades = [trade]
    adapter = _OrphanOKX(avail=0.0)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    row = memdb.execute(
        "SELECT status, exit_state, pnl_r FROM positions "
        "WHERE position_id = 'pos-mae'"
    ).fetchone()
    assert row[0] == "reconciled"
    assert row[1] == "reconciled"
    # pnl_r is LEFT NULL — a tracking failure is excluded from the R ledger.
    assert row[2] is None
    # The rough $ drift estimate (mae_r × risk_usd) is in the audit row instead.
    audit = memdb.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'orphan_reconciled' "
        "AND payload_json LIKE '%pos-mae%'"
    ).fetchone()
    assert audit is not None
    est_drift_usd = json.loads(audit[0])["est_drift_usd"]
    assert est_drift_usd == pytest.approx(-5.21 * 300.0)
    # Still no fabricated close fill, and not a learner-fed closed trade.
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id = 'pos-mae' "
        "AND is_close = 1"
    ).fetchone()
    assert close_rows[0] == 0
    assert state.closed_trades == []


@pytest.mark.asyncio
async def test_transient_reject_keeps_position_open(
    memdb: sqlite3.Connection,
) -> None:
    """A transient reject (balance unknown → None) preserves status='open' +
    retries — distinguished from the orphan reconcile path."""
    base_qty = 5.0  # small so the single child path is taken
    _seed_open(memdb, position_id="pos-transient", base_qty=base_qty,
               entry_price=10.0)
    state = ProdLoopState()
    trade = _trade("pos-transient", base_qty)
    trade.entry_price = 10.0
    state.open_trades = [trade]
    adapter = _OrphanOKX(avail=None)  # unknown balance, venue no-fill → None

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is False  # not handled → retry next tick
    row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = 'pos-transient'"
    ).fetchone()
    assert row[0] == "open"  # still open, will retry
    assert len(state.open_trades) == 1
    assert state.venue_close_rejects == 1
    # No reconcile audit for a transient reject.
    audit = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = 'orphan_reconciled'"
    ).fetchone()
    assert audit[0] == 0


# ---------------------------------------------------------------------------
# Layer 2b — Capital deal_id orphan vs transient (no session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_no_deal_id_returns_orphan_sentinel() -> None:
    """A Capital position with no deal_id (the open never captured one — a confirm
    that stayed PENDING past the poll budget) is un-addressable on the venue →
    CloseOrphan (reconcile, terminal), NOT a forever-retry None. New opens DO
    capture a deal_id (confirm-poll + positions.deal_id persist); this fires only
    for a legacy/PENDING orphan, which then reconciles instead of error-looping."""
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="capital", symbol="US100",
        strategy_id="micro_reversion", side="long", entry_price=100.0,
        notional_usd=1000.0, open_ts=NOW, position_id="pos-cap-orphan",
    )
    trade.deal_id = None
    result = await _real_close_fill(
        trade=trade, fresh_mark=100.0, okx_adapter=None, capital_session=object(),
    )
    assert isinstance(result, CloseOrphan)


@pytest.mark.asyncio
async def test_capital_no_session_returns_none_transient() -> None:
    """No session this tick (e.g. token refresh) is TRANSIENT → None (retry next
    tick), NOT an orphan reconcile — the deal_id is present, the session returns."""
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="capital", symbol="US100",
        strategy_id="micro_reversion", side="long", entry_price=100.0,
        notional_usd=1000.0, open_ts=NOW, position_id="pos-cap-live",
    )
    trade.deal_id = "00601567-0055-311e-0000-000084b918e3"
    result = await _real_close_fill(
        trade=trade, fresh_mark=100.0, okx_adapter=None, capital_session=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Layer 3 — status queries exclude 'reconciled'
# ---------------------------------------------------------------------------


def test_hydrate_excludes_reconciled(memdb: sqlite3.Connection) -> None:
    """A reconciled position is NOT re-hydrated on restart (no endless retry)."""
    from polaris.core.lifecycle.recover import hydrate_open_positions

    _seed_open(memdb, position_id="pos-rec", base_qty=146.8)
    memdb.execute(
        "UPDATE positions SET status = 'reconciled', closed_ts = ? "
        "WHERE position_id = 'pos-rec'",
        (NOW,),
    )
    assert hydrate_open_positions(memdb) == []


def test_load_active_position_rows_excludes_reconciled(
    memdb: sqlite3.Connection,
) -> None:
    """The live-recalc sweep must NOT pick up a reconciled position — otherwise
    the exit engine keeps trying to close it forever."""
    from polaris.scripts._production_recalc import load_active_position_rows

    _seed_open(memdb, position_id="pos-rec2", base_qty=146.8)
    memdb.execute(
        "UPDATE positions SET status = 'reconciled', closed_ts = ? "
        "WHERE position_id = 'pos-rec2'",
        (NOW,),
    )
    rows = load_active_position_rows(memdb)
    assert all(r["position_id"] != "pos-rec2" for r in rows)


def test_real_pnl_r_records_true_loss_beyond_old_10r_clamp(
    memdb: sqlite3.Connection,
) -> None:
    """No hidden ±10 clamp — the realised R records the TRUE move up to ±100.

    Step N (2026-06-23): realised R is the stream-common ``pnl_usd / R_budget``
    (OKX R_budget = 0.02 × $79k = $1,580), NOT the per-trade ATR anchor. The old
    ±10 clamp that HID losses is gone; the unified bound is ±100. A long entered
    at 100 closing at 30 → pnl_usd −70 → −70/1580 R (a small but HONEST loss),
    and a truly catastrophic $ loss clamps at −100R (not −10)."""
    from polaris.scripts._production_close import real_pnl_r_from_fills

    _seed_open(memdb, position_id="pos-clamp", base_qty=1.0, entry_price=100.0)
    trade = _trade("pos-clamp", 1.0)
    pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=30.0
    )
    assert exit_price == pytest.approx(30.0)
    assert pnl_usd == pytest.approx(-70.0)
    assert pnl_r == pytest.approx(-70.0 / r_budget_for_venue("okx"))

    # A genuinely catastrophic $ loss clamps at −100R (NOT the old −10 ceiling).
    # base_qty 10k @ entry 100 → $1M notional; exit 1 → ~−$990k → clamp −100R.
    _seed_open(memdb, position_id="pos-cat", base_qty=10_000.0, entry_price=100.0)
    memdb.execute(  # ensure fresh recent bars don't trump the override exit
        "DELETE FROM bars WHERE instrument_id='okx:FIL-USDT'"
    )
    cat_r, _u, _e = real_pnl_r_from_fills(
        memdb, trade=_trade("pos-cat", 10_000.0), exit_price_override=1.0,
    )
    assert cat_r == pytest.approx(-100.0)
    assert cat_r < -10.0
