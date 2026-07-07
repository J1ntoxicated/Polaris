"""P2 — partial-close R-ledger leak: a PARTIAL close must stamp positions.pnl_r
AND fold the slice into cell/learner/posterior, summing with the eventual full
close to EXACTLY ONE whole-position fold (no double-count).

Forensic (2026-06-23 g1 audit): 2 positions (LUNA risk $67.33 / DOT $16.01)
closed predominantly via the PARTIAL path → positions.pnl_r stayed NULL and the
−$14.85 NET (93% of the gross loss) NEVER reached the cell matrix / Layer-5
learners / posterior. The R ledger read ~break-even (−0.0007R) while the dollar
ledger was −$15.93. The learning layer under-recognised the loss (optimism bias).

FIX (measurement/learning only — sizing / -1.0R rail / order path untouched,
flow_not_block): the partial-close path now computes the SLICE's R rescaled by
the position's own staked risk (``realised_r`` is LINEAR in pnl_usd, so a
slice's R is its fraction of the whole-position R automatically), stamps it
cumulatively onto ``positions.pnl_r`` (so a partial-only position is no longer
NULL), and folds the REAL-fee NET slice R into the cell matrix / learners /
meta-label / posterior. The full-close path folds only its FINAL-SLICE R, so
partial + remainder sum to exactly one whole-position fold. A SINGLE-SHOT full
close (no prior partial) is byte-identical — the slice IS the whole.

2026-07-07 re-base: the R denominator is now ``positions.risk_usd`` (per-trade
staked risk) instead of the per-stream ``R_budget`` constant
(``realised_r_stream``) — ``_seed`` below stamps a ``risk_usd`` so every
expected-value computation in this file uses ``realised_r(pnl_usd, risk_usd)``.

DEMO/PAPER only; virtual funds. No defensive throttle, no size dampen, no chain
multiplier. #46 single-truth preserved: fills.pnl_usd (gross) + fills.fee_usd
(real) stay the truth; net R is an in-memory learning derivation.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import realised_r
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


_RISK_USD = 500.0  # per-trade staked risk stamped on every seeded position


def _seed(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float,
    entry_price: float = 100.0, bar_close: float = 130.0, side: str = "long",
    risk_usd: float = _RISK_USD,
) -> None:
    """OPEN position + entry fill + fresh 1m bars (mark vs entry sets the sign)."""
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', ?, ?, 'open', ?, 0, ?)",
        (position_id, side, base_qty, NOW, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', ?, ?, ?, ?, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, side, base_qty, entry_price,
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


def _trade(position_id: str, base_qty: float, *, side: str = "long") -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side=side, entry_price=100.0,
        notional_usd=base_qty * 100.0, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


class _FixedFillOKX:
    """OKX adapter mock closing EXACTLY ``filled_base`` (within-child partial)."""

    def __init__(self, *, filled_base: float, price: float = 130.0) -> None:
        self._filled = filled_base
        self._price = price
        self.requested: list[float] = []
        self._used = False
        self._ord_prefix = f"sell_{uuid.uuid4().hex[:6]}"

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        return {"data": []}

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        self.requested.append(float(kwargs["notional_usd"]))
        ord_id = f"{self._ord_prefix}_{len(self.requested)}"
        return OKXOrderResponse(
            ok=True, venue_order_id=ord_id, client_order_id="cl",
            code="0", msg="", raw={},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Cell-matrix / learner fold spy: capture every (pnl_r, won) folded so we can
# assert (a) a partial close folds, (b) partial+full sum to the whole exactly
# once, (c) a single-shot full close is byte-identical (whole, once).
# ---------------------------------------------------------------------------


class _FoldSpy:
    def __init__(self) -> None:
        self.cell: list[tuple[float, bool]] = []
        self.learner: list[tuple[float, bool]] = []
        self.posterior: list[tuple[float, float]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch BOTH the full-close namespace (``_production_close`` imports the
        # helpers locally) AND the slice-fold namespace (``_production_close_
        # effects.fold_close_slice`` calls them from its own module), so a fold
        # via EITHER path is captured.
        import polaris.scripts._production_close as full_mod
        import polaris.scripts._production_close_effects as eff_mod

        def _cell(conn, *, trade, regime, pnl_r, won, now_ts, state):  # type: ignore[no-untyped-def]
            self.cell.append((pnl_r, won))

        def _learn(conn, *, trade, regime, pnl_r, won, now_ts, state):  # type: ignore[no-untyped-def]
            self.learner.append((pnl_r, won))

        def _post(conn, *, trade, regime, pnl_r_net, pnl_r_gross, now_ts):  # type: ignore[no-untyped-def]
            self.posterior.append((pnl_r_net, pnl_r_gross))

        def _meta(conn, *, trade, regime, pnl_r, won, now_ts, state):  # type: ignore[no-untyped-def]
            pass

        for mod in (full_mod, eff_mod):
            monkeypatch.setattr(mod, "_safe_update_cell_matrix", _cell)
            monkeypatch.setattr(mod, "_safe_run_learners", _learn)
            monkeypatch.setattr(mod, "_safe_update_posterior", _post)
            monkeypatch.setattr(mod, "_safe_record_meta_label", _meta)


@pytest.mark.asyncio
async def test_partial_close_folds_slice_into_learners(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LOSING partial close (60 of 100 base) folds the slice's NET R into the
    cell matrix + learners with won=False, and stamps positions.pnl_r non-NULL
    (the −$14.85 leak fix). Entry 100, exit 80 → loss."""
    spy = _FoldSpy()
    spy.install(monkeypatch)
    base_qty = 100.0
    _seed(memdb, position_id="pos-loss", base_qty=base_qty,
          entry_price=100.0, bar_close=80.0)
    state = ProdLoopState()
    trade = _trade("pos-loss", base_qty)
    state.open_trades = [trade]
    adapter = _FixedFillOKX(filled_base=60.0, price=80.0)

    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=adapter,
    )

    assert ok is True
    # Position kept OPEN (remainder closes next tick), but the slice DID fold.
    assert len(state.open_trades) == 1
    assert len(spy.cell) == 1
    assert len(spy.learner) == 1
    slice_r, won = spy.cell[0]
    # Losing slice → folded with won=False and a NEGATIVE R (loss recognised).
    assert won is False
    assert slice_r < 0.0
    # positions.pnl_r stamped non-NULL (slice contribution) — the leak fix.
    pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-loss'"
    ).fetchone()[0]
    assert pnl_r is not None
    assert pnl_r < 0.0


@pytest.mark.asyncio
async def test_partial_then_full_folds_whole_once_no_double_count(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """partial(60) + full(40) folds two slice events whose R SUMS to the whole
    position R EXACTLY ONCE (no double-count), and positions.pnl_r ends at the
    whole-position R. Entry 100, exit 130 → win."""
    spy = _FoldSpy()
    spy.install(monkeypatch)
    base_qty = 100.0
    _seed(memdb, position_id="pos-pf", base_qty=base_qty,
          entry_price=100.0, bar_close=130.0)
    state = ProdLoopState()
    trade = _trade("pos-pf", base_qty)
    state.open_trades = [trade]

    # Tick 1 — 60 of 100 (partial, stays open).
    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=60.0),
    )
    # Tick 2 — remaining 40 (full close).
    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW + 60,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=40.0),
    )

    assert trade.closed is True
    # Two fold events (one per slice).
    assert len(spy.cell) == 2
    # The whole-position R the dollar ledger implies: Σ close-fill pnl_usd / R_budget.
    fills_total = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = 'pos-pf' "
        "AND is_close = 1"
    ).fetchone()[0]
    whole_gross_r = realised_r(pnl_usd=fills_total, risk_usd=_RISK_USD)
    # spy.posterior stores (pnl_r_net, pnl_r_gross); sum the GROSS (index 1).
    folded_gross_sum = sum(gross for _net, gross in spy.posterior)
    # No double-count: the GROSS R folded across both slices sums to EXACTLY the
    # whole-position gross R (realised_r is linear in pnl_usd, so the two
    # slice Rs add to the whole). Both slices are wins.
    assert folded_gross_sum == pytest.approx(whole_gross_r, rel=1e-6)
    assert all(won for _, won in spy.cell)
    # positions.pnl_r ends at the whole-position R (accumulated slice stamps).
    pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-pf'"
    ).fetchone()[0]
    assert pnl_r == pytest.approx(whole_gross_r, rel=1e-6)


@pytest.mark.asyncio
async def test_single_shot_full_close_fold_unchanged(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION GUARD: a single full close (no prior partial) folds the WHOLE
    position R ONCE — byte-identical to legacy. The slice IS the whole."""
    spy = _FoldSpy()
    spy.install(monkeypatch)
    base_qty = 100.0
    _seed(memdb, position_id="pos-1shot", base_qty=base_qty,
          entry_price=100.0, bar_close=130.0)
    state = ProdLoopState()
    trade = _trade("pos-1shot", base_qty)
    state.open_trades = [trade]

    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=99.8),
    )

    assert trade.closed is True
    assert len(spy.cell) == 1  # exactly one fold
    fills_total = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = 'pos-1shot' "
        "AND is_close = 1"
    ).fetchone()[0]
    whole_gross_r = realised_r(pnl_usd=fills_total, risk_usd=_RISK_USD)
    # The single fold's GROSS R is the whole position R.
    assert spy.posterior[0][1] == pytest.approx(whole_gross_r, rel=1e-9)
    # positions.pnl_r is the whole position R.
    pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-1shot'"
    ).fetchone()[0]
    assert pnl_r == pytest.approx(whole_gross_r, rel=1e-9)


@pytest.mark.asyncio
async def test_partial_only_position_no_nulled_r_leak(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LUNA/DOT leak: a position that only ever closes via PARTIALS (the
    remainder never reaches a full close in this window) still carries its
    realised R on positions.pnl_r and has folded its loss — NOT a NULL/0 leak.
    Two losing partials (40 + 35 of 100), remainder 25 stays open."""
    spy = _FoldSpy()
    spy.install(monkeypatch)
    base_qty = 100.0
    _seed(memdb, position_id="pos-luna", base_qty=base_qty,
          entry_price=100.0, bar_close=70.0)  # entry 100 → exit 70 = loss
    state = ProdLoopState()
    trade = _trade("pos-luna", base_qty)
    state.open_trades = [trade]

    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=40.0, price=70.0),
    )
    await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW + 60,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True, okx_adapter=_FixedFillOKX(filled_base=35.0, price=70.0),
    )

    # Still open (remainder 25), but BOTH partials folded their losses.
    assert len(state.open_trades) == 1
    assert len(spy.cell) == 2
    assert all(won is False for _, won in spy.cell)
    # positions.pnl_r reflects the realised loss so far (sum of two slice stamps),
    # NOT NULL — the leak is closed.
    pnl_r = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-luna'"
    ).fetchone()[0]
    assert pnl_r is not None
    closed_usd = memdb.execute(
        "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = 'pos-luna' "
        "AND is_close = 1"
    ).fetchone()[0]
    expected_r = realised_r(pnl_usd=closed_usd, risk_usd=_RISK_USD)
    assert pnl_r == pytest.approx(expected_r, rel=1e-6)
