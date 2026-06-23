"""G1 — deterministic scored-ranker cutover (AI-conductor P1).

Spec / mandate (Jin 2026-05-30, .claude/plans/ai_conductor_architecture_2026-05-30.md):
- DEMO/PAPER virtual capital. G1 Universe Scanner is cut over from a GPT
  selector to a DETERMINISTIC scored ranker — no LLM call. The score is
  ``w1*log(vol) + w2*cell_quartile + w3*realized_vol(ATR proxy) + w4*hit_rate``
  and the top-N rows (clamp 12-48) become the focus list.
- AGGRESSIVE bias untouched: G1 STILL always PASS (it is a focus SELECTOR, not
  a KILL gate). Coverage stays WIDE (default N=30, FOCUS_MAX=48), focus is never
  starved (cold/empty data → vol-only ranking, never empty when universe exists).
- BEHAVIOUR PRESERVED: when only ``vol_24h_usd`` is present (today's production
  universe row), ``w1*log(vol)`` is monotonic in vol → the ranking is IDENTICAL
  to the old ``deterministic_top_n`` fallback (top-N by vol). The cutover output
  must MATCH that legacy deterministic output.
- The ``should_call_gpt_g1`` fingerprint+cooldown logic is REUSED as the
  RECOMPUTE trigger (recompute on listing change / top-K turnover>=25% / cooldown
  elapse; otherwise reuse the cached focus).
"""

from __future__ import annotations

import uuid
from typing import Any

from polaris.core.pipeline.agents.universe_scanner import (
    DEFAULT_FOCUS_N,
    FOCUS_MAX,
    FOCUS_MIN,
    deterministic_top_n,
    scored_top_n,
    universe_scanner_gate,
)
from polaris.core.pipeline.g1_focus_gate import G1FocusCache
from polaris.core.pipeline.gate_state import (
    GATE_STRATEGY_SIGNAL,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


def _ctx(payload: dict[str, Any]) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id=None,
        gate_id=1,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


def _vol_only(n: int, *, venue: str = "okx") -> list[dict[str, Any]]:
    """Production-shaped rows: only venue/symbol/vol_24h_usd, descending vol."""
    return [
        {"venue": venue, "symbol": f"T{i:03d}-USDT", "vol_24h_usd": 1e9 - i * 1e6}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# scored_top_n — ordering
# ---------------------------------------------------------------------------


def test_scored_top_n_vol_only_orders_by_vol_desc() -> None:
    rows = [
        {"venue": "okx", "symbol": "A", "vol_24h_usd": 100.0},
        {"venue": "okx", "symbol": "B", "vol_24h_usd": 200.0},
        {"venue": "okx", "symbol": "C", "vol_24h_usd": 50.0},
    ]
    top = scored_top_n(rows, n=2)
    assert [r["symbol"] for r in top] == ["B", "A"]


def test_scored_top_n_vol_only_matches_deterministic_top_n() -> None:
    # BEHAVIOUR PRESERVATION: with only vol present, the scored ranking is a
    # monotonic transform of vol → identical ordering to the legacy fallback.
    uni = _vol_only(40)
    scored = scored_top_n(uni, n=30)
    legacy = deterministic_top_n(uni, n=30)
    assert [r["symbol"] for r in scored] == [r["symbol"] for r in legacy]


def test_scored_top_n_missing_fields_contribute_zero() -> None:
    # No atr / quartile / hit-rate keys → those terms are 0; vol decides.
    rows = _vol_only(5)
    top = scored_top_n(rows, n=5)
    assert [r["symbol"] for r in top] == [r["symbol"] for r in rows]


def test_scored_top_n_atr_lifts_rank_on_equal_vol() -> None:
    # Two equal-vol symbols: the higher realized-vol (ATR) ranks first.
    rows = [
        {"venue": "okx", "symbol": "LOW", "vol_24h_usd": 1e8, "atr_24h_pct": 1.0},
        {"venue": "okx", "symbol": "HIGH", "vol_24h_usd": 1e8, "atr_24h_pct": 8.0},
    ]
    top = scored_top_n(rows, n=2)
    assert top[0]["symbol"] == "HIGH"


def test_scored_top_n_quartile_lifts_rank_on_equal_vol() -> None:
    rows = [
        {"venue": "okx", "symbol": "BOT", "vol_24h_usd": 1e8, "cell_quartile": "bottom"},
        {"venue": "okx", "symbol": "TOP", "vol_24h_usd": 1e8, "cell_quartile": "top"},
    ]
    top = scored_top_n(rows, n=2)
    assert top[0]["symbol"] == "TOP"


def test_scored_top_n_hit_rate_lifts_rank_on_equal_vol() -> None:
    rows = [
        {"venue": "okx", "symbol": "COLD", "vol_24h_usd": 1e8, "hit_rate": 0.0},
        {"venue": "okx", "symbol": "HOT", "vol_24h_usd": 1e8, "hit_rate": 0.9},
    ]
    top = scored_top_n(rows, n=2)
    assert top[0]["symbol"] == "HOT"


def test_scored_top_n_vol_dominates_secondary_terms() -> None:
    # Vol is the dominant axis: a far-higher-vol symbol with the WORST secondary
    # signals still outranks a low-vol symbol with the BEST secondary signals.
    rows = [
        {
            "venue": "okx", "symbol": "BIGVOL", "vol_24h_usd": 5e9,
            "atr_24h_pct": 0.0, "cell_quartile": "bottom", "hit_rate": 0.0,
        },
        {
            "venue": "okx", "symbol": "SMALLVOL", "vol_24h_usd": 3e7,
            "atr_24h_pct": 10.0, "cell_quartile": "top", "hit_rate": 1.0,
        },
    ]
    top = scored_top_n(rows, n=2)
    assert top[0]["symbol"] == "BIGVOL"


def test_scored_top_n_empty_returns_empty() -> None:
    assert scored_top_n([], n=30) == []


# ---------------------------------------------------------------------------
# universe_scanner_gate — always PASS, deterministic, no client
# ---------------------------------------------------------------------------


async def test_gate_always_pass_and_python() -> None:
    uni = _vol_only(40)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    assert res.decision == GateDecision.PASS
    assert res.next_gate == GATE_STRATEGY_SIGNAL
    assert res.model_used == "python"


async def test_gate_default_focus_size_is_clamped_default() -> None:
    uni = _vol_only(100)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    focus = res.payload["focus"]
    assert len(focus) == DEFAULT_FOCUS_N  # 30 by default
    assert FOCUS_MIN <= len(focus) <= FOCUS_MAX


async def test_gate_output_matches_legacy_fallback() -> None:
    # The cutover output MUST equal the old deterministic fallback (top-30 by
    # vol, "venue:symbol" tokens) when only vol is present.
    uni = _vol_only(40)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    legacy_tokens = [
        f"{r['venue']}:{r['symbol']}" for r in deterministic_top_n(uni, n=DEFAULT_FOCUS_N)
    ]
    assert res.payload["focus"] == legacy_tokens


async def test_gate_reads_universe_from_payload() -> None:
    uni = _vol_only(40)
    res = await universe_scanner_gate(_ctx({"universe": uni}))
    assert len(res.payload["focus"]) == DEFAULT_FOCUS_N


async def test_gate_empty_universe_pass_empty_focus() -> None:
    res = await universe_scanner_gate(_ctx({"universe": []}), universe=[])
    assert res.decision == GateDecision.PASS
    assert res.payload["focus"] == []


async def test_gate_empty_cell_summary_production_path() -> None:
    # Production payload: cell_summary="" and rows carry ONLY venue/symbol/vol.
    uni = _vol_only(40)
    res = await universe_scanner_gate(
        _ctx({"universe": uni, "cell_summary": ""}), universe=uni
    )
    assert res.decision == GateDecision.PASS
    legacy = [f"{r['venue']}:{r['symbol']}" for r in deterministic_top_n(uni, n=DEFAULT_FOCUS_N)]
    assert res.payload["focus"] == legacy


async def test_gate_clamps_to_focus_min_when_universe_small() -> None:
    # Universe smaller than FOCUS_MIN → best-effort focus = whole universe
    # (can't exceed what exists). Still PASS, never starved beyond reality.
    uni = _vol_only(8)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    assert res.decision == GateDecision.PASS
    assert len(res.payload["focus"]) == 8


# ---------------------------------------------------------------------------
# Env-override focus target (clamped to 12-48)
# ---------------------------------------------------------------------------


async def test_gate_focus_n_env_override_clamped_high(monkeypatch: Any) -> None:
    monkeypatch.setenv("POLARIS_G1_FOCUS_N", "100")  # over FOCUS_MAX
    uni = _vol_only(100)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    assert len(res.payload["focus"]) == FOCUS_MAX  # clamped to 48


async def test_gate_focus_n_env_override_clamped_low(monkeypatch: Any) -> None:
    monkeypatch.setenv("POLARIS_G1_FOCUS_N", "3")  # under FOCUS_MIN
    uni = _vol_only(100)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    assert len(res.payload["focus"]) == FOCUS_MIN  # clamped to 12


async def test_gate_focus_n_env_override_in_band(monkeypatch: Any) -> None:
    monkeypatch.setenv("POLARIS_G1_FOCUS_N", "40")
    uni = _vol_only(100)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    assert len(res.payload["focus"]) == 40


# ---------------------------------------------------------------------------
# Weight env overrides
# ---------------------------------------------------------------------------


def test_weight_env_overrides_parsed(monkeypatch: Any) -> None:
    from polaris.core.pipeline.agents import universe_scanner as us

    monkeypatch.setenv("POLARIS_G1_W_VOL", "2.5")
    monkeypatch.setenv("POLARIS_G1_W_QUARTILE", "1.5")
    monkeypatch.setenv("POLARIS_G1_W_RVOL", "0.7")
    monkeypatch.setenv("POLARIS_G1_W_HITRATE", "0.9")
    w = us._load_weights()
    assert w.vol == 2.5
    assert w.quartile == 1.5
    assert w.realized_vol == 0.7
    assert w.hit_rate == 0.9


def test_weight_env_override_drives_secondary_term(monkeypatch: Any) -> None:
    # With a huge hit-rate weight, hit-rate can override a vol gap.
    monkeypatch.setenv("POLARIS_G1_W_VOL", "0.0")
    monkeypatch.setenv("POLARIS_G1_W_HITRATE", "10.0")
    rows = [
        {"venue": "okx", "symbol": "BIGVOL", "vol_24h_usd": 5e9, "hit_rate": 0.0},
        {"venue": "okx", "symbol": "HOT", "vol_24h_usd": 3e7, "hit_rate": 1.0},
    ]
    top = scored_top_n(rows, n=2)
    assert top[0]["symbol"] == "HOT"


# ---------------------------------------------------------------------------
# RECOMPUTE trigger (reused should_call_gpt_g1 fingerprint+cooldown)
# ---------------------------------------------------------------------------


async def test_recompute_cold_then_reuse_inside_cooldown() -> None:
    uni = _vol_only(40)
    cache = G1FocusCache()
    # Cold cache → recompute + record.
    r1 = await universe_scanner_gate(
        _ctx({"universe": uni}), universe=uni, focus_cache=cache, now_ts=1_000,
    )
    assert r1.decision == GateDecision.PASS
    assert cache.last_focus() == r1.payload["focus"]

    # Unchanged universe inside cooldown → reuse cached focus (no recompute).
    r2 = await universe_scanner_gate(
        _ctx({"universe": uni}), universe=uni, focus_cache=cache, now_ts=1_010,
    )
    assert r2.decision == GateDecision.PASS
    assert r2.payload.get("focus_source") == "cache"
    assert r2.payload["focus"] == r1.payload["focus"]


async def test_recompute_on_listing_change() -> None:
    uni = _vol_only(40)
    cache = G1FocusCache()
    await universe_scanner_gate(
        _ctx({"universe": uni}), universe=uni, focus_cache=cache, now_ts=1_000,
    )
    changed = [*uni, {"venue": "okx", "symbol": "NEW-USDT", "vol_24h_usd": 5e9}]
    r = await universe_scanner_gate(
        _ctx({"universe": changed}), universe=changed, focus_cache=cache, now_ts=1_010,
    )
    # Recompute (not a cache reuse) → NEW-USDT (highest vol) is now in focus.
    assert r.payload.get("focus_source") != "cache"
    assert "okx:NEW-USDT" in r.payload["focus"]


async def test_focus_emitted_every_cycle_cached_or_fresh() -> None:
    uni = _vol_only(40)
    cache = G1FocusCache()
    for i in range(4):
        r = await universe_scanner_gate(
            _ctx({"universe": uni}), universe=uni, focus_cache=cache, now_ts=1_000 + i,
        )
        assert r.decision == GateDecision.PASS
        assert FOCUS_MIN <= len(r.payload["focus"]) <= FOCUS_MAX


# ---------------------------------------------------------------------------
# B3 #5 — documented structural pass-through (vestigial gate honesty)
# ---------------------------------------------------------------------------
# The AUTHORITATIVE G1 selection is Layer-0 ``rank_active_universe`` /
# ``compute_dynamic_focus``; this per-signal gate re-ranks an already-selected
# active set and emits a focus echo that NO downstream gate consumes. It must
# stay an always-PASS pass-through and label its focus as non-authoritative so a
# reader does not mistake it for the real selector. flow_not_block — no KILL.


async def test_gate_fresh_focus_marked_nonauthoritative() -> None:
    from polaris.core.pipeline.agents.universe_scanner import (
        FOCUS_SOURCE_NONAUTHORITATIVE,
    )

    uni = _vol_only(40)
    res = await universe_scanner_gate(_ctx({"universe": uni}), universe=uni)
    # Fresh emit (no cache) is tagged as a display-only echo, never authoritative.
    assert res.payload.get("focus_source") == FOCUS_SOURCE_NONAUTHORITATIVE


async def test_gate_never_kills_even_on_empty_universe() -> None:
    # A pass-through never gates selection: empty universe still PASSes forward.
    res = await universe_scanner_gate(_ctx({"universe": []}), universe=[])
    assert res.decision == GateDecision.PASS
    assert res.next_gate == GATE_STRATEGY_SIGNAL
