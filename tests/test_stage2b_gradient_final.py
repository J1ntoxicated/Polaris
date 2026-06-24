"""STAGE 2b — gradient final: full Alpaca enrichment ON, INC2 cap removal,
INC5 staleness SLA, resource guard.

DEMO/PAPER aggressive bot. Design SSOT:
``vault/50_research/debates/rank_attention_gradient_2026-06-24.md``.

flow_not_block: watch-all-valid widens FLOW (no drop / block / size-cut); the
Alpaca watch-floor is a per-venue RESOURCE BOUND on a 13k-row venue (a KNOWN
sub-floor real datum, never an unknown), the OKX/Capital watch sets stay
ALL-valid (their floor remains on the trade gate). 9-stack untouched (tier is
not a sizing multiplier). in-loop GPT = 0 (deterministic merit rank → tier →
cadence). Aggressive bias preserved.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.universe._alpaca import _snapshot_full_sweep
from polaris.core.universe._ranking import (
    apply_alpaca_watch_floor,
    rank_active_universe,
)
from polaris.core.universe.schema import (
    UniverseInstrument,
    auto_scale_tail_cadence,
    is_flash_spike,
    is_stale_for_force_poll,
    max_stale_age_sec,
    per_cycle_poll_budget,
    recency_decay_penalty,
    should_refresh_before_promote,
    tier_with_hysteresis,
    universe_watch_max,
)
from polaris.scripts._production_layers import get_focus_targets
from polaris.storage.schema import init_db

NOW = 1_780_000_000


def _inst(
    symbol: str,
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    quote: str = "USDT",
    vol: float = 5e8,
    price: float = 0.0,
    atr: float = 4.0,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy=quote,
        state="live",
        vol_24h_usd=vol,
        spread_bps=2.0,
        atr_24h_pct=atr,
        depth_10bps_usd=200_000.0,
        last_seen_ts=NOW,
        last_price=price,
    )


# ---------------------------------------------------------------------------
# A — Alpaca full-snapshot enrichment is the PRODUCTION DEFAULT (ON)
# ---------------------------------------------------------------------------


def test_full_sweep_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset env → full sweep is the production default (real vol → all rows),
    # so the floor (min_vol 5M / min_price $1) auto-bounds Alpaca to real liquid.
    monkeypatch.delenv("POLARIS_ALPACA_SNAPSHOT_FULL", raising=False)
    assert _snapshot_full_sweep() is True


def test_full_sweep_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit 0/false/off → the bounded screener path (escape hatch).
    for off in ("0", "false", "off", "no"):
        monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", off)
        assert _snapshot_full_sweep() is False


def test_full_sweep_explicit_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_ALPACA_SNAPSHOT_FULL", "1")
    assert _snapshot_full_sweep() is True


# ---------------------------------------------------------------------------
# A/B — Alpaca watch-floor: real sub-floor rows removed, sentinel KEPT
# ---------------------------------------------------------------------------


def test_alpaca_watch_floor_drops_real_subfloor() -> None:
    # Real datum below the $5M vol floor → removed from the WATCH set (resource
    # bound on the 13k venue). A real datum below the $1 price floor → removed.
    rows = [
        _inst("AAPL", venue="alpaca", asset_class="equity", quote="USD",
              vol=2e10, price=180.0),  # liquid → kept
        _inst("PENNYVOL", venue="alpaca", asset_class="equity", quote="USD",
              vol=2.6e4, price=12.0),  # real vol < 5M → dropped
        _inst("SUBDOLLAR", venue="alpaca", asset_class="equity", quote="USD",
              vol=9e6, price=0.59),  # real price < $1 → dropped
    ]
    kept = apply_alpaca_watch_floor(rows)
    syms = {i.symbol for i in kept}
    assert syms == {"AAPL"}


def test_alpaca_watch_floor_keeps_sentinel() -> None:
    # vol==0 sentinel (un-enriched, UNKNOWN) is NOT a known-bad datum → KEPT
    # (flow_not_block: never drop on a missing datum).
    rows = [
        _inst("UNK", venue="alpaca", asset_class="equity", quote="USD",
              vol=0.0, price=0.0),
    ]
    kept = apply_alpaca_watch_floor(rows)
    assert [i.symbol for i in kept] == ["UNK"]


def test_alpaca_watch_floor_leaves_non_alpaca_untouched() -> None:
    # OKX/Capital rows pass through byte-identical — their floor stays on the
    # trade gate (watch-all-valid preserved → no OKX 176/186 re-cut regression).
    okx = [_inst(f"C{i}-USDT", vol=2.6e4) for i in range(5)]  # sub-OKX-floor vol
    kept = apply_alpaca_watch_floor(okx)
    assert kept == okx


# ---------------------------------------------------------------------------
# B — INC2: remove the WATCH_MAX cap (watch ALL valid, no per-venue cliff)
# ---------------------------------------------------------------------------


def test_watch_all_valid_no_120_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1900 valid rows → ALL become active (the 120 cliff is GONE). flow_not_block.
    monkeypatch.delenv("POLARIS_WATCH_MAX", raising=False)
    monkeypatch.delenv("POLARIS_UNIVERSE_RANK_TOP_N", raising=False)
    actives = [_inst(f"C{i}-USDT", vol=1e9 - i * 1e5) for i in range(1900)]
    selected = rank_active_universe(actives)
    assert len(selected) == 1900


def test_watch_max_env_is_generous_safety_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default ceiling is a GENEROUS safety backstop (>= 3000), never the old
    # 120 — it must not bind the real ~1900 watched set.
    monkeypatch.delenv("POLARIS_WATCH_MAX", raising=False)
    assert universe_watch_max() >= 3000


def test_watch_max_env_override_still_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit env ceiling still binds (operator resource override).
    monkeypatch.setenv("POLARIS_WATCH_MAX", "50")
    monkeypatch.delenv("POLARIS_UNIVERSE_RANK_TOP_N", raising=False)
    actives = [_inst(f"C{i}-USDT", vol=1e9 - i * 1e5) for i in range(200)]
    selected = rank_active_universe(actives)
    assert len(selected) == 50


# ---------------------------------------------------------------------------
# C — INC5 staleness SLA
# ---------------------------------------------------------------------------


def test_max_stale_age_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_MAX_STALE_AGE_SEC", raising=False)
    assert max_stale_age_sec() > 0
    monkeypatch.setenv("POLARIS_MAX_STALE_AGE_SEC", "900")
    assert max_stale_age_sec() == 900.0


def test_is_stale_force_poll() -> None:
    # A tail row not polled past max_stale_age → force-poll regardless of cadence.
    max_age = 600.0
    assert is_stale_for_force_poll(last_poll_ts=NOW - 601, now_ts=NOW, max_age_sec=max_age)
    assert not is_stale_for_force_poll(last_poll_ts=NOW - 10, now_ts=NOW, max_age_sec=max_age)
    # Never polled (last_poll_ts None) → stale (must be polled at least once).
    assert is_stale_for_force_poll(last_poll_ts=None, now_ts=NOW, max_age_sec=max_age)


def test_recency_decay_penalty_monotone() -> None:
    # Older data → larger penalty (a stale datum must not drive a hot decision).
    fresh = recency_decay_penalty(age_sec=0.0, half_life_sec=300.0)
    mid = recency_decay_penalty(age_sec=300.0, half_life_sec=300.0)
    old = recency_decay_penalty(age_sec=3000.0, half_life_sec=300.0)
    assert fresh == 0.0
    assert 0.0 < mid < old
    # Half-life: at one half-life the penalty is ~0.5 of the asymptote (1.0).
    assert mid == pytest.approx(0.5, abs=0.01)


def test_refresh_before_promote_only_on_upgrade() -> None:
    # Promotion to a hotter tier requires a fresh REST refresh first (no hot
    # decision on stale data); a demotion / same tier does not.
    assert should_refresh_before_promote("T", "S") is True
    assert should_refresh_before_promote("B", "A") is True
    assert should_refresh_before_promote("S", "T") is False
    assert should_refresh_before_promote("A", "A") is False


def test_tier_hysteresis_min_dwell() -> None:
    # A row cannot churn tiers until it has dwelt min_dwell cycles — anti-churn.
    # Below min_dwell → keep the previous tier even if merit moved.
    assert tier_with_hysteresis("B", "S", dwell_cycles=1, min_dwell=3) == "B"
    # At/after min_dwell → adopt the new tier.
    assert tier_with_hysteresis("B", "S", dwell_cycles=3, min_dwell=3) == "S"
    # Same tier → no churn regardless of dwell.
    assert tier_with_hysteresis("A", "A", dwell_cycles=0, min_dwell=3) == "A"


def test_flash_spike_fast_path() -> None:
    # An extreme move bypasses the cadence band (Fast-Path) regardless of tier.
    thr = 0.05  # 5%
    assert is_flash_spike(pct_move=0.08, threshold=thr) is True
    assert is_flash_spike(pct_move=-0.08, threshold=thr) is True  # abs move
    assert is_flash_spike(pct_move=0.01, threshold=thr) is False


# ---------------------------------------------------------------------------
# D — resource guard: auto-scale tail cadence + per-cycle poll budget
# ---------------------------------------------------------------------------


def test_auto_scale_tail_cadence_grows_with_watched() -> None:
    # As watched grows, M grows so the per-cycle tail poll set stays bounded by
    # the budget. A small watched set keeps the base M (no spurious slowdown).
    base_m = 6
    budget = 200
    assert auto_scale_tail_cadence(watched=120, base_m=base_m, per_cycle_budget=budget) == base_m
    big = auto_scale_tail_cadence(watched=1900, base_m=base_m, per_cycle_budget=budget)
    assert big >= base_m
    # The tail rows are ~40% of watched; per M-cycle each tail row fires once, so
    # tail-rows-per-cycle ≈ (0.4*watched)/M must be <= budget.
    tail = int(0.4 * 1900)
    assert tail / big <= budget


def test_auto_scale_never_below_base() -> None:
    assert auto_scale_tail_cadence(watched=10, base_m=6, per_cycle_budget=200) == 6


def test_per_cycle_poll_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_PER_CYCLE_POLL_BUDGET", raising=False)
    assert per_cycle_poll_budget() > 0
    monkeypatch.setenv("POLARIS_PER_CYCLE_POLL_BUDGET", "256")
    assert per_cycle_poll_budget() == 256


# ---------------------------------------------------------------------------
# D — resource guard WIRED into the consume seam: a large watched set rarefies
# the tail so per-cycle work stays bounded (auto-scaled M), no membership cut.
# ---------------------------------------------------------------------------


def _seed_focus(conn: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
    for i, (venue, symbol, tier) in enumerate(rows):
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_focus "
            "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
            " evict_reason, tier) VALUES (?, ?, ?, ?, ?, 'core', NULL, ?)",
            (NOW, venue, symbol, 100.0 - i, i, tier),
        )
    conn.commit()


def test_auto_scaled_m_defers_tail_under_large_watched(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    # Tiny per-cycle budget + a large watched set → M auto-scales well above the
    # base 6, so a Tier-T row does NOT fire on cycle 6 (it would at base M=6). It
    # is STILL watched — it fires on the (later) scaled-M boundary. flow_not_block.
    monkeypatch.delenv("POLARIS_TIER_CADENCE_K", raising=False)
    monkeypatch.delenv("POLARIS_TIER_CADENCE_M", raising=False)
    monkeypatch.setenv("POLARIS_PER_CYCLE_POLL_BUDGET", "2")
    conn = init_db(tmp_path / "rg.sqlite")
    try:
        # 100 watched rows, mostly tail → 0.4*100/2 = 20 ⇒ M scales to 20.
        rows = [("okx", "S-USDT", "S"), ("okx", "A-USDT", "A")]
        rows += [("okx", f"T{i}-USDT", "T") for i in range(98)]
        _seed_focus(conn, rows)
        # Cycle 6: base M=6 would fire T; auto-scaled M=20 does NOT (6 % 20 != 0).
        got = {
            s for _v, s, _ac, _g in get_focus_targets(
                conn, cycle_ts=NOW, max_n=1000, cycle_index=6
            )
        }
        assert "S-USDT" in got and "A-USDT" in got  # hot tiers always fire
        assert not any(s.startswith("T") for s in got)  # tail deferred this cycle
    finally:
        conn.close()


def test_explicit_cadence_m_overrides_auto_scale(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    # An explicit cadence_m wins over the auto-scale (operator knob).
    monkeypatch.setenv("POLARIS_PER_CYCLE_POLL_BUDGET", "2")
    conn = init_db(tmp_path / "rg2.sqlite")
    try:
        rows = [("okx", f"T{i}-USDT", "T") for i in range(100)]
        _seed_focus(conn, rows)
        # Cycle 6 with explicit M=6 → tail fires despite the tiny budget.
        got = {
            s for _v, s, _ac, _g in get_focus_targets(
                conn, cycle_ts=NOW, max_n=1000, cycle_index=6, cadence_m=6
            )
        }
        assert any(s.startswith("T") for s in got)
    finally:
        conn.close()
