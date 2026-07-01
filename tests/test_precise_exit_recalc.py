"""#26 precise-exit WIRING — tick-loop integration over the recalc engine.

Verifies the precise-exit engine is wired into ``recalc_active_positions``:
- peak/MFE tracking persists to the positions row each tick;
- the ATR-trailing stop ratchets and persists (never loosens);
- the MFE FSM advances and persists;
- a round-tripped winner (protected BEP) closes;
- a stale loser closes but a winner does NOT;
- the dropped G7 EXIT_NOW decision now actually closes the position;
- the G6 EXIT_NOW path is still honoured (hard rail unchanged).

EXPECTANCY, not a throttle: per-position close only — no size change, no entry
block, no strategy/bot halt. The G6 -1.0R hard stop_hit rail is in the G6 gate
(deterministic), not this module; nothing here removes it.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.live_recalc.exit_engine import EXIT_LOSER_TIMEOUT_SEC
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import (
    ActivePositionRow,
    recalc_active_positions,
)
from polaris.scripts._production_recalc_exit import run_precise_exit
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _MockGPTClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ['{"decision":"HOLD","reason":"x"}']
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)
                idx = min(len(outer.calls) - 1, len(outer.responses) - 1)
                text = outer.responses[idx]

                class _Block:
                    text = ""

                _Block.text = text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _seed(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    side: str = "long",
    entry_price: float = 100.0,
    last_price: float = 100.0,
    opened_ts: int = NOW,
    strategy: str = "vb",
    band: float = 0.5,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, ?, 0.001, "
        " 'open', ?, 0)",
        (position_id, strategy, strategy, strategy, side, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, 'okx:BTC-USDT', 'okx', ?, 0.001, ?, 80.0, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, strategy, side, entry_price,
            position_id, uuid.uuid4().hex,
        ),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
            (
                ts, entry_price, last_price + band, last_price - band,
                last_price, last_price, last_price, last_price,
            ),
        )


def _set_last_price(
    conn: sqlite3.Connection, *, last_price: float, band: float = 0.5,
    base_ts: int = NOW,
) -> None:
    """Append a NEWER 1m bar so ``load_active_position_rows`` reads the new
    last_price WITHOUT touching the positions row (preserves tracked state)."""
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
        " ts, open, high, low, close, volume, notional_usd, trade_count, "
        " vwap, bid_close, ask_close, spread_bps_close, source) "
        "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
        " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
        (
            base_ts + 60, last_price, last_price + band, last_price - band,
            last_price, last_price, last_price, last_price,
        ),
    )


def _trade(
    position_id: str, side: str = "long", strategy: str = "vb"
) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id=strategy, side=side, entry_price=100.0, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


async def _recalc(
    conn: sqlite3.Connection, state: ProdLoopState, *, now_ts: int = NOW,
    gpt: _MockGPTClient | None = None,
) -> int:
    return await recalc_active_positions(
        conn, state=state, now_ts=now_ts,
        gpt_client=gpt or _MockGPTClient(['{"decision":"HOLD"}'] * 5),
        phase="P1", lookup_regime=_regime,
        close_specific=close_specific_position,
    )


def _pos_row(conn: sqlite3.Connection, position_id: str) -> dict:
    r = conn.execute(
        "SELECT status, stop_price, peak_price, trough_price, mfe_r, mae_r, "
        "exit_state FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return {
        "status": r[0], "stop_price": r[1], "peak_price": r[2],
        "trough_price": r[3], "mfe_r": r[4], "mae_r": r[5], "exit_state": r[6],
    }


# --- peak / MFE tracking persists ------------------------------------------


@pytest.mark.asyncio
async def test_precise_exit_tracks_peak_and_mfe(memdb: sqlite3.Connection) -> None:
    # Winner well above entry but inside the trail (atr ~0.5%).
    _seed(memdb, position_id="pos-win", entry_price=100.0, last_price=100.4)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-win")]
    await _recalc(memdb, state)
    row = _pos_row(memdb, "pos-win")
    assert row["status"] == "open"  # not closed (winner, fresh)
    assert row["peak_price"] is not None
    assert row["peak_price"] >= 100.4
    assert row["mfe_r"] is not None and row["mfe_r"] > 0.0
    assert row["stop_price"] is not None  # ATR trail persisted


# --- ATR stop ratchets up not down across two ticks ------------------------


@pytest.mark.asyncio
async def test_atr_stop_ratchets_across_ticks(memdb: sqlite3.Connection) -> None:
    _seed(memdb, position_id="pos-r", entry_price=100.0, last_price=100.3)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-r")]
    await _recalc(memdb, state)
    stop1 = _pos_row(memdb, "pos-r")["stop_price"]
    # New higher bar → peak advances → stop ratchets UP (positions row intact).
    _set_last_price(memdb, last_price=101.0)
    await _recalc(memdb, state, now_ts=NOW + 5)
    stop2 = _pos_row(memdb, "pos-r")["stop_price"]
    assert stop2 >= stop1  # ratcheted, never loosened


# --- FSM advances on MFE ----------------------------------------------------


@pytest.mark.asyncio
async def test_fsm_advances_to_protected(memdb: sqlite3.Connection) -> None:
    # band 0.05 → atr_one ~0.1, atr_r ~0.2%; last 101.0 → mfe ~1.67R → PROTECTED.
    # (last_price widened from 100.3 to 101.0, [[fee_aware_runit_floor_2026-07-02]]
    # P0-1: the fee-aware floor now lifts this tight-ATR R-unit to cover the OKX
    # real round-trip fee, so a smaller price move no longer crosses +1.0R.)
    _seed(
        memdb, position_id="pos-fsm", entry_price=100.0, last_price=101.0,
        band=0.05,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-fsm")]
    await _recalc(memdb, state)
    row = _pos_row(memdb, "pos-fsm")
    assert row["status"] == "open"
    assert row["exit_state"] in ("protected", "harvest")


# --- protected BEP closes a round-tripped winner ---------------------------


@pytest.mark.asyncio
async def test_protected_bep_closes_round_tripped_winner(
    memdb: sqlite3.Connection,
) -> None:
    # last_price widened from 100.3 to 101.0 — see test_fsm_advances_to_protected.
    _seed(
        memdb, position_id="pos-rt", entry_price=100.0, last_price=101.0,
        band=0.05,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-rt")]
    await _recalc(memdb, state)  # advance to PROTECTED
    assert _pos_row(memdb, "pos-rt")["status"] == "open"
    assert _pos_row(memdb, "pos-rt")["exit_state"] in ("protected", "harvest")
    # Round-trip: price falls back below entry → protected BEP close (positions
    # row preserved so the PROTECTED state survives to this tick).
    _set_last_price(memdb, last_price=99.7, band=0.05)
    await _recalc(memdb, state, now_ts=NOW + 5)
    assert _pos_row(memdb, "pos-rt")["status"] == "closed"
    assert state.recalc_precise_exit == 1


# --- generalized harvest: a newly-covered bar strategy banks a sub-1R MFE ---


@pytest.mark.asyncio
async def test_newly_covered_spot_strategy_harvests_sub_protect_excursion(
    memdb: sqlite3.Connection,
) -> None:
    # [[harvest_generalization_2026-06-23]]: a registered SPOT strategy
    # (ema_crossover — spot_donchian was un-registered 2026-06-27 in the #56
    # stop-bleeders KILL, so the live OKX 1H spot vehicle is ema_crossover) that
    # reaches +0.5R MFE — ABOVE the schedule protect rung
    # (+0.45R, locking +0.20R) but BELOW the dead FSM PROTECT rung (1.0R) — now
    # has its profit floored. When it round-trips to +0.1R the locked-floor stop
    # fires instead of giving the excursion back. band 0.25 → atr_one 0.5,
    # atr_r 1.0; last 100.5 → mfe 0.5R (touched, not FSM-protected).
    _seed(
        memdb, position_id="pos-spot", entry_price=100.0, last_price=100.5,
        band=0.25, strategy="ema_crossover",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-spot", strategy="ema_crossover")]
    await _recalc(memdb, state)
    row = _pos_row(memdb, "pos-spot")
    assert row["status"] == "open"
    assert row["exit_state"] == "touched"  # 0.5R < 1.0R FSM PROTECT rung
    # Schedule locked +0.20R → stop floored at ~100.2. Round-trip to 100.1 (still
    # a +0.1R winner, never red) → the locked floor fires, banking the excursion.
    _set_last_price(memdb, last_price=100.1, band=0.25)
    await _recalc(memdb, state, now_ts=NOW + 5)
    assert _pos_row(memdb, "pos-spot")["status"] == "closed"
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_unregistered_strategy_no_schedule_holds_with_remap_off(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CONTROL (re-map OFF, byte-identical pre-fix path): the SAME excursion path on
    # an UNREGISTERED id ("vb") has NO mfe schedule — the wide 2-ATR trail
    # (peak 100.5 - 1.0 = 99.5) is the only floor, so a round-trip to 100.1 HOLDS.
    # POLARIS_EXIT_ADAPTIVE_THESIS=OFF restores this exact baseline.
    monkeypatch.setattr(
        "polaris.scripts._production_recalc_exit.EXIT_ADAPTIVE_THESIS_ON", False
    )
    _seed(
        memdb, position_id="pos-unreg", entry_price=100.0, last_price=100.5,
        band=0.25, strategy="vb",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-unreg", strategy="vb")]
    await _recalc(memdb, state)
    _set_last_price(memdb, last_price=100.1, band=0.25)
    await _recalc(memdb, state, now_ts=NOW + 5)
    assert _pos_row(memdb, "pos-unreg")["status"] == "open"  # no floor → holds
    assert state.recalc_precise_exit == 0


@pytest.mark.asyncio
async def test_adaptive_thesis_banks_hard_giveback_on_unregistered(
    memdb: sqlite3.Connection,
) -> None:
    # NEW (re-map ON by default): the adaptive thesis re-map manages EVERY
    # position (unregistered → bucket=TREND). The same round-trip — peak +0.5R
    # (band 0.25 → atr_r 1.0; 100.5) handed back to +0.1R (100.1) = 80% give-back
    # > hard_frac 0.6 — now BANKS the gain (thesis_harvest), instead of riding the
    # wide trail back to a loss. EXPECTANCY (bank a leaking winner near its peak),
    # never a size/entry/halt change; the G6 -1.0R rail is untouched.
    _seed(
        memdb, position_id="pos-gb", entry_price=100.0, last_price=100.5,
        band=0.25, strategy="vb",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-gb", strategy="vb")]
    await _recalc(memdb, state)
    _set_last_price(memdb, last_price=100.1, band=0.25)
    # PAST the thesis grace (~25s): a hard give-back only banks once the position
    # has aged out of grace — a fresh (<grace) position is never thesis-closed.
    await _recalc(memdb, state, now_ts=NOW + 30)
    assert _pos_row(memdb, "pos-gb")["status"] == "closed"
    assert state.recalc_precise_exit == 1


# --- loser timeout closes a stale loser but NOT a winner -------------------


@pytest.mark.asyncio
async def test_loser_timeout_closes_stale_loser(memdb: sqlite3.Connection) -> None:
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-stale", entry_price=100.0, last_price=99.8,
        opened_ts=opened,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-stale")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-stale")["status"] == "closed"
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_loser_timeout_does_not_close_aged_winner(
    memdb: sqlite3.Connection,
) -> None:
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-aged-win", entry_price=100.0, last_price=100.3,
        opened_ts=opened,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-aged-win")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-aged-win")["status"] == "open"
    assert state.recalc_precise_exit == 0


# --- Component B: exit horizon ∝ timeframe ----------------------------------


def test_loser_timeout_for_strategy_scales_to_timeframe() -> None:
    from polaris.scripts._production_recalc_exit import (
        LOSER_TIMEOUT_CAP_SEC,
        _loser_timeout_for_strategy,
    )

    # ema_crossover = 1H → bar-scaled floor (2×3600=7200s) but CAPPED at the
    # named drift backstop (POLARIS_LOSER_TIMEOUT_SEC, default 3600s): a
    # dead-thesis drifter is cut faster (precise-exit loss-defense), not left 2hr.
    h1_timeout = _loser_timeout_for_strategy("ema_crossover")
    assert h1_timeout == LOSER_TIMEOUT_CAP_SEC  # capped 7200 → 3600s
    assert h1_timeout > EXIT_LOSER_TIMEOUT_SEC  # still > the flat 900s
    # volume_burst = 1m → max(900, 2×60=120) = 900s, under the cap (fast
    # strategy / tick scalps stay short — unaffected by the cap).
    assert _loser_timeout_for_strategy("volume_burst") == EXIT_LOSER_TIMEOUT_SEC
    # Unregistered id → flat default (back-compat), also under the cap.
    assert _loser_timeout_for_strategy("vb") == EXIT_LOSER_TIMEOUT_SEC


def test_loser_timeout_daily_class_exempt_from_1h_cap() -> None:
    # [[1d_exit_horizon_fix_2026-06-26]] FIX A: a 1D-class strategy's bar-scaled
    # floor (2×86400=172800s) must NOT be truncated to the 1H LOSER_TIMEOUT_CAP_SEC
    # (3600s) — that cap is scalp/1H drift logic leaking into a daily thesis. The
    # ≥4H/1D timeframe-class is EXEMPT so the 1D thesis respects its own horizon
    # (equity is EOD-flattened → the session rail is the real backstop). The G6
    # -1.0R rail + the ATR-trail (which cut real losers far earlier) are untouched.
    from polaris.core.isolation.reentry import bar_seconds
    from polaris.scripts._production_recalc_exit import (
        EXIT_LOSER_TIMEOUT_MIN_BARS,
        LOSER_TIMEOUT_CAP_SEC,
        _loser_timeout_for_strategy,
    )

    # connors_rsi2 = 1D → exempt → full bar-scaled floor, well ABOVE the 1H cap.
    daily_floor = float(EXIT_LOSER_TIMEOUT_MIN_BARS * bar_seconds("1D"))
    for sid in ("connors_rsi2",):
        t = _loser_timeout_for_strategy(sid)
        assert t == daily_floor, sid
        assert t > LOSER_TIMEOUT_CAP_SEC, sid  # NOT truncated to the 1H cap
    # 1H strategies stay CAPPED (scalp/1H drift backstop unchanged).
    assert _loser_timeout_for_strategy("ema_crossover") == LOSER_TIMEOUT_CAP_SEC
    assert _loser_timeout_for_strategy("xau_indices_trend") == LOSER_TIMEOUT_CAP_SEC


def test_mfe_protect_covers_all_registered_strategies() -> None:
    # [[harvest_generalization_2026-06-23]]: the harvest floor was ONLY wired to
    # equity (asset_class=='equity'); the 9 other bar strategies passed
    # mfe_protect=None → below the FSM 1.0R PROTECT rung the only floor was the
    # wide 2-ATR trail, so the measured +0.3R / +0.45R excursions (29.2% / 19.7%
    # of trades) round-tripped. _mfe_protect_for_strategy now routes EVERY
    # registered bar strategy — every asset_class (spot/fx/index/commodity/equity)
    # — to a calibrated MFE-protect schedule. EXPECTANCY, only ratchets toward
    # profit; size / entry side / the G6 -1.0R rail untouched.
    from polaris.core.live_recalc.exit_engine import (
        EXIT_EQUITY_MFE_BEP_R,
        EXIT_EQUITY_MFE_LOCK_R,
        EXIT_EQUITY_MFE_PROTECT_R,
    )
    from polaris.scripts._production_recalc_exit import _mfe_protect_for_strategy
    from polaris.strategies import STRATEGY_REGISTRY

    # Equity-class keeps its OWN proven, separately-named schedule (byte-identical).
    # (The equity_* strategies were KILLed; connors_rsi2 is the registered
    # equity-asset-class strategy that routes into the equity schedule.)
    for equity_id in ("connors_rsi2",):
        sched = _mfe_protect_for_strategy(equity_id)
        assert sched is not None, equity_id
        assert sched.bep_at_r == EXIT_EQUITY_MFE_BEP_R
        assert sched.protect_at_r == EXIT_EQUITY_MFE_PROTECT_R
        assert sched.lock_r == EXIT_EQUITY_MFE_LOCK_R
    # BEP arms below the equity MFE peak (~0.65R) — the round-trip winners
    # (ENTX +0.65R, NXTS +0.64R) now reach the floor instead of giving it all back.
    assert EXIT_EQUITY_MFE_BEP_R < 0.65
    # Every previously-UNCOVERED registered bar strategy now harvests too: a
    # calibrated schedule (BEP below where ~30% of trades reach, lock positive R)
    # — converts the +0.3R excursion into a break-even-or-better exit.
    for sid in STRATEGY_REGISTRY:
        sched = _mfe_protect_for_strategy(sid)
        assert sched is not None, sid
        assert 0.0 < sched.bep_at_r <= sched.protect_at_r
        assert sched.lock_r > 0.0
    # Tick-engine ids (own schedule, routed in the tick engine) + unregistered →
    # None → byte-identical to the pre-fix exit for the bar path.
    assert _mfe_protect_for_strategy("flow_pressure") is None  # tick (own sched)
    assert _mfe_protect_for_strategy("micro_reversion") is None  # tick (own sched)
    assert _mfe_protect_for_strategy("") is None
    assert _mfe_protect_for_strategy("not_a_strategy") is None


def test_loser_timeout_cap_env_tunable(monkeypatch: pytest.MonkeyPatch) -> None:
    # POLARIS_LOSER_TIMEOUT_SEC re-tunes the drift backstop cap. The constant +
    # the helper now live in ``core.live_recalc.loser_timeout`` (the module read
    # is there); ``_production_recalc_exit`` re-exports them. Reload the CORE
    # module so its module-level env read picks up the override, then reload the
    # scripts re-export so its binding refreshes too.
    import importlib

    monkeypatch.setenv("POLARIS_LOSER_TIMEOUT_SEC", "1800")
    import polaris.core.live_recalc.loser_timeout as loser_mod
    import polaris.scripts._production_recalc_exit as recalc_mod

    loser_mod = importlib.reload(loser_mod)
    recalc_mod = importlib.reload(recalc_mod)
    try:
        assert recalc_mod.LOSER_TIMEOUT_CAP_SEC == 1800.0
        # ema_crossover (1H, 7200s floor) capped at the re-tuned 1800s.
        assert recalc_mod._loser_timeout_for_strategy("ema_crossover") == 1800.0
    finally:
        monkeypatch.delenv("POLARIS_LOSER_TIMEOUT_SEC", raising=False)
        importlib.reload(loser_mod)
        importlib.reload(recalc_mod)


@pytest.mark.asyncio
async def test_1h_thesis_not_force_closed_at_900s(
    memdb: sqlite3.Connection,
) -> None:
    # A ema_crossover (1H) loser aged 901s — past the flat 900s but WITHIN its
    # capped 3600s horizon — must NOT be force-closed (hold-to-thesis). last_price
    # 99.8 keeps it a small loser, never touched profit, so only the timeout
    # applies.
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 1  # 901s held
    _seed(
        memdb, position_id="pos-1h", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="ema_crossover",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1h")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-1h")["status"] == "open"  # horizon respected
    assert state.recalc_precise_exit == 0


@pytest.mark.asyncio
async def test_1m_strategy_still_times_out_at_900s(
    memdb: sqlite3.Connection,
) -> None:
    # A 1m-equivalent loser (unregistered "vb" → flat 900s) past 900s STILL
    # times out — the short horizon for fast strategies is preserved.
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-1m", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="vb",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1m")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-1m")["status"] == "closed"
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_1h_drifter_closes_at_capped_backstop(
    memdb: sqlite3.Connection,
) -> None:
    # A ema_crossover (1H) sideways-drift loser held past the named drift backstop
    # (LOSER_TIMEOUT_CAP_SEC=3600s) now closes — was 7200s (the uncapped
    # 2-bar 1H floor), so a dead-thesis drifter is cut ~1hr faster. Small loser
    # (99.8 = -0.2%), never touched profit → only the timeout path applies
    # (the −1R rail / ATR-trail are not the trigger here).
    from polaris.scripts._production_recalc_exit import LOSER_TIMEOUT_CAP_SEC

    opened = NOW - int(LOSER_TIMEOUT_CAP_SEC) - 60  # 3660s held, past 3600 cap
    _seed(
        memdb, position_id="pos-drift", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="ema_crossover",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-drift")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-drift")["status"] == "closed"  # cut at 3600s
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_1h_drifter_within_cap_stays_open(
    memdb: sqlite3.Connection,
) -> None:
    # The same ema_crossover (1H) drifter still WITHIN the 3600s backstop holds —
    # the cap shortened the horizon (7200→3600), it did not make exits
    # trigger-happy before the backstop. 1800s held < 3600s cap → open.
    opened = NOW - 1800  # well under the 3600s cap
    _seed(
        memdb, position_id="pos-drift-young", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="ema_crossover",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-drift-young")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-drift-young")["status"] == "open"
    assert state.recalc_precise_exit == 0


# --- G7 EXIT_NOW dropped-decision bug fix: now actually closes -------------


@pytest.mark.asyncio
async def test_g7_exit_now_now_closes_position(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # W3 cutover adaptation (NOT a behavior change): pins the LEGACY GPT
    # path under POLARIS_AI_FREE=0; flag=1 is covered by test_ai_free_cutover.py.
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    # Strong winner (pnl_r > 0.7R) → deterministic G6 ADJUST_EXIT chains G7;
    # G7 GPT emits EXIT_NOW → position must close now. tight band keeps the
    # precise-exit engine from closing first (winner, harvest, not stopped).
    # ai_conductor P3: G6 is deterministic, so the mock only feeds G7.
    _seed(memdb, position_id="pos-g7x", entry_price=100.0, last_price=101.0, band=0.1)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-g7x")]
    gpt = _MockGPTClient([
        '{"decision":"EXIT_NOW","reason":"regime flip","new_exit_atr":0.0}',  # G7
    ])
    await _recalc(memdb, state, gpt=gpt)
    assert _pos_row(memdb, "pos-g7x")["status"] == "closed"
    assert state.recalc_g6_calls == 1
    assert state.recalc_g7_calls == 1
    assert state.recalc_exit_now == 1
    # precise-exit did NOT close it (the G7 EXIT_NOW did).
    assert state.recalc_precise_exit == 0
    # G7 is the only GPT call — G6 is deterministic (no per-position GPT).
    assert len(gpt.calls) == 1


# --- G6 hard loss rail unchanged (deterministic EXIT_NOW at pnl_r <= -1R) ---


@pytest.mark.asyncio
async def test_g6_hard_rail_exit_now_unchanged(memdb: sqlite3.Connection) -> None:
    """The deterministic G6 -1.0R EXIT_NOW rail is unchanged (ai_conductor P3).

    A hard loser closes within one tick (the FSM precise-exit owns the stop
    close; the G6 rail is the deterministic backstop behind it). No GPT call is
    made for G6.
    """
    _seed(memdb, position_id="pos-g6x", entry_price=100.0, last_price=98.0, band=0.1)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-g6x")]
    gpt = _MockGPTClient(['{"decision":"HOLD","reason":"x"}'])
    await _recalc(memdb, state, gpt=gpt)
    # The hard loser is gone within the tick (FSM stop / G6 rail backstop).
    assert _pos_row(memdb, "pos-g6x")["status"] == "closed"
    # No GPT call was needed to close a deterministic loser.
    assert gpt.calls == []


# --- stale-snapshot seal: resume from the FRESHEST persisted state -----------


def _stale_pos_row(position_id: str) -> ActivePositionRow:
    """A bar-cycle snapshot taken BEFORE a tick pass ratcheted the DB row —
    looser stop, lower peak, regressed FSM."""
    pos = ActivePositionRow()
    pos.update(
        {
            "position_id": position_id,
            "venue": "okx",
            "symbol": "BTC-USDT",
            "side": "long",
            "active_strategy_id": "vb",
            "strategy": "vb",
            "stop_price": 101.0,
            "peak_price": 104.0,
            "trough_price": 99.9,
            "exit_state": "touched",
        }
    )
    return pos


async def _run_precise_exit_direct(
    conn: sqlite3.Connection, pos: ActivePositionRow,
) -> bool:
    return await run_precise_exit(
        conn=conn, state=ProdLoopState(), pos=pos, side="long",
        entry_price=100.0, last_price=104.5, atr_pct=0.019, pnl_r=1.0,
        held_seconds=60, now_ts=NOW, close_specific=close_specific_position,
        lookup_regime=_regime, gpt_client=None, phase="P1",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
    )


@pytest.mark.asyncio
async def test_precise_exit_resumes_freshest_persisted_state(
    memdb: sqlite3.Connection,
) -> None:
    """run_precise_exit must resume from the FRESHEST persisted positions row,
    not the caller's snapshot. The bar cycle snapshots ALL positions at cycle
    start, then awaits G6/G7/close network calls between positions — a 500ms
    tick pass can ratchet the row in that window, and evaluating from the
    stale snapshot would roll the ratchet back (stop loosened ≤1 bar-cycle).
    FSM seed is "harvest" so the exact-match assert cannot be satisfied by
    re-derivation (the re-derived target tops out at "protected")."""
    _seed(memdb, position_id="pos-fresh", entry_price=100.0, last_price=104.5)
    # The DB row carries the freshest ratchet (a tick pass just wrote it).
    memdb.execute(
        "UPDATE positions SET stop_price=104.0, peak_price=106.0, "
        "trough_price=99.5, exit_state='harvest' WHERE position_id='pos-fresh'"
    )

    closed = await _run_precise_exit_direct(memdb, _stale_pos_row("pos-fresh"))

    assert closed is False  # 104.5 > the 104.0+ stop — held
    row = _pos_row(memdb, "pos-fresh")
    assert row["stop_price"] is not None and row["stop_price"] >= 104.0, (
        f"stale snapshot rolled the ratchet back: {row['stop_price']} < 104.0"
    )
    assert row["peak_price"] == pytest.approx(106.0)
    assert row["trough_price"] == pytest.approx(99.5)
    assert row["exit_state"] == "harvest"


@pytest.mark.asyncio
async def test_precise_exit_missing_row_uses_caller_fields(
    memdb: sqlite3.Connection,
) -> None:
    """No positions row (synthetic/replay caller) → the caller-supplied pos
    fields drive the resume; persist is a no-op; never raises.

    last_price 100.9 sits BETWEEN the caller-field stop (101.0 → trail fires,
    close decision True) and a from-entry re-seeded trail (≈97 → would hold) —
    so replacing the fallback with init_exit_state() flips this test RED."""
    pos = _stale_pos_row("pos-ghost")  # not in the DB
    closed = await run_precise_exit(
        conn=memdb, state=ProdLoopState(), pos=pos, side="long",
        entry_price=100.0, last_price=100.9, atr_pct=0.019, pnl_r=0.5,
        held_seconds=60, now_ts=NOW, close_specific=close_specific_position,
        lookup_regime=_regime, gpt_client=None, phase="P1",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
    )
    assert closed is True  # 100.9 ≤ the caller-field 101.0 stop — trail fired
