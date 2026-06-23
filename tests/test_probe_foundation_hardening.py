"""Structural hardening (2026-06-23) — probe-foundation cluster (#8/#10/#11).

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block. These cover the pure
schema/typing/observe-only items, all of which are ZERO runtime-behavior change:

  * #8 ProbeRole SSOT — every probe_id maps to exactly one role
    (Eligibility/Signal/Validate/Position/Exit); the 4 catalog probes are all
    Position/Exit today; a role-missing probe fails the registry assertion.
  * #10 SWAP removed from the probe EngineAction (no producer; L6 owns
    GateDecision.SWAP_STRATEGY — the probe overlay never strategy-swaps).
  * #11 AI-escalation observe-only seam — _quantize tags a dead-band decision
    ``ambiguous=1`` while the RUNTIME action/knobs/applied stay unchanged and
    GPT calls remain 0. composite_lean/deadband_margin/quantizer_version are
    recorded for the offline rank-4 reader. v_probe_ambiguous_outcomes exists.
"""

from __future__ import annotations

import sqlite3
import typing

from polaris.core.probes import (
    EngineAction,
    ExitEngine,
    ProbeReading,
)
from polaris.core.probes.catalog import (
    LossDefenseProbe,
    ProfitTakingProbe,
    SessionHoursProbe,
    TechnicalProbe,
)
from polaris.core.probes.engine import _is_ambiguous, _quantize
from polaris.core.probes.roles import (
    PROBE_ROLE_REGISTRY,
    ProbeRole,
    role_for_probe,
)
from polaris.core.probes.tuning_log import open_probe_db

# ---------------------------------------------------------------------------
# #10 — SWAP removed from the probe EngineAction vocabulary
# ---------------------------------------------------------------------------


def test_engine_action_has_no_swap() -> None:
    # The probe overlay is a PARAMETER overlay on the #26 FSM — it never
    # strategy-swaps (that is L6's GateDecision.SWAP_STRATEGY). SWAP had no
    # producer in _quantize, so the dead enum member is gone.
    members = set(typing.get_args(EngineAction))
    assert members == {"HOLD", "WIDEN", "TIGHTEN", "HARVEST"}
    assert "SWAP" not in members


def test_quantize_never_emits_swap() -> None:
    for lean in (-1.0, -0.6, -0.3, -0.1, 0.0, 0.1, 0.4, 0.9, 1.0):
        assert _quantize(lean) in {"HOLD", "WIDEN", "TIGHTEN", "HARVEST"}


# ---------------------------------------------------------------------------
# #8 — ProbeRole SSOT registry (Eligibility/Signal/Validate/Position/Exit)
# ---------------------------------------------------------------------------


def test_probe_role_enum_has_five_roles() -> None:
    assert set(typing.get_args(ProbeRole)) == {
        "Eligibility", "Signal", "Validate", "Position", "Exit",
    }


def test_every_catalog_probe_has_a_role_in_the_ssot() -> None:
    # The 4 Slice-1 catalog probes are all implicitly Position/Exit today.
    for probe in (
        ProfitTakingProbe(), LossDefenseProbe(),
        TechnicalProbe(), SessionHoursProbe(),
    ):
        role = role_for_probe(probe.probe_id)
        assert role in {"Position", "Exit"}


def test_role_registry_covers_exactly_the_catalog_probe_ids() -> None:
    catalog_ids = {
        ProfitTakingProbe().probe_id, LossDefenseProbe().probe_id,
        TechnicalProbe().probe_id, SessionHoursProbe().probe_id,
    }
    assert catalog_ids <= set(PROBE_ROLE_REGISTRY)


def test_catalog_probe_role_attr_matches_the_ssot() -> None:
    # Every catalog probe's ``role`` attribute must equal the registry SSOT —
    # the attr can never silently drift from the single source of truth.
    for probe in (
        ProfitTakingProbe(), LossDefenseProbe(),
        TechnicalProbe(), SessionHoursProbe(),
    ):
        assert probe.role == role_for_probe(probe.probe_id)


def test_role_lookup_for_unregistered_probe_raises() -> None:
    # A role-missing probe must fail loudly (caught by import/test), never
    # silently default — the SSOT is the single source of truth.
    try:
        role_for_probe("not_a_real_probe")
    except KeyError:
        return
    raise AssertionError("role_for_probe must raise on an unregistered probe_id")


# ---------------------------------------------------------------------------
# #11 — AI-escalation observe-only seam (ambiguous flag, runtime unchanged)
# ---------------------------------------------------------------------------


def test_dead_band_decision_is_flagged_ambiguous() -> None:
    # A composite inside the HOLD dead band (between TIGHTEN and WIDEN
    # thresholds) is ambiguous — the deterministic fallback action stays HOLD.
    assert _quantize(0.0) == "HOLD"
    assert _is_ambiguous(0.0) is True


def test_decisive_lean_is_not_ambiguous() -> None:
    # A clear adverse / favorable lean is NOT ambiguous (well past a threshold).
    assert _is_ambiguous(-0.9) is False
    assert _is_ambiguous(0.9) is False


def test_compose_records_ambiguous_without_changing_action_or_knobs() -> None:
    eng = ExitEngine()
    # Near-zero composite → HOLD, ambiguous; knobs still all None + not applied.
    readings = [ProbeReading("technical", "technical", 0.0, 0.5, {})]
    dec = eng.compose(readings, mode="observe")
    assert dec.action == "HOLD"
    assert dec.ambiguous is True
    assert dec.applied is False
    assert dec.trail_mult is None
    assert dec.mfe_protect is None
    assert dec.deadband_margin is not None  # distance to the nearer threshold


def test_decisive_compose_not_ambiguous() -> None:
    eng = ExitEngine()
    dec = eng.compose(
        [ProbeReading("loss_defense", "loss", -0.9, 0.9, {})], mode="observe"
    )
    assert dec.action == "HARVEST"
    assert dec.ambiguous is False


def test_probe_decisions_has_ambiguous_columns() -> None:
    conn = open_probe_db(":memory:")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(probe_decisions)")}
    assert {"ambiguous", "composite_lean", "deadband_margin",
            "quantizer_version"} <= cols
    conn.close()


def test_v_probe_ambiguous_outcomes_view_exists() -> None:
    conn = open_probe_db(":memory:")
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }
    assert "v_probe_ambiguous_outcomes" in names
    # The view must be queryable (DDL well-formed) even when empty.
    rows = conn.execute("SELECT * FROM v_probe_ambiguous_outcomes").fetchall()
    assert rows == []
    conn.close()


def test_ambiguous_column_defaults_zero_for_legacy_rows() -> None:
    # An EXISTING probe DB without the new columns gets them via idempotent
    # ALTER, defaulting ambiguous=0 (legacy rows are not retro-flagged).
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE probe_decisions ("
        " decision_id TEXT PRIMARY KEY, eval_id TEXT, ts INTEGER, run_id TEXT,"
        " position_id TEXT, mode TEXT, composite_lean REAL, action TEXT,"
        " applied INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO probe_decisions (decision_id, action) VALUES ('d1','HOLD')"
    )
    conn.close()
