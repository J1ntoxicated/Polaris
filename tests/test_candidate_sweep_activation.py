"""Converged per-symbol activation/edge tests (#39 Capital activation redesign).

DEMO/PAPER. AGGRESSIVE / flow_not_block. Source spec (both GPT+Gemini signed):
``vault/50_research/debates/capital_activation_2026-06-26.md``.

These pin the debate-CONVERGED math that replaces the old gate-multiplier:

  candidate = cold_start_mult × clamp01(0.75×activation + 0.25×edge)
  activation = clamp01(0.30×tick_rate + 0.25×short_move + 0.15×intraday_range
                       + 0.20×spread_tradeability + 0.10×daily_vol)   ← ADDITIVE
  edge = clamp01(0.35×sentiment + 0.65×tech)  (tech-dominant; missing → 0.5)

🚨 9-stack ban: NO ≤1 multiplier may be stacked beyond the single cold-start
term. activation is a pure ADDITIVE clamped blend; spread_tradeability is an
ADDITIVE term INSIDE activation, NEVER a multiplier on the candidate. These tests
fail if anyone reintroduces a multiplicative gate/tradeability stack.
"""

from __future__ import annotations

import time

import pytest

from polaris.scripts import _candidate_sweep as cs

# ---------------------------------------------------------------------------
# candidate_score — additive 0.75/0.25 blend, NOT a gate multiplier.
# ---------------------------------------------------------------------------


def test_candidate_score_additive_blend() -> None:
    """candidate = clamp01(0.75×activation + 0.25×edge) — additive, not a gate."""
    assert cs.candidate_score(1.0, 1.0) == pytest.approx(1.0)
    assert cs.candidate_score(1.0, 0.0) == pytest.approx(0.75)
    assert cs.candidate_score(0.0, 1.0) == pytest.approx(0.25)
    assert cs.candidate_score(0.4, 0.6) == pytest.approx(0.75 * 0.4 + 0.25 * 0.6)


def test_candidate_score_no_zero_gate_on_empty_edge() -> None:
    """An ACTIVE major with empty (0) edge is NOT zeroed — the whole point.

    Under the old gate (activation×(0.5+0.5×edge)) a 0-edge mover kept half its
    activation; under the converged additive blend a strong mover with empty edge
    keeps 0.75× its activation — it is NOT dragged to ~0 by a sparse FX edge.
    """
    active_no_edge = cs.candidate_score(1.0, 0.0)
    assert active_no_edge >= 0.7  # active major survives a sparse-edge venue


def test_candidate_score_weights_are_env_named() -> None:
    """The blend weights are NAMED constants (no magic-in-place)."""
    assert pytest.approx(0.75) == cs.CANDIDATE_ACTIVATION_WEIGHT
    assert pytest.approx(0.25) == cs.CANDIDATE_EDGE_WEIGHT


# ---------------------------------------------------------------------------
# activation_score — 5-component ADDITIVE clamped blend.
# ---------------------------------------------------------------------------


def _flat_daily_bars(now: int) -> dict[str, list]:
    """Bars carrying no realized-vol signal (daily_vol component → 0)."""
    from tests.test_candidate_sweep import _flat_bars

    return {
        "15m": _flat_bars("capital", "EURUSD", "15m", 30, now),
        "1H": _flat_bars("capital", "EURUSD", "1H", 30, now),
        "1D": _flat_bars("capital", "EURUSD", "1D", 30, now),
    }


def test_activation_zero_when_no_motion_no_spread_no_vol() -> None:
    """No live motion + no spread + flat bars → activation ~0."""
    now = int(time.time())
    act = cs.activation_score(_flat_daily_bars(now), None, None, now)
    assert act == pytest.approx(0.0)


def test_activation_tick_rate_component_lifts_score() -> None:
    """A high per-symbol tick rate alone lifts activation (0.30 weight)."""
    now = int(time.time())
    bars = _flat_daily_bars(now)
    hot = {
        "ticks_600s": cs.ACT_TICK_RATE_HOT_600S,
        "last_mid": 100.0,
        "mid_120s_ago": 100.0,
        "mid_high_600s": 100.0,
        "mid_low_600s": 100.0,
        "last_ts": now,
    }
    act = cs.activation_score(bars, hot, None, now)
    # tick_rate maxes its 0.30-weight component (no other component fires).
    assert act == pytest.approx(cs.ACT_TICK_RATE_WEIGHT, abs=1e-9)


def test_activation_short_move_component_lifts_score() -> None:
    """A recent per-symbol price displacement lifts activation (0.25 weight)."""
    now = int(time.time())
    bars = _flat_daily_bars(now)
    move_bps = cs.ACT_SHORT_MOVE_HOT_BPS  # exactly the HOT threshold → component 1.0
    last = 100.0
    ref = last * (1.0 - move_bps / 1e4)  # (last - ref)/last × 1e4 == move_bps exactly
    motion = {
        "ticks_600s": 0,
        "last_mid": last,
        "mid_120s_ago": ref,
        "mid_high_600s": last,
        "mid_low_600s": last,
        "last_ts": now,
    }
    act = cs.activation_score(bars, motion, None, now)
    assert act == pytest.approx(cs.ACT_SHORT_MOVE_WEIGHT, abs=1e-6)


def test_activation_intraday_range_component_lifts_score() -> None:
    """A wide per-symbol intraday range lifts activation (0.15 weight)."""
    now = int(time.time())
    bars = _flat_daily_bars(now)
    mid = 100.0
    range_bps = cs.ACT_RANGE_HOT_BPS
    span = mid * range_bps / 1e4
    motion = {
        "ticks_600s": 0,
        "last_mid": mid,
        "mid_120s_ago": mid,
        "mid_high_600s": mid + span / 2,
        "mid_low_600s": mid - span / 2,
        "last_ts": now,
    }
    act = cs.activation_score(bars, motion, None, now)
    assert act == pytest.approx(cs.ACT_RANGE_WEIGHT, abs=1e-6)


def test_activation_spread_is_additive_tight_high_wide_low() -> None:
    """spread_tradeability is ADDITIVE inside activation — tight→up, wide→down.

    🚨 9-stack: spread enters as an ADDITIVE term, NEVER a multiplier. A tight
    spread RAISES activation vs a wide spread under identical motion/bars.
    """
    now = int(time.time())
    bars = _flat_daily_bars(now)
    motion = {
        "ticks_600s": 0,
        "last_mid": 100.0,
        "mid_120s_ago": 100.0,
        "mid_high_600s": 100.0,
        "mid_low_600s": 100.0,
        "last_ts": now,
    }
    tight = cs.activation_score(bars, motion, cs.ACT_SPREAD_TIGHT_BPS, now)
    wide = cs.activation_score(bars, motion, cs.ACT_SPREAD_WIDE_BPS, now)
    assert tight == pytest.approx(cs.ACT_SPREAD_WEIGHT, abs=1e-9)  # tight → full
    assert wide == pytest.approx(0.0, abs=1e-9)                    # wide → zero
    assert tight > wide


def test_activation_is_purely_additive_sum_of_components() -> None:
    """activation == clamp01(Σ weightᵢ × componentᵢ) — provably additive.

    All five components fire at their max; the result equals the SUM of the five
    weights (clamped). If any component were multiplicative this identity breaks.
    """
    now = int(time.time())
    bars = _flat_daily_bars(now)
    mid = 100.0
    move_bps = cs.ACT_SHORT_MOVE_HOT_BPS
    range_bps = cs.ACT_RANGE_HOT_BPS
    span = mid * range_bps / 1e4
    motion = {
        "ticks_600s": cs.ACT_TICK_RATE_HOT_600S,
        "last_mid": mid,
        "mid_120s_ago": mid * (1.0 - move_bps / 1e4),
        "mid_high_600s": mid + span / 2,
        "mid_low_600s": mid - span / 2,
        "last_ts": now,
    }
    act = cs.activation_score(bars, motion, cs.ACT_SPREAD_TIGHT_BPS, now)
    # tick+move+range+spread saturate; daily_vol stays 0 (flat bars).
    expected = (
        cs.ACT_TICK_RATE_WEIGHT
        + cs.ACT_SHORT_MOVE_WEIGHT
        + cs.ACT_RANGE_WEIGHT
        + cs.ACT_SPREAD_WEIGHT
    )
    assert act == pytest.approx(min(1.0, expected), abs=1e-6)


def test_activation_component_weights_sum_to_one_and_named() -> None:
    """The five component weights are NAMED and sum to 1.0 (debate spec)."""
    total = (
        cs.ACT_TICK_RATE_WEIGHT
        + cs.ACT_SHORT_MOVE_WEIGHT
        + cs.ACT_RANGE_WEIGHT
        + cs.ACT_SPREAD_WEIGHT
        + cs.ACT_DAILY_VOL_WEIGHT
    )
    assert total == pytest.approx(1.0)
    # Live motion = 0.70 influence; spread 0.20; daily vol demoted to 0.10.
    live_motion = (
        cs.ACT_TICK_RATE_WEIGHT + cs.ACT_SHORT_MOVE_WEIGHT + cs.ACT_RANGE_WEIGHT
    )
    assert live_motion == pytest.approx(0.70)
    assert pytest.approx(0.20) == cs.ACT_SPREAD_WEIGHT
    assert pytest.approx(0.10) == cs.ACT_DAILY_VOL_WEIGHT


# ---------------------------------------------------------------------------
# edge_score — tech-dominant; missing component → 0.5 NEUTRAL (never 0).
# ---------------------------------------------------------------------------


def test_edge_missing_sentiment_defaults_neutral_not_zero() -> None:
    """Missing sentiment (no ground) → that component is NEUTRAL 0.5, never 0.

    An active FX/index major with no COT/sentiment is NOT dragged below neutral by
    an empty edge component.
    """
    now = int(time.time())
    from tests.test_candidate_sweep import _flat_bars

    flat_bars = {
        "15m": _flat_bars("capital", "EURUSD", "15m", 30, now),
        "1H": _flat_bars("capital", "EURUSD", "1H", 30, now),
        "1D": _flat_bars("capital", "EURUSD", "1D", 30, now),
    }
    # No ground (sentiment missing) + flat bars (tech neutral) → both 0.5.
    edge = cs.edge_score(flat_bars, None, now)
    assert edge == pytest.approx(0.5, abs=1e-9)
    assert edge >= 0.5  # never below neutral when both components are absent


def test_edge_is_tech_dominant() -> None:
    """tech carries 0.65 of the edge (vs 0.35 sentiment) — Capital sentiment sparse."""
    assert pytest.approx(0.65) == cs.EDGE_W_TECH_ALIGN
    assert pytest.approx(0.35) == cs.EDGE_W_SENTIMENT
    assert cs.EDGE_W_TECH_ALIGN > cs.EDGE_W_SENTIMENT


def test_edge_tech_alignment_outweighs_sentiment() -> None:
    """With strong tech agreement but neutral sentiment, edge rises above neutral
    by the tech-dominant weight — proving tech drives the blend."""
    now = int(time.time())
    from tests.test_candidate_sweep import _moving_bars

    up_bars = {
        "15m": _moving_bars("capital", "US100", "15m", 30, now, amp=1.0),
        "1H": _moving_bars("capital", "US100", "1H", 30, now, amp=1.0),
        "1D": _moving_bars("capital", "US100", "1D", 30, now, amp=1.0),
    }
    # No ground → sentiment neutral 0.5; all-TF-up → tech ~1.0.
    edge = cs.edge_score(up_bars, None, now)
    # edge = 0.35×0.5 + 0.65×(tech≈1.0) → well above 0.5.
    assert edge > 0.5 + 0.65 * 0.4  # tech dominance pushes edge clearly up


def test_edge_missing_default_constant_named() -> None:
    """The NEUTRAL default for a missing edge component is a named 0.5 constant."""
    assert pytest.approx(0.5) == cs.EDGE_MISSING_COMPONENT_DEFAULT
