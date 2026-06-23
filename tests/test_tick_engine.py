"""P5 tick-decision engine — integration TDD.

Replays a synthetic live tick stream into a fake ``quote_writer`` and asserts
the engine's decision → risk-gate → executor + per-tick exit behaviour:

  (a) a clear burst produces an entry intent → sized order (non-shadow),
  (b) shadow mode logs the decision but places NO order,
  (c) the freshness gate skips a stale symbol,
  (d) the bidirectional rule drops a 'short' on a long-only venue (direct),
  (e) no double-open when a position already exists on the symbol,
  (f) a reversion position exits on flow reversal.

D3 (/debate trading_params_audit_2026-06-22): the tick engine OWNS the
data-rich venues (``TICK_ENGINE_OWNED_VENUES`` = ``{okx, capital}``). OKX
carries full order-book depth + trade prints → all three microstructure signals
fire there, so the momentum/burst loop tests drive an ``okx:`` focus symbol.
Capital streams price quotes only (sizes/tape zeroed) → its tick path is
restricted to the overshoot fade (``micro_reversion``) — see
``test_capital_tick_path_is_fade_only``. Alpaca stays on the bar pipeline and is
filtered out by ``PHASE1_VENUES``.

Runs against the in-memory ``memdb`` fixture + a fake in-mem quote_writer, so no
WS / demo-venue network happens. Entries use the SIM fill path
(``real_roundtrip=False``) — the same ``reserve_and_submit`` the bar pipeline
uses, exercised without a live adapter.

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.core.ticks.config import (
    TICK_ENGINE_OWNED_VENUES,
    TickEngineConfig,
    venue_allowed_signals,
)
from polaris.core.ticks.types import TickSample
from polaris.scripts import _production_tick_engine as eng_mod
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_engine import (
    _FLOW_PRESSURE_TRAIL_MULT_DEFAULT,
    TickEngineState,
    _collect_intents,
    _drop_for_bidirectional,
    _flow_decay_exit,
    _mfe_protect_schedule,
    _momentum_trail_mult,
    _run_entries,
    _run_exits,
    _scalp_exit_decision,
)
from polaris.scripts._smoke_fills import SimulatedTrade

# OKX (spot, full depth) is the venue the tick engine drives for the momentum /
# burst loop tests — its WS feed carries the order-book sizes + trade tape every
# microstructure signal needs, so all three signals are structurally live. It is
# in ``TICK_ENGINE_OWNED_VENUES`` (D3) so it passes the ``PHASE1_VENUES`` filter.
VENUE = "okx"
SYMBOL = "BTC-USDT"
GROUP = "crypto:BTC"
ASSET_CLASS = "crypto"
INSTRUMENT = f"{VENUE}:{SYMBOL}"

# Capital (CFD) — owned, but price-quote-only → its tick path is overshoot-fade
# (``micro_reversion``) ONLY. Used by ``test_capital_tick_path_is_fade_only``.
CAP_VENUE = "capital"
CAP_SYMBOL = "GOLD"
CAP_GROUP = "cfd:GOLD"
CAP_ASSET_CLASS = "index"
CAP_INSTRUMENT = f"{CAP_VENUE}:{CAP_SYMBOL}"


# ---------------------------------------------------------------------------
# Fake quote writer — in-mem live_px + feature_window (mirrors the real API).
# ---------------------------------------------------------------------------


class FakeQuoteWriter:
    """In-mem stand-in for ``QuoteTickWriter`` (live_px + feature_window only)."""

    def __init__(self) -> None:
        self._window: dict[str, list[TickSample]] = {}
        self._px: dict[str, tuple[float, float]] = {}

    def set_stream(
        self, instrument_id: str, window: list[TickSample], last_mono: float
    ) -> None:
        self._window[instrument_id] = window
        self._px[instrument_id] = (window[-1].mid if window else 0.0, last_mono)

    def live_px(self, instrument_id: str) -> tuple[float, float] | None:
        return self._px.get(instrument_id)

    def feature_window(self, instrument_id: str) -> list[TickSample]:
        return self._window.get(instrument_id, [])


# Window ts are EPOCH SECONDS (the same clock domain ``compute_tick_features``
# compares ``now_ts`` against for its freshness gate). Ticks are spaced 1s and
# the newest sits at ``now_ts`` so the window reads as FRESH (age = 0). The
# ``live_px`` freshness uses the separately-supplied monotonic stamp.
def _ts_at(now_ts: int, i: int, n_ticks: int) -> int:
    """Epoch-second ts of tick ``i`` (of ``n_ticks``), newest landing on ``now_ts``."""
    return now_ts - (n_ticks - 1 - i)


def _tick(
    ts: int,
    mid: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    last_trade_price: float | None = None,
    last_trade_size: float = 1.0,
    spread: float = 0.02,
) -> TickSample:
    half = spread / 2.0
    bid = mid - half
    ask = mid + half
    spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    return TickSample(
        ts=ts,
        bid=bid,
        ask=ask,
        mid=mid,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price if last_trade_price is not None else mid,
        last_trade_size=last_trade_size,
        spread_bps=spread_bps,
    )


def _burst_window(now_ts: int, direction: int) -> list[TickSample]:
    """16 near-flat ticks then a sharp directional jump with aggressor flow.

    ``direction`` +1 = up burst (buyer-aggressor) → burst_rider long.
    The newest tick is stamped at ``now_ts`` (epoch seconds → fresh). The jump
    trade prints on the aggressor side so aggr_flow agrees with velocity.
    """
    n = 19
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.001 * (1 if i % 2 == 0 else -1)  # tiny noise baseline
        ticks.append(_tick(_ts_at(now_ts, i, n), mid))
    # Sharp jump on the last 3 ticks, trades printing on the aggressor side.
    for j in range(3):
        mid += direction * 0.5
        ltp = mid + direction * 0.05  # buyer lifts offer (up) / seller hits bid
        ticks.append(
            _tick(
                _ts_at(now_ts, 16 + j, n), mid,
                last_trade_price=ltp, last_trade_size=20.0,
                bid_size=30.0 if direction > 0 else 10.0,
                ask_size=10.0 if direction > 0 else 30.0,
            )
        )
    return ticks


def _overshoot_window(now_ts: int, direction: int) -> list[TickSample]:
    """A stretched mid with EXHAUSTING flow → micro_reversion fade.

    ``direction`` +1 = stretched UP with selling aggressor (flow opposes) →
    micro_reversion SHORT. The newest tick is stamped at ``now_ts`` (fresh).

    The stretch is a RAMP-then-sharp-final step (not a uniform jump): a uniform
    step is z-score scale-invariant (overshoot_z pinned ≈ 1.6), which fell below
    the re-aimed θ_r=2.0 fat-tail bar; the ramp pushes the latest mid well past
    its 3s EWMA anchor → overshoot_z ≈ 2.2 ≫ θ_r, a real cost-clearing overshoot.
    """
    n = 18
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(14):
        mid += 0.001 * (1 if i % 2 == 0 else -1)
        ticks.append(_tick(_ts_at(now_ts, i, n), mid))
    # Stretch the mid (ramp into a sharp final step), but trades print AGAINST the
    # move (exhaustion). overshoot_z ≈ 2.2 > θ_r=2.0 → the fade fires.
    for j, step in enumerate((0.2, 0.3, 0.4, 0.8)):
        mid += direction * step
        ltp = mid - direction * 0.1  # opposing aggressor (push exhausting)
        ticks.append(
            _tick(
                _ts_at(now_ts, 14 + j, n), mid,
                last_trade_price=ltp, last_trade_size=15.0,
            )
        )
    return ticks


# ---------------------------------------------------------------------------
# DB seeding.
# ---------------------------------------------------------------------------


def _seed(
    conn: sqlite3.Connection,
    *,
    regime: str,
    now_ts: int,
    venue: str = VENUE,
    symbol: str = SYMBOL,
    instrument: str = INSTRUMENT,
    group: str = GROUP,
    asset_class: str = ASSET_CLASS,
    product_class: str = "spot",
    stream_id: str = "A_okx_crypto",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " product_class, stream_id, quote_ccy, state, vol_24h_usd, spread_bps, "
        " atr_24h_pct, depth_10bps_usd, signal_density_7d, last_seen_ts, "
        " is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            venue, symbol, instrument, group, asset_class, product_class,
            stream_id, "USD", "live", 5e8, 2.0, 1.0, 1e7, 1.0, now_ts, 1,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO watchlist_focus "
        "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket) "
        "VALUES (?,?,?,?,?,?)",
        (now_ts, venue, symbol, 1.0, 1, "focus"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO regime_state "
        "(venue, underlying_group_id, regime, confidence, updated_ts) "
        "VALUES (?,?,?,?,?)",
        (venue, group, regime, 0.9, now_ts),
    )


def _seed_capital(conn: sqlite3.Connection, *, regime: str, now_ts: int) -> None:
    """Seed the Capital (CFD, price-quote-only) focus symbol."""
    _seed(
        conn, regime=regime, now_ts=now_ts, venue=CAP_VENUE, symbol=CAP_SYMBOL,
        instrument=CAP_INSTRUMENT, group=CAP_GROUP, asset_class=CAP_ASSET_CLASS,
        product_class="cfd", stream_id="B_capital_cfd",
    )


def _focus() -> list[tuple[str, str, str, str]]:
    return [(VENUE, SYMBOL, ASSET_CLASS, GROUP)]


def _focus_capital() -> list[tuple[str, str, str, str]]:
    return [(CAP_VENUE, CAP_SYMBOL, CAP_ASSET_CLASS, CAP_GROUP)]


# ---------------------------------------------------------------------------
# (a) clear burst → entry intent → sized order (non-shadow).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_produces_sized_order(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_ts, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.orders == 1, "a clear up-burst should open exactly one position"
    assert len(state.open_trades) == 1
    trade = state.open_trades[0]
    assert trade.side == "long"
    assert trade.venue == VENUE and trade.symbol == SYMBOL
    assert trade.notional_usd > 0.0, "the order must be sized via compute_size"
    position_id = trade.position_id
    assert position_id is not None, "the opened position must carry a persisted id"
    # The opened position is tagged with the tick signal_family (momentum here).
    assert eng.family_by_position[position_id] == "momentum"
    # A persisted positions row exists (entry path wrote it, not the hot path).
    row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", (position_id,)
    ).fetchone()
    assert row is not None and row[0] == "open"


# ---------------------------------------------------------------------------
# (b) shadow mode logs the decision but places NO order.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_logs_no_order(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_ts, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=True))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.shadow_logs >= 1, "shadow mode must LOG the decision"
    assert eng.orders == 0, "shadow mode must NOT place an order"
    assert state.open_trades == []
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# (c) freshness gate skips a stale symbol.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_symbol_skipped(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    # Same burst window, but the live_px monotonic stamp is far in the past →
    # stale past cfg.fresh_sec → the symbol is skipped.
    stale_mono = now_mono - 999.0
    writer.set_stream(INSTRUMENT, _burst_window(now_ts, +1), stale_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.skips_stale == 1
    assert eng.orders == 0
    assert state.open_trades == []


# ---------------------------------------------------------------------------
# (d) the bidirectional rule drops a 'short' on long-only venues (OKX/Alpaca)
#     but keeps it on bidirectional Capital. Tested directly: the tick engine
#     now owns only Capital (bidirectional), so the drop never fires in the
#     loop — but the long-only-drop logic must still be covered.
# ---------------------------------------------------------------------------


def test_bidirectional_rule_drops_short_on_long_only_venues() -> None:
    # Long-only spot (OKX) + cash equity (Alpaca): a SHORT is dropped (the rule
    # never silently flips a short into a long).
    assert _drop_for_bidirectional("okx", "short") is True
    assert _drop_for_bidirectional("alpaca", "short") is True
    # Capital (CFD, bidirectional): a SHORT is kept.
    assert _drop_for_bidirectional("capital", "short") is False
    # A 'long' is never dropped on any venue.
    assert _drop_for_bidirectional("okx", "long") is False
    assert _drop_for_bidirectional("alpaca", "long") is False
    assert _drop_for_bidirectional("capital", "long") is False


# ---------------------------------------------------------------------------
# (d2) D3 venue routing: OWN both data-rich venues; OKX runs the full signal
#      set, Capital is restricted to the price-based overshoot fade ONLY (its
#      WS feed zeroes sizes/tape so the flow signals are structurally dead).
# ---------------------------------------------------------------------------


def test_okx_and_capital_are_owned_okx_full_capital_fade_only() -> None:
    # D3: the fast loop owns the data-rich venues.
    assert "okx" in TICK_ENGINE_OWNED_VENUES
    assert "capital" in TICK_ENGINE_OWNED_VENUES
    # OKX (full depth + tape) → all three microstructure signals.
    assert venue_allowed_signals("okx") == frozenset(
        {"burst_rider", "flow_pressure", "micro_reversion"}
    )
    # Capital (price quotes only) → overshoot fade ONLY.
    assert venue_allowed_signals("capital") == frozenset({"micro_reversion"})
    # An unlisted venue degrades to the FULL set (flow_not_block — never muted).
    assert venue_allowed_signals("kraken") == frozenset(
        {"burst_rider", "flow_pressure", "micro_reversion"}
    )


def test_capital_collect_intents_drops_flow_signals_keeps_fade() -> None:
    """On Capital, a window that WOULD fire the flow signals (burst_rider /
    flow_pressure) yields NONE of them — only ``micro_reversion`` can be
    collected. Driven through ``_collect_intents`` directly so it isolates the
    venue routing from the entry/sizing path."""
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    now_ts = int(time.time())
    # A burst window (up) — on OKX this fires ``burst_rider``; on Capital it must
    # NOT (flow signal, structurally dead on the price-only feed). Regime=trend
    # so burst_rider IS regime-active — the only thing dropping it is the venue.
    burst = _burst_window(now_ts, +1)
    cap_intents = _collect_intents(
        eng, venue=CAP_VENUE, symbol=CAP_SYMBOL, window=burst,
        regime="bull_trend", now_ts=now_ts,
    )
    assert all(i.signal_id == "micro_reversion" for i in cap_intents), (
        "Capital tick path must never fire a flow signal (burst/ofi) — fade only"
    )
    # The SAME burst window on OKX DOES fire burst_rider (full-depth venue).
    eng_okx = TickEngineState(cfg=TickEngineConfig(shadow=False))
    okx_intents = _collect_intents(
        eng_okx, venue=VENUE, symbol=SYMBOL, window=burst,
        regime="bull_trend", now_ts=now_ts,
    )
    assert any(i.signal_id == "burst_rider" for i in okx_intents), (
        "OKX (full depth) must still fire the momentum burst signal"
    )


# ---------------------------------------------------------------------------
# (d3) Upstream long-only short dead-path: a SHORT whose only candidate venue
#      is long-only (OKX spot / Alpaca equity) is never CONSTRUCTED in
#      ``_collect_intents`` — the directionality check moved upstream of intent
#      construction so the loop no longer generates-then-drops it (the L535
#      ``_drop_for_bidirectional`` stays as a backstop). Direction-neutral:
#      removes no executable trade (spot/equity shorts are unexecutable);
#      Capital (bidirectional CFD) still collects the short.
# ---------------------------------------------------------------------------


def test_collect_intents_never_builds_short_on_long_only_venue() -> None:
    """An overshoot-up window fades SHORT (``micro_reversion``). On OKX (spot,
    long-only) that short is NEVER collected — gated upstream of construction —
    while OKX still collects longs and the ``drops_short`` counter records the
    gate firing. The SAME window on Capital (bidirectional CFD) DOES collect the
    short."""
    now_ts = int(time.time())
    # chop regime → micro_reversion is regime-active on OKX (full signal set).
    overshoot_up = _overshoot_window(now_ts, +1)

    eng_okx = TickEngineState(cfg=TickEngineConfig(shadow=False))
    okx_intents = _collect_intents(
        eng_okx, venue=VENUE, symbol=SYMBOL, window=overshoot_up,
        regime="chop", now_ts=now_ts,
    )
    # No SHORT intent survives construction on the long-only spot venue.
    assert all(i.side != "short" for i in okx_intents), (
        "OKX (long-only spot) must never CONSTRUCT a short intent — gated "
        "upstream of intent construction (not generate-then-drop)"
    )
    # The fade WOULD have fired a short here, so the upstream gate registered it.
    assert eng_okx.drops_short >= 1, (
        "the upstream long-only short gate must still record the drop in "
        "drops_short telemetry"
    )

    # Same window on Capital (bidirectional CFD): the short IS collected.
    eng_cap = TickEngineState(cfg=TickEngineConfig(shadow=False))
    cap_intents = _collect_intents(
        eng_cap, venue=CAP_VENUE, symbol=CAP_SYMBOL, window=overshoot_up,
        regime="chop", now_ts=now_ts,
    )
    assert any(i.side == "short" for i in cap_intents), (
        "Capital (bidirectional CFD) must still collect the micro_reversion short"
    )
    assert eng_cap.drops_short == 0, (
        "no short is gated on a bidirectional venue"
    )


def test_collect_intents_still_builds_long_on_long_only_venue() -> None:
    """The upstream gate is direction-neutral: a LONG on OKX (a burst-up window
    firing ``burst_rider`` long) is unaffected — collected, never gated."""
    now_ts = int(time.time())
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    intents = _collect_intents(
        eng, venue=VENUE, symbol=SYMBOL, window=_burst_window(now_ts, +1),
        regime="bull_trend", now_ts=now_ts,
    )
    assert any(i.side == "long" for i in intents), (
        "a long on a long-only venue must still be collected"
    )
    assert eng.drops_short == 0, "a long is never gated by the short check"


@pytest.mark.asyncio
async def test_capital_tick_path_is_fade_only(memdb: sqlite3.Connection) -> None:
    """End-to-end on Capital: an overshoot window in a chop regime opens a
    reversion (fade) position — and NO momentum/flow entry is ever produced.
    Proves the restriction routes (does not block): the fade edge still fires."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()  # rebind the process fence to this test's memdb conn
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed_capital(memdb, regime="chop", now_ts=now_ts)
    writer = FakeQuoteWriter()
    # Stretched-up mid with exhausting (opposing) flow → micro_reversion SHORT
    # (Capital is bidirectional CFD, so a short is allowed).
    writer.set_stream(CAP_INSTRUMENT, _overshoot_window(now_ts, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus_capital(), regime_cache={},
    )

    assert eng.orders == 1, "the overshoot fade must still open on Capital"
    trade = state.open_trades[0]
    assert trade.venue == CAP_VENUE and trade.symbol == CAP_SYMBOL
    assert trade.side == "short", "a stretched-up mid fades short"
    pid = trade.position_id
    assert pid is not None
    # The opened position is the reversion family (drives the fast-scalp exit).
    assert eng.family_by_position[pid] == "reversion"


# ---------------------------------------------------------------------------
# (e) no double-open when a position already exists on the symbol.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_double_open_when_already_held(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_ts, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    # A position already open on (capital, GOLD) — the dedup must skip it.
    state.open_trades.append(
        SimulatedTrade(
            signal_id="prior", venue=VENUE, symbol=SYMBOL,
            strategy_id="prior", side="long", entry_price=100.0,
            notional_usd=50.0, open_ts=now_ts, position_id="pos_prior",
        )
    )
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.skips_dedup == 1, "an already-held symbol must be deduped"
    assert eng.orders == 0
    assert len(state.open_trades) == 1, "no second position stacked on the symbol"


# ---------------------------------------------------------------------------
# (f) a reversion position exits on flow reversal.
# ---------------------------------------------------------------------------


def test_scalp_exit_decision_flow_reversal() -> None:
    # A long reversion bet (faded a down-overshoot) with flow now firmly selling
    # again (ofi < 0) and pnl in the dead band → flow_reversal exit.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.0, ofi=-0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )
    # Symmetric short reversion with flow firmly buying again.
    assert (
        _scalp_exit_decision(
            side="short", entry_price=100.0, last_mid=100.0, ofi=0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )
    # Flow aligned + pnl in band → hold.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.0, ofi=0.3, pnl_r=0.0
        )
        is None
    )
    # Micro-stop fires regardless of flow.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.0, ofi=0.3, pnl_r=-1.0
        )
        == "scalp_stop"
    )
    # Small-R target banks the snap-back.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=101.0, ofi=0.3, pnl_r=1.0
        )
        == "scalp_target"
    )


def test_micro_reversion_harvests_bounded_revert_at_calibrated_target() -> None:
    # [[harvest_generalization_2026-06-23]]: micro_reversion (reversion family →
    # scalp exit) is the top harvest target (avg MFE +0.523R, 120 trades, realized
    # only -0.148R) — its bounded revert-to-mean reached +0.45R for 44% of trades
    # yet round-tripped because the scalp target sat at +0.50R. A per-strategy
    # take-profit harvests the measured +0.30-0.45R revert BEFORE it gives back.
    # Profit side ONLY — the loss side (_SCALP_STOP_R) is untouched (NOT a tighter
    # cut). An excursion at the calibrated target banks; below it holds.
    from polaris.scripts._production_tick_engine import (
        _MICRO_REVERSION_TARGET_R,
        _scalp_exit_decision,
    )

    assert _MICRO_REVERSION_TARGET_R < 0.5  # harvests the revert sooner than 0.5R
    # At the calibrated target → harvest.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.4, ofi=0.3,
            pnl_r=_MICRO_REVERSION_TARGET_R, strategy_id="micro_reversion",
        )
        == "scalp_target"
    )
    # Just below the target (and flow still aligned) → hold (no premature exit).
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.2, ofi=0.3,
            pnl_r=_MICRO_REVERSION_TARGET_R - 0.05, strategy_id="micro_reversion",
        )
        is None
    )
    # Loss side UNCHANGED: the micro-stop still fires at the same _SCALP_STOP_R
    # (no tighter cut — flow_not_block / no defensive throttle).
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.0, ofi=0.3, pnl_r=-1.0,
            strategy_id="micro_reversion",
        )
        == "scalp_stop"
    )
    # An UNMAPPED reversion id keeps the shared default target (byte-identical).
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.4, ofi=0.3,
            pnl_r=_MICRO_REVERSION_TARGET_R, strategy_id="other_reversion",
        )
        is None
    )


@pytest.mark.asyncio
async def test_reversion_position_exits_on_flow_reversal(
    memdb: sqlite3.Connection,
) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="chop", now_ts=now_ts)
    writer = FakeQuoteWriter()
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    # An open reversion (long) position on the symbol, tracked by the engine.
    trade = SimulatedTrade(
        signal_id="tick_micro_reversion_x", venue=VENUE, symbol=SYMBOL,
        strategy_id="micro_reversion", side="long", entry_price=100.0,
        notional_usd=50.0, open_ts=now_ts, position_id="pos_rev",
    )
    state.open_trades.append(trade)
    eng.family_by_position["pos_rev"] = "reversion"
    eng.entry_ref_by_position["pos_rev"] = 100.0

    # A flow window that is firmly SELLING (bid_size << ask_size → ofi < 0):
    # the long fade is failing → flow_reversal exit.
    base_ms = int((now_mono - 1.0) * 1000)
    sell_window = [
        _tick(base_ms + i * 50, 100.0, bid_size=2.0, ask_size=40.0)
        for i in range(20)
    ]
    writer.set_stream(INSTRUMENT, sell_window, now_mono)

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    assert eng.scalp_exits == 1, "the reversion position must exit on flow reversal"
    assert "pos_rev" not in eng.family_by_position
    # The position is no longer open in the tracked book.
    assert all(t.position_id != "pos_rev" or t.closed for t in state.open_trades)


# ---------------------------------------------------------------------------
# (g) the momentum exit pass resumes the PERSISTED exit state — the ~500ms
# tick pass must not re-seed stop/peak/trough from entry (that loosened the
# bar recalc's ratcheted stop and reset the excursion telemetry / FSM).
# ---------------------------------------------------------------------------


def _insert_position_row(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    opened_ts: int,
    stop_price: float | None,
    peak_price: float | None,
    trough_price: float | None,
    exit_state: str | None,
    entry_atr_pct: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, stop_price, peak_price, trough_price, exit_state, "
        "entry_atr_pct) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            position_id, VENUE, SYMBOL, GROUP, "burst_rider", "burst_rider",
            "burst_rider", "long", 0.5, "active", opened_ts,
            stop_price, peak_price, trough_price, exit_state, entry_atr_pct,
        ),
    )


def _momentum_setup(
    memdb: sqlite3.Connection, *, now_ts: int,
) -> tuple[ProdLoopState, TickEngineState, FakeQuoteWriter]:
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    state.open_trades.append(
        SimulatedTrade(
            signal_id="tick_burst_x", venue=VENUE, symbol=SYMBOL,
            strategy_id="burst_rider", side="long", entry_price=100.0,
            notional_usd=50.0, open_ts=now_ts - 60, position_id="pos_mom",
        )
    )
    eng.family_by_position["pos_mom"] = "momentum"
    eng.entry_ref_by_position["pos_mom"] = 100.0
    return state, eng, writer


def _alternating_window(
    now_mono: float, *, lo: float, hi: float, last: float,
) -> list[TickSample]:
    base_ms = int((now_mono - 1.0) * 1000)
    window = [
        _tick(base_ms + i * 50, lo if i % 2 == 0 else hi) for i in range(19)
    ]
    window.append(_tick(base_ms + 19 * 50, last))
    return window


@pytest.mark.asyncio
async def test_momentum_exit_pass_does_not_loosen_ratcheted_stop(
    memdb: sqlite3.Connection,
) -> None:
    """The bar recalc ratcheted stop=104 / peak=106 / FSM=harvest into the
    positions row; a pullback tick to 104.5 (still ABOVE the stop) must hold
    the position AND leave the persisted state un-loosened. A from-entry
    re-seed recomputes stop≈100.7 / peak=104.5 — the ratchet-invariant
    violation. The FSM seed is HARVEST deliberately: the re-derived target
    from the restored peak is only "protected" (mfe≈1.57 < 2.0), so the
    exact-match assert below fails if exit_state restoration is dropped —
    a "protected" seed would be silently re-derived and mask that mutation."""
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=104.0, peak_price=106.0, trough_price=99.5,
        exit_state="harvest",
    )
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=103.0, hi=105.0, last=104.5),
        now_mono,
    )

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    row = memdb.execute(
        "SELECT stop_price, peak_price, trough_price, exit_state "
        "FROM positions WHERE position_id = 'pos_mom'",
    ).fetchone()
    assert row is not None
    stop_price, peak_price, trough_price, exit_state = row
    assert stop_price is not None and stop_price >= 104.0, (
        f"ratchet invariant violated: stop loosened to {stop_price} (< 104.0)"
    )
    assert peak_price == pytest.approx(106.0), f"peak reset toward entry: {peak_price}"
    assert trough_price == pytest.approx(99.5), f"trough reset to entry: {trough_price}"
    # Exact match: monotone FSM can never re-derive "harvest" from the
    # restored peak (target tops out at "protected") — only true restoration
    # of the persisted exit_state keeps it.
    assert exit_state == "harvest", f"FSM state not restored: {exit_state}"
    # 104.5 > the 104.0 stop → the position must still be open.
    assert any(
        t.position_id == "pos_mom" and not t.closed for t in state.open_trades
    )


@pytest.mark.asyncio
async def test_momentum_exit_pass_enforces_ratcheted_stop(
    memdb: sqlite3.Connection,
) -> None:
    """A live tick at 102.0 — below the ratcheted stop floor of 104.0 (the
    restored state can only ratchet it tighter) — must close the position
    (atr_trail_stop). The from-entry re-seed computed a fresh stop≈100 and
    held straight through the bar recalc's ratcheted level."""
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=104.0, peak_price=106.0, trough_price=99.5,
        exit_state="protected",
    )
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=101.5, hi=102.5, last=102.0),
        now_mono,
    )

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    assert "pos_mom" not in eng.family_by_position, (
        "a tick below the ratcheted stop must close the momentum position"
    )
    assert all(t.position_id != "pos_mom" or t.closed for t in state.open_trades)


@pytest.mark.asyncio
async def test_momentum_exit_pass_fresh_position_initialises_state(
    memdb: sqlite3.Connection,
) -> None:
    """A just-opened position (NULL exit columns) seeds from entry exactly as
    before, and a tracked position with NO positions row degrades gracefully
    (no crash, stays open) — the DB-resume path must not break first-tick
    initialisation."""
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=None, peak_price=None, trough_price=None, exit_state=None,
    )
    # A second tracked momentum position with NO positions row (orphan).
    state.open_trades.append(
        SimulatedTrade(
            signal_id="tick_burst_y", venue=VENUE, symbol=SYMBOL,
            strategy_id="burst_rider", side="long", entry_price=100.0,
            notional_usd=50.0, open_ts=now_ts - 60, position_id="pos_orphan",
        )
    )
    eng.family_by_position["pos_orphan"] = "momentum"
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=100.1, hi=100.3, last=100.2),
        now_mono,
    )

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    row = memdb.execute(
        "SELECT stop_price, peak_price, trough_price FROM positions "
        "WHERE position_id = 'pos_mom'",
    ).fetchone()
    assert row is not None
    stop_price, peak_price, trough_price = row
    # First tick seeded the running extremes from entry + live mark.
    assert peak_price == pytest.approx(100.2)
    assert trough_price == pytest.approx(100.0)
    assert stop_price is not None and stop_price < 100.2
    # Both positions held (no spurious close on a flat window).
    assert any(
        t.position_id == "pos_mom" and not t.closed for t in state.open_trades
    )
    assert any(
        t.position_id == "pos_orphan" and not t.closed for t in state.open_trades
    )


@pytest.mark.asyncio
async def test_momentum_exit_pass_uses_entry_anchored_r_units(
    memdb: sqlite3.Connection,
) -> None:
    """positions.entry_atr_pct anchors the R denominator (pnl_r/mfe_r/mae_r +
    FSM thresholds) in the tick exit pass — the same ruler the bar recalc
    uses — so the persisted excursion telemetry stops flapping between the
    live tick-window ruler (500ms) and the entry-anchored ruler (5s).

    entry=100, anchor 4% → atr_r = 100*0.04*2 = 8.0; restored peak 106 →
    mfe_r = 6/8 = 0.75 → FSM "touched". The ~1.9% tick-window ruler would
    inflate mfe_r to ≈1.57 and advance the FSM to "protected"."""
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=104.0, peak_price=106.0, trough_price=99.5,
        exit_state="open", entry_atr_pct=0.04,
    )
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=103.0, hi=105.0, last=104.5),
        now_mono,
    )

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    row = memdb.execute(
        "SELECT mfe_r, mae_r, exit_state FROM positions "
        "WHERE position_id = 'pos_mom'",
    ).fetchone()
    assert row is not None
    mfe_r, mae_r, exit_state = row
    assert mfe_r == pytest.approx(0.75), (
        f"mfe_r not entry-anchored: {mfe_r} (tick-window ruler would be ≈1.57)"
    )
    assert mae_r == pytest.approx(-0.0625), f"mae_r not entry-anchored: {mae_r}"
    # FSM advances on the ANCHORED ruler: 0.75 ≥ touch(0.5) but < protect(1.0).
    assert exit_state == "touched", f"FSM advanced on the wrong ruler: {exit_state}"
    # Held: 104.5 > the 104.0 ratcheted stop.
    assert any(
        t.position_id == "pos_mom" and not t.closed for t in state.open_trades
    )


@pytest.mark.asyncio
async def test_momentum_exit_pass_profit_target_uses_anchored_pnl_r(
    memdb: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pnl_r fed to the precise-exit close decisions (target_harvest) must
    be measured on the ENTRY-ANCHORED ruler, not the tick-window ruler. With a
    0.8R target: anchored pnl_r = 4.5/8 = 0.5625 → HOLD; the inflated window
    ruler (≈1.18) would falsely harvest. Today's tick momentum signals carry
    no profit_target_r — this pins the seam for any registered strategy that
    does."""
    from polaris.scripts import _production_recalc_exit as rex

    monkeypatch.setattr(rex, "_profit_target_for_strategy", lambda _sid: 0.8)
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=104.0, peak_price=106.0, trough_price=99.5,
        exit_state="open", entry_atr_pct=0.04,
    )
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=103.0, hi=105.0, last=104.5),
        now_mono,
    )

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    # Anchored 0.5625 < 0.8 target → held (window 1.18 would have harvested).
    assert any(
        t.position_id == "pos_mom" and not t.closed for t in state.open_trades
    ), "profit target fired on the inflated tick-window pnl_r ruler"


# ---------------------------------------------------------------------------
# (h) flow_pressure RE-AIM (lever 2): the tick momentum exit gives a
# flow_pressure position a WIDER let-winners-run trail so favourable OFI drift
# runs past the old fast scalp, while every other tick momentum strategy
# (burst_rider) keeps the module-default trail. Same drifting tick → flow_pressure
# HOLDS, burst_rider CLOSES. Not a throttle: only LOOSENS the running trail.
# ---------------------------------------------------------------------------


def test_momentum_trail_mult_routes_flow_pressure_wider_only() -> None:
    # The router gives flow_pressure the wider trail; burst_rider → None (default).
    assert _momentum_trail_mult("flow_pressure") == _FLOW_PRESSURE_TRAIL_MULT_DEFAULT
    assert _momentum_trail_mult("burst_rider") is None
    assert _momentum_trail_mult("micro_reversion") is None


def test_mfe_protect_schedule_covers_all_momentum_signals() -> None:
    # [[harvest_generalization_2026-06-23]]: the MFE-protect harvest was wired
    # ONLY to flow_pressure; burst_rider (the other MOMENTUM tick signal →
    # run_precise_exit) passed mfe_protect=None, so its measured +0.3R excursions
    # (27% reach +0.30R) round-tripped on the wide ATR trail. Both momentum tick
    # signals now get the cfg MFE-protect schedule. micro_reversion is REVERSION
    # family (scalp exit, not run_precise_exit) so it has NO mfe_protect schedule
    # here — it harvests via its own scalp profit target.
    cfg = TickEngineConfig()
    for momentum_id in ("flow_pressure", "burst_rider"):
        sched = _mfe_protect_schedule(momentum_id, cfg)
        assert sched is not None, momentum_id
        assert sched.bep_at_r == cfg.mfe_bep_r
        assert sched.protect_at_r == cfg.mfe_protect_r
        assert sched.lock_r == cfg.mfe_protect_lock_r
    # micro_reversion routes through the scalp exit, not the mfe_protect floor.
    assert _mfe_protect_schedule("micro_reversion", cfg) is None
    assert _mfe_protect_schedule("not_a_signal", cfg) is None


def test_mfe_protect_schedule_badfit_tightens_harvest() -> None:
    # Seam3 (regime-fit): a BAD momentum fit (chop, the churn case) TIGHTENS the
    # harvest — bep/protect pull IN (bank sooner = precise exit); a GOOD fit
    # (bull_trend) LOOSENS them (LET_RUN). flow_not_block: this is exit-timing
    # precision on an OPEN position, never a size cut / entry block.
    cfg = TickEngineConfig()
    bad = _mfe_protect_schedule("flow_pressure", cfg, regime="chop")
    good = _mfe_protect_schedule("flow_pressure", cfg, regime="bull_trend")
    neutral = _mfe_protect_schedule("flow_pressure", cfg, regime="unknown")
    assert bad is not None and good is not None and neutral is not None
    # Bad fit banks sooner: lower BEP + protect thresholds than good fit.
    assert bad.bep_at_r < good.bep_at_r
    assert bad.protect_at_r < good.protect_at_r
    # Unknown regime → byte-identical to the base schedule (no shaping).
    assert neutral.bep_at_r == cfg.mfe_bep_r
    assert neutral.protect_at_r == cfg.mfe_protect_r
    assert neutral.lock_r == cfg.mfe_protect_lock_r


def test_mfe_protect_schedule_ratchets_toward_profit_only() -> None:
    # A tightened (bad-fit) schedule NEVER locks LESS positive R than the base —
    # the ratchet-toward-profit invariant (mandate loss-defense = precise exit).
    cfg = TickEngineConfig()
    for regime in ("chop", "crisis", "bull_trend", "bear_trend", "unknown", None):
        sched = _mfe_protect_schedule("flow_pressure", cfg, regime=regime)
        assert sched is not None
        assert sched.lock_r >= cfg.mfe_protect_lock_r
        assert sched.bep_at_r > 0.0 and sched.protect_at_r > 0.0


def test_flow_decay_exit_only_when_green_and_flow_fails() -> None:
    gate = TickEngineConfig().mfe_bep_r  # the +MFE gate (0.35R)
    # RED (pnl below the gate) → never exits, regardless of flow (G6 owns red).
    assert _flow_decay_exit(
        side="long", ofi=-0.9, flow_confirmed=False, pnl_r=0.0, mfe_gate_r=gate,
    ) is False
    # GREEN long + OFI flipped negative (book turned against) → exit near peak.
    assert _flow_decay_exit(
        side="long", ofi=-0.5, flow_confirmed=True, pnl_r=0.6, mfe_gate_r=gate,
    ) is True
    # GREEN long + OFI still positive but follow-through FAILED (bid withdrawal /
    # microprice failure) → exit near peak.
    assert _flow_decay_exit(
        side="long", ofi=0.5, flow_confirmed=False, pnl_r=0.6, mfe_gate_r=gate,
    ) is True
    # GREEN long + OFI still aligned + follow-through holding → HOLD (let it run).
    assert _flow_decay_exit(
        side="long", ofi=0.5, flow_confirmed=True, pnl_r=0.6, mfe_gate_r=gate,
    ) is False
    # Symmetric short: green + OFI flipped positive → exit.
    assert _flow_decay_exit(
        side="short", ofi=0.5, flow_confirmed=True, pnl_r=0.6, mfe_gate_r=gate,
    ) is True


def _flow_pressure_momentum_setup(
    memdb: sqlite3.Connection, *, now_ts: int, strategy_id: str,
) -> tuple[ProdLoopState, TickEngineState, FakeQuoteWriter]:
    """A tick momentum position tagged with ``strategy_id`` (flow_pressure vs
    burst_rider) with a high persisted peak but NO ratcheted stop yet — so the
    trail width on THIS tick decides the stop (and thus hold-vs-close)."""
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    state.open_trades.append(
        SimulatedTrade(
            signal_id=f"tick_{strategy_id}_x", venue=VENUE, symbol=SYMBOL,
            strategy_id=strategy_id, side="long", entry_price=100.0,
            notional_usd=50.0, open_ts=now_ts - 60, position_id="pos_fp",
        )
    )
    eng.family_by_position["pos_fp"] = "momentum"
    eng.entry_ref_by_position["pos_fp"] = 100.0
    # Persisted peak 114, NO stop yet (first ratchet happens THIS tick), entry-
    # anchored 4% ATR. The live mark (104) sits ABOVE the MFE-protect lock floor
    # (entry + 0.25R = 100 + 0.25·8 = 102) so the protect floor is satisfied and
    # the TRAIL WIDTH alone decides hold-vs-close on this tick (the running trail
    # is what this test isolates; the give-back protect is covered separately).
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, stop_price, peak_price, trough_price, exit_state, "
        "entry_atr_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pos_fp", VENUE, SYMBOL, GROUP, strategy_id, strategy_id,
            strategy_id, "long", 0.5, "active", now_ts - 60,
            None, 114.0, 101.5, "open", 0.04,
        ),
    )
    return state, eng, writer


@pytest.mark.asyncio
async def test_flow_pressure_momentum_holds_drift_burst_rider_closes(
    memdb: sqlite3.Connection,
) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()

    # A drifting tick STILL near its high: peak was 114, now marks ~104 (above the
    # 102 MFE-protect lock). The live-window ATR% proxy (~3.85% from the lo/hi span
    # around 104) drives the trail width: the default 2.0-ATR trail (stop ≈ 106.3)
    # is breached by the 104 mark → CLOSE, while the wider 4.0-ATR flow_pressure
    # trail (stop ≈ 98.6, floored to the 102 protect lock) HOLDS the 104 mark.
    window = _alternating_window(now_mono, lo=102.0, hi=106.0, last=104.0)

    # burst_rider (default trail) → the pullback breaches the trail → CLOSE.
    state_b, eng_b, writer_b = _flow_pressure_momentum_setup(
        memdb, now_ts=now_ts, strategy_id="burst_rider",
    )
    writer_b.set_stream(INSTRUMENT, window, now_mono)
    await _run_exits(
        memdb, state_b, eng_b, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )
    assert "pos_fp" not in eng_b.family_by_position, (
        "burst_rider on the DEFAULT trail must close on this drift pullback"
    )

    # flow_pressure (wider trail) on the SAME drift → HOLD (drift keeps running).
    memdb.execute("DELETE FROM positions WHERE position_id = 'pos_fp'")
    state_f, eng_f, writer_f = _flow_pressure_momentum_setup(
        memdb, now_ts=now_ts, strategy_id="flow_pressure",
    )
    writer_f.set_stream(INSTRUMENT, window, now_mono)
    await _run_exits(
        memdb, state_f, eng_f, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )
    assert "pos_fp" in eng_f.family_by_position, (
        "flow_pressure on the WIDER trail must HOLD the drift past the old scalp"
    )
    assert any(
        t.position_id == "pos_fp" and not t.closed for t in state_f.open_trades
    )


# ---------------------------------------------------------------------------
# (i) flow_pressure RE-AIM (lever 3): the entry path routes flow_pressure OKX
# entries maker-first (prefer_maker=True → post-only at touch → TAKER fallback),
# while burst_rider keeps the strength-gated default (prefer_maker=False). The
# taker fallback inside reserve_and_submit/real_okx_open_fill guarantees the
# trade is never missed — covered in test_okx_limit_execution.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_pressure_entry_routes_prefer_maker(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.scripts._production_tick_engine import TickIntent, _try_open

    captured: dict[str, bool] = {}

    async def _fake_submit(**kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
        captured[kwargs["sig"].strategy_id] = kwargs["prefer_maker"]
        return None  # no trade object → _try_open returns False cleanly

    monkeypatch.setattr(eng_mod, "reserve_and_submit", _fake_submit)
    # A non-zero notional so _try_open reaches the executor (compute_size path is
    # bypassed by stubbing _sized_notional to a fixed positive value).
    monkeypatch.setattr(eng_mod, "_sized_notional", lambda *a, **k: 100.0)

    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="chop", now_ts=now_ts)
    state = ProdLoopState()
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    async def _open(signal_id: str) -> None:
        intent = TickIntent(
            venue=VENUE, symbol=SYMBOL, side="long", conviction=0.9,
            signal_id=signal_id, signal_family="momentum", ref_price=100.0,
        )
        await _try_open(
            memdb, state, eng, intent=intent, asset_class=ASSET_CLASS,
            underlying_group_id=GROUP, regime="chop", now_ts=now_ts,
            now_mono=now_mono, okx_adapter=object(), capital_session=None,
            alpaca_adapter=None, real_roundtrip=True,
        )

    await _open("flow_pressure")
    await _open("micro_reversion")
    await _open("burst_rider")

    assert captured["flow_pressure"] is True, (
        "flow_pressure OKX entry must route maker-first (prefer_maker=True)"
    )
    assert captured["micro_reversion"] is True, (
        "micro_reversion overshoot fade must route maker-first (prefer_maker=True)"
    )
    assert captured["burst_rider"] is False, (
        "burst_rider keeps the strength-gated default (prefer_maker=False)"
    )


# ---------------------------------------------------------------------------
# (j) sub-min sizing drop: a POSITIVE-but-sub-minimum notional (binding daily/
# cap headroom clipped final_risk_pct to a tiny residual → e.g. $0.0001) must be
# treated as a clean sizing-drop, NOT submitted. Submitting it produced the OKX
# 51020 "below minimum order amount" flood (4000+ rejects, ADA/XRP) + the Alpaca
# notional=$0 validation rejects. flow_not_block AT THE SOURCE: we stop firing
# orders that 100% reject; a genuinely fundable signal still flows. NOT a
# throttle — a $0.0001 headroom literally cannot fund a tradeable order, and
# flooring it up would BREACH the binding daily cap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sub_min_notional_is_sizing_drop_not_submitted(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.scripts._production_tick_engine import TickIntent, _try_open

    submitted: list[float] = []

    async def _fake_submit(**kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
        submitted.append(kwargs["notional_usd"])
        return None

    monkeypatch.setattr(eng_mod, "reserve_and_submit", _fake_submit)
    # compute_size returns a positive-but-sub-minimum residual (binding cap).
    monkeypatch.setattr(eng_mod, "_sized_notional", lambda *a, **k: 0.0001)

    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="chop", now_ts=now_ts)
    state = ProdLoopState()
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    intent = TickIntent(
        venue=VENUE, symbol=SYMBOL, side="long", conviction=0.9,
        signal_id="burst_rider", signal_family="momentum", ref_price=100.0,
    )
    placed = await _try_open(
        memdb, state, eng, intent=intent, asset_class=ASSET_CLASS,
        underlying_group_id=GROUP, regime="chop", now_ts=now_ts,
        now_mono=now_mono, okx_adapter=object(), capital_session=None,
        alpaca_adapter=None, real_roundtrip=True,
    )

    assert placed is False, "a sub-min notional must NOT place an order"
    assert submitted == [], (
        "reserve_and_submit must NOT be called with a sub-min (guaranteed-51020) "
        f"notional; got submissions {submitted}"
    )
    assert eng.drops_sizing == 1, (
        "a sub-min notional is a sizing/headroom drop (same class as <=0)"
    )


# ---------------------------------------------------------------------------
# (h) Structural hardening #2 (2026-06-23) — observe_probes threaded into the
# TICK exit pass (mark_source='tick'). The sidecar is OBSERVE-ONLY: it logs a
# 'tick'-bucket decision row but threads NOTHING into run_precise_exit, so the
# live exit (positions row + close outcome) is BYTE-IDENTICAL to a run without
# it. Upstream of the rank-4 / rank-16 calibration readers (previously the
# P&L-driving tick half emitted 0 probe rows).
# ---------------------------------------------------------------------------


def _wire_tick_probes(state: ProdLoopState, probe_db: str) -> None:
    from polaris.core.probes import ExitEngine, ProbeBus
    from polaris.core.probes.catalog import (
        LossDefenseProbe,
        ProfitTakingProbe,
        SessionHoursProbe,
        TechnicalProbe,
    )
    from polaris.core.probes.tuning_log import open_probe_db

    state.probe_conn = open_probe_db(probe_db)  # type: ignore[attr-defined]
    state.probe_bus = ProbeBus(  # type: ignore[attr-defined]
        [ProfitTakingProbe(), LossDefenseProbe(), TechnicalProbe(),
         SessionHoursProbe()]
    )
    state.probe_engine = ExitEngine()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tick_exit_pass_logs_a_tick_bucket_observe_row(
    memdb: sqlite3.Connection, tmp_path: Any,
) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    state, eng, writer = _momentum_setup(memdb, now_ts=now_ts)
    _insert_position_row(
        memdb, position_id="pos_mom", opened_ts=now_ts - 60,
        stop_price=104.0, peak_price=106.0, trough_price=99.5,
        exit_state="harvest", entry_atr_pct=0.01,
    )
    # A pullback tick to 104.5 (above the 104 stop) → the winner HOLDS, so the
    # exit does not fire and we are sure the probe observed an OPEN position.
    writer.set_stream(
        INSTRUMENT,
        _alternating_window(now_mono, lo=103.0, hi=105.0, last=104.5),
        now_mono,
    )
    probe_db = f"{tmp_path}/probes_tick.sqlite"
    _wire_tick_probes(state, probe_db)

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    pconn = state.probe_conn  # type: ignore[attr-defined]
    n_tick = pconn.execute(
        "SELECT COUNT(*) FROM probe_decisions WHERE position_id='pos_mom' "
        "AND mode='observe' AND mark_source='tick' AND applied=0"
    ).fetchone()[0]
    assert n_tick == 1, "the tick exit pass must log exactly one 'tick' observe row"
    # No 'bar' bucket row leaked from the tick pass.
    n_bar = pconn.execute(
        "SELECT COUNT(*) FROM probe_decisions WHERE mark_source='bar'"
    ).fetchone()[0]
    assert n_bar == 0
    pconn.close()


def test_tick_exit_pass_probe_sidecar_is_byte_identical(
    tmp_path: Any,
) -> None:
    # Same seeded scenario twice — one run with the probe sidecar wired, one
    # without. The live exit outcome (positions row + open/closed book) must be
    # IDENTICAL: the observe sidecar threads NOTHING into run_precise_exit.
    # Synchronous test: each run owns its own asyncio.run loop.
    import asyncio

    from polaris.storage.schema import init_db

    def _run(with_probes: bool) -> tuple[Any, ...]:
        db_path = f"{tmp_path}/byteid_{with_probes}.sqlite"
        c = init_db(db_path)
        now_ts = int(time.time())
        now_mono = time.monotonic()
        state, eng, writer = _momentum_setup(c, now_ts=now_ts)
        _insert_position_row(
            c, position_id="pos_mom", opened_ts=now_ts - 60,
            stop_price=104.0, peak_price=106.0, trough_price=99.5,
            exit_state="harvest", entry_atr_pct=0.01,
        )
        writer.set_stream(
            INSTRUMENT,
            _alternating_window(now_mono, lo=103.0, hi=105.0, last=104.5),
            now_mono,
        )
        if with_probes:
            _wire_tick_probes(state, f"{tmp_path}/p_{with_probes}.sqlite")
        asyncio.run(_run_exits(
            c, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
            real_roundtrip=False, okx_adapter=None, capital_session=None,
            lookup_regime=eng_mod._lookup_regime_str,
        ))
        row = c.execute(
            "SELECT status, stop_price, peak_price, trough_price, mfe_r, mae_r, "
            "exit_state, pnl_r, closed_ts FROM positions WHERE position_id='pos_mom'",
        ).fetchone()
        book = sorted(
            (t.position_id, t.closed) for t in state.open_trades
        )
        if with_probes:
            state.probe_conn.close()  # type: ignore[attr-defined]
        c.close()
        return (row, book)

    baseline = _run(with_probes=False)
    with_probe = _run(with_probes=True)
    assert baseline == with_probe, (
        f"tick observe sidecar changed the live exit: "
        f"baseline={baseline} with_probe={with_probe}"
    )
