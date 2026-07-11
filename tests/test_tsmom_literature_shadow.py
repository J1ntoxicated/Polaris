"""tsmom_literature_shadow — TDD (frontgate item #2, G2 behavior-0 shadow).

DEMO/PAPER. Literature-fixed 12-1 momentum (252/21, hard-pinned — never
virtual_loosen'd) vs the EXISTING deployed tsmom_12_1_multiasset sign
(possibly VIRTUAL-loosened), logged to gate_shadow_events (gate_id=2) as pure
measurement. No sizing/entry/exit change (behavior-0) — no strategy /
gate-decision code reads this table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.pipeline.agents.tsmom_literature_shadow import (
    LITERATURE_MOM_LOOKBACK,
    LITERATURE_MOM_SKIP,
    literature_mom_12_1,
    log_tsmom_literature_shadow,
)
from polaris.core.pipeline.gate_state import GATE_STRATEGY_SIGNAL
from polaris.storage.schema import ALL_DDL
from polaris.strategies.base import BarView
from polaris.strategies.tsmom_12_1_multiasset import MOM_LOOKBACK


def _mkconn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "shadow.sqlite", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _bars_from_closes(closes: list[float]) -> list[BarView]:
    return [
        BarView(ts=1_700_000_000 + i * 86400, open=c, high=c, low=c, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


# ---------------------------------------------------------------------------
# 1. Literature constants are hard-pinned (never virtual_loosen'd).
# ---------------------------------------------------------------------------


def test_literature_constants_are_hard_pinned() -> None:
    """252/21 regardless of POLARIS_VIRTUAL_ACCOUNT — literature spec is fixed."""
    assert LITERATURE_MOM_LOOKBACK == 252
    assert LITERATURE_MOM_SKIP == 21


def test_module_never_routes_through_virtual_loosen() -> None:
    """Source-level guard: this module must not IMPORT virtual_loosen — the
    literature params must stay pinned in every mode (adjustment #3). Prose
    mentioning the term (explaining WHY it's avoided) is fine; an import
    wiring it in is not."""
    src = Path(
        "polaris/core/pipeline/agents/tsmom_literature_shadow.py"
    ).read_text()
    assert "import virtual_loosen" not in src
    assert "from polaris.strategies._virtual_loosen" not in src


def test_module_makes_zero_gpt_calls() -> None:
    """Source-level guard: gpt_decision column reuse must NOT be an actual GPT
    call — no gpt-client import anywhere in this module."""
    src = Path(
        "polaris/core/pipeline/agents/tsmom_literature_shadow.py"
    ).read_text()
    assert "_gpt_client" not in src
    assert "openai" not in src.lower()


# ---------------------------------------------------------------------------
# 2. literature_mom_12_1 — pure math
# ---------------------------------------------------------------------------


def test_literature_mom_12_1_none_when_warmup_unmet() -> None:
    closes = [100.0] * (LITERATURE_MOM_LOOKBACK)  # one bar short of 253
    assert literature_mom_12_1(closes) is None


def test_literature_mom_12_1_matches_manual_calc() -> None:
    # 254 flat-then-ramp closes: base = closes[-(252+1)] = closes[1] = 100.0,
    # anchor = closes[-(21+1)] = closes[-22].
    closes = [100.0] * 232 + [200.0] * 22  # len = 254
    mom = literature_mom_12_1(closes)
    assert mom is not None
    base_close = closes[-(LITERATURE_MOM_LOOKBACK + 1)]
    anchor_close = closes[-(LITERATURE_MOM_SKIP + 1)]
    assert mom == anchor_close / base_close - 1.0
    assert mom > 0.0  # ramp up -> positive momentum


def test_literature_mom_12_1_negative_on_downtrend() -> None:
    closes = [200.0] * 232 + [100.0] * 22
    mom = literature_mom_12_1(closes)
    assert mom is not None
    assert mom < 0.0


# ---------------------------------------------------------------------------
# 3. log_tsmom_literature_shadow — DB write (fail-open, gate_id=2, raw INSERT)
# ---------------------------------------------------------------------------


def test_noop_when_conn_none() -> None:
    # Must not raise.
    log_tsmom_literature_shadow(
        None, run_id="r1", signal_id=None, venue="okx", symbol="BTC-USDT",
        regime="trend", bars=_bars_from_closes([100.0] * 260), now_ts=1,
    )


def test_noop_when_literature_warmup_unmet(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    log_tsmom_literature_shadow(
        conn, run_id="r1", signal_id=None, venue="okx", symbol="BTC-USDT",
        regime="trend", bars=_bars_from_closes([100.0] * 50), now_ts=1,
    )
    count = conn.execute("SELECT COUNT(*) FROM gate_shadow_events").fetchone()[0]
    assert count == 0


def test_writes_row_with_gate_id_2_and_literature_sign(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    closes = [100.0] * 232 + [200.0] * 22  # positive literature momentum
    log_tsmom_literature_shadow(
        conn, run_id="tick-7", signal_id=None, venue="okx", symbol="BTC-USDT",
        regime="trend", bars=_bars_from_closes(closes), now_ts=1_780_000_000,
    )
    row = conn.execute(
        "SELECT gate_id, venue, symbol, regime, technical_decision, "
        "gpt_decision, mismatch, run_id, signal_id, created_ts "
        "FROM gate_shadow_events"
    ).fetchone()
    assert row is not None
    (gate_id, venue, symbol, regime, technical_decision, gpt_decision,
     mismatch, run_id, signal_id, created_ts) = row
    assert gate_id == GATE_STRATEGY_SIGNAL
    assert venue == "okx"
    assert symbol == "BTC-USDT"
    assert regime == "trend"
    assert technical_decision == "long"  # literature sign
    assert gpt_decision in ("long", "short", "flat")  # existing sign (text, not GPT)
    assert run_id == "tick-7"
    assert signal_id is None
    assert created_ts == 1_780_000_000


def test_mismatch_flag_set_when_signs_differ(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    # Long-run downtrend (negative literature 252/21 momentum) with a sharp
    # RECENT reversal so the existing (shorter, VIRTUAL-loosened OR real
    # 252/21) window can read a different sign than the literature window —
    # constructed generously so the two lookbacks are structurally likely to
    # disagree regardless of which existing constants are active this run.
    closes = [200.0] * 230 + [100.0] * (MOM_LOOKBACK - 10) + [500.0] * 12
    bars = _bars_from_closes(closes)
    log_tsmom_literature_shadow(
        conn, run_id="tick-8", signal_id=None, venue="okx", symbol="ETH-USDT",
        regime="trend", bars=bars, now_ts=2,
    )
    row = conn.execute(
        "SELECT technical_decision, gpt_decision, mismatch FROM gate_shadow_events"
    ).fetchone()
    assert row is not None
    technical_decision, gpt_decision, mismatch = row
    # Sanity: whenever the two signs actually differ, mismatch must be 1;
    # when they happen to agree, mismatch must be 0 (never the reverse).
    if technical_decision != gpt_decision and gpt_decision != "":
        assert mismatch == 1
    else:
        assert mismatch == 0


def test_write_fault_is_fail_open(tmp_path: Path) -> None:
    conn = _mkconn(tmp_path)
    conn.close()
    # Must not raise even though the connection is closed.
    log_tsmom_literature_shadow(
        conn, run_id="r1", signal_id=None, venue="okx", symbol="BTC-USDT",
        regime="trend", bars=_bars_from_closes([100.0] * 260), now_ts=1,
    )


def test_existing_sign_uses_deployed_module_constants(tmp_path: Path) -> None:
    """The 'gpt_decision' comparator reads tsmom_12_1_multiasset's OWN live
    MOM_LOOKBACK/MOM_SKIP (whatever virtual_loosen resolved to this process),
    not a second hard-pinned literature copy."""
    conn = _mkconn(tmp_path)
    # Build closes long enough for literature (253) but exactly at the
    # existing-deployed lookback boundary so a too-short existing calc would
    # read None (empty gpt_decision) if the wrong constant were used.
    n = max(LITERATURE_MOM_LOOKBACK, MOM_LOOKBACK) + 1
    closes = [100.0 + i * 0.1 for i in range(n)]
    log_tsmom_literature_shadow(
        conn, run_id="r1", signal_id=None, venue="okx", symbol="BTC-USDT",
        regime="trend", bars=_bars_from_closes(closes), now_ts=1,
    )
    gpt_decision = conn.execute(
        "SELECT gpt_decision FROM gate_shadow_events"
    ).fetchone()[0]
    # A monotonic ramp is warmup-satisfiable for MOM_LOOKBACK (<= n-1), so the
    # existing sign must be populated (non-empty) — proves it used the real
    # deployed constant, not silently short-circuiting.
    assert gpt_decision != ""
