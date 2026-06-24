"""Increment 1 — entrance probe judgment (deterministic, AI-free).

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack ban · in-loop GPT=0.

The entrance judge activates the reserved-seat Eligibility role: it scores each
active-universe candidate across 5 deterministic lenses (liquidity / ATR /
technical / regime-fit / alt-data tilt) into an ``opportunity_score`` + a
``trade_eligible`` flag + an ``ambiguous`` conflict flag. These tests prove the
lenses are pure, the composite is confidence-weighted, eligibility is
flow-preserving (defaults eligible, permissive floor), and ambiguity is a
conflict/thin-evidence flag — NEVER a blanket veto.
"""

from __future__ import annotations

from polaris.core.probes.entrance import (
    DEFAULT_TRADE_FLOOR,
    EntranceJudge,
    JudgeReading,
    trade_floor_from_env,
)
from polaris.core.universe.schema import UniverseInstrument


def _ins(
    symbol: str = "BTC-USDT",
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    quote_ccy: str = "USDT",
    vol: float = 5e8,
    spread: float = 2.0,
    atr: float = 4.0,
    depth: float = 1e6,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state="live",
        vol_24h_usd=vol,
        spread_bps=spread,
        atr_24h_pct=atr,
        depth_10bps_usd=depth,
    )


def test_empty_universe_yields_no_readings() -> None:
    judge = EntranceJudge()
    assert judge.judge_universe([]) == {}


def test_reading_shape_and_score_bounds() -> None:
    judge = EntranceJudge()
    universe = [_ins("BTC-USDT", vol=9e8), _ins("ETH-USDT", vol=5e8), _ins("AAA-USDT", vol=3e7)]
    out = judge.judge_universe(universe)
    assert set(out) == {ins.instrument_id for ins in universe}
    for r in out.values():
        assert isinstance(r, JudgeReading)
        assert 0.0 <= r.opportunity_score <= 1.0
        assert isinstance(r.trade_eligible, bool)
        assert isinstance(r.ambiguous, bool)
        # contributing lens ids are recorded for provenance
        assert r.contributing
        assert "liquidity" in r.contributing


def test_higher_liquidity_outscores_lower_within_group() -> None:
    judge = EntranceJudge()
    universe = [
        _ins("BTC-USDT", vol=9e8, depth=5e6),
        _ins("MID-USDT", vol=2e8, depth=1e6),
        _ins("LOW-USDT", vol=3e7, depth=4e4),
    ]
    out = judge.judge_universe(universe)
    assert out["okx:BTC-USDT"].opportunity_score > out["okx:LOW-USDT"].opportunity_score


def test_default_eligible_when_no_judgment_signal() -> None:
    # A single-instrument universe → grouped-z all zero → neutral composite.
    # Flow-preserving: a neutral/unknown score must stay trade_eligible (default 1).
    judge = EntranceJudge()
    out = judge.judge_universe([_ins("SOLO-USDT")])
    assert out["okx:SOLO-USDT"].trade_eligible is True


def test_permissive_floor_keeps_most_eligible() -> None:
    # Aggressive bias: the floor is permissive so the MAJORITY stay trade-eligible.
    judge = EntranceJudge(trade_floor=DEFAULT_TRADE_FLOOR)
    universe = [_ins(f"S{i}-USDT", vol=1e7 * (i + 1), atr=1.0 + i) for i in range(12)]
    out = judge.judge_universe(universe)
    eligible = sum(1 for r in out.values() if r.trade_eligible)
    assert eligible >= len(universe) // 2


def test_trade_floor_env_override(monkeypatch: object) -> None:
    monkeypatch.setenv("POLARIS_ENTRANCE_TRADE_FLOOR", "0.42")  # type: ignore[attr-defined]
    assert trade_floor_from_env() == 0.42
    monkeypatch.delenv("POLARIS_ENTRANCE_TRADE_FLOOR")  # type: ignore[attr-defined]
    assert trade_floor_from_env() == DEFAULT_TRADE_FLOOR


def test_trade_floor_env_garbage_falls_back(monkeypatch: object) -> None:
    monkeypatch.setenv("POLARIS_ENTRANCE_TRADE_FLOOR", "not-a-float")  # type: ignore[attr-defined]
    assert trade_floor_from_env() == DEFAULT_TRADE_FLOOR


def test_ambiguous_on_lens_conflict() -> None:
    # Construct a clear lens CONFLICT: strong favourable liquidity/atr but an
    # adverse technical + alt-data tilt → small composite, large individual leans.
    judge = EntranceJudge()
    universe = [
        _ins("BTC-USDT", vol=9e8, atr=8.0),
        _ins("ETH-USDT", vol=1e8, atr=1.0),
    ]
    # technical leans: BTC adverse (exhaustion), regime favourable → conflict.
    tech = {"okx:BTC-USDT": -0.9, "okx:ETH-USDT": 0.1}
    regime = {"okx:BTC-USDT": 0.9, "okx:ETH-USDT": 0.0}
    alt = {"okx:BTC-USDT": -0.8, "okx:ETH-USDT": 0.0}
    out = judge.judge_universe(
        universe, technical_lean=tech, regime_lean=regime, altdata_lean=alt
    )
    assert out["okx:BTC-USDT"].ambiguous is True
    # Ambiguous never forces ineligibility (flow-preserving): default stays eligible
    # unless the score itself is below the floor — and an ambiguous flag alone
    # never drops it.
    assert isinstance(out["okx:BTC-USDT"].trade_eligible, bool)


def test_thin_confidence_flags_ambiguous_not_block() -> None:
    # All lenses abstain (no optional leans, single instrument → zero z) → thin
    # evidence. Thin evidence is ambiguous-flagged but stays trade_eligible.
    judge = EntranceJudge()
    out = judge.judge_universe([_ins("SOLO-USDT")])
    r = out["okx:SOLO-USDT"]
    assert r.ambiguous is True
    assert r.trade_eligible is True


def test_subfloor_name_watched_but_trade_ineligible() -> None:
    # WATCH/TRADE decouple (2026-06-24): a sub-floor OKX name (thin vol) is SCORED
    # + RANKED (watched) but the floor overlay forces trade_eligible=False — even
    # if its other lenses score it above the trade floor. Slippage protection.
    judge = EntranceJudge(trade_floor=0.0)  # score gate wide open → isolate floor
    universe = [
        _ins("JUNK-USDT", vol=1e6, spread=2.0, atr=9.0, depth=1e4),  # sub-vol floor
        _ins("GOOD-USDT", vol=5e8, spread=2.0, atr=9.0, depth=1e6),
    ]
    out = judge.judge_universe(universe)
    # Both are scored (watched); the sub-floor name is NOT trade-eligible.
    assert out["okx:JUNK-USDT"].opportunity_score >= 0.0  # still judged/watched
    assert out["okx:JUNK-USDT"].trade_eligible is False  # floor overlay vetoes trade
    assert out["okx:GOOD-USDT"].trade_eligible is True


def test_okx_non_usd_quote_watched_but_trade_ineligible() -> None:
    # STEP 2 scope-widen review fix (HIGH): a crypto-quoted OKX pair (BTC-ETH,
    # quote=ETH) is now ADMITTED + WATCHED (flow_not_block), but the OKX order
    # path / fill accounting assume a USD-equivalent quote (sz=notional in quote
    # ccy). So a non-USD-equivalent quote forces trade_eligible=False — the name
    # is watched/ranked, its ENTRY is deferred (correctness, not a defensive cut).
    # USD-equivalent quotes (USDT/USDC/USD) stay fully trade-eligible.
    judge = EntranceJudge(trade_floor=0.0)  # isolate the quote overlay
    universe = [
        _ins("BTC-ETH", quote_ccy="ETH", vol=5e8, spread=2.0, atr=9.0, depth=1e6),
        _ins("BTC-USDC", quote_ccy="USDC", vol=5e8, spread=2.0, atr=9.0, depth=1e6),
        _ins("BTC-USDT", quote_ccy="USDT", vol=5e8, spread=2.0, atr=9.0, depth=1e6),
    ]
    out = judge.judge_universe(universe)
    assert out["okx:BTC-ETH"].opportunity_score >= 0.0          # still watched
    assert out["okx:BTC-ETH"].trade_eligible is False           # non-USD quote → defer
    assert out["okx:BTC-USDC"].trade_eligible is True           # USD-equiv → eligible
    assert out["okx:BTC-USDT"].trade_eligible is True


def test_capital_non_usd_quote_unaffected_by_okx_overlay() -> None:
    # The USD-quote overlay is OKX-specific (it guards the OKX adapter's quote-ccy
    # sizing). A Capital forex row (quote not USDT/USDC/USD) must NOT be vetoed by
    # it — Capital CFD pricing is venue-side USD. Only OKX gets the overlay.
    judge = EntranceJudge(trade_floor=0.0)
    universe = [
        _ins("EURUSD", venue="capital", asset_class="forex", quote_ccy="USD",
             vol=5e8, spread=2.0, atr=9.0, depth=1e6),
    ]
    out = judge.judge_universe(universe)
    assert out["capital:EURUSD"].trade_eligible is True


def test_wide_spread_is_hard_trade_block() -> None:
    # Spread beyond the venue cap (OKX 30bps) is a HARD trade-block regardless of
    # score (the dominant slippage term), but the name is still watched/scored.
    judge = EntranceJudge(trade_floor=0.0)
    universe = [
        _ins("WIDE-USDT", vol=5e8, spread=99.0, atr=9.0, depth=1e6),
        _ins("TIGHT-USDT", vol=5e8, spread=2.0, atr=9.0, depth=1e6),
    ]
    out = judge.judge_universe(universe)
    assert out["okx:WIDE-USDT"].trade_eligible is False
    assert out["okx:TIGHT-USDT"].trade_eligible is True


def test_floor_clearing_name_still_score_gated() -> None:
    # The floor overlay is AND'd with the score gate: a floor-clearing name with a
    # below-floor score is still trade-ineligible (score gate unchanged).
    judge = EntranceJudge(trade_floor=0.99)  # score gate nearly impossible
    out = judge.judge_universe([_ins("MEH-USDT", vol=5e8)])
    assert out["okx:MEH-USDT"].trade_eligible is False  # cleared floor, failed score


def test_judge_is_deterministic() -> None:
    judge = EntranceJudge()
    universe = [_ins("BTC-USDT", vol=9e8), _ins("ETH-USDT", vol=5e8)]
    a = judge.judge_universe(universe)
    b = judge.judge_universe(universe)
    assert {k: v.opportunity_score for k, v in a.items()} == {
        k: v.opportunity_score for k, v in b.items()
    }
