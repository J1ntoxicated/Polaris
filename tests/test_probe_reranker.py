"""TDD — pts-classes (group F) probe slot reranker.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``rank_candidates``/``assign_slots`` are pure capital-ROUTING decision
functions (same shape as ``transition.evaluate_transition`` /
``r_pool.allocate_r_pool``) — no DB I/O, no sizing/9-stack/-1.0R rail touch. A
PROVE candidate that misses a slot keeps signaling/learning/shadow-pricing
(aggressive_always_profit / no_block_filter_architecture) — losing a slot is a
capital-routing outcome, never a block.

Spec source: prove_then_scale_classes_2026-07-03.md 빌드 스펙 ⑥ + 유효반박 #2 —
07:30 daily rerank, pessimistic-shadow score_F desc (reentry-ladder qualifiers
only), tie-break fill_rate -> expected fee -> wait age, concurrent probe cap
3/2/1 per track (A/B/C), 24h probe fee cap 6/4/2 x F_track_cap (07:30-frozen).
"""

from __future__ import annotations

from polaris.core.classes.probe_reranker import (
    PROBE_FEE_CAP_MULT,
    ProbeCandidate,
    assign_slots,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# rank_candidates — pessimistic-shadow score_F desc, tie-break chain
# ---------------------------------------------------------------------------


def _cand(**kw: object) -> ProbeCandidate:
    defaults: dict[str, object] = dict(
        venue="okx",
        strategy_id="s1",
        track="A",
        shadow_score_mean=0.0,
        fill_rate=0.5,
        expected_fee_usd=1.0,
        wait_age_sec=0,
        probe_fee_24h_usd=0.0,
        f_track_cap_usd=10.0,
    )
    defaults.update(kw)
    return ProbeCandidate(**defaults)  # type: ignore[arg-type]


def test_rank_sorts_by_shadow_score_desc() -> None:
    low = _cand(strategy_id="low", shadow_score_mean=1.0)
    high = _cand(strategy_id="high", shadow_score_mean=5.0)
    mid = _cand(strategy_id="mid", shadow_score_mean=3.0)
    ranked = rank_candidates([low, high, mid])
    assert [c.strategy_id for c in ranked] == ["high", "mid", "low"]


def test_tie_break_fill_rate_desc_when_score_equal() -> None:
    a = _cand(strategy_id="a", shadow_score_mean=2.0, fill_rate=0.10)
    b = _cand(strategy_id="b", shadow_score_mean=2.0, fill_rate=0.90)
    ranked = rank_candidates([a, b])
    assert [c.strategy_id for c in ranked] == ["b", "a"]


def test_tie_break_expected_fee_asc_when_score_and_fill_rate_equal() -> None:
    cheap = _cand(strategy_id="cheap", shadow_score_mean=2.0, fill_rate=0.5, expected_fee_usd=0.5)
    pricey = _cand(strategy_id="pricey", shadow_score_mean=2.0, fill_rate=0.5, expected_fee_usd=5.0)
    ranked = rank_candidates([pricey, cheap])
    assert [c.strategy_id for c in ranked] == ["cheap", "pricey"]


def test_tie_break_wait_age_desc_as_final_tiebreak() -> None:
    fresh = _cand(strategy_id="fresh", shadow_score_mean=2.0, fill_rate=0.5, expected_fee_usd=1.0, wait_age_sec=10)
    stale = _cand(strategy_id="stale", shadow_score_mean=2.0, fill_rate=0.5, expected_fee_usd=1.0, wait_age_sec=99_999)
    ranked = rank_candidates([fresh, stale])
    assert [c.strategy_id for c in ranked] == ["stale", "fresh"]


def test_rank_is_stable_across_disjoint_tracks() -> None:
    """Ranking is a pure sort over whatever list it is given — track scoping
    (per-track slot cap enforcement) is assign_slots's job, not rank's."""
    a1 = _cand(strategy_id="a1", track="A", shadow_score_mean=9.0)
    b1 = _cand(strategy_id="b1", track="B", shadow_score_mean=1.0)
    ranked = rank_candidates([b1, a1])
    assert [c.strategy_id for c in ranked] == ["a1", "b1"]


# ---------------------------------------------------------------------------
# assign_slots — concurrent-probe cap (3/2/1) + 24h fee cap (6/4/2 x F_track_cap)
# ---------------------------------------------------------------------------


def test_concurrent_cap_track_a_admits_top_3_only() -> None:
    ranked = [
        _cand(strategy_id=f"s{i}", track="A", shadow_score_mean=float(10 - i))
        for i in range(5)
    ]
    result = assign_slots(ranked, track="A")
    active = [a.strategy_id for a in result.assignments if a.slot_active]
    assert active == ["s0", "s1", "s2"]
    inactive = [a.strategy_id for a in result.assignments if not a.slot_active]
    assert inactive == ["s3", "s4"]


def test_concurrent_cap_track_b_admits_top_2() -> None:
    ranked = [
        _cand(strategy_id=f"s{i}", track="B", shadow_score_mean=float(10 - i))
        for i in range(3)
    ]
    result = assign_slots(ranked, track="B")
    active = [a.strategy_id for a in result.assignments if a.slot_active]
    assert active == ["s0", "s1"]


def test_concurrent_cap_track_c_admits_top_1() -> None:
    ranked = [
        _cand(strategy_id=f"s{i}", track="C", shadow_score_mean=float(10 - i))
        for i in range(2)
    ]
    result = assign_slots(ranked, track="C")
    active = [a.strategy_id for a in result.assignments if a.slot_active]
    assert active == ["s0"]


def test_rank_field_is_1_indexed_and_monotonic() -> None:
    ranked = [
        _cand(strategy_id=f"s{i}", track="A", shadow_score_mean=float(10 - i))
        for i in range(4)
    ]
    result = assign_slots(ranked, track="A")
    assert [a.rank for a in result.assignments] == [1, 2, 3, 4]


def test_24h_fee_cap_exhausted_blocks_slot_even_within_concurrency_cap() -> None:
    """6x F_track_cap for track A — a candidate whose OWN probe_fee_24h_usd
    already exceeds the remaining track-wide fee budget does not get a slot,
    even if it ranked within the top-3 concurrency cap."""
    f_track_cap = 10.0
    budget = PROBE_FEE_CAP_MULT["A"] * f_track_cap  # 6 x 10 = 60
    s0 = _cand(strategy_id="s0", track="A", shadow_score_mean=9.0, f_track_cap_usd=f_track_cap, probe_fee_24h_usd=budget - 5.0)
    s1 = _cand(strategy_id="s1", track="A", shadow_score_mean=8.0, f_track_cap_usd=f_track_cap, probe_fee_24h_usd=10.0)
    result = assign_slots([s0, s1], track="A")
    active = {a.strategy_id: a.slot_active for a in result.assignments}
    assert active["s0"] is True
    # s0 alone already consumes budget-5 of the 60 budget; s1's own 24h spend
    # (10) would push cumulative track spend past the 60 cap (55+10=65>60).
    assert active["s1"] is False


def test_24h_fee_cap_budget_uses_mean_f_track_cap_not_top_rank_only() -> None:
    """F_track_cap is a per-(venue,strategy_id) value, not per-A/B/C-track —
    the shared track-wide 24h budget must be a rank-order-independent
    aggregate (mean), not silently keyed off whichever candidate ranked
    first."""
    # Track C cap mult = 2. s0 (rank1) owns f_track_cap=100 but s1 (rank2)
    # owns f_track_cap=0 -> mean=50 -> budget=100. Using s0's OWN value alone
    # would have given budget=200 and let s1 slip in; the mean must clamp it.
    s0 = _cand(strategy_id="s0", track="C", shadow_score_mean=9.0, f_track_cap_usd=100.0, probe_fee_24h_usd=90.0)
    s1 = _cand(strategy_id="s1", track="C", shadow_score_mean=8.0, f_track_cap_usd=0.0, probe_fee_24h_usd=50.0)
    result = assign_slots([s0, s1], track="C")
    active = {a.strategy_id: a.slot_active for a in result.assignments}
    assert active["s0"] is True  # 90 <= 100 budget
    # Track C concurrency cap = 1 anyway, so s1 loses to CONCURRENCY_CAP first
    # here -- use a track with a wider concurrency cap to isolate the fee math.
    reasons = {a.strategy_id: a.reason for a in result.assignments}
    assert reasons["s1"] == "CONCURRENCY_CAP_EXHAUSTED"


def test_24h_fee_cap_budget_mean_isolated_from_concurrency_cap() -> None:
    f_hi, f_lo = 100.0, 0.0
    mean = (f_hi + f_lo) / 2  # 50
    budget = PROBE_FEE_CAP_MULT["A"] * mean  # 6 x 50 = 300
    s0 = _cand(strategy_id="s0", track="A", shadow_score_mean=9.0, f_track_cap_usd=f_hi, probe_fee_24h_usd=250.0)
    s1 = _cand(strategy_id="s1", track="A", shadow_score_mean=8.0, f_track_cap_usd=f_lo, probe_fee_24h_usd=100.0)
    result = assign_slots([s0, s1], track="A")
    active = {a.strategy_id: a.slot_active for a in result.assignments}
    assert active["s0"] is True  # running=250 <= 300
    # running = 250+100 = 350 > 300 budget -> s1 blocked by FEE_CAP even
    # though Track A's concurrency cap (3) has room to spare.
    assert active["s1"] is False
    reasons = {a.strategy_id: a.reason for a in result.assignments}
    assert reasons["s1"] == "FEE_CAP_EXHAUSTED"
    assert budget == 300.0  # sanity-check the hand-computed expectation


def test_24h_fee_cap_not_exhausted_admits_within_concurrency_cap() -> None:
    f_track_cap = 10.0
    s0 = _cand(strategy_id="s0", track="C", shadow_score_mean=9.0, f_track_cap_usd=f_track_cap, probe_fee_24h_usd=1.0)
    result = assign_slots([s0], track="C")
    assert result.assignments[0].slot_active is True


def test_zero_candidates_returns_empty_assignments() -> None:
    result = assign_slots([], track="A")
    assert result.assignments == []


def test_reason_field_distinguishes_slot_vs_concurrency_vs_fee_cap() -> None:
    ranked = [
        _cand(strategy_id=f"s{i}", track="C", shadow_score_mean=float(10 - i))
        for i in range(2)
    ]
    result = assign_slots(ranked, track="C")
    reasons = {a.strategy_id: a.reason for a in result.assignments}
    assert reasons["s0"] == "SLOT_ACTIVE"
    assert reasons["s1"] == "CONCURRENCY_CAP_EXHAUSTED"


def test_probe_fee_cap_mult_matches_3_2_1_ab_c_list_order() -> None:
    assert PROBE_FEE_CAP_MULT == {"A": 6, "B": 4, "C": 2}
