"""backgate-plan W2-d (design-exit-matrix.md §B) — RegimeFitProbe observe attach.

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block. RegimeFitProbe rides the
SAME observe-only G6 attach as the Slice-1 catalog (ProfitTaking/LossDefense/
Technical/SessionHours): it can never double-tighten the live exit because
observe mode threads ZERO knobs regardless of which probes ran (§ conflict
resolution ④, backgate-plan/master-sequence.md). These tests prove: the
regime_fit(family, regime) lean/ABSTAIN contract, the ProbeContext version
stamp + signal_family default (byte-identical construction for a caller that
predates both fields), and that RegimeFitProbe folds into the SAME
confidence-weighted composite as every other catalog probe.
"""

from __future__ import annotations

from polaris.core.probes import ExitEngine, ProbeContext, ProbeReading
from polaris.core.probes.catalog import RegimeFitProbe
from polaris.core.probes.roles import role_for_probe


def _ctx(**over: object) -> ProbeContext:
    base: dict[str, object] = dict(
        position_id="p1",
        venue="okx",
        symbol="BTC-USDT",
        underlying_group_id="BTC",
        side="long",
        entry_price=100.0,
        last_price=101.0,
        atr_pct=0.01,
        pnl_r=0.3,
        mfe_r=0.6,
        mae_r=-0.1,
        held_seconds=120,
        volume_now=10.0,
        volume_z=0.5,
        atr_slope=0.001,
        recent_ticks=[],
        exit_state="open",
        regime="bull_trend",
        seconds_to_close=None,
        now_ts=1000,
    )
    base.update(over)
    return ProbeContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProbeContext extension — signal_family default + version stamp
# ---------------------------------------------------------------------------


def test_probe_context_default_signal_family_and_version_stamp() -> None:
    """A caller that predates W2 (omits both kwargs) still constructs cleanly —
    the pre-W2 degenerate shape (momentum) is preserved, never a raise."""
    ctx = _ctx()
    assert ctx.signal_family == "momentum"
    assert ctx.probe_ctx_version == 2


def test_probe_context_signal_family_is_caller_overridable() -> None:
    ctx = _ctx(signal_family="reversion")
    assert ctx.signal_family == "reversion"


def test_probe_context_frozen_includes_new_fields() -> None:
    ctx = _ctx()
    try:
        ctx.signal_family = "reversion"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ProbeContext must stay frozen")


# ---------------------------------------------------------------------------
# RegimeFitProbe — lean/ABSTAIN contract
# ---------------------------------------------------------------------------


def test_regime_fit_probe_role_is_exit() -> None:
    assert role_for_probe("regime_fit") == "Exit"


def test_regime_fit_probe_momentum_trend_is_full_favorable() -> None:
    r = RegimeFitProbe().evaluate(
        _ctx(signal_family="momentum", regime="bull_trend")
    )
    assert r is not None
    assert r.lean == 1.0
    assert r.confidence == 1.0
    assert r.kind == "regime"
    assert r.probe_id == "regime_fit"


def test_regime_fit_probe_momentum_range_is_full_adverse() -> None:
    # The churn case regime_fit exists to shape (momentum in chop).
    r = RegimeFitProbe().evaluate(_ctx(signal_family="momentum", regime="chop"))
    assert r is not None
    assert r.lean == -1.0


def test_regime_fit_probe_reversion_range_is_full_favorable() -> None:
    r = RegimeFitProbe().evaluate(_ctx(signal_family="reversion", regime="chop"))
    assert r is not None
    assert r.lean == 1.0


def test_regime_fit_probe_abstains_on_unknown_regime() -> None:
    # regime_fit degrades an unrecognised regime to neutral (0.0) — the probe
    # ABSTAINs rather than inject a false-confident zero into the composite.
    assert RegimeFitProbe().evaluate(_ctx(regime=None)) is None
    assert RegimeFitProbe().evaluate(_ctx(regime="")) is None


def test_regime_fit_probe_abstains_on_unrecognised_family() -> None:
    r = RegimeFitProbe().evaluate(
        _ctx(signal_family="mystery_family", regime="bull_trend")
    )
    assert r is None


def test_regime_fit_probe_evidence_carries_inputs() -> None:
    r = RegimeFitProbe().evaluate(
        _ctx(signal_family="momentum", regime="bull_trend")
    )
    assert r is not None
    assert r.evidence["signal_family"] == "momentum"
    assert r.evidence["regime"] == "bull_trend"
    assert r.evidence["fit"] == 1.0


# ---------------------------------------------------------------------------
# Composite fold — RegimeFitProbe is just another confidence-weighted reading
# ---------------------------------------------------------------------------


def test_regime_fit_reading_folds_into_composite_like_any_probe() -> None:
    reading = RegimeFitProbe().evaluate(
        _ctx(signal_family="momentum", regime="chop")  # adverse fit
    )
    assert reading is not None
    dec = ExitEngine().compose([reading], mode="observe")
    # observe mode: still zero knobs, still byte-identical guarantee.
    assert dec.applied is False
    assert dec.trail_mult is None
    assert dec.composite_lean == reading.lean  # sole reading dominates weighted mean


def test_regime_fit_probe_never_produces_none_reading_object() -> None:
    # ProbeBus drops abstentions; a returned reading is never a None-lean.
    reading: ProbeReading | None = RegimeFitProbe().evaluate(
        _ctx(signal_family="momentum", regime="bull_trend")
    )
    assert reading is not None
    assert -1.0 <= reading.lean <= 1.0
    assert 0.0 <= reading.confidence <= 1.0
