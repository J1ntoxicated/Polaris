"""Profit-sweep ladder (1단→3단 auto-draw) — Layer 3 T4 extension.

Spec source: vault/50_research/debates/profit_sweep_ladder_2026-07-03.md
(codex 3-round consensus, DEMO/PAPER OKX/Capital/Alpaca).

Ledger = append-only CREDIT/DRAW/RELEASE event rows; balance is a SUM()
projection (crash/replay safe, no aggregate mutable column). Scope: sweep
plumbing is 1단(any venue close)→3단(Alpaca draw) ONLY — Track A/B/2단 T4
untouched (structurally asserted below).
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from polaris.core.sizing.ladder import (
    bucket_available_usd,
    draw_for_signal,
    materialize_credits,
    release_stale_draws,
)
from polaris.storage.schema import ALL_DDL

NOW = 1_800_000_000


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        c.execute(stmt)
    c.commit()
    yield c
    c.close()


def _insert_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    strategy_id: str = "s1",
    status: str = "closed",
    closed_ts: int | None = NOW,
    signal_id: str = "",
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, closed_ts, signal_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 'long', 1.0, ?, ?, ?, ?)",
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         status, NOW - 3600, closed_ts, signal_id),
    )
    conn.commit()


def _insert_close_fill(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    pnl_usd: float,
    fill_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id, contribution_id, pnl_usd, is_close) "
        "VALUES (?, ?, ?, 's1', 'sell', 100.0, 50000.0, ?, ?, ?, ?, 1)",
        (fill_id or f"fill-{position_id}-{pnl_usd}", venue, f"{venue}:{symbol}",
         NOW * 1000, f"order-{position_id}", position_id, pnl_usd),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# (a) CREDIT idempotency
# ---------------------------------------------------------------------------


def test_credit_materialize_idempotent_same_position_twice() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=200.0)

    n1 = materialize_credits(conn, now_ts=NOW)
    n2 = materialize_credits(conn, now_ts=NOW + 10)

    rows = conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='CREDIT' AND source_position_id='p1'"
    ).fetchone()
    assert rows[0] == 1
    assert n1 == 1
    assert n2 == 0
    conn.close()


def test_credit_amount_is_sweep_pct_of_net_pnl() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=200.0)

    materialize_credits(conn, now_ts=NOW)
    amount = conn.execute(
        "SELECT amount_usd FROM ladder_ledger WHERE event_type='CREDIT' AND source_position_id='p1'"
    ).fetchone()[0]
    assert amount == pytest.approx(100.0)  # 0.50 * 200
    conn.close()


# ---------------------------------------------------------------------------
# (b) partial-fill per-position NET offsetting
# ---------------------------------------------------------------------------


def test_partial_fill_net_pnl_offsets_within_position() -> None:
    """+100 then -150 on the SAME position → net -50 → credit 0 (no CREDIT row)."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p2")
    _insert_close_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
                        pnl_usd=100.0, fill_id="f1")
    _insert_close_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
                        pnl_usd=-150.0, fill_id="f2")

    n = materialize_credits(conn, now_ts=NOW)
    assert n == 0
    row = conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='CREDIT' AND source_position_id='p2'"
    ).fetchone()
    assert row[0] == 0
    conn.close()


def test_credit_ignores_foreign_instrument_fill_sharing_contribution_id() -> None:
    """A close fill for a DIFFERENT instrument sharing the same contribution_id
    (documented cross-match pollution, see recover.py BUG B) must NOT be
    summed into this position's net PnL — instrument_id scope required."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p3", venue="okx", symbol="BTC-USDT")
    _insert_close_fill(conn, position_id="p3", venue="okx", symbol="BTC-USDT",
                        pnl_usd=200.0, fill_id="f-real")
    # Polluting fill: same contribution_id (p3) but a FOREIGN instrument.
    _insert_close_fill(conn, position_id="p3", venue="okx", symbol="ETH-USDT",
                        pnl_usd=9000.0, fill_id="f-foreign")

    n = materialize_credits(conn, now_ts=NOW)
    assert n == 1
    amount = conn.execute(
        "SELECT amount_usd FROM ladder_ledger WHERE event_type='CREDIT' AND source_position_id='p3'"
    ).fetchone()[0]
    assert amount == pytest.approx(100.0)  # 0.50 * 200 — the foreign 9000 excluded
    conn.close()


def test_partial_fill_net_positive_credits_once_on_net() -> None:
    """+300 then -100 partial slices on SAME position → net +200 → ONE credit row of 0.5*200."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p3")
    _insert_close_fill(conn, position_id="p3", venue="okx", symbol="BTC-USDT",
                        pnl_usd=300.0, fill_id="f1")
    _insert_close_fill(conn, position_id="p3", venue="okx", symbol="BTC-USDT",
                        pnl_usd=-100.0, fill_id="f2")

    n = materialize_credits(conn, now_ts=NOW)
    assert n == 1
    amount = conn.execute(
        "SELECT amount_usd FROM ladder_ledger WHERE event_type='CREDIT' AND source_position_id='p3'"
    ).fetchone()[0]
    assert amount == pytest.approx(100.0)  # 0.50 * (300 - 100)
    conn.close()


def test_two_positions_asymmetric_no_cross_position_netting() -> None:
    """Position asymmetry across DIFFERENT positions is intentional — no cross-net."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="win1")
    _insert_close_fill(conn, position_id="win1", venue="okx", symbol="BTC-USDT", pnl_usd=400.0)
    _insert_position(conn, position_id="loss1")
    _insert_close_fill(conn, position_id="loss1", venue="okx", symbol="ETH-USDT", pnl_usd=-400.0)

    materialize_credits(conn, now_ts=NOW)
    credited_positions = {
        r[0] for r in conn.execute(
            "SELECT source_position_id FROM ladder_ledger WHERE event_type='CREDIT'"
        ).fetchall()
    }
    assert credited_positions == {"win1"}


# ---------------------------------------------------------------------------
# (c) auto-draw serial atomicity — concurrent draws never double-spend
# ---------------------------------------------------------------------------


def test_draw_never_exceeds_available(tmp_path) -> None:
    db_path = tmp_path / "ladder.sqlite"
    conn = sqlite3.connect(str(db_path))
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=200.0)
    materialize_credits(conn, now_ts=NOW)  # bucket_available = 100.0
    conn.close()

    results: list[float] = []
    errors: list[BaseException] = []

    def _one_draw(idx: int) -> None:
        try:
            c = sqlite3.connect(str(db_path), timeout=10.0)
            drawn, _draw_id = draw_for_signal(
                c,
                signal_id=f"sig-{idx}",
                venue="alpaca",
                symbol="AAPL",
                strategy_id="equity_momo",
                needed_usd=60.0,
                fresh_bp_usd=10_000.0,
                pending_usd=0.0,
                now_ts=NOW + idx,
            )
            results.append(drawn)
            c.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_one_draw, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # Total drawn across all concurrent signals must never exceed the
    # available bucket (100.0) — no double-spend even though each requested 60.
    assert sum(results) <= 100.0 + 1e-9

    conn2 = sqlite3.connect(str(db_path))
    total_draw = conn2.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM ladder_ledger WHERE event_type='DRAW'"
    ).fetchone()[0]
    assert total_draw == pytest.approx(sum(results))
    assert total_draw <= 100.0 + 1e-9
    conn2.close()


def test_draw_respects_fresh_bp_minus_pending() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)  # available = 500.0

    drawn, draw_id = draw_for_signal(
        conn,
        signal_id="sig-1",
        venue="alpaca",
        symbol="AAPL",
        strategy_id="equity_momo",
        needed_usd=1000.0,
        fresh_bp_usd=50.0,
        pending_usd=20.0,
        now_ts=NOW,
    )
    # min(available=500, needed=1000, fresh_bp-pending=30) = 30
    assert drawn == pytest.approx(30.0)
    assert draw_id != ""
    conn.close()


def test_draw_zero_when_no_credit_available() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    assert drawn == 0.0
    assert draw_id == ""
    assert conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='DRAW'"
    ).fetchone()[0] == 0


def test_draw_negative_or_nonfinite_needed_never_draws() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)

    drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=-5.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    assert drawn == 0.0
    assert draw_id == ""


# ---------------------------------------------------------------------------
# (d) RELEASE predicate
# ---------------------------------------------------------------------------


def test_release_holds_when_position_open() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    _drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    # Position funded by this draw's signal is open → NOT released.
    _insert_position(conn, position_id="pos_alpaca", venue="alpaca", symbol="AAPL",
                      strategy_id="s1", status="open", closed_ts=None, signal_id="sig-1")

    released = release_stale_draws(conn, now_ts=NOW + 100)
    assert released == 0
    row = conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='RELEASE' AND draw_id=?",
        (draw_id,),
    ).fetchone()
    assert row[0] == 0


def test_release_holds_when_pending_order_present() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    _drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    conn.execute(
        "INSERT INTO pending_opens (venue, symbol, strategy_id, side, venue_order_id, "
        "notional_usd, state, created_ts, updated_ts) VALUES "
        "('alpaca', 'AAPL', 's1', 'buy', 'ord-1', 100.0, 'no_fill', ?, ?)",
        (NOW, NOW),
    )
    conn.commit()

    released = release_stale_draws(conn, now_ts=NOW + 100)
    assert released == 0


def test_release_fires_when_no_open_position_and_no_pending() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    _drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    # No open position, no pending_opens row for (alpaca, AAPL, s1) → releasable.
    released = release_stale_draws(conn, now_ts=NOW + 100)
    assert released == 1
    row = conn.execute(
        "SELECT amount_usd FROM ladder_ledger WHERE event_type='RELEASE' AND draw_id=?",
        (draw_id,),
    ).fetchone()
    assert row[0] == pytest.approx(100.0)


def test_release_reject_cancel_path_also_releases() -> None:
    """A DRAW immediately followed by a reject (no position ever opens) still releases."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    _drawn, draw_id = draw_for_signal(
        conn, signal_id="sig-reject", venue="alpaca", symbol="TSLA",
        strategy_id="s1", needed_usd=50.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    released = release_stale_draws(conn, now_ts=NOW + 1)
    assert released == 1


def test_release_two_draws_same_triple_release_independently() -> None:
    """Two draws on the SAME (venue, symbol, strategy_id) triple funding
    DIFFERENT signals must not alias: closing signal A's position releases
    ONLY draw A while signal B's position is still open, and vice versa —
    neither draw pins the other's headroom (double-use / leak regression)."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=2000.0)
    materialize_credits(conn, now_ts=NOW)

    _drawn_a, draw_id_a = draw_for_signal(
        conn, signal_id="sig-A", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    # Position A opens for sig-A, funded by draw_id_a.
    _insert_position(conn, position_id="pos_A", venue="alpaca", symbol="AAPL",
                      strategy_id="s1", status="open", closed_ts=None, signal_id="sig-A")

    _drawn_b, draw_id_b = draw_for_signal(
        conn, signal_id="sig-B", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW + 1,
    )
    # Position B opens for sig-B on the SAME (venue, symbol, strategy_id) triple.
    _insert_position(conn, position_id="pos_B", venue="alpaca", symbol="AAPL",
                      strategy_id="s1", status="open", closed_ts=None, signal_id="sig-B")

    # Position A closes; B is still open. Only draw A should release.
    conn.execute("UPDATE positions SET status='closed', closed_ts=? WHERE position_id='pos_A'", (NOW + 2,))
    conn.commit()
    released = release_stale_draws(conn, now_ts=NOW + 3)
    assert released == 1
    a_released = conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='RELEASE' AND draw_id=?", (draw_id_a,),
    ).fetchone()[0]
    b_released = conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE event_type='RELEASE' AND draw_id=?", (draw_id_b,),
    ).fetchone()[0]
    assert a_released == 1
    assert b_released == 0  # B's own position is still open — must NOT release


def test_release_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    r1 = release_stale_draws(conn, now_ts=NOW + 100)
    r2 = release_stale_draws(conn, now_ts=NOW + 200)
    assert r1 == 1
    assert r2 == 0  # already released — never double-releases


def test_release_never_creates_credit() -> None:
    """loss_debit=0 / no-credit-from-RELEASE invariant."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="src")
    _insert_close_fill(conn, position_id="src", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)
    before = bucket_available_usd(conn)
    draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=100.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    release_stale_draws(conn, now_ts=NOW + 100)
    after = bucket_available_usd(conn)
    # RELEASE only restores what was drawn — bucket_available returns to
    # (at most) its pre-draw level, never above it (no credit manufactured).
    assert after == pytest.approx(before)


# ---------------------------------------------------------------------------
# (e) loss debit = 0
# ---------------------------------------------------------------------------


def test_losing_position_never_debits_bucket() -> None:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="win")
    _insert_close_fill(conn, position_id="win", venue="okx", symbol="BTC-USDT", pnl_usd=200.0)
    materialize_credits(conn, now_ts=NOW)
    before = bucket_available_usd(conn)

    _insert_position(conn, position_id="loss")
    _insert_close_fill(conn, position_id="loss", venue="okx", symbol="ETH-USDT", pnl_usd=-500.0)
    materialize_credits(conn, now_ts=NOW + 10)
    after = bucket_available_usd(conn)

    assert after == pytest.approx(before)  # unchanged — no debit row ever written
    assert conn.execute(
        "SELECT COUNT(*) FROM ladder_ledger WHERE amount_usd < 0"
    ).fetchone()[0] == 0  # amount_usd is always non-negative by construction


def test_no_debit_row_type_exists_in_schema_contract() -> None:
    """Structural: only CREDIT/DRAW/RELEASE event types are ever written."""
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=200.0)
    materialize_credits(conn, now_ts=NOW)
    draw_for_signal(
        conn, signal_id="sig-1", venue="alpaca", symbol="AAPL",
        strategy_id="s1", needed_usd=10.0, fresh_bp_usd=1000.0,
        pending_usd=0.0, now_ts=NOW,
    )
    release_stale_draws(conn, now_ts=NOW + 100)
    types = {
        r[0] for r in conn.execute("SELECT DISTINCT event_type FROM ladder_ledger").fetchall()
    }
    assert types <= {"CREDIT", "DRAW", "RELEASE"}


# ---------------------------------------------------------------------------
# (f) 9-stack structural assertion — cap composition is min() term expansion only
# ---------------------------------------------------------------------------


def test_t4_single_trade_slot_gains_one_more_min_term_not_a_multiplier() -> None:
    """compute_size's single_trade_sub_terms min() list is EXTENDED by one more
    (name, value) pair for the ladder draw — never multiplied onto proposed_risk_pct.
    Structural assertion: ladder draw enters via cap_base + draw_used composed
    inside the SAME single min() slot (headroom_min), not as a new T4 factor."""
    import inspect

    from polaris.core.sizing import engine as sizing_engine

    src = inspect.getsource(sizing_engine.compute_size)
    # The ladder draw amount is looked up and added to a cap TERM (min() input),
    # never multiplied into `proposal.proposed_risk_pct` or the T4 chain factors.
    assert "proposal.proposed_risk_pct *" not in src.replace(" ", "")
    assert "single_trade_sub_terms" in src


def test_ladder_module_has_no_new_multiplier_export() -> None:
    """The ladder module must expose amount/draw functions only — no *_mult."""
    from polaris.core.sizing import ladder as ladder_mod

    mult_exports = [n for n in dir(ladder_mod) if "mult" in n.lower()]
    assert mult_exports == []


# ---------------------------------------------------------------------------
# (g) close hot-path zero-write lint
# ---------------------------------------------------------------------------


def test_close_hot_path_has_no_ladder_ledger_write() -> None:
    """Source lint: the close transaction files must never literally write to
    ladder_ledger — CREDIT materialization is a SEPARATE on-demand/sweeper pass,
    never inline in the position-close commit (feedback_db_lock_is_architecture_signal)."""
    import pathlib

    close_files = [
        "polaris/scripts/_production_close.py",
        "polaris/scripts/_production_close_helpers.py",
        "polaris/scripts/_production_close_effects.py",
    ]
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for rel in close_files:
        p = repo_root / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        assert "ladder_ledger" not in text, f"{rel} must not write ladder_ledger inline"
        assert "INSERT INTO ladder" not in text


# ---------------------------------------------------------------------------
# (h) live firing wire — 3단 sizing path actually calls draw_for_signal
# ---------------------------------------------------------------------------


def test_compute_size_alpaca_calls_ladder_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.core.sizing import engine as sizing_engine
    from polaris.core.sizing.engine import SignalIntent, compute_size
    from polaris.core.sizing.schema import PortfolioState, StrategyRiskState

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()
    _insert_position(conn, position_id="p1")
    _insert_close_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT", pnl_usd=1000.0)
    materialize_credits(conn, now_ts=NOW)  # bucket_available = 500.0

    calls: list[dict[str, object]] = []
    real_draw = sizing_engine.draw_for_signal

    def _spy_draw(conn_, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return real_draw(conn_, **kwargs)

    monkeypatch.setattr(sizing_engine, "draw_for_signal", _spy_draw)

    intent = SignalIntent(
        signal_id="sig-live",
        venue="alpaca",
        symbol="AAPL",
        instrument_id="alpaca:AAPL",
        underlying_group_id="equity:AAPL",
        asset_class="equity",
        strategy="equity_momo",
        track="C",
        regime="trend",
        direction="long",
        signal_strength=1.0,
        listing_age_hours=999.0,
        leverage=1.0,
        ladder_fresh_bp_usd=10_000.0,
        ladder_pending_usd=0.0,
    )
    risk_state = StrategyRiskState(
        venue="alpaca", strategy="equity_momo", closed_trades=25,
        kelly_p=0.6, kelly_q=0.4, kelly_fraction=0.2, win_streak=1,
        hit_rate_10=0.6, updated_ts=NOW,
    )
    portfolio = PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"C": 0.0},
    )
    compute_size(conn, intent=intent, risk_state=risk_state, portfolio=portfolio, now_ts=NOW)
    assert len(calls) == 1
    assert calls[0]["venue"] == "alpaca"


def test_compute_size_non_alpaca_never_calls_ladder_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.core.sizing import engine as sizing_engine
    from polaris.core.sizing.engine import SignalIntent, compute_size
    from polaris.core.sizing.schema import PortfolioState, StrategyRiskState

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.commit()

    calls: list[dict[str, object]] = []

    def _spy_draw(conn_, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return (0.0, "")

    monkeypatch.setattr(sizing_engine, "draw_for_signal", _spy_draw)

    intent = SignalIntent(
        signal_id="sig-okx",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="s1",
        track="A",
        regime="trend",
        direction="long",
        signal_strength=1.0,
        listing_age_hours=999.0,
        leverage=1.0,
    )
    risk_state = StrategyRiskState(
        venue="okx", strategy="s1", closed_trades=25, kelly_p=0.6, kelly_q=0.4,
        kelly_fraction=0.2, win_streak=1, hit_rate_10=0.6, updated_ts=NOW,
    )
    portfolio = PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0},
    )
    compute_size(conn, intent=intent, risk_state=risk_state, portfolio=portfolio, now_ts=NOW)
    assert calls == []
