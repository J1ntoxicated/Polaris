"""#37 micro_reversion 0-second-hold fix — flow_reversal needs a genuine break
or a meaningful (spread+fee-clearing) capture before it exits.

DEMO/PAPER · aggressive · flow_not_block (the fix DEFERS a marginal-flow exit to a
meaningful capture — it never blocks an entry, never cuts size, never widens the
loss rail) · ASYMMETRY preserved (the loss side — G6 -1.0R rail + the reversion
``scalp_stop`` -0.4R micro-stop — is UNTOUCHED; only the marginal-flow EXIT timing
changes) · 9-stack untouched (no sizing multiplier added — exit timing only).

MEASURED (#37): micro_reversion closes at 0.0s holds — every close a -0.001R micro
loss eaten by spread+fee (31% of OKX closes <= 2s). The ``flow_reversal`` scalp
branch fired on the FIRST tick whose book imbalance ``ofi`` marginally opposed the
side (zero deadband ``ofi < -0.0``). But a freshly-faded overshoot's book is
NORMALLY still marginally opposed at entry — so the exit fired at 0s, capturing
nothing but the round-trip cost. A 1-tick spike -> 1-tick revert -> 0s close on a
move smaller than the spread is a GUARANTEED loss.

FIX (flow_not_block, exempt genuine break):
  - A MARGINAL opposing ``ofi`` (|ofi| below ``_SCALP_FLOW_REVERSAL_OFI``) with the
    capture still inside the spread+fee cost band -> HOLD (no 0s exit). The position
    rides to ``scalp_target`` (meaningful capture) or ``scalp_stop`` (the unchanged
    -0.4R rail) instead.
  - A GENUINE break — ``|ofi|`` FIRMLY reversed past ``_SCALP_FLOW_REVERSAL_OFI`` —
    exits IMMEDIATELY (misclassification CORRECTION, NOT deferring a real break).
  - Once a MEANINGFUL capture (``pnl_r >= min_capture_r``, the spread+fee floor in R)
    has been banked, a marginal flow turn may bank it (the capture cleared cost).
  - The loss side (``scalp_stop`` -0.4R) is byte-identical regardless.
"""

from __future__ import annotations

import math

import pytest

from polaris.core.economics.fees import (
    CAPITAL_REAL_BPS,
    OKX_REAL_TAKER_BPS,
    real_fee_bps,
)
from polaris.scripts._production_tick_mfe import (
    _SCALP_FLOW_REVERSAL_OFI,
    _SCALP_STOP_R,
    _scalp_exit_decision,
    _scalp_min_capture_r,
)

ENTRY = 100.0


# === the 0-second bug: a marginal opposing ofi must NOT exit at 0s ============


def test_marginal_opposing_ofi_holds_not_zero_second_exit_long() -> None:
    # THE #37 BUG: a long fade, flow only MARGINALLY opposing (ofi just past zero,
    # below the genuine-break floor) and pnl ~0 (no capture yet) -> must HOLD, not
    # bank a 0-second -0.001R micro loss. A meaningful-capture floor is supplied.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY, ofi=-0.05, pnl_r=0.0,
            strategy_id="micro_reversion", min_capture_r=0.08,
        )
        is None
    )


def test_marginal_opposing_ofi_holds_not_zero_second_exit_short() -> None:
    # Symmetric short fade: a marginally bid-positive book (ofi just past zero) is
    # the baseline post-fade state, not a genuine reversal -> HOLD.
    assert (
        _scalp_exit_decision(
            side="short", entry_price=ENTRY, last_mid=ENTRY, ofi=0.05, pnl_r=0.0,
            strategy_id="micro_reversion", min_capture_r=0.08,
        )
        is None
    )


# === genuine break is EXEMPT — exits immediately (misclassification fix) =======


def test_firm_ofi_reversal_exits_immediately_long() -> None:
    # A GENUINE break: ofi FIRMLY reversed (|ofi| past the floor) = the snap-back
    # truly failed -> exit NOW even at pnl ~0. This is the misclassification
    # CORRECTION, not deferring a real break (the genuine-break exemption).
    firm = -(_SCALP_FLOW_REVERSAL_OFI + 0.05)
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY, ofi=firm, pnl_r=0.0,
            strategy_id="micro_reversion", min_capture_r=0.08,
        )
        == "flow_reversal"
    )


def test_firm_ofi_reversal_exits_immediately_short() -> None:
    firm = _SCALP_FLOW_REVERSAL_OFI + 0.05
    assert (
        _scalp_exit_decision(
            side="short", entry_price=ENTRY, last_mid=ENTRY, ofi=firm, pnl_r=0.0,
            strategy_id="micro_reversion", min_capture_r=0.08,
        )
        == "flow_reversal"
    )


# === once a meaningful capture clears spread+fee, a marginal turn may bank =====


def test_marginal_ofi_exits_once_capture_clears_cost_floor() -> None:
    # The position has captured PAST the spread+fee floor (pnl_r >= min_capture_r);
    # now a marginal flow turn may bank it — the capture is meaningful, not a 0s
    # micro loss. (Below the per-strategy scalp_target so this is the flow path.)
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY + 0.2, ofi=-0.05,
            pnl_r=0.12, strategy_id="micro_reversion", min_capture_r=0.08,
        )
        == "flow_reversal"
    )


def test_marginal_ofi_still_holds_below_cost_floor() -> None:
    # A capture that has NOT yet cleared the spread+fee floor (pnl_r < min_capture_r)
    # with only a marginal opposing flow -> HOLD (do not bank a sub-cost crumb).
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY + 0.05, ofi=-0.05,
            pnl_r=0.04, strategy_id="micro_reversion", min_capture_r=0.08,
        )
        is None
    )


# === ASYMMETRY: the loss rail is UNTOUCHED by the fix =========================


def test_scalp_stop_loss_rail_unchanged_with_capture_floor() -> None:
    # The -0.4R micro-stop fires regardless of flow / capture floor — the loss side
    # is byte-identical (no defensive throttle, no widened rail).
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY - 1.0,
            ofi=-0.05, pnl_r=_SCALP_STOP_R - 0.1,
            strategy_id="micro_reversion", min_capture_r=0.08,
        )
        == "scalp_stop"
    )


def test_marginal_adverse_drift_rides_to_stop_not_zero_second_close() -> None:
    # A marginal opposing ofi with a small NEGATIVE pnl (above the stop) must HOLD
    # (ride to the unchanged scalp_stop) — never a flow_reversal micro-loss close.
    # min_capture_r is positive so a negative pnl can never clear it.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY - 0.1, ofi=-0.05,
            pnl_r=-0.10, strategy_id="micro_reversion", min_capture_r=0.08,
        )
        is None
    )


# === byte-identical: legacy callers (no min_capture_r) only see firm break =====


def test_legacy_no_capture_floor_only_firm_break_exits() -> None:
    # min_capture_r omitted (legacy / momentum) -> the capture path is DISABLED;
    # only a genuine firm-break ofi exits via flow_reversal. A marginal opposing
    # ofi holds (the bug fix is unconditional); a firm one still exits.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY, ofi=-0.05, pnl_r=0.0,
        )
        is None
    )
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY,
            ofi=-(_SCALP_FLOW_REVERSAL_OFI + 0.1), pnl_r=0.0,
        )
        == "flow_reversal"
    )


def test_existing_firm_reversal_contract_preserved() -> None:
    # The pre-fix integration contract (ofi=-0.5 / +0.5 = firmly opposed) still
    # fires flow_reversal — |0.5| >= the genuine-break floor.
    assert _SCALP_FLOW_REVERSAL_OFI <= 0.5
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY, ofi=-0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )
    assert (
        _scalp_exit_decision(
            side="short", entry_price=ENTRY, last_mid=ENTRY, ofi=0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )


# === the spread+fee -> R cost floor helper ====================================


def test_min_capture_r_clears_spread_plus_roundtrip_real_fee() -> None:
    # The capture floor = (spread_bps + 2*real_fee_bps(venue)) / 1e4 / (atr_pct *
    # stop_atr_mult). OKX real taker 10bps/leg -> 20bps round-trip; with a 4bps
    # spread -> 24bps cost over an atr_pct=0.01 / 2x ruler.
    spread_bps = 4.0
    atr_pct = 0.01
    got = _scalp_min_capture_r(
        venue="okx", spread_bps=spread_bps, entry_atr_pct=atr_pct,
    )
    assert got is not None
    expect = (spread_bps + 2.0 * OKX_REAL_TAKER_BPS) / 1e4 / (atr_pct * 2.0)
    assert math.isclose(got, expect, rel_tol=1e-9)
    assert got > 0.0


def test_min_capture_r_uses_real_not_punitive_demo_fee() -> None:
    # The floor uses the REAL fee schedule (the documented go-live edge basis), NOT
    # the 70bps OKX demo billing artifact — otherwise the floor would be ~0.7%+ and
    # effectively block every reversion exit (a defensive throttle). real OKX = 10.
    assert real_fee_bps("okx") == OKX_REAL_TAKER_BPS
    assert real_fee_bps("capital") == CAPITAL_REAL_BPS
    # Capital's tiny 3bps proxy -> a much smaller floor than OKX for the same spread.
    okx = _scalp_min_capture_r(venue="okx", spread_bps=2.0, entry_atr_pct=0.01)
    cap = _scalp_min_capture_r(venue="capital", spread_bps=2.0, entry_atr_pct=0.01)
    assert okx is not None and cap is not None
    assert cap < okx


def test_min_capture_r_degrades_to_none_on_missing_atr_anchor() -> None:
    # A missing / non-positive entry-ATR anchor -> None (capture path disabled,
    # firm-break-only) — legacy-graceful, never a divide-by-zero.
    assert _scalp_min_capture_r(venue="okx", spread_bps=2.0, entry_atr_pct=None) is None
    assert _scalp_min_capture_r(venue="okx", spread_bps=2.0, entry_atr_pct=0.0) is None


def test_scalp_target_still_priority_over_flow_branch() -> None:
    # Reaching the per-strategy take-profit still banks first — the capture floor /
    # firm-break gate never pre-empts a genuine target hit.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=ENTRY, last_mid=ENTRY + 1.0, ofi=-0.05,
            pnl_r=1.0, strategy_id="micro_reversion", min_capture_r=0.08,
        )
        == "scalp_target"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
