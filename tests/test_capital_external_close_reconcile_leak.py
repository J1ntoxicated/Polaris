"""FIX_EXIT — Capital external-close position must be mark-closed, not reconciled.

ROOT CAUSE (autopsy 2026-06-26, DB-proven on ``data/polaris_live.sqlite``):
Capital CFD positions ended ``status='reconciled'`` with ``pnl_r=NULL`` — their
realised edge silently discarded. 3/6 Capital closes leaked (fx_breakout_basket /
fx_range_fade EURUSD, each with a REAL persisted entry fill, zero close fills).

The leak: ``real_capital_close_fill`` fires ``close_position(deal_id)``; when the
venue REJECTS it AND ``list_positions`` no longer lists the dealId
([[capital/close]] "rejected AND no longer listed"), the deal is GONE — it was
CLOSED EXTERNALLY (demo EOD flatten / demo expiry / a Capital-side stop) before
our order. The helper returns ``CloseOrphan`` so the close stops re-firing — but
``_close_trade_with_real_pnl`` then routed ``CloseOrphan`` straight to
``_reconcile_orphan`` (pnl_r NULL) because the OKX winner-leak fix
([[okx_winner_reconcile_leak_2026-06-26]]) was gated on ``venue == "okx"``. The
position WAS real and DID realise a close at the venue — dropping it is the same
survivorship bias the OKX fix removed, just on the Capital track.

FIX (flow_not_block, exit-capture — not an entry block, not a throttle): when the
Capital close returns ``CloseOrphan`` but the position has a REAL entry fill and a
usable fresh mark, book a MARK-to-market close (real pnl_r, learner-fed) instead
of reconcile-with-NULL — the externally-closed position keeps its realised edge.
A position with NO real entry fill (a genuine un-addressable orphan — e.g. a
deal_id that was never captured) or no priceable mark still reconciles.

DEMO/PAPER only; virtual funds. The close leg is monkeypatched to the venue's
``CloseOrphan`` verdict; no network call.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import realised_r
from polaris.scripts import _production_close as pc
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_roundtrip_shared import CloseOrphan
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


def _seed_open(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    base_qty: float,
    entry_price: float,
    side: str = "long",
    symbol: str = "EURUSD",
    with_entry_fill: bool = True,
) -> None:
    entry_dir = "buy" if side == "long" else "sell"
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, mfe_r, mae_r, risk_usd) "
        "VALUES (?, 'capital', ?, ?, 'fx_breakout_basket', 'fx_breakout_basket', "
        " 'fx_breakout_basket', ?, ?, 'open', ?, 0, 1.2, -0.4, 24.5)",
        (position_id, symbol, f"fx:{symbol}", side, base_qty, NOW),
    )
    if with_entry_fill:
        conn.execute(
            "INSERT INTO fills "
            "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
            " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
            " contribution_id, order_id, state) "
            f"VALUES (?, ?, 'fx_breakout_basket', 'capital:{symbol}', 'capital', "
            f" ?, ?, ?, ?, 0.0, 1.0, 0.0, 0, ?, ?, 'filled')",
            (
                uuid.uuid4().hex, NOW * 1000, entry_dir, base_qty, entry_price,
                base_qty * entry_price, position_id, uuid.uuid4().hex,
            ),
        )


def _seed_fresh_bar(
    conn: sqlite3.Connection, *, close: float, symbol: str = "EURUSD",
) -> None:
    conn.execute(f"DELETE FROM bars WHERE instrument_id='capital:{symbol}'")
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        f"VALUES ('capital:{symbol}', 'fx:{symbol}', 'capital', ?, "
        " '1m', ?, ?, ?, ?, ?, 1000.0)",
        (symbol, NOW, close, close, close, close),
    )


def _trade(
    position_id: str, base_qty: float, entry_price: float,
    *, side: str = "long", symbol: str = "EURUSD",
) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="capital", symbol=symbol,
        strategy_id="fx_breakout_basket", side=side, entry_price=entry_price,
        notional_usd=base_qty * entry_price, open_ts=NOW, position_id=position_id,
        deal_id="deal-gone",
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "fx_trend"


def _patch_close_orphan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The venue close leg returns ``CloseOrphan`` (rejected AND no longer listed
    — the deal was closed externally) every tick."""

    async def _orphan(**kwargs: Any) -> CloseOrphan:
        return CloseOrphan(available=0.0)

    monkeypatch.setattr(pc, "_real_close_fill", _orphan)


@pytest.mark.asyncio
async def test_external_close_with_entry_fill_books_mark_close_not_reconcile(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leaked EURUSD position: venue close rejected + deal gone (external
    close), but a REAL entry fill + fresh mark → MARK close (status='closed',
    pnl_r STAMPED, close fill persisted, learner-fed) — NOT reconciled-with-NULL.
    The realised edge is captured."""
    entry, mark = 1.14035, 1.14235  # +20 pip favourable, a real winner
    _seed_open(memdb, position_id="pos-cap-win", base_qty=4300.0, entry_price=entry)
    _seed_fresh_bar(memdb, close=mark)
    _patch_close_orphan(monkeypatch)
    state = ProdLoopState()
    trade = _trade("pos-cap-win", 4300.0, entry)
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, capital_session=object(),
    )

    assert ok is True
    row = memdb.execute(
        "SELECT status, exit_state, pnl_r FROM positions "
        "WHERE position_id='pos-cap-win'"
    ).fetchone()
    assert row[0] == "closed"  # NOT 'reconciled'
    assert row[1] != "reconciled"
    assert row[2] is not None  # pnl_r STAMPED — the realised edge stays in the ledger
    # A real close fill is persisted (the realised exit), not dropped.
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills "
        "WHERE contribution_id='pos-cap-win' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 1
    # Learner-fed (real outcome), popped from open_trades, no orphan audit row.
    assert trade in state.closed_trades
    assert trade not in state.open_trades
    audit = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='orphan_reconciled'"
    ).fetchone()
    assert audit[0] == 0


@pytest.mark.asyncio
async def test_external_close_books_true_realised_pnl(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The booked mark-close PnL reflects the REAL move (entry vs fresh mark) on
    the real CFD exposure (size_usd from the entry fill), so the captured pnl_r is
    the actual realised edge — no longer NULL."""
    entry, mark, qty = 1.14035, 1.14235, 4300.0
    size_usd = qty * entry  # the persisted entry-fill CFD exposure
    _seed_open(memdb, position_id="pos-cap-win2", base_qty=qty, entry_price=entry)
    _seed_fresh_bar(memdb, close=mark)
    _patch_close_orphan(monkeypatch)
    state = ProdLoopState()
    trade = _trade("pos-cap-win2", qty, entry)
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, capital_session=object(),
    )
    assert ok is True
    row = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id='pos-cap-win2'"
    ).fetchone()
    assert row[0] is not None
    expected_pnl_usd = (mark - entry) / entry * size_usd
    assert row[0] == pytest.approx(
        realised_r(pnl_usd=expected_pnl_usd, risk_usd=24.5), rel=1e-6,
    )
    assert row[0] > 0.0  # captured a real WIN, not discarded


@pytest.mark.asyncio
async def test_external_close_short_books_correct_sign(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SELL-entry (short) externally-closed position books a correctly-signed
    realised pnl_r: price ROSE against a short → a real LOSS is captured (not
    dropped as NULL, not flipped to a win)."""
    entry, mark, qty = 1.14027, 1.14227, 4300.0  # price up = loss for a short
    _seed_open(
        memdb, position_id="pos-cap-short", base_qty=qty, entry_price=entry,
        side="short",
    )
    _seed_fresh_bar(memdb, close=mark)
    _patch_close_orphan(monkeypatch)
    state = ProdLoopState()
    trade = _trade("pos-cap-short", qty, entry, side="short")
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, capital_session=object(),
    )
    assert ok is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id='pos-cap-short'"
    ).fetchone()
    assert row[0] == "closed"
    assert row[1] is not None
    assert row[1] < 0.0  # short into a rising price = a real captured LOSS


@pytest.mark.asyncio
async def test_external_close_no_entry_fill_still_reconciles(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A position with NO real entry fill (an un-addressable orphan — the deal_id
    was never captured, nothing real to book) still reconciles with NULL pnl_r:
    the mark-close path applies ONLY to genuinely-tracked positions, so the
    orphan-recovery semantics are preserved (no fabricated outcome)."""
    _seed_open(
        memdb, position_id="pos-cap-ghost", base_qty=4300.0, entry_price=1.14035,
        with_entry_fill=False,
    )
    _seed_fresh_bar(memdb, close=1.14235)
    _patch_close_orphan(monkeypatch)
    state = ProdLoopState()
    trade = _trade("pos-cap-ghost", 4300.0, 1.14035)
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, capital_session=object(),
    )
    assert ok is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id='pos-cap-ghost'"
    ).fetchone()
    assert row[0] == "reconciled"  # no real base to book → reconcile preserved
    assert row[1] is None  # NO fabricated PnL
    close_rows = memdb.execute(
        "SELECT COUNT(*) FROM fills "
        "WHERE contribution_id='pos-cap-ghost' AND is_close=1"
    ).fetchone()
    assert close_rows[0] == 0  # no fabricated close fill


@pytest.mark.asyncio
async def test_external_close_no_fresh_mark_still_reconciles(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real entry fill but NO priceable fresh mark (no bar, no quote) →
    reconcile preserved: with no usable mark we will not fabricate a price. The
    homologous OKX guard ``fresh_mark > 0`` is mirrored — the conservative
    fall-through is keyed on a real bar/quote mark, not the stale entry price."""
    # NO _seed_fresh_bar — the only mark available is the stale entry price.
    _seed_open(
        memdb, position_id="pos-cap-nomark", base_qty=4300.0, entry_price=1.14035,
    )
    _patch_close_orphan(monkeypatch)
    state = ProdLoopState()
    trade = _trade("pos-cap-nomark", 4300.0, 1.14035)
    state.open_trades = [trade]

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, capital_session=object(),
    )
    assert ok is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id='pos-cap-nomark'"
    ).fetchone()
    assert row[0] == "reconciled"  # no real fresh mark → conservative reconcile
    assert row[1] is None
