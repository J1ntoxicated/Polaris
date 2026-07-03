"""pts-classes group G — telemetry: EXEC_STARVED alert key, engine.py
binding-cap log naming for the new R-pool/track_R addon + PROVE probe
constant terms, and probe-fee-exhaustion logging.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved: every assertion here is OBSERVATION-only — none
of this changes a sizing/routing decision (no_defensive_param_dampen /
no_block_filter_architecture); it only makes existing capital-routing
decisions falsifiable via structured logs the ops watchdog can key on.
No 9-stack/-1.0R rail touch (log-line additions only).
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from polaris.core.classes.r_pool import TrackMember
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.probe_notional import probe_notional_usd, prove_stop_dist_floor_pct
from polaris.storage.schema import init_db
from tools.ops.probe_reranker import run_rerank

NOW = 1_780_000_000
DAY = 86_400


def _risk_state(n: int = 25) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=n, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _intent(
    *, strategy_class: str = "EARN", atr_pct: float = 0.0, stop_atr_mult: float = 0.0,
) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1", venue="okx", symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT", underlying_group_id="crypto:PL",
        asset_class="crypto", strategy="volume_burst", track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
        strategy_class=strategy_class, atr_pct=atr_pct, stop_atr_mult=stop_atr_mult,
    )


def _portfolio(
    *,
    bench_freed_usd: dict[str, float] | None = None,
    track_members: dict[str, list[TrackMember]] | None = None,
) -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
        bench_freed_usd=bench_freed_usd or {},
        track_members=track_members or {},
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    from polaris.storage.schema import init_db
    return init_db(":memory:")


def test_r_pool_addon_log_names_track_r_ceiling(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """The R-pool add-on log line must name the ``0.5 x track_R`` ceiling
    value (not just the resulting cap_effective) so a human/alert reading
    the binding-cap trail can see WHICH new term widened the cap."""
    members = {
        "A": [TrackMember(strategy_id="volume_burst", strategy_class="EARN", score_f_w=2.0)]
    }
    with caplog.at_level(logging.INFO, logger="polaris.core.sizing.engine"):
        compute_size(
            conn, intent=_intent(), risk_state=_risk_state(),
            portfolio=_portfolio(bench_freed_usd={"A": 500.0}, track_members=members),
            now_ts=NOW,
        )
    lines = [r.getMessage() for r in caplog.records if "[T4/r-pool-addon]" in r.getMessage()]
    assert lines, "expected an r-pool-addon log line"
    assert "track_r_ceiling=" in lines[0]


def test_prove_probe_log_names_probe_notional_constant(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """The PROVE-class log line must name the fixed probe-notional constant
    driving the size (T4-chain-external — never a multiplier), not just the
    final clipped notional."""
    stop_dist = prove_stop_dist_floor_pct("okx") + 0.05
    with caplog.at_level(logging.INFO, logger="polaris.core.sizing.engine"):
        compute_size(
            conn,
            intent=_intent(strategy_class="PROVE", atr_pct=stop_dist / 2.0, stop_atr_mult=2.0),
            risk_state=_risk_state(), portfolio=_portfolio(), now_ts=NOW,
        )
    lines = [r.getMessage() for r in caplog.records if "[T4/pts-class]" in r.getMessage()]
    assert lines, "expected a pts-class PROVE log line"
    assert f"probe_notional_usd={probe_notional_usd('okx'):.2f}" in lines[0]


# ---------------------------------------------------------------------------
# probe-fee-exhaustion log (tools/ops/probe_reranker.py — the DB-facing 07:30
# sweeper; distinct from polaris.core.classes.probe_reranker's pure rank/
# assign functions, group F's own module, untouched here). Reused fixture
# shape from tests/test_ops_probe_reranker.py (group F's own test file, not
# modified — this group's test lives in its own file per scope discipline).
# ---------------------------------------------------------------------------


@pytest.fixture
def rerank_conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_strategy_class(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
    f_track_cap: float = 1.0,
    probe_fee_24h: float = 0.0,
    shadow_ring: list[float] | None = None,
) -> None:
    import json

    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, f_track_cap, dwell, epoch_id, last_transition_ts, "
        "kill_state, ladder_step, shadow_ring, probe_fee_24h) "
        "VALUES (?, ?, 'PROVE', 20, ?, 0, 1, ?, 'ACTIVE', 0, ?, ?)",
        (
            venue, strategy_id, f_track_cap, NOW - DAY,
            json.dumps(shadow_ring or [1.0]), probe_fee_24h,
        ),
    )
    conn.commit()


def test_probe_reranker_logs_fee_cap_exhausted(
    rerank_conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    """A candidate the pure ``assign_slots`` marks FEE_CAP_EXHAUSTED must
    produce a structured log line the ops watchdog's log_scan can key on —
    today ``run_rerank`` persists the reason to SQLite but never logs it
    (silent from an ops-observability standpoint)."""
    # ``_load_candidates`` derives f_track_cap_usd from score_f.f_track_cap
    # (empty score_f_events ledger here -> falls back to
    # probe_notional_usd("okx") == $10.0), NOT the strategy_class.f_track_cap
    # column passed to the fixture below (that column is a different,
    # unrelated field this sweeper never reads). Track A budget = 6 x $10 =
    # $60. leader (rank 1) spends $40 -> running=40<=60, clears. laggard
    # (rank 2) adds another $40 -> running=80>60, trips FEE_CAP_EXHAUSTED
    # (both stay within the concurrency cap of 3, isolating the fee path).
    _mk_strategy_class(
        rerank_conn, venue="okx", strategy_id="leader",
        probe_fee_24h=40.0, shadow_ring=[9.0],
    )
    _mk_strategy_class(
        rerank_conn, venue="okx", strategy_id="laggard",
        probe_fee_24h=40.0, shadow_ring=[1.0],
    )
    with caplog.at_level(logging.INFO, logger="tools.ops.probe_reranker"):
        run_rerank(rerank_conn, now_ts=NOW)
    lines = [
        r.getMessage() for r in caplog.records if "FEE_CAP_EXHAUSTED" in r.getMessage()
    ]
    assert lines, "expected a FEE_CAP_EXHAUSTED log line"
    assert "laggard" in lines[0]
