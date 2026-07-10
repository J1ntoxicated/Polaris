"""Tests for friction_floor (fee-split v0 — best_case_friction structural
floor). DEMO/PAPER only. Spec:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 1.
"""
from __future__ import annotations

import pytest

from polaris.core.classes.friction_floor import (
    FrictionSample,
    best_case_friction,
    effective_friction_floor,
    realized_friction_q20,
)

_H_1H = 3600.0


# ---------------------------------------------------------------------------
# best_case_friction
# ---------------------------------------------------------------------------


def test_maker_capable_uses_maker_floor_plus_slip():
    result = best_case_friction(
        maker_capable=True, maker_floor_bps=8.0, taker_floor_bps=10.0,
        slip_floor_bps=2.0, spread_p05_bps=5.0,
    )
    assert result == pytest.approx(2 * 8.0 + 2.0)  # 18.0


def test_taker_only_uses_taker_floor_plus_spread_p05():
    result = best_case_friction(
        maker_capable=False, maker_floor_bps=8.0, taker_floor_bps=10.0,
        slip_floor_bps=2.0, spread_p05_bps=5.0,
    )
    assert result == pytest.approx(2 * 10.0 + 5.0)  # 25.0


def test_maker_capable_never_reads_taker_or_spread_inputs():
    """maker-capable path must be blind to taker_floor/spread_p05 (a caller
    that only has taker/spread data for a non-maker venue must not silently
    leak into the maker branch)."""
    maker = best_case_friction(
        maker_capable=True, maker_floor_bps=8.0, taker_floor_bps=999.0,
        slip_floor_bps=2.0, spread_p05_bps=999.0,
    )
    assert maker == pytest.approx(18.0)


def test_okx_real_schedule_maker_vs_taker_ordering():
    """OKX real schedule (economics.fees): maker 8bps < taker 10bps -> the
    maker-capable floor must be strictly cheaper than taker-only, all else
    equal (a maker-capable venue floor should never exceed taker-only)."""
    common = {"maker_floor_bps": 8.0, "taker_floor_bps": 10.0,
              "slip_floor_bps": 3.0, "spread_p05_bps": 3.0}
    maker = best_case_friction(maker_capable=True, **common)
    taker = best_case_friction(maker_capable=False, **common)
    assert maker < taker


# ---------------------------------------------------------------------------
# realized_friction_q20
# ---------------------------------------------------------------------------


def test_realized_q20_empty_is_none():
    assert realized_friction_q20([], now_ts=100_000, characteristic_time_seconds=_H_1H) is None


def test_realized_q20_uniform_cost_returns_that_cost():
    samples = [FrictionSample(cost_bps=15.0, ts=100_000 - i) for i in range(10)]
    result = realized_friction_q20(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result == pytest.approx(15.0)


def test_realized_q20_is_low_percentile_of_spread():
    """q20 must sit toward the LOW end of a spread of realized costs (near
    the cheapest observed fills, not the median/mean)."""
    samples = [FrictionSample(cost_bps=float(c), ts=100_000) for c in range(1, 101)]
    result = realized_friction_q20(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result is not None
    assert result < 30.0  # well below the median (50.5)


# ---------------------------------------------------------------------------
# effective_friction_floor — min() composition, realized can only LOWER
# ---------------------------------------------------------------------------


def test_effective_floor_no_realized_evidence_uses_structural():
    assert effective_friction_floor(structural_floor_bps=20.0, realized_q20_bps=None) == 20.0


def test_effective_floor_realized_lower_wins():
    assert effective_friction_floor(structural_floor_bps=20.0, realized_q20_bps=12.0) == 12.0


def test_effective_floor_realized_higher_never_raises_structural():
    """Realized evidence can ONLY lower the floor — a higher realized q20
    must NOT push the effective floor above the structural best case."""
    assert effective_friction_floor(structural_floor_bps=20.0, realized_q20_bps=35.0) == 20.0
