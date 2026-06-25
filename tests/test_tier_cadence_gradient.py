"""STAGE 1 — rank-attention gradient: tier metadata + tier-driven cadence + quota removal.

DEMO/PAPER aggressive bot. Design SSOT:
``vault/50_research/debates/rank_attention_gradient_2026-06-24.md``.

flow_not_block: tier is an OBSERVATION-CADENCE band (S/A/B/T), not a membership cut
— ALL active rows are watched, the tier only differentiates HOW OFTEN each is
polled/eval'd. No hard block, no drop, no size cut. 9-stack untouched (tier is not
a sizing multiplier). in-loop GPT=0 (deterministic merit rank → tier).
"""

from __future__ import annotations

import pytest

from polaris.core.universe.schema import (
    UniverseInstrument,
    crowding_lambda,
    tier_band_boundaries,
    tier_cadence,
    ws_budget_for_venue,
)
from polaris.core.universe.watchlist import (
    apply_crowding_penalty,
    assign_tiers,
    cadence_fires,
    compute_dynamic_focus,
    score_focus_candidates,
    tier_for_rank,
)

NOW = 1_780_000_000


def _make_inst(
    symbol: str = "BTC-USDT",
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    vol: float = 5e8,
    atr_pct: float = 4.0,
    group_id: str | None = None,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=group_id or f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=vol,
        spread_bps=2.0,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=200_000.0,
        last_seen_ts=NOW,
    )


# ---------------------------------------------------------------------------
# INC1 — tier band metadata (rank-percentile → {S,A,B,T})
# ---------------------------------------------------------------------------


def test_tier_band_boundaries_defaults_ordered() -> None:
    s_cut, a_cut, b_cut = tier_band_boundaries()
    assert 0.0 < s_cut < a_cut < b_cut < 1.0


def test_tier_band_boundaries_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_TIER_S_PCT", "0.05")
    monkeypatch.setenv("POLARIS_TIER_A_PCT", "0.25")
    monkeypatch.setenv("POLARIS_TIER_B_PCT", "0.55")
    assert tier_band_boundaries() == (0.05, 0.25, 0.55)


def test_tier_band_boundaries_garbage_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_TIER_S_PCT", "not-a-float")
    s_cut, _a, _b = tier_band_boundaries()
    assert 0.0 < s_cut < 1.0


def test_tier_for_rank_top_is_S() -> None:
    # rank 1 of 100 → top percentile → S.
    assert tier_for_rank(1, 100) == "S"


def test_tier_for_rank_bottom_is_T() -> None:
    # rank 100 of 100 → tail → T.
    assert tier_for_rank(100, 100) == "T"


def test_tier_for_rank_full_band_coverage() -> None:
    s_cut, a_cut, b_cut = tier_band_boundaries()
    total = 100
    tiers = [tier_for_rank(r, total) for r in range(1, total + 1)]
    # Every band present and monotone non-improving down the rank order.
    assert set(tiers) == {"S", "A", "B", "T"}
    order = {"S": 0, "A": 1, "B": 2, "T": 3}
    assert [order[t] for t in tiers] == sorted(order[t] for t in tiers)
    # Boundary counts track the percentile cuts.
    assert tiers.count("S") == round(s_cut * total)
    assert tiers.count("S") + tiers.count("A") == round(a_cut * total)
    assert tiers.count("S") + tiers.count("A") + tiers.count("B") == round(b_cut * total)


def test_tier_for_rank_single_row_is_S() -> None:
    # A lone watched name is the best by definition → top tier.
    assert tier_for_rank(1, 1) == "S"


def test_assign_tiers_parallel_to_rank_order() -> None:
    tiers = assign_tiers(120)
    assert len(tiers) == 120
    assert tiers[0] == "S"
    assert tiers[-1] == "T"


# ---------------------------------------------------------------------------
# INC1 — tier persisted on the focus row, all 120 placed (no 30-cut)
# ---------------------------------------------------------------------------


def test_all_active_placed_in_tier_no_focus_cut() -> None:
    # 120 active rows → ALL become focus rows (no [12,48]/quota truncation),
    # each carrying a tier. The 30-focus cliff is gone (flow_not_block).
    actives = [_make_inst(f"C{i}-USDT", vol=1e9 - i * 1e6) for i in range(120)]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    assert len(focus) == 120
    assert all(f.tier in {"S", "A", "B", "T"} for f in focus)
    # Best-ranked row is tier S, worst is tier T.
    assert focus[0].tier == "S"
    assert focus[-1].tier == "T"
    # Ranks are a dense 1..N and scores descend (single merit order).
    assert [f.rank for f in focus] == list(range(1, 121))
    assert [f.focus_score for f in focus] == sorted(
        (f.focus_score for f in focus), reverse=True
    )


# ---------------------------------------------------------------------------
# INC3 — tier-driven cadence (S=every, A=every, B=K-cyclic, T=M-cyclic)
# ---------------------------------------------------------------------------


def test_tier_cadence_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_TIER_CADENCE_K", raising=False)
    monkeypatch.delenv("POLARIS_TIER_CADENCE_M", raising=False)
    k, m = tier_cadence()
    assert k >= 2 and m > k  # T is rarer than B


def test_tier_cadence_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_TIER_CADENCE_K", "4")
    monkeypatch.setenv("POLARIS_TIER_CADENCE_M", "12")
    assert tier_cadence() == (4, 12)


def test_cadence_S_and_A_fire_every_cycle() -> None:
    for cyc in range(10):
        assert cadence_fires("S", cyc, 3, 6) is True
        assert cadence_fires("A", cyc, 3, 6) is True


def test_cadence_B_fires_every_k_cycles() -> None:
    k, m = 3, 6
    fires = [cadence_fires("B", c, k, m) for c in range(9)]
    # Fires on multiples of k (0,3,6) — 3 of 9.
    assert fires == [True, False, False, True, False, False, True, False, False]


def test_cadence_T_fires_every_m_cycles() -> None:
    k, m = 3, 6
    fires = [cadence_fires("T", c, k, m) for c in range(12)]
    assert fires.count(True) == 2  # cycles 0 and 6
    assert fires[0] is True and fires[6] is True


def test_cadence_tail_floor_positive() -> None:
    # f_floor > 0: even the tail is polled at least once in any M-window.
    k, m = 3, 6
    window = [cadence_fires("T", c, k, m) for c in range(m)]
    assert any(window)


# ---------------------------------------------------------------------------
# INC3 — per-venue WS budget (Capital 40 hard, OKX/Alpaca 60 default, raisable)
# ---------------------------------------------------------------------------


def test_ws_budget_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    assert ws_budget_for_venue("capital") == 40
    assert ws_budget_for_venue("okx") == 60
    # Alpaca SIP (paid, default) has no symbol cap → 60 (focus headroom).
    assert ws_budget_for_venue("alpaca") == 60


def test_ws_budget_alpaca_iex_feed_caps_at_30(monkeypatch: pytest.MonkeyPatch) -> None:
    # IEX free feed hard-caps at 30 symbols (quotes-only = 1 channel/sym), so the
    # Alpaca budget MUST be 30 on IEX — a >30 subscribe frame trips "symbol limit
    # exceeded" → reconnect churn → feed death.
    monkeypatch.setenv("POLARIS_ALPACA_FEED", "iex")
    assert ws_budget_for_venue("alpaca") == 30


def test_ws_budget_env_raisable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_WS_BUDGET_OKX", "150")
    assert ws_budget_for_venue("okx") == 150
    # Alpaca is env-raisable too (overrides the feed-driven default).
    monkeypatch.setenv("POLARIS_WS_BUDGET_ALPACA", "45")
    assert ws_budget_for_venue("alpaca") == 45
    # Unknown venue → a permissive default (>0), never 0 (flow_not_block).
    assert ws_budget_for_venue("mystery") > 0


# ---------------------------------------------------------------------------
# INC4 — quota removed: single merit rank decides order/tier (no class floors)
# ---------------------------------------------------------------------------


def test_no_quota_pure_merit_order_cross_venue() -> None:
    # A low-merit Capital row does NOT get a guaranteed seat ahead of higher-merit
    # crypto: order is pure single grouped-z merit (quota gone). It is still
    # WATCHED (tier T), never dropped — flow_not_block.
    crypto = [_make_inst(f"C{i}-USDT", vol=1e9 - i * 1e6) for i in range(40)]
    capital = [_make_inst("EURUSD", venue="capital", asset_class="forex", vol=0.0)]
    focus = compute_dynamic_focus(crypto + capital, cycle_ts=NOW)
    # Every row present (watched), none dropped by a quota redistribution.
    assert len(focus) == 41
    # Score order strictly descending — no quota promotion reshuffles it.
    scores = [f.focus_score for f in focus]
    assert scores == sorted(scores, reverse=True)


def test_focus_grouped_z_merit_is_single_rank() -> None:
    # The merit rank == the grouped-z composite (RANK_WEIGHT_OPPORTUNITY_Z reused);
    # focus_rank is exactly the argsort of score_focus_candidates.
    actives = [_make_inst(f"C{i}-USDT", vol=1e9 - i * 7e6, atr_pct=3.0 + (i % 4)) for i in range(30)]
    scores = score_focus_candidates(actives)
    expected_order = sorted(range(len(actives)), key=lambda i: scores[i], reverse=True)
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    got_symbols = [f.symbol for f in focus]
    assert got_symbols == [actives[i].symbol for i in expected_order]


# ---------------------------------------------------------------------------
# INC4 — soft-crowding penalty seam: env-tunable, DEFAULT OFF (pure merit)
# ---------------------------------------------------------------------------


def test_crowding_lambda_default_off() -> None:
    assert crowding_lambda() == 0.0


def test_crowding_lambda_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CROWDING_LAMBDA", "0.3")
    assert crowding_lambda() == pytest.approx(0.3)


def test_crowding_penalty_off_is_byte_identical() -> None:
    scores = [3.0, 2.0, 1.0, 0.5]
    clusters = ["btc", "btc", "eth", "eth"]
    assert apply_crowding_penalty(scores, clusters, 0.0) == scores


def test_crowding_penalty_on_nudges_crowded_cluster_down() -> None:
    # Two btc-cluster names + one eth: with lambda>0 the second btc name is nudged
    # below where pure score would put it (attention-share penalty). It is a RANK
    # nudge only — still watched, never blocked.
    scores = [3.0, 2.9, 2.8]
    clusters = ["btc", "btc", "eth"]
    penalized = apply_crowding_penalty(scores, clusters, 0.5)
    # The 2nd btc row (crowded) drops more than the lone eth row.
    assert penalized[1] < scores[1]
    assert penalized[2] == pytest.approx(scores[2])  # eth uncrowded → unchanged


def test_crowding_default_off_in_compute_dynamic_focus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_CROWDING_LAMBDA", raising=False)
    actives = [
        _make_inst("A-USDT", vol=9e8, group_id="crypto:BTC"),
        _make_inst("B-USDT", vol=8e8, group_id="crypto:BTC"),
        _make_inst("C-USDT", vol=7e8, group_id="crypto:ETH"),
    ]
    focus = compute_dynamic_focus(actives, cycle_ts=NOW)
    # Default OFF → pure merit order preserved.
    assert [f.symbol for f in focus] == ["A-USDT", "B-USDT", "C-USDT"]
