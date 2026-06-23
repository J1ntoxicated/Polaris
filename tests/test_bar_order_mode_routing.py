"""Build B — bar-pipeline per-family order-mode routing.

A breakout/TREND bar strategy crosses the spread (marketable-limit); a
reversion/range bar strategy rests passively (post-only). The resolver returns
``(prefer_maker, marketable_limit)`` consumed by ``reserve_and_submit``. An
unregistered id keeps the strength-gated default (both False) so flow is never
blocked ([[ab_letrun_maker_2026-06-24]]).
"""

from __future__ import annotations

import pytest

from polaris.scripts._production_run_signal import _bar_order_mode
from polaris.strategies import STRATEGY_REGISTRY


def _first_strategy_in_bucket(want_trend: bool) -> str | None:
    from polaris.core.live_recalc.exit_thesis import bucket_from_correlation_group
    from polaris.core.live_recalc.exit_types import Bucket

    for sid, cls in STRATEGY_REGISTRY.items():
        bucket = bucket_from_correlation_group(cls.metadata.correlation_group_id)
        if (bucket is Bucket.TREND) == want_trend:
            return sid
    return None


def test_bar_trend_strategy_routes_marketable_limit() -> None:
    sid = _first_strategy_in_bucket(want_trend=True)
    assert sid is not None, "expected at least one TREND bar strategy registered"
    prefer_maker, marketable_limit = _bar_order_mode(sid)
    assert marketable_limit is True
    assert prefer_maker is False  # a momentum entry crosses, never rests


def test_bar_reversion_strategy_routes_post_only() -> None:
    sid = _first_strategy_in_bucket(want_trend=False)
    if sid is None:
        pytest.skip("no REVERSION bar strategy registered")
    prefer_maker, marketable_limit = _bar_order_mode(sid)
    assert prefer_maker is True   # rest passively at the touch
    assert marketable_limit is False


def test_bar_unregistered_strategy_keeps_default() -> None:
    # An unregistered id must not force either mode (strength-gated default) so
    # the entry is never blocked (flow_not_block).
    prefer_maker, marketable_limit = _bar_order_mode("___not_a_real_strategy___")
    assert (prefer_maker, marketable_limit) == (False, False)
