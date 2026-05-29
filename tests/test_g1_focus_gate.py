"""G1-EFF — G1 Universe Scanner GPT call-efficiency gate (on-change-only).

Spec / mandate (Jin 2026-05-30):
- DEMO/PAPER virtual capital. EFFICIENCY only — this gate cuts the dominant GPT
  cost (G1 = 56.5% of all GPT spend, ~876 calls/4.1h, structurally ALWAYS PASS)
  WITHOUT changing what GPT decides. The focus list is still GPT-chosen; only the
  call FREQUENCY drops (reuse the cached focus when the universe composition is
  materially unchanged — the proven g6_call_gate cooldown+delta pattern).
- AGGRESSIVE bias untouched: G1 STILL always PASS (it is a focus SELECTOR, not a
  KILL gate). No entry blocked, no size cut. Conservative: when in doubt → call
  GPT (never starve the focus list).

These tests pin the *pure* gating logic in ``g1_focus_gate`` — the
``universe_scanner_gate`` integration (cached focus reuse / prompt cap) is
covered in test_layer2_gpt_gates.
"""

from __future__ import annotations

from typing import Any

from polaris.core.pipeline.g1_focus_gate import (
    DEFAULT_G1_COOLDOWN_SEC,
    DEFAULT_G1_TURNOVER_THRESHOLD,
    DEFAULT_G1_TURNOVER_TOP_K,
    G1FocusCache,
    composition_fingerprint,
    should_call_gpt_g1,
)


def _universe(symbols: list[str], *, venue: str = "okx") -> list[dict[str, Any]]:
    """Build a universe with descending volume so top-K order is deterministic."""
    n = len(symbols)
    return [
        {"venue": venue, "symbol": s, "vol_24h_usd": float((n - i) * 1_000_000)}
        for i, s in enumerate(symbols)
    ]


# Universe larger than the top-K head so top-K is a true (churn-able) subset.
_BASE = [f"T{i:03d}-USDT" for i in range(80)]


# ---------------------------------------------------------------------------
# Cold cache — always call (never starve the focus)
# ---------------------------------------------------------------------------


def test_no_prior_entry_calls_gpt() -> None:
    cache = G1FocusCache()
    assert should_call_gpt_g1(cache, _universe(_BASE), now_ts=1_000) is True


# ---------------------------------------------------------------------------
# Unchanged composition inside cooldown → skip (reuse cached focus)
# ---------------------------------------------------------------------------


def test_unchanged_universe_inside_cooldown_skips() -> None:
    cache = G1FocusCache()
    uni = _universe(_BASE)
    cache.record(uni, focus=["okx:T000-USDT"], now_ts=1_000)
    # Identical composition, 10s later (inside cooldown) → reuse cached focus.
    assert should_call_gpt_g1(cache, uni, now_ts=1_010) is False


# ---------------------------------------------------------------------------
# Cooldown elapsed → call even if composition unchanged
# ---------------------------------------------------------------------------


def test_cooldown_elapsed_calls_gpt() -> None:
    cache = G1FocusCache()
    uni = _universe(_BASE)
    cache.record(uni, focus=["okx:T000-USDT"], now_ts=1_000)
    later = 1_000 + DEFAULT_G1_COOLDOWN_SEC + 1
    assert should_call_gpt_g1(cache, uni, now_ts=int(later)) is True


# ---------------------------------------------------------------------------
# New listing → material change → call (never miss a fresh ticker)
# ---------------------------------------------------------------------------


def test_new_listing_calls_gpt() -> None:
    cache = G1FocusCache()
    cache.record(_universe(_BASE), focus=["okx:T000-USDT"], now_ts=1_000)
    # A brand-new symbol appears in the universe — must re-decide the focus.
    changed = _universe([*_BASE, "NEWCOIN-USDT"])
    assert should_call_gpt_g1(cache, changed, now_ts=1_010) is True


# ---------------------------------------------------------------------------
# Top-K membership turnover beyond threshold → call
# ---------------------------------------------------------------------------


def test_topk_turnover_beyond_threshold_calls_gpt() -> None:
    cache = G1FocusCache()
    cache.record(_universe(_BASE), focus=["okx:T000-USDT"], now_ts=1_000)
    # Reorder so a large fraction of the top-K membership churns out: move the
    # bottom symbols to the top by volume. Same SET, different top-K membership.
    rotated = list(reversed(_BASE))
    assert (
        should_call_gpt_g1(cache, _universe(rotated), now_ts=1_010) is True
    )


def test_topk_minor_turnover_below_threshold_skips() -> None:
    cache = G1FocusCache()
    cache.record(_universe(_BASE), focus=["okx:T000-USDT"], now_ts=1_000)
    # Same membership SET; swap the volume rank of the boundary pair (index
    # k-1 ⇄ k) so exactly ONE symbol churns out of the top-K (1/40 = 2.5%,
    # below the 25% threshold) → reuse the cached focus.
    minor = list(_BASE)
    k = DEFAULT_G1_TURNOVER_TOP_K
    minor[k - 1], minor[k] = minor[k], minor[k - 1]
    assert should_call_gpt_g1(cache, _universe(minor), now_ts=1_010) is False


# ---------------------------------------------------------------------------
# Empty / shrinking universe handled conservatively (call)
# ---------------------------------------------------------------------------


def test_removed_listing_calls_gpt() -> None:
    cache = G1FocusCache()
    cache.record(_universe(_BASE), focus=["okx:T000-USDT"], now_ts=1_000)
    smaller = _universe(_BASE[:-1])  # one symbol delisted
    assert should_call_gpt_g1(cache, smaller, now_ts=1_010) is True


# ---------------------------------------------------------------------------
# composition_fingerprint — order-independent membership identity
# ---------------------------------------------------------------------------


def test_fingerprint_order_independent() -> None:
    a = composition_fingerprint(_universe(_BASE))
    b = composition_fingerprint(_universe(list(reversed(_BASE))))
    # Same SET of symbols → same membership fingerprint regardless of order.
    assert a.symbols == b.symbols


def test_fingerprint_distinguishes_membership() -> None:
    a = composition_fingerprint(_universe(_BASE))
    b = composition_fingerprint(_universe([*_BASE, "NEWCOIN-USDT"]))
    assert a.symbols != b.symbols


# ---------------------------------------------------------------------------
# Cache reuse — last focus retrievable
# ---------------------------------------------------------------------------


def test_last_focus_retrievable() -> None:
    cache = G1FocusCache()
    cache.record(_universe(_BASE), focus=["okx:BTC-USDT", "okx:ETH-USDT"], now_ts=1_000)
    assert cache.last_focus() == ["okx:BTC-USDT", "okx:ETH-USDT"]


def test_last_focus_none_when_empty() -> None:
    assert G1FocusCache().last_focus() is None


# ---------------------------------------------------------------------------
# Env-tunable defaults are sane
# ---------------------------------------------------------------------------


def test_defaults_positive() -> None:
    assert DEFAULT_G1_COOLDOWN_SEC > 0
    assert 0.0 < DEFAULT_G1_TURNOVER_THRESHOLD <= 1.0
    assert DEFAULT_G1_TURNOVER_TOP_K > 0
