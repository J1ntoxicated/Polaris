"""#15 — G6 GPT call-efficiency gate (cooldown + context-change trigger).

Spec / mandate (Jin 2026-05-29):
- DEMO/PAPER virtual capital. AGGRESSIVE bias is *unaffected* — this gate only
  cuts redundant GPT cost, decision quality is preserved (the hard loss rail +
  Q8 swap still fire as Python fast-path BEFORE any call-gate logic).
- G6 fired gpt-5.5 per open position per 5s tick (2880 calls/24h = 96% of GPT
  spend). When the last GPT call was recent (< cooldown sec AND < min ticks
  ago) AND the decision context is unchanged (|Δprice| < X%, same regime, same
  PnL band) → reuse the prior GPT decision (Python fast-path, no call). Any
  context change → call GPT.

These tests pin the *pure* gating logic in ``g6_call_gate`` — the
``position_monitor_gate`` integration is covered in test_g6_position_monitor_gpt.
"""

from __future__ import annotations

from polaris.core.pipeline.g6_call_gate import (
    DEFAULT_G6_COOLDOWN_SEC,
    DEFAULT_G6_MIN_TICKS,
    DEFAULT_G6_PRICE_DELTA_PCT,
    G6CallCache,
    G6CallContext,
    pnl_band,
    should_call_gpt,
)


def _ctx(
    *,
    price: float = 100.0,
    regime: str = "bull_trend",
    pnl_r: float = 0.3,
    now_ts: int = 1_000,
    tick_idx: int = 100,
) -> G6CallContext:
    return G6CallContext(
        price=price, regime=regime, pnl_r=pnl_r, now_ts=now_ts, tick_idx=tick_idx,
    )


# ---------------------------------------------------------------------------
# Cold cache — always call
# ---------------------------------------------------------------------------


def test_no_prior_entry_calls_gpt() -> None:
    cache = G6CallCache()
    assert should_call_gpt(cache, "pos-1", _ctx()) is True


# ---------------------------------------------------------------------------
# Cooldown not elapsed + context unchanged → skip
# ---------------------------------------------------------------------------


def test_within_cooldown_and_unchanged_context_skips() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(now_ts=1_000, tick_idx=100), decision="HOLD")
    # 30s later, +5 ticks, identical price/regime/pnl → both windows say "skip"
    nxt = _ctx(now_ts=1_030, tick_idx=105)
    assert should_call_gpt(cache, "pos-1", nxt) is False


# ---------------------------------------------------------------------------
# Cooldown elapsed → call (even if context unchanged)
# ---------------------------------------------------------------------------


def test_cooldown_elapsed_calls_gpt() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(now_ts=1_000, tick_idx=100), decision="HOLD")
    nxt = _ctx(now_ts=1_000 + DEFAULT_G6_COOLDOWN_SEC + 1, tick_idx=105)
    assert should_call_gpt(cache, "pos-1", nxt) is True


def test_min_ticks_elapsed_calls_gpt() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(now_ts=1_000, tick_idx=100), decision="HOLD")
    # time still inside cooldown, but enough ticks have passed
    nxt = _ctx(now_ts=1_010, tick_idx=100 + DEFAULT_G6_MIN_TICKS + 1)
    assert should_call_gpt(cache, "pos-1", nxt) is True


# ---------------------------------------------------------------------------
# Context change inside cooldown → call
# ---------------------------------------------------------------------------


def test_price_move_beyond_threshold_calls_gpt() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(price=100.0, now_ts=1_000, tick_idx=100), decision="HOLD")
    # price move larger than the delta threshold, still inside cooldown
    moved = 100.0 * (1.0 + DEFAULT_G6_PRICE_DELTA_PCT * 2.0)
    nxt = _ctx(price=moved, now_ts=1_005, tick_idx=101)
    assert should_call_gpt(cache, "pos-1", nxt) is True


def test_price_move_below_threshold_skips() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(price=100.0, now_ts=1_000, tick_idx=100), decision="HOLD")
    moved = 100.0 * (1.0 + DEFAULT_G6_PRICE_DELTA_PCT * 0.5)
    nxt = _ctx(price=moved, now_ts=1_005, tick_idx=101)
    assert should_call_gpt(cache, "pos-1", nxt) is False


def test_regime_change_calls_gpt() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(regime="bull_trend", now_ts=1_000, tick_idx=100), decision="HOLD")
    nxt = _ctx(regime="chop", now_ts=1_005, tick_idx=101)
    assert should_call_gpt(cache, "pos-1", nxt) is True


def test_pnl_band_change_calls_gpt() -> None:
    cache = G6CallCache()
    # band edges at multiples of 0.5R by default; cross from +0.3R band to +1.2R band
    cache.record("pos-1", _ctx(pnl_r=0.3, now_ts=1_000, tick_idx=100), decision="HOLD")
    nxt = _ctx(pnl_r=1.2, now_ts=1_005, tick_idx=101)
    assert should_call_gpt(cache, "pos-1", nxt) is True


def test_pnl_same_band_skips() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(pnl_r=0.10, now_ts=1_000, tick_idx=100), decision="HOLD")
    nxt = _ctx(pnl_r=0.40, now_ts=1_005, tick_idx=101)  # both in [0,0.5) band
    assert should_call_gpt(cache, "pos-1", nxt) is False


# ---------------------------------------------------------------------------
# pnl_band helper
# ---------------------------------------------------------------------------


def test_pnl_band_buckets() -> None:
    assert pnl_band(0.0) == pnl_band(0.49)
    assert pnl_band(0.0) != pnl_band(0.51)
    assert pnl_band(-0.2) != pnl_band(0.2)
    assert pnl_band(1.1) == pnl_band(1.4)


# ---------------------------------------------------------------------------
# Reuse — last decision retrievable
# ---------------------------------------------------------------------------


def test_last_decision_retrievable() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(), decision="ADJUST_EXIT")
    assert cache.last_decision("pos-1") == "ADJUST_EXIT"
    assert cache.last_decision("missing") is None


def test_record_updates_anchor() -> None:
    cache = G6CallCache()
    cache.record("pos-1", _ctx(price=100.0, now_ts=1_000, tick_idx=100), decision="HOLD")
    # after a real call we re-anchor; a tiny move from the NEW anchor skips
    cache.record("pos-1", _ctx(price=110.0, now_ts=1_200, tick_idx=140), decision="HOLD")
    # sub-threshold move from the NEW anchor (110.0) → skip
    moved = 110.0 * (1.0 + DEFAULT_G6_PRICE_DELTA_PCT * 0.5)
    nxt = _ctx(price=moved, now_ts=1_205, tick_idx=141)
    assert should_call_gpt(cache, "pos-1", nxt) is False


# ---------------------------------------------------------------------------
# Env-tunable defaults are sane
# ---------------------------------------------------------------------------


def test_defaults_positive() -> None:
    assert DEFAULT_G6_COOLDOWN_SEC > 0
    assert DEFAULT_G6_MIN_TICKS > 0
    assert DEFAULT_G6_PRICE_DELTA_PCT > 0.0
