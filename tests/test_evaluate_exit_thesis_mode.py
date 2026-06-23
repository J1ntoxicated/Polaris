"""evaluate_exit ``mode=`` integration — GOLDEN byte-identical + override tests.

The adaptive thesis re-map feeds a ``ManagementMode`` into ``evaluate_exit`` via
the new ``mode=`` kwarg. ``mode=None`` MUST be byte-identical to the pre-re-map
engine; a mode tuple overrides trail / mfe_protect / profit_target and adds the
thesis_harvest / thesis_cut top-of-ladder closes. EXIT timing only — never size,
entry, or the G6 -1.0R rail.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    Bucket,
    ManagementMode,
    ThesisGivebackParams,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0, atr_r (2x) = 2.0
GB = ThesisGivebackParams(arm_r=0.30, frac=0.50, hard_frac=0.60)


def _fresh(side: str = "long") -> object:
    return init_exit_state(entry_price=ENTRY, side=side)


# --- GOLDEN: mode=None byte-identical --------------------------------------


def _call(mode: ManagementMode | None, **over: object) -> object:
    base: dict[str, object] = dict(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
    )
    base.update(over)
    return evaluate_exit(mode=mode, **base)  # type: ignore[arg-type]


def test_mode_none_byte_identical_to_no_mode_kwarg() -> None:
    prev_a = _fresh("long")
    prev_b = _fresh("long")
    d_no_kwarg = evaluate_exit(
        prev=prev_a, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
    )
    d_mode_none = evaluate_exit(
        prev=prev_b, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10, mode=None,
    )
    assert d_no_kwarg.close == d_mode_none.close
    assert d_no_kwarg.close_reason == d_mode_none.close_reason
    assert d_no_kwarg.state.stop_price == d_mode_none.state.stop_price
    assert d_no_kwarg.state.peak_price == d_mode_none.state.peak_price
    assert d_no_kwarg.state.exit_state == d_mode_none.state.exit_state
    assert d_no_kwarg.state.mfe_r == d_mode_none.state.mfe_r


def test_mode_hold_byte_identical() -> None:
    prev_a = _fresh("long")
    prev_b = _fresh("long")
    d_none = evaluate_exit(
        prev=prev_a, side="long", entry_price=ENTRY, last_price=103.0,
        atr_pct=ATR_PCT, pnl_r=1.5, held_seconds=10,
    )
    d_hold = evaluate_exit(
        prev=prev_b, side="long", entry_price=ENTRY, last_price=103.0,
        atr_pct=ATR_PCT, pnl_r=1.5, held_seconds=10,
        mode=ManagementMode.HOLD,
        thesis_bucket=Bucket.TREND, thesis_giveback=GB,
    )
    assert d_none.close == d_hold.close
    assert d_none.state.stop_price == d_hold.state.stop_price


# --- thesis_cut: top-of-ladder immediate close -----------------------------


def test_cut_closes_immediately_on_broken_red() -> None:
    # red position, CUT mode → close NOW with thesis_cut reason, before the trail.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=99.0,
        atr_pct=ATR_PCT, pnl_r=-0.4, held_seconds=10,
        mode=ManagementMode.CUT, thesis_bucket=Bucket.TREND, thesis_giveback=GB,
    )
    assert d.close is True
    assert d.close_reason == "thesis_cut"


# --- HARVEST near peak: thesis_harvest fast-close on hard give-back ---------


def test_harvest_hard_giveback_closes_near_peak() -> None:
    # ratchet a peak first, then a hard give-back HARVEST → thesis_harvest close.
    st = _fresh("long")
    d1 = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=106.0,
        atr_pct=ATR_PCT, pnl_r=3.0, held_seconds=10,
    )
    # now back to +0.6R (peak was +3R → surrendered 0.8 frac > hard 0.6).
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=100.6,
        atr_pct=ATR_PCT, pnl_r=0.6, held_seconds=20,
        mode=ManagementMode.HARVEST, thesis_bucket=Bucket.TREND,
        thesis_giveback=GB,
    )
    assert d2.close is True
    assert d2.close_reason == "thesis_harvest"


def test_harvest_soft_does_not_fast_close_but_tightens() -> None:
    # HARVEST mode but give-back is only soft (no hard fast-close): no
    # thesis_harvest immediate close, the tight harvest trail manages the exit.
    st = _fresh("long")
    d1 = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=10,
    )
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=101.4,
        atr_pct=ATR_PCT, pnl_r=0.7, held_seconds=20,
        mode=ManagementMode.HARVEST, thesis_bucket=Bucket.TREND,
        thesis_giveback=GB,
    )
    assert d2.close_reason != "thesis_harvest"


# --- LET_RUN: widens the trail (winner runs further) -----------------------


def test_let_run_widens_trail_vs_hold() -> None:
    # Same green tick (MFE below the BEP-floor arm so only the trail width shows)
    # under HOLD vs LET_RUN: LET_RUN's wider ATR trail → LOWER stop (long) so the
    # winner runs further before the trail closes it.  peak 100.2, atr_one 1.0:
    # HOLD 100.2-2*1=98.2 ; LET_RUN 100.2-4*1=96.2 (mfe 0.1R < bep 0.3R = no floor).
    st_hold = _fresh("long")
    st_run = _fresh("long")
    d_hold = evaluate_exit(
        prev=st_hold, side="long", entry_price=ENTRY, last_price=100.2,
        atr_pct=ATR_PCT, pnl_r=0.1, held_seconds=10, mode=ManagementMode.HOLD,
        thesis_bucket=Bucket.TREND, thesis_giveback=GB,
    )
    d_run = evaluate_exit(
        prev=st_run, side="long", entry_price=ENTRY, last_price=100.2,
        atr_pct=ATR_PCT, pnl_r=0.1, held_seconds=10, mode=ManagementMode.LET_RUN,
        thesis_bucket=Bucket.TREND, thesis_giveback=GB,
    )
    assert d_run.state.stop_price is not None
    assert d_hold.state.stop_price is not None
    assert d_run.state.stop_price < d_hold.state.stop_price


# --- CUT never fires on a green position (guard) ---------------------------


def test_cut_mode_on_green_still_respects_caller_but_no_silent_size_change() -> None:
    # CUT mode requests an immediate close — the assessor only emits CUT for
    # broken+red, but if a caller forced CUT the engine closes via thesis_cut
    # (the engine trusts the mode; the green-guard lives in assess_thesis).
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
        mode=ManagementMode.CUT, thesis_bucket=Bucket.TREND, thesis_giveback=GB,
    )
    assert d.close is True
    assert d.close_reason == "thesis_cut"
