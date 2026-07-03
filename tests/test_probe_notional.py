"""pts-classes (group D) — probe_notional pure formula + admission gate.

Spec source: MEMORY.md group-D task header (U2 해석):
``probe_notional = max(K x round_trip_fee_usd_fixed, venue_min_notional)``.

U2 resolution (SSOT — supersedes any conflicting spec prose): FEE_FLOOR_K (3.0,
exit_strategy_config.py) and BASE_RISK_PCT (0.02, risk_unit.py) are unrelated
constants — the K x fee term is a no-op for every CURRENTLY-REGISTERED venue
(OKX/Capital/Alpaca all charge a %-of-notional fee, never a flat per-trade
fee), so ``venue_min_notional`` is what actually binds. Only a hypothetical
flat-fee venue would make the K x fee term bind. Tests assert this
degenerate-to-venue-min behaviour explicitly, plus the admission gate that
routes an under-fee-floor PROVE signal to shadow.

DEMO/PAPER only. Aggressive bias preserved (flow_not_block, no defensive
throttle) -- probe_notional is a capital-ROUTING constant for the PROVE class,
never a block/reject filter (BENCH/shadow-routed signals still compute +
learn). 9-stack ban: this module introduces ZERO new T4 multiplier slots --
probe_notional is a fixed-USD floor consumed OUTSIDE the T4 chain (T4 chain
byte-identical for EARN).
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.cell_matrix.schema import ROUTING_BOTTOM_MULT, ROUTING_TOP_MULT
from polaris.core.sizing.probe_notional import (
    DEFAULT_VENUE_MIN_NOTIONAL_USD,
    FEE_FLOOR_K,
    bottom_cell_shadow_hit,
    prove_admission_ok,
    prove_stop_dist_floor_pct,
    resolve_strategy_class,
    venue_min_notional_usd,
)
from polaris.core.sizing.probe_notional import probe_notional_usd as probe_notional

# ---------------------------------------------------------------------------
# probe_notional_usd — pure formula
# ---------------------------------------------------------------------------


def test_probe_notional_degenerates_to_venue_min_for_pct_fee_venue() -> None:
    """OKX/Capital/Alpaca all charge %-of-notional fees -> round_trip_fee_usd_fixed
    defaults to 0.0 -> the K-term is 0 and venue_min_notional always wins (U2)."""
    assert probe_notional("okx") == pytest.approx(venue_min_notional_usd("okx"))
    assert probe_notional("capital") == pytest.approx(venue_min_notional_usd("capital"))
    assert probe_notional("alpaca") == pytest.approx(venue_min_notional_usd("alpaca"))


def test_probe_notional_flat_fee_venue_k_term_can_bind() -> None:
    """A hypothetical flat-fee venue (fixed USD/trade, not %) makes the K x fee
    term bind once it exceeds venue_min_notional -- the ONLY scenario U2 says
    the K-term is live."""
    tiny_min = 1.0
    big_flat_fee = 100.0  # K=3.0 x 100 = 300 > tiny_min
    out = probe_notional(
        "okx", round_trip_fee_usd_fixed=big_flat_fee, venue_min_notional_override=tiny_min
    )
    assert out == pytest.approx(FEE_FLOOR_K * big_flat_fee)
    assert out > tiny_min


def test_probe_notional_never_negative_or_nan() -> None:
    assert probe_notional("okx", round_trip_fee_usd_fixed=-5.0) >= 0.0
    assert math.isfinite(probe_notional("unknown-venue"))


@given(
    venue=st.sampled_from(["okx", "capital", "alpaca", "weird"]),
    flat_fee=st.floats(min_value=-10.0, max_value=1000.0, allow_nan=False),
)
def test_probe_notional_always_finite_and_nonnegative(venue: str, flat_fee: float) -> None:
    out = probe_notional(venue, round_trip_fee_usd_fixed=flat_fee)
    assert math.isfinite(out)
    assert out >= 0.0


# ---------------------------------------------------------------------------
# venue_min_notional_usd
# ---------------------------------------------------------------------------


def test_venue_min_notional_reuses_entry_notional_floor_ssot() -> None:
    """No competing constant -- reuses the existing static per-trade dollar
    floor (schema.ENTRY_NOTIONAL_FLOOR_USD) as the generic venue-min default
    (anti-pattern check: no second '$10 floor' definition)."""
    assert venue_min_notional_usd("okx") == pytest.approx(DEFAULT_VENUE_MIN_NOTIONAL_USD)
    assert venue_min_notional_usd("capital") == pytest.approx(DEFAULT_VENUE_MIN_NOTIONAL_USD)
    assert venue_min_notional_usd("totally-unknown") == pytest.approx(
        DEFAULT_VENUE_MIN_NOTIONAL_USD
    )


# ---------------------------------------------------------------------------
# PROVE admission gate: stop_dist_pct > 3 x round-trip fee_rate
# ---------------------------------------------------------------------------


def test_prove_admission_passes_when_stop_dist_clears_fee_floor() -> None:
    # OKX round-trip fee_rate (real taker x2) = 2 x 10bps = 20bps = 0.0020.
    # 3x floor = 0.0060. A stop_dist_pct comfortably above it admits.
    assert prove_admission_ok(venue="okx", stop_dist_pct=0.05) is True


def test_prove_admission_fails_when_stop_dist_below_fee_floor() -> None:
    # 3x OKX real round-trip = 0.0060; 0.001 is far below -> shadow-route.
    assert prove_admission_ok(venue="okx", stop_dist_pct=0.001) is False


def test_prove_admission_boundary_is_strict_greater_than() -> None:
    floor = prove_stop_dist_floor_pct("okx")
    assert prove_admission_ok(venue="okx", stop_dist_pct=floor) is False
    assert prove_admission_ok(venue="okx", stop_dist_pct=floor * 1.0001) is True


def test_prove_admission_non_finite_or_nonpositive_never_admits() -> None:
    assert prove_admission_ok(venue="okx", stop_dist_pct=0.0) is False
    assert prove_admission_ok(venue="okx", stop_dist_pct=-0.01) is False
    assert prove_admission_ok(venue="okx", stop_dist_pct=float("nan")) is False


# ---------------------------------------------------------------------------
# bottom_cell_shadow_hit — "cell_mult=0 cell" eligibility (P0 floor is 0.5,
# never literally 0 — apply_cell_routing_mult's own defensive comment: "a cell
# mult of 0 would silently kill the trade" — so ELIGIBILITY here means the
# bottom-quartile-suppression mult, the actual 0-analog in this P0 scheme).
# ---------------------------------------------------------------------------


def test_bottom_cell_shadow_hit_true_at_bottom_mult() -> None:
    assert bottom_cell_shadow_hit(ROUTING_BOTTOM_MULT) is True


def test_bottom_cell_shadow_hit_false_at_mid_or_top() -> None:
    assert bottom_cell_shadow_hit(1.0) is False
    assert bottom_cell_shadow_hit(ROUTING_TOP_MULT) is False


def test_bottom_cell_shadow_hit_true_below_bottom_mult() -> None:
    # A hypothetical future scheme with mult < 0.5 (incl. exactly 0) must still
    # shadow-route -- never treated as "not bottom" just because it undercuts
    # the current P0 floor.
    assert bottom_cell_shadow_hit(0.0) is True
    assert bottom_cell_shadow_hit(0.3) is True


# NOTE: the sibling PROVE shadow-routing trigger (learner-table anti-edge
# read, p_pos<=0.20 & n_samples>=20) lives in
# ``polaris.core.cell_matrix.fetch_learner_anti_edge`` — tested in
# tests/test_cell_routing_edge_p0.py, NOT here. Sizing must never read the
# Bayesian-learner edge table directly (SSOT guard,
# test_edge_validation.test_sizing_does_not_import_posterior forbids even a
# comment mentioning that table name inside polaris/core/sizing/*.py).


# ---------------------------------------------------------------------------
# resolve_strategy_class — shared (venue, strategy_id) -> class reader for the
# 3 class-aware compute_size call sites.
# ---------------------------------------------------------------------------


def test_resolve_strategy_class_reads_persisted_row(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class) "
        "VALUES ('okx', 'volume_burst', 'PROVE')"
    )
    assert resolve_strategy_class(memdb, venue="okx", strategy_id="volume_burst") == "PROVE"


def test_resolve_strategy_class_defaults_to_earn_when_no_row(memdb: sqlite3.Connection) -> None:
    """A strategy with no strategy_class row yet (bootstrap hasn't run / brand
    new strategy) defaults to EARN -- byte-identical pre-pts-classes sizing,
    never a silent BENCH/shadow lockout on a data gap."""
    assert resolve_strategy_class(memdb, venue="okx", strategy_id="never-seen") == "EARN"


def test_resolve_strategy_class_db_error_defaults_to_earn(memdb: sqlite3.Connection) -> None:
    memdb.execute("DROP TABLE strategy_class")
    assert resolve_strategy_class(memdb, venue="okx", strategy_id="volume_burst") == "EARN"


def test_resolve_strategy_class_none_conn_defaults_to_earn() -> None:
    """``conn=None`` is a real caller shape (test-only compute_size stand-ins
    that never touch the DB, e.g. test_tick_bar_parity_p1_11.py) -- fail-open,
    never a crash."""
    assert resolve_strategy_class(None, venue="okx", strategy_id="volume_burst") == "EARN"
