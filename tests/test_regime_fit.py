"""Regime-fit — bidirectional conviction shaping (NOT a tradeable yes/no gate).

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack collapse permanently blocked.

regime_fit(family, regime) ∈ [-1, +1] is a deterministic (signal_family × regime)
lookup. ``regime_scalar`` folds the fit into the ONE T4 continuous scalar
(0.75–1.5×) — good fit boosts (≤1.5×), bad fit shrinks to the 0.75 FLOOR (never
0, anti-collapse). It is bidirectional; it is never a veto / skip / block.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from polaris.core.regime_fit import (
    CONT_SCALAR_MAX as RF_MAX,
)
from polaris.core.regime_fit import (
    CONT_SCALAR_MIN as RF_MIN,
)
from polaris.core.regime_fit import (
    confirm_tightness,
    exit_tightness,
    regime_fit,
    regime_scalar,
)
from polaris.core.sizing.schema import CONT_SCALAR_MAX, CONT_SCALAR_MIN


def test_band_constants_match_sizing_ssot() -> None:
    # The duplicated band (kept to break the sizing import cycle) MUST track the
    # schema SSOT — a guard against silent drift.
    assert (RF_MIN, RF_MAX) == (CONT_SCALAR_MIN, CONT_SCALAR_MAX)


def test_lookup_table_values() -> None:
    # momentum: trend-family regimes are good, chop is the churn case (bad).
    assert regime_fit("momentum", "bull_trend") == 1.0
    assert regime_fit("momentum", "bear_trend") == 1.0
    assert regime_fit("momentum", "chop") == -1.0
    assert regime_fit("momentum", "crisis") == -0.5
    # reversion: chop is good (mean-reversion edge); trend is bad.
    assert regime_fit("reversion", "chop") == 1.0
    assert regime_fit("reversion", "bull_trend") == -1.0
    assert regime_fit("reversion", "bear_trend") == -1.0
    assert regime_fit("reversion", "crisis") == 0.5
    # unknown / None regime → neutral 0 (no shaping, full flow).
    assert regime_fit("momentum", "unknown") == 0.0
    assert regime_fit("momentum", None) == 0.0
    assert regime_fit("reversion", "") == 0.0
    # generic bucket aliases normalise the same as canonical labels.
    assert regime_fit("momentum", "trend") == 1.0
    assert regime_fit("reversion", "range") == 1.0


def test_unknown_family_is_neutral() -> None:
    # An unrecognised family never shapes (degrade-never-block).
    assert regime_fit("breakout", "bull_trend") == 0.0
    assert regime_fit("", "chop") == 0.0


def test_regime_scalar_bidirectional_and_clamped() -> None:
    # +1 → 1.5× (boost), -1 → 0.75× (shrink to FLOOR, not 0), 0 → 1.0× (neutral).
    assert regime_scalar(1.0) == CONT_SCALAR_MAX
    assert regime_scalar(-1.0) == CONT_SCALAR_MIN
    assert regime_scalar(0.0) == 1.0
    # -0.5 → 0.75 (1 + -0.5*0.5 = 0.75 == floor); +0.5 → 1.25.
    assert regime_scalar(-0.5) == 0.75
    assert regime_scalar(0.5) == 1.25


@given(st.floats(min_value=-1.0, max_value=1.0))
def test_regime_scalar_never_zero_within_band(fit: float) -> None:
    s = regime_scalar(fit)
    assert CONT_SCALAR_MIN <= s <= CONT_SCALAR_MAX
    assert s > 0.0  # anti-collapse: never zeroes the scalar.


@given(
    st.floats(min_value=-1.0, max_value=1.0),
    st.floats(min_value=-1.0, max_value=1.0),
)
def test_regime_scalar_monotone(a: float, b: float) -> None:
    # Better fit never yields a smaller scalar (bidirectional, monotone up).
    if a <= b:
        assert regime_scalar(a) <= regime_scalar(b) + 1e-12


def test_confirm_tightness_bidirectional() -> None:
    # Bad fit RAISES the confirmation bar (>1 = stricter follow-through = DELAY);
    # good fit RELAXES it (<1); neutral leaves it unchanged.
    assert confirm_tightness(-1.0) > 1.0
    assert confirm_tightness(0.0) == 1.0
    assert confirm_tightness(1.0) < 1.0


@given(
    st.floats(min_value=-1.0, max_value=1.0),
    st.floats(min_value=-1.0, max_value=1.0),
)
def test_confirm_tightness_monotone_down(a: float, b: float) -> None:
    # Better fit → looser (smaller) confirm requirement.
    if a <= b:
        assert confirm_tightness(a) >= confirm_tightness(b) - 1e-12
    assert confirm_tightness(a) > 0.0


def test_exit_tightness_bidirectional() -> None:
    # Bad fit TIGHTENS the exit (>1 = bank sooner); good fit LOOSENS (<1, LET_RUN).
    assert exit_tightness(-1.0) > 1.0
    assert exit_tightness(0.0) == 1.0
    assert exit_tightness(1.0) < 1.0


@given(st.floats(min_value=-1.0, max_value=1.0))
def test_exit_tightness_positive(fit: float) -> None:
    assert exit_tightness(fit) > 0.0
