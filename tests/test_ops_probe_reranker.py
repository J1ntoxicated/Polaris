"""TDD — pts-classes (group F) DB-facing 07:30 probe reranker sweeper.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). This
sweeper reads ``strategy_class`` PROVE-class candidates per track (venue
resolved via ``polaris.core.streams.config.resolve_stream``), ranks + assigns
slots via ``polaris.core.classes.probe_reranker`` (pure functions, group F's
own module — tested separately in ``tests/test_probe_reranker.py``), and
persists ONE append-only snapshot batch into ``probe_slot_assignment`` per
run — batch-commit sweeper, zero writes on any hot path
(feedback_db_lock_is_architecture_signal). Never blocks/throttles a strategy
— an unslotted PROVE candidate is unaffected outside of this table
(no_block_filter_architecture / aggressive_always_profit); the actual G5
compute_size gating on ``slot_active`` is a separate consumer (out of this
group's scope).
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from polaris.storage.schema import init_db
from tools.ops import log_scan
from tools.ops.ops_config import OpsConfig
from tools.ops.probe_reranker import main, run_rerank

NOW = 1_780_000_000
DAY = 86_400


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_strategy_class(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
    strategy_class: str = "PROVE",
    shadow_ring: list[float] | None = None,
    probe_fee_24h: float = 0.0,
    f_track_cap: float = 10.0,
    last_transition_ts: int = NOW - DAY,
) -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, f_track_cap, dwell, epoch_id, last_transition_ts, "
        "kill_state, ladder_step, shadow_ring, probe_fee_24h) "
        "VALUES (?, ?, ?, 20, ?, 0, 1, ?, 'ACTIVE', 0, ?, ?)",
        (
            venue, strategy_id, strategy_class, f_track_cap, last_transition_ts,
            json.dumps(shadow_ring or [1.0]), probe_fee_24h,
        ),
    )
    conn.commit()


def _mk_fill_history(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, n_signals: int, n_fills: int
) -> None:
    for i in range(n_signals):
        conn.execute(
            "INSERT INTO signals (strategy_id, signal_id, instrument_id, "
            "direction, score, thesis, ts) VALUES (?, ?, ?, 'long', 0.5, 't', ?)",
            (
                strategy_id, f"sig-{venue}-{strategy_id}-{i}",
                f"{venue}:BTC-USDT", NOW - DAY + i * 60,
            ),
        )
    for i in range(n_fills):
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, pnl_usd, is_close, ts_ms, "
            "order_id, contribution_id) VALUES (?, ?, ?, ?, 'long', 100.0, "
            "100.0, 0.1, 0.0, 0, ?, ?, ?)",
            (
                f"fill-{venue}-{strategy_id}-{i}", venue, f"{venue}:BTC-USDT",
                strategy_id, (NOW - DAY + i * 60) * 1000,
                f"ord-{venue}-{strategy_id}-{i}", f"contrib-{venue}-{strategy_id}-{i}",
            ),
        )
    conn.commit()


def test_run_rerank_persists_one_row_per_prove_candidate(conn: sqlite3.Connection) -> None:
    _mk_strategy_class(conn, venue="okx", strategy_id="s1", shadow_ring=[5.0, 5.0])
    _mk_strategy_class(conn, venue="okx", strategy_id="s2", shadow_ring=[1.0, 1.0])
    inserted = run_rerank(conn, now_ts=NOW)
    assert inserted == 2
    rows = conn.execute(
        "SELECT strategy_id, rank, slot_active FROM probe_slot_assignment "
        "WHERE run_ts = ? ORDER BY rank", (NOW,),
    ).fetchall()
    assert [r[0] for r in rows] == ["s1", "s2"]
    assert [r[1] for r in rows] == [1, 2]
    assert all(r[2] == 1 for r in rows)  # both within track A's cap of 3


def test_run_rerank_only_considers_prove_class(conn: sqlite3.Connection) -> None:
    _mk_strategy_class(conn, venue="okx", strategy_id="earner", strategy_class="EARN")
    _mk_strategy_class(conn, venue="okx", strategy_id="benched", strategy_class="BENCH")
    _mk_strategy_class(conn, venue="okx", strategy_id="prover", strategy_class="PROVE")
    run_rerank(conn, now_ts=NOW)
    rows = conn.execute(
        "SELECT strategy_id FROM probe_slot_assignment WHERE run_ts = ?", (NOW,)
    ).fetchall()
    assert [r[0] for r in rows] == ["prover"]


def test_run_rerank_scopes_concurrency_cap_per_track(conn: sqlite3.Connection) -> None:
    # Track A (okx) cap=3 -> 4th candidate loses its slot.
    for i in range(4):
        _mk_strategy_class(
            conn, venue="okx", strategy_id=f"a{i}", shadow_ring=[float(10 - i)]
        )
    # Track C (alpaca) cap=1 -> only the top-scored candidate keeps a slot.
    _mk_strategy_class(conn, venue="alpaca", strategy_id="c_low", shadow_ring=[1.0])
    _mk_strategy_class(conn, venue="alpaca", strategy_id="c_high", shadow_ring=[9.0])
    run_rerank(conn, now_ts=NOW)

    a_rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT strategy_id, slot_active FROM probe_slot_assignment "
            "WHERE run_ts = ? AND track = 'A'", (NOW,),
        ).fetchall()
    }
    assert a_rows == {"a0": 1, "a1": 1, "a2": 1, "a3": 0}

    c_rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT strategy_id, slot_active FROM probe_slot_assignment "
            "WHERE run_ts = ? AND track = 'C'", (NOW,),
        ).fetchall()
    }
    assert c_rows == {"c_high": 1, "c_low": 0}


def test_run_rerank_wait_age_derived_from_last_transition_ts(conn: sqlite3.Connection) -> None:
    _mk_strategy_class(
        conn, venue="okx", strategy_id="s1", last_transition_ts=NOW - 5_000
    )
    run_rerank(conn, now_ts=NOW)
    row = conn.execute(
        "SELECT wait_age_sec FROM probe_slot_assignment WHERE run_ts = ? "
        "AND strategy_id = 's1'", (NOW,),
    ).fetchone()
    assert row[0] == 5_000


def test_run_rerank_fill_rate_derived_from_recent_fills_over_signals(
    conn: sqlite3.Connection,
) -> None:
    _mk_strategy_class(conn, venue="okx", strategy_id="s1")
    _mk_fill_history(conn, venue="okx", strategy_id="s1", n_signals=10, n_fills=4)
    run_rerank(conn, now_ts=NOW)
    row = conn.execute(
        "SELECT fill_rate FROM probe_slot_assignment WHERE run_ts = ? "
        "AND strategy_id = 's1'", (NOW,),
    ).fetchone()
    assert row[0] == pytest.approx(0.4)


def test_run_rerank_no_prove_candidates_is_a_clean_noop(conn: sqlite3.Connection) -> None:
    inserted = run_rerank(conn, now_ts=NOW)
    assert inserted == 0
    count = conn.execute("SELECT COUNT(*) FROM probe_slot_assignment").fetchone()[0]
    assert count == 0


def test_run_rerank_unknown_venue_skipped_not_crashed(conn: sqlite3.Connection) -> None:
    _mk_strategy_class(conn, venue="totally_unknown_venue", strategy_id="ghost")
    _mk_strategy_class(conn, venue="okx", strategy_id="real")
    inserted = run_rerank(conn, now_ts=NOW)
    assert inserted == 1
    rows = conn.execute(
        "SELECT strategy_id FROM probe_slot_assignment WHERE run_ts = ?", (NOW,)
    ).fetchall()
    assert [r[0] for r in rows] == ["real"]


def test_run_rerank_idempotent_same_run_ts_is_noop_reinsert(conn: sqlite3.Connection) -> None:
    _mk_strategy_class(conn, venue="okx", strategy_id="s1")
    first = run_rerank(conn, now_ts=NOW)
    second = run_rerank(conn, now_ts=NOW)
    assert first == 1
    assert second == 0  # same run_ts already has this row (PK collision -> ignore)
    count = conn.execute(
        "SELECT COUNT(*) FROM probe_slot_assignment WHERE run_ts = ?", (NOW,)
    ).fetchone()[0]
    assert count == 1


def test_main_routes_fee_cap_exhausted_to_bot_log_for_watchdog(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (pts-classes group G blocker): ``main()`` never called
    ``setup_polaris_logging``/``basicConfig`` (root logger stayed at default
    WARNING, discarding the FEE_CAP_EXHAUSTED INFO line before it even
    reached stdout) AND — even if it had logged — this job runs as a
    SEPARATE launchd process writing its own ``probe_reranker.log``, not
    ``cfg.bot_log`` (the file ``tools/ops/log_scan.py``/``watchdog.py``
    actually read). Exercises the REAL end-to-end path: ``main()`` ->
    ``cfg.bot_log`` on disk -> ``log_scan.scan()`` (the watchdog's actual
    consumer) — not a ``caplog`` capture that bypasses both breaks.

    Root-logger handlers are process-global; reset after the test so this
    doesn't leak into other tests in the same run.
    """
    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    try:
        proj = tmp_path / "proj"
        (proj / "data" / "paper").mkdir(parents=True)
        db_path = proj / "data" / "polaris_live.sqlite"
        bot_log = proj / "data" / "paper" / "polaris_runtime.log"
        cfg = OpsConfig(
            project_dir=proj, db_path=db_path, bot_log=bot_log,
            pidfile=proj / "data" / "paper" / "production.pid",
            sentinel=proj / "data" / "paper" / "MANUAL_STOP",
            ops_dir=proj / "data" / "paper" / "ops",
            vault_dir=tmp_path / "vault",
            python_bin=proj / ".venv" / "bin" / "python",
        )
        monkeypatch.setattr(OpsConfig, "default", classmethod(lambda cls: cfg))
        monkeypatch.setattr("time.time", lambda: float(NOW))

        conn = init_db(db_path)
        _mk_strategy_class(conn, venue="okx", strategy_id="leader", probe_fee_24h=40.0, shadow_ring=[9.0])
        _mk_strategy_class(conn, venue="okx", strategy_id="laggard", probe_fee_24h=40.0, shadow_ring=[1.0])
        conn.close()

        assert main() == 0

        assert bot_log.exists(), "main() must write to cfg.bot_log, not its own StandardOutPath"
        text = bot_log.read_text(encoding="utf-8")
        assert "FEE_CAP_EXHAUSTED" in text
        assert "laggard" in text

        counts = log_scan.scan(cfg)
        assert counts["probe_fee_exhausted"] == 1
    finally:
        root.handlers = prev_handlers
        root.setLevel(prev_level)


def test_run_rerank_slot_active_actually_reassigns_across_runs(
    conn: sqlite3.Connection,
) -> None:
    """Verifies actual slot REASSIGNMENT (not merely registering a row once):
    a track-C (cap=1) candidate that held the slot on day 1 loses it on day 2
    once a higher-scoring sibling enters PROVE — same track, same cap, a
    DIFFERENT winner."""
    _mk_strategy_class(conn, venue="alpaca", strategy_id="incumbent", shadow_ring=[3.0])
    day1 = run_rerank(conn, now_ts=NOW)
    assert day1 == 1
    row = conn.execute(
        "SELECT slot_active FROM probe_slot_assignment WHERE run_ts = ? "
        "AND strategy_id = 'incumbent'", (NOW,),
    ).fetchone()
    assert row[0] == 1

    _mk_strategy_class(conn, venue="alpaca", strategy_id="challenger", shadow_ring=[9.0])
    day2_ts = NOW + DAY
    day2 = run_rerank(conn, now_ts=day2_ts)
    assert day2 == 2
    rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT strategy_id, slot_active FROM probe_slot_assignment "
            "WHERE run_ts = ?", (day2_ts,),
        ).fetchall()
    }
    assert rows == {"challenger": 1, "incumbent": 0}
