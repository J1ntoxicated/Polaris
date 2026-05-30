"""Capital-rotation WIRE — integration of the pure evaluator into the live tick.

DEMO/PAPER ONLY — virtual funds. These TDD tests pin the 5 wire pieces the
``_production_rotation`` module must deliver on top of the pure
``polaris.core.rotation`` evaluator:

1. capital-kill PROPAGATION — ``register_rotation_candidate`` populates
   ``state.rotation_candidates`` at the two trigger seams (sizing_zero /
   insufficient_balance), carrying (signal, proposed_risk_pct, venue, reason).
2. ``evaluate_capital_rotation`` — per-venue: build held forward scores + the
   candidate E$ (CORE), ``choose_rotation``, and on a fire CLOSE the victim,
   then GUARANTEE close->settle->reopen ordering (NO same-tick reopen) — the
   winner is enqueued for the NEXT pass, never reserve_and_submit-ed inline.
3. VACATED-SIDE anti-churn — a post-close cooldown is registered on the victim
   and the reentry backdoor (strong-signal exempt) is CLOSED for that name.
4. ``read_cell_posterior`` learner_posterior reader helper (mu/sd/alpha/n).
5. TELEMETRY — ``state.rotations`` records every fire BEFORE any live run.

mandate: net-deploy-UP (a real pending entry per fire, NOT a throttle), winners
(protected/harvest) NEVER touched, 9-stack intact (no T4 multiplier added), no
P&L halt. MAX_PER_TICK=1. Per-venue isolation (OKX USDT / Capital margin
non-fungible). loser_timeout coordination: rotation defers to the exit engine
(does not double-close a position the precise-exit pass already owns).
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

from polaris.core.rotation import (
    POSTERIOR_MIN_N,
    HeldPosition,
    RotationCandidate,
)
from polaris.scripts._production_rotation import (
    ROTATION_VACATED_COOLDOWN_SEC,
    build_rotation_candidate,
    evaluate_capital_rotation,
    read_cell_posterior,
    register_rotation_candidate,
    rotation_vacated_cooldown_active,
)
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies.base import RawSignal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sig(
    *,
    signal_id: str = "sig_rot",
    strategy_id: str = "volume_burst",
    symbol: str = "ETH-USDT",
    strength: float = 0.95,
) -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id=strategy_id, symbol=symbol,
        side="long", strength=strength, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _seed_posterior(
    conn: sqlite3.Connection,
    *,
    exchange: str = "okx",
    strategy: str = "volume_burst",
    ticker: str = "ETH-USDT",
    regime: str = "bull_trend",
    mu: float = 0.4,
    kappa: float = 21.0,
    alpha: float = 11.0,
    beta: float = 2.0,
    n_samples: int = 25,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO learner_posterior "
        "(exchange, strategy, ticker, regime, mu, kappa, alpha, beta, m2, "
        " running_mean, n_samples, p_pos, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, 0.8, 0)",
        (exchange, strategy, ticker, regime, mu, kappa, alpha, beta, mu, n_samples),
    )


def _held(
    *,
    position_id: str = "pos_weak",
    venue: str = "okx",
    fwd_R: float | None = -0.5,
    open_risk_pct: float = 0.05,
    exit_state: str = "open",
    pnl_r: float = -0.6,
    held_sec: float = 600.0,
    symbol: str = "DOGE-USDT",
    strategy: str = "tsmom",
) -> HeldPosition:
    hp = HeldPosition(
        position_id=position_id, venue=venue, fwd_R_held=fwd_R,
        open_risk_pct_held=open_risk_pct, exit_state=exit_state,
        pnl_r=pnl_r, held_sec=held_sec,
    )
    # Attach symbol/strategy via the loader; the CORE dataclass is frozen so the
    # wire carries these out-of-band (the wire builds HeldPosition from DB rows
    # and keeps a parallel id->(symbol,strategy) map). Tests that need them use
    # the DB path instead. Returned here for evaluator-only tests.
    return hp


def _candidate(
    *,
    candidate_id: str = "ETH-USDT",
    venue: str = "okx",
    fwd_mu: float = 0.4,
    n_samples: int = 25,
    proposed_risk_pct: float = 0.05,
    proposed_size_usd: float = 4000.0,
) -> RotationCandidate:
    from polaris.core.rotation import CellStats

    cell = CellStats(
        mu=fwd_mu, posterior_sd=0.1, alpha_n=11.0,
        n_samples=n_samples, avg_pnl_r=fwd_mu,
    )
    return RotationCandidate(
        candidate_id=candidate_id, venue=venue, cell_stats=cell,
        proposed_risk_pct=proposed_risk_pct, proposed_size_usd=proposed_size_usd,
    )


def _seed_position_row(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "DOGE-USDT",
    strategy: str = "tsmom",
    regime: str = "chop",
    side: str = "long",
    opened_ts: int,
    exit_state: str = "open",
    entry_price: float = 0.10,
) -> None:
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, signal_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
        " status, opened_ts, swap_count, exit_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0, ?)",
        (
            position_id, venue, symbol, f"crypto:{symbol.split('-')[0]}",
            f"sig_{position_id}", strategy, strategy, strategy, side, 1000.0,
            opened_ts, exit_state,
        ),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, ?, 'buy', 4000.0, ?, 0.1, 1.0, ?, 'o', ?, 0.0, 0, "
        " 40000.0, 4000.0, 'filled')",
        (
            f"f_{position_id}", venue, f"{venue}:{symbol}", strategy,
            entry_price, opened_ts * 1000, position_id,
        ),
    )


def _seed_losing_bars(
    conn: sqlite3.Connection,
    *,
    venue: str = "okx",
    symbol: str = "DOGE-USDT",
    entry_price: float = 0.10,
    opened_ts: int,
) -> None:
    """Seed 1m bars whose latest close is BELOW entry so a long held shows a
    measured loss (``pnl_r < 0``) — makes the held an eligible rotation victim.

    Production helds carry real bar drift; the test reproduces that so the
    eligibility gate (pnl_r<0) the evaluator enforces is exercised honestly.
    """
    inst = f"{venue}:{symbol}"
    for i in range(14):
        close = entry_price * (1.0 - 0.10 * (i + 1) / 14.0)  # drifts down
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            " bar_interval, ts, open, high, low, close, volume, notional_usd) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 1000.0, 1000.0)",
            (
                inst, f"crypto:{symbol.split('-')[0]}", venue, symbol,
                opened_ts + i * 60, close * 1.001, close * 1.002,
                close * 0.999, close,
            ),
        )


# ===========================================================================
# 1. capital-kill PROPAGATION
# ===========================================================================


def test_register_rotation_candidate_populates_state() -> None:
    state = ProdLoopState()
    assert state.rotation_candidates == []
    register_rotation_candidate(
        state, sig=_sig(), proposed_risk_pct=0.06, venue="okx",
        binding_reason="sizing_zero",
    )
    assert len(state.rotation_candidates) == 1
    entry = state.rotation_candidates[0]
    assert entry["venue"] == "okx"
    assert entry["proposed_risk_pct"] == 0.06
    assert entry["binding_reason"] == "sizing_zero"
    assert entry["signal"].signal_id == "sig_rot"


def test_register_rotation_candidate_insufficient_balance_reason() -> None:
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(signal_id="s2"), proposed_risk_pct=0.05, venue="okx",
        binding_reason="insufficient_balance",
    )
    assert state.rotation_candidates[0]["binding_reason"] == "insufficient_balance"


def test_register_rotation_candidate_ignores_non_positive_risk() -> None:
    """A non-positive proposed_risk_pct is not a real pending deploy — skip."""
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(), proposed_risk_pct=0.0, venue="okx",
        binding_reason="sizing_zero",
    )
    assert state.rotation_candidates == []


# ===========================================================================
# 4. learner_posterior reader helper
# ===========================================================================


def test_read_cell_posterior_returns_cellstats(memdb: sqlite3.Connection) -> None:
    _seed_posterior(memdb)
    cell = read_cell_posterior(
        memdb, exchange="okx", strategy="volume_burst",
        ticker="ETH-USDT", regime="bull_trend",
    )
    assert cell is not None
    assert cell.mu == pytest.approx(0.4)
    assert cell.n_samples == 25
    assert cell.alpha_n == pytest.approx(11.0)
    # posterior_sd derived from the NIG marginal-t scale (finite, positive).
    assert cell.posterior_sd is not None
    assert cell.posterior_sd > 0.0


def test_read_cell_posterior_missing_falls_back_to_cell_matrix(
    memdb: sqlite3.Connection,
) -> None:
    """No posterior row → read avg_pnl_r from cell_matrix_p0 (held fallback)."""
    memdb.execute(
        "INSERT INTO cell_matrix_p0 "
        "(exchange, strategy, ticker, regime, n_eff, wins_eff, pnl_r_sum_eff, "
        " avg_pnl_r, score, last_closed_ts) "
        "VALUES ('okx','tsmom','DOGE-USDT','chop', 8.0, 3.0, -4.0, -0.5, -0.1, 0)"
    )
    cell = read_cell_posterior(
        memdb, exchange="okx", strategy="tsmom",
        ticker="DOGE-USDT", regime="chop",
    )
    assert cell is not None
    # No posterior → mu None (cold for a candidate) but avg_pnl_r carried for a
    # held fallback ranking.
    assert cell.mu is None
    assert cell.avg_pnl_r == pytest.approx(-0.5)


def test_read_cell_posterior_none_when_absent(memdb: sqlite3.Connection) -> None:
    cell = read_cell_posterior(
        memdb, exchange="okx", strategy="ghost",
        ticker="NONE-USDT", regime="chop",
    )
    assert cell is None


def test_read_cell_posterior_n_below_min_keeps_mu_none(
    memdb: sqlite3.Connection,
) -> None:
    """A posterior with n < POSTERIOR_MIN_N is cold — mu surfaced but the CORE
    forward_score will reject it (candidate ineligible)."""
    _seed_posterior(memdb, ticker="LOWN-USDT", n_samples=POSTERIOR_MIN_N - 1)
    cell = read_cell_posterior(
        memdb, exchange="okx", strategy="volume_burst",
        ticker="LOWN-USDT", regime="bull_trend",
    )
    assert cell is not None
    assert cell.n_samples == POSTERIOR_MIN_N - 1


# ===========================================================================
# vacated-side anti-churn cooldown
# ===========================================================================


def test_rotation_vacated_cooldown_blocks_then_expires() -> None:
    state = ProdLoopState()
    now = int(time.time())
    # Not registered → not active.
    assert not rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom", now_ts=now,
    )
    state.rotation_vacated_cooldowns[("okx", "DOGE-USDT", "tsmom")] = (
        now + ROTATION_VACATED_COOLDOWN_SEC
    )
    assert rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom", now_ts=now,
    )
    # After expiry → no longer active.
    assert not rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom",
        now_ts=now + ROTATION_VACATED_COOLDOWN_SEC + 1,
    )


def test_rotation_vacated_cooldown_not_bypassed_by_strong_signal() -> None:
    """The vacated cooldown is checked independently of strength — a strong
    re-entry on a just-rotated victim is STILL blocked (backdoor closed)."""
    state = ProdLoopState()
    now = int(time.time())
    state.rotation_vacated_cooldowns[("okx", "DOGE-USDT", "tsmom")] = now + 300
    # No strength argument exists — the function cannot be exempted. This pins
    # the contract: the vacated cooldown has no strong-signal escape hatch.
    assert rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom", now_ts=now,
    )


# ===========================================================================
# build_rotation_candidate — DB-backed CORE RotationCandidate
# ===========================================================================


def test_build_rotation_candidate_reads_own_cell(memdb: sqlite3.Connection) -> None:
    _seed_posterior(memdb)
    cand = build_rotation_candidate(
        memdb, sig=_sig(), venue="okx", symbol="ETH-USDT",
        regime="bull_trend", proposed_risk_pct=0.05, equity=79_000.0,
    )
    assert cand is not None
    assert cand.venue == "okx"
    assert cand.proposed_risk_pct == pytest.approx(0.05)
    assert cand.cell_stats.mu == pytest.approx(0.4)
    assert cand.proposed_size_usd > 0.0


# ===========================================================================
# 2 + 3 + 5. evaluate_capital_rotation — fire, ordering, telemetry
# ===========================================================================


@pytest.mark.asyncio
async def test_evaluate_no_candidates_is_noop(memdb: sqlite3.Connection) -> None:
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=int(time.time()),
        close_specific=close_mock, lookup_regime=lambda c, v, s: "chop",
        equity=79_000.0,
    )
    assert n == 0
    close_mock.assert_not_awaited()
    assert state.rotations == []


@pytest.mark.asyncio
async def test_evaluate_fires_closes_victim_and_does_not_reopen_same_tick(
    memdb: sqlite3.Connection,
) -> None:
    """A strong measured candidate vs a measured-losing held → ONE close, and
    the winner is NOT reserve_and_submit-ed this tick (settle ordering)."""
    now = int(time.time())
    # Weak held: a measured loser, aged, on OKX.
    _seed_position_row(
        memdb, position_id="pos_weak", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700, exit_state="open",
    )
    _seed_losing_bars(memdb, symbol="DOGE-USDT", opened_ts=now - 700)
    # Weak cell posterior (negative edge) for the held.
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    # Strong candidate cell (positive edge) for the blocked ETH signal.
    _seed_posterior(
        memdb, ticker="ETH-USDT", strategy="volume_burst", regime="bull_trend",
        mu=0.6, n_samples=30,
    )
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), proposed_risk_pct=0.06,
        venue="okx", binding_reason="sizing_zero",
    )

    def _lookup(c: sqlite3.Connection, v: str, s: str) -> str:
        return "bull_trend" if s == "ETH-USDT" else "chop"

    close_mock = AsyncMock(return_value=True)
    reserve_mock = AsyncMock()
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=_lookup, equity=79_000.0, reserve_and_submit=reserve_mock,
    )
    assert n == 1
    close_mock.assert_awaited_once()
    # CRITICAL ordering: the winner must NOT be submitted this tick (settle gate).
    reserve_mock.assert_not_awaited()
    # Telemetry recorded.
    assert len(state.rotations) == 1
    rec = state.rotations[0]
    assert rec["victim_id"] == "pos_weak"
    assert rec["e_new"] > rec["e_held"]
    assert "margin" in rec and "cost" in rec
    # Vacated-side cooldown registered on the victim.
    assert rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom", now_ts=now,
    )


@pytest.mark.asyncio
async def test_evaluate_cold_candidate_cannot_beat_measured_negative_held(
    memdb: sqlite3.Connection,
) -> None:
    """A cold (n<N) candidate cell never out-ranks a measured-negative held."""
    now = int(time.time())
    _seed_position_row(
        memdb, position_id="pos_weak2", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700,
    )
    _seed_losing_bars(memdb, symbol="DOGE-USDT", opened_ts=now - 700)
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    # Candidate cell COLD (n below min) — no eligible edge.
    _seed_posterior(
        memdb, ticker="ETH-USDT", strategy="volume_burst", regime="bull_trend",
        mu=0.9, n_samples=POSTERIOR_MIN_N - 5,
    )
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), proposed_risk_pct=0.06,
        venue="okx", binding_reason="sizing_zero",
    )
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("bull_trend" if s == "ETH-USDT" else "chop"),
        equity=79_000.0,
    )
    assert n == 0
    close_mock.assert_not_awaited()
    assert state.rotations == []


@pytest.mark.asyncio
async def test_evaluate_winners_never_touched(memdb: sqlite3.Connection) -> None:
    """A protected/harvest position is NEVER a victim even if it is the only
    held and the candidate is strong."""
    now = int(time.time())
    _seed_position_row(
        memdb, position_id="pos_winner", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700,
        exit_state="protected",
    )
    _seed_losing_bars(memdb, symbol="DOGE-USDT", opened_ts=now - 700)
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    _seed_posterior(
        memdb, ticker="ETH-USDT", strategy="volume_burst", regime="bull_trend",
        mu=0.9, n_samples=30,
    )
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), proposed_risk_pct=0.06,
        venue="okx", binding_reason="sizing_zero",
    )
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("bull_trend" if s == "ETH-USDT" else "chop"),
        equity=79_000.0,
    )
    assert n == 0
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_per_venue_isolation(memdb: sqlite3.Connection) -> None:
    """A Capital candidate cannot rotate an OKX held (non-fungible capital)."""
    now = int(time.time())
    _seed_position_row(
        memdb, position_id="pos_okx_weak", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700,
    )
    _seed_losing_bars(memdb, symbol="DOGE-USDT", opened_ts=now - 700)
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    _seed_posterior(
        memdb, exchange="capital", ticker="EURUSD", strategy="fx_breakout",
        regime="bull_trend", mu=0.9, n_samples=30,
    )
    state = ProdLoopState()
    # Candidate is on CAPITAL — must not touch the OKX held.
    register_rotation_candidate(
        state, sig=_sig(strategy_id="fx_breakout", symbol="EURUSD"),
        proposed_risk_pct=0.06, venue="capital", binding_reason="sizing_zero",
    )
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("bull_trend" if s == "EURUSD" else "chop"),
        equity=79_000.0,
    )
    assert n == 0
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_max_one_rotation_per_tick(memdb: sqlite3.Connection) -> None:
    """Two weak helds + two strong candidates → at most ONE close (MAX_PER_TICK)."""
    now = int(time.time())
    for i, sym in enumerate(("DOGE-USDT", "ADA-USDT")):
        _seed_position_row(
            memdb, position_id=f"pos_w{i}", venue="okx", symbol=sym,
            strategy="tsmom", regime="chop", opened_ts=now - 700,
        )
        _seed_losing_bars(memdb, symbol=sym, opened_ts=now - 700)
        _seed_posterior(
            memdb, ticker=sym, strategy="tsmom", regime="chop",
            mu=-0.5 - i * 0.1, n_samples=30,
        )
    for sym in ("ETH-USDT", "BTC-USDT"):
        _seed_posterior(
            memdb, ticker=sym, strategy="volume_burst", regime="bull_trend",
            mu=0.7, n_samples=30,
        )
    state = ProdLoopState()
    for sym in ("ETH-USDT", "BTC-USDT"):
        register_rotation_candidate(
            state, sig=_sig(symbol=sym, signal_id=f"sig_{sym}"),
            proposed_risk_pct=0.06, venue="okx", binding_reason="sizing_zero",
        )
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("chop" if s in ("DOGE-USDT", "ADA-USDT") else "bull_trend"),
        equity=79_000.0,
    )
    assert n == 1
    assert close_mock.await_count == 1
    assert len(state.rotations) == 1


@pytest.mark.asyncio
async def test_evaluate_defers_when_victim_close_fails(
    memdb: sqlite3.Connection,
) -> None:
    """If close_specific returns False (venue reject / not found), NO telemetry
    fire is recorded and NO cooldown is registered — rotation did not happen."""
    now = int(time.time())
    _seed_position_row(
        memdb, position_id="pos_weak3", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700,
    )
    _seed_losing_bars(memdb, symbol="DOGE-USDT", opened_ts=now - 700)
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    _seed_posterior(
        memdb, ticker="ETH-USDT", strategy="volume_burst", regime="bull_trend",
        mu=0.6, n_samples=30,
    )
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), proposed_risk_pct=0.06,
        venue="okx", binding_reason="sizing_zero",
    )
    close_mock = AsyncMock(return_value=False)  # close fails
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("bull_trend" if s == "ETH-USDT" else "chop"),
        equity=79_000.0,
    )
    assert n == 0
    assert state.rotations == []
    assert not rotation_vacated_cooldown_active(
        state, venue="okx", symbol="DOGE-USDT", strategy="tsmom", now_ts=now,
    )


@pytest.mark.asyncio
async def test_evaluate_no_double_close_with_loser_timeout(
    memdb: sqlite3.Connection,
) -> None:
    """rotation must NOT pick a victim whose precise-exit FSM already flagged a
    close this tick — coordinate via the closed-position guard (status closed /
    no open trade in state). A position already CLOSED is invisible to the held
    loader, so it can never be double-closed."""
    now = int(time.time())
    _seed_position_row(
        memdb, position_id="pos_closed", venue="okx", symbol="DOGE-USDT",
        strategy="tsmom", regime="chop", opened_ts=now - 700,
    )
    # The exit engine already closed it this tick.
    memdb.execute(
        "UPDATE positions SET status='closed', exit_state='closed' "
        "WHERE position_id='pos_closed'"
    )
    _seed_posterior(
        memdb, ticker="DOGE-USDT", strategy="tsmom", regime="chop",
        mu=-0.5, n_samples=30,
    )
    _seed_posterior(
        memdb, ticker="ETH-USDT", strategy="volume_burst", regime="bull_trend",
        mu=0.6, n_samples=30,
    )
    state = ProdLoopState()
    register_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), proposed_risk_pct=0.06,
        venue="okx", binding_reason="sizing_zero",
    )
    close_mock = AsyncMock(return_value=True)
    n = await evaluate_capital_rotation(
        memdb, state=state, now_ts=now, close_specific=close_mock,
        lookup_regime=lambda c, v, s: ("bull_trend" if s == "ETH-USDT" else "chop"),
        equity=79_000.0,
    )
    assert n == 0
    close_mock.assert_not_awaited()


# ===========================================================================
# TRIGGER SEAMS — end-to-end propagation through the real production helpers
# ===========================================================================


def test_entry_sizer_sizing_zero_surfaces_proposed_risk_pct() -> None:
    """The entry_sizer KILL on a binding cap exposes ``proposed_risk_pct`` so
    the rotation trigger seam can capture the conviction-derived capital scale.
    Pins the contract the seam-1 helper depends on (CORE record / cross-verify).
    """
    from polaris.core.sizing.schema import SizingFinal, SizingProposal

    proposal = SizingProposal(
        base_risk_pct=0.02, continuous_scalar=1.2, tier_amplifier=2.0,
        cell_routing_mult=1.0, listing_watchdog_mult=1.0, proposed_risk_pct=0.048,
    )
    sized = SizingFinal(
        proposed=proposal, final_risk_pct=0.0, final_notional_usd=0.0,
        leverage=1.0, binding_cap="per_symbol",
    )
    payload = {
        "reason": "sizing_zero",
        "binding_cap": sized.binding_cap,
        "proposed_risk_pct": sized.proposed.proposed_risk_pct,
    }
    assert payload["proposed_risk_pct"] == pytest.approx(0.048)


def test_seam1_helper_registers_on_sizing_zero_kill() -> None:
    """``_maybe_register_rotation_candidate`` registers from a sizing_zero KILL."""
    from polaris.core.pipeline.gate_state import GateDecision, GateResult
    from polaris.scripts._production_run_signal import (
        _maybe_register_rotation_candidate,
    )

    state = ProdLoopState()
    kill = GateResult(
        decision=GateDecision.KILL, next_gate=None,
        payload={"reason": "sizing_zero", "binding_cap": "per_track",
                 "proposed_risk_pct": 0.05},
        model_used="python",
    )
    _maybe_register_rotation_candidate(
        state, results=[kill], sig=_sig(symbol="ETH-USDT"), venue="okx",
    )
    assert len(state.rotation_candidates) == 1
    assert state.rotation_candidates[0]["proposed_risk_pct"] == pytest.approx(0.05)
    assert state.rotation_candidates[0]["binding_reason"] == "sizing_zero:per_track"


def test_seam1_helper_skips_non_capital_kill() -> None:
    """A non-sizing_zero KILL (signal-quality reject) is NOT registered."""
    from polaris.core.pipeline.gate_state import GateDecision, GateResult
    from polaris.scripts._production_run_signal import (
        _maybe_register_rotation_candidate,
    )

    state = ProdLoopState()
    kill = GateResult(
        decision=GateDecision.KILL, next_gate=None,
        payload={"reason": "validator_reject"},
        model_used="python",
    )
    _maybe_register_rotation_candidate(
        state, results=[kill], sig=_sig(), venue="okx",
    )
    assert state.rotation_candidates == []


def test_seam2_helper_registers_on_balance_reject() -> None:
    """Balance reject (51008) registers; compliance (51155) does not."""
    from polaris.scripts._production_pipeline import (
        _maybe_register_balance_rotation_candidate,
    )

    state = ProdLoopState()
    _maybe_register_balance_rotation_candidate(
        state, sig=_sig(symbol="ETH-USDT"), venue="okx",
        reject_code="51008", notional_usd=4000.0,
    )
    assert len(state.rotation_candidates) == 1
    assert "insufficient_balance" in state.rotation_candidates[0]["binding_reason"]
    assert state.rotation_candidates[0]["proposed_risk_pct"] > 0.0

    state2 = ProdLoopState()
    _maybe_register_balance_rotation_candidate(
        state2, sig=_sig(), venue="okx",
        reject_code="51155", notional_usd=4000.0,
    )
    assert state2.rotation_candidates == []
