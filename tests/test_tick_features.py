"""P5 tick features — pure-function property + safety tests.

``compute_tick_features`` must be PURE and SAFE: a thin/stale/empty window
yields the all-None sentinel (no manufactured signal), and the real features
respect their mathematical bounds + monotonicity. We assert these with
hypothesis so the invariants hold across a wide synthetic tick space — not just
a few hand-picked windows.

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md §"신규 모듈".
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.features import TickFeatures, compute_tick_features
from polaris.core.ticks.types import TickSample

CFG = TickEngineConfig()
# A monotonic "now" far enough above the synthetic ts origin that windows ending
# near ts≈0..N seconds read as fresh (age within fresh_sec).
NOW = 1_000.0


def _tick(
    ts_ms: int,
    mid: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    last_trade_price: float | None = None,
    last_trade_size: float = 1.0,
    spread: float = 0.02,
) -> TickSample:
    """Build a TickSample with a symmetric book around ``mid``."""
    half = spread / 2.0
    bid = mid - half
    ask = mid + half
    spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    return TickSample(
        ts=ts_ms,
        bid=bid,
        ask=ask,
        mid=mid,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price if last_trade_price is not None else mid,
        last_trade_size=last_trade_size,
        spread_bps=spread_bps,
    )


def _linear_window(n: int, *, start_mid: float = 100.0, step: float = 0.0) -> list[TickSample]:
    """n ticks, 100ms apart (ts ms), mid moving ``step`` per tick, anchored near NOW."""
    base_ms = int((NOW - 1.0) * 1000)  # ~1s before NOW → fresh
    return [_tick(base_ms + i * 100, start_mid + i * step) for i in range(n)]


# ---------------------------------------------------------------------------
# Safety: empty / thin / stale windows → all-None sentinel (no false signal)
# ---------------------------------------------------------------------------


def test_empty_window_is_safe_sentinel() -> None:
    f = compute_tick_features([], NOW, CFG)
    assert f.n_ticks == 0
    assert f.age_sec is None
    for v in (f.velocity, f.accel, f.burst_z, f.ofi, f.aggr_flow, f.overshoot_z, f.spread_bps):
        assert v is None


@given(n=st.integers(min_value=1, max_value=CFG.min_ticks - 1))
def test_thin_window_is_safe_sentinel(n: int) -> None:
    f = compute_tick_features(_linear_window(n, step=0.1), NOW, CFG)
    assert f.n_ticks == n
    # Below min_ticks → no z-score/EWMA fields manufactured.
    assert f.velocity is None
    assert f.burst_z is None
    assert f.ofi is None
    assert f.overshoot_z is None


def test_stale_window_is_safe_sentinel() -> None:
    # Newest tick is well older than fresh_sec vs now_mono.
    w = _linear_window(CFG.min_ticks + 5, step=0.1)
    stale_now = NOW + CFG.fresh_sec + 100.0
    f = compute_tick_features(w, stale_now, CFG)
    assert f.velocity is None
    assert f.age_sec is not None and f.age_sec > CFG.fresh_sec


# ---------------------------------------------------------------------------
# Bounds: ofi ∈ [-1, 1]; spread_bps == latest; burst_z finite; n_ticks correct
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=CFG.min_ticks, max_value=60),
    bid_sizes=st.lists(st.floats(min_value=0.1, max_value=1e6), min_size=60, max_size=60),
    ask_sizes=st.lists(st.floats(min_value=0.1, max_value=1e6), min_size=60, max_size=60),
)
@settings(max_examples=150)
def test_ofi_within_unit_bounds(n: int, bid_sizes: list[float], ask_sizes: list[float]) -> None:
    base_ms = int((NOW - 1.0) * 1000)
    w = [
        _tick(base_ms + i * 100, 100.0 + i * 0.01, bid_size=bid_sizes[i], ask_size=ask_sizes[i])
        for i in range(n)
    ]
    f = compute_tick_features(w, NOW, CFG)
    assert f.ofi is not None
    assert -1.0 <= f.ofi <= 1.0


@given(n=st.integers(min_value=CFG.min_ticks, max_value=60))
def test_features_finite_and_window_state(n: int) -> None:
    w = _linear_window(n, step=0.05)
    f = compute_tick_features(w, NOW, CFG)
    assert f.n_ticks == n
    assert f.age_sec is not None and math.isfinite(f.age_sec)
    for v in (f.velocity, f.accel, f.burst_z, f.ofi, f.aggr_flow, f.overshoot_z, f.spread_bps):
        assert v is not None and math.isfinite(v)
    # spread_bps must be exactly the latest tick's stored value.
    assert f.spread_bps == w[-1].spread_bps


def test_flat_window_has_zero_velocity_and_finite_z() -> None:
    # A perfectly flat mid → zero velocity, and the z-scores degrade to 0 (no
    # variance) rather than NaN/inf (the _MIN_STD guard).
    f = compute_tick_features(_linear_window(20, step=0.0), NOW, CFG)
    assert f.velocity == 0.0
    assert f.burst_z == 0.0
    assert f.overshoot_z == 0.0
    assert f.accel == 0.0


# ---------------------------------------------------------------------------
# Monotonicity / sign: a faster up-move → larger velocity; symmetric for down
# ---------------------------------------------------------------------------


@given(
    slow=st.floats(min_value=0.001, max_value=0.05),
    fast_mult=st.floats(min_value=1.5, max_value=20.0),
)
@settings(max_examples=120)
def test_velocity_monotone_in_step(slow: float, fast_mult: float) -> None:
    f_slow = compute_tick_features(_linear_window(20, step=slow), NOW, CFG)
    f_fast = compute_tick_features(_linear_window(20, step=slow * fast_mult), NOW, CFG)
    assert f_slow.velocity is not None and f_fast.velocity is not None
    # Same Δt, larger per-tick step → strictly larger (signed) velocity.
    assert f_fast.velocity > f_slow.velocity > 0.0


@given(step=st.floats(min_value=0.001, max_value=0.5))
@settings(max_examples=120)
def test_velocity_sign_tracks_direction(step: float) -> None:
    up = compute_tick_features(_linear_window(20, step=step), NOW, CFG)
    down = compute_tick_features(_linear_window(20, step=-step), NOW, CFG)
    assert up.velocity is not None and down.velocity is not None
    assert up.velocity > 0.0
    assert down.velocity < 0.0


def test_ofi_sign_tracks_book_imbalance() -> None:
    base_ms = int((NOW - 1.0) * 1000)
    bid_heavy = [_tick(base_ms + i * 100, 100.0, bid_size=100.0, ask_size=5.0) for i in range(20)]
    ask_heavy = [_tick(base_ms + i * 100, 100.0, bid_size=5.0, ask_size=100.0) for i in range(20)]
    f_bid = compute_tick_features(bid_heavy, NOW, CFG)
    f_ask = compute_tick_features(ask_heavy, NOW, CFG)
    assert f_bid.ofi is not None and f_bid.ofi > 0.0
    assert f_ask.ofi is not None and f_ask.ofi < 0.0


def test_purity_no_mutation_of_input() -> None:
    # The window list + samples are frozen dataclasses; compute must not mutate
    # the list and must be deterministic (same input → same output).
    w = _linear_window(20, step=0.1)
    snapshot = list(w)
    a = compute_tick_features(w, NOW, CFG)
    b = compute_tick_features(w, NOW, CFG)
    assert w == snapshot
    assert a == b
    assert isinstance(a, TickFeatures)
