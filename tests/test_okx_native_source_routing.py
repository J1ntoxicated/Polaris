"""① source-routing — BTC + top-N majors routed OKX-NATIVE (not Yahoo).

DEMO/PAPER only. flow_not_block: bar-HISTORY source only — the live entry/exit
price still rides the WS quote path (untouched). #21 regression guard: the OKX
candles bucket is unchanged (storm physically impossible), and ``POLARIS_OKX_
NATIVE_TOP_N`` is the instant kill-switch (0 → full #21-era Yahoo routing back).

Why: BTC/ETH/SOL... are verified-base, so ``map_to_yahoo_ticker`` sent them to
Yahoo ``BASE-USD`` (集計지연). Routing the top-N majors OKX-NATIVE gives the
exchange's own fresher candles. The TRAP this guards: the OKX-native fallback is
gated by a 300s cooldown — a naive None-return would STARVE BTC 1m to a 5-min
refetch (freshness REGRESSION). The preferred set must BYPASS that cooldown.

Verifies:
1. A preferred base → ``map_to_yahoo_ticker`` returns None (→ OKX-native path).
2. A NON-preferred verified base still maps to Yahoo (no over-reach).
3. ``POLARIS_OKX_NATIVE_TOP_N=0`` disables routing (instant #21 rollback).
4. The preferred set is a subset of the verified bases (never an unverified coin).
5. ``fetch_bars_one`` routes a preferred OKX symbol to the exchange even when the
   300s fallback cooldown is active (the freshness-regression trap is closed).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import polaris.scripts._yahoo_bars as ybars
from polaris.scripts._yahoo_bars import (
    _OKX_YAHOO_VERIFIED_BASES,
    map_to_yahoo_ticker,
    okx_native_preferred_bases,
    should_fetch_exchange_fallback,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: Any) -> Iterator[None]:
    ybars._YAHOO_TICKER_CACHE.clear()
    ybars._FALLBACK_LAST_MONO.clear()
    ybars._YF_FRAME_CACHE.clear()
    # Default the env to a non-zero top-N so routing is on for the assertions.
    monkeypatch.setenv("POLARIS_OKX_NATIVE_TOP_N", "30")
    ybars._refresh_okx_native_preferred()
    yield
    ybars._YAHOO_TICKER_CACHE.clear()
    ybars._FALLBACK_LAST_MONO.clear()
    ybars._YF_FRAME_CACHE.clear()
    # Restore the module default (OFF) so this file's env override never leaks
    # into another test file's expectations (the verified-base → Yahoo default).
    monkeypatch.undo()
    ybars._refresh_okx_native_preferred()


def test_preferred_base_routes_okx_native() -> None:
    """BTC (preferred) → None so the OKX-native fallback supplies the bars."""
    assert "BTC" in okx_native_preferred_bases()
    assert map_to_yahoo_ticker("okx", "BTC-USDT", "crypto") is None


def test_non_preferred_verified_base_still_yahoo() -> None:
    """A verified base OUTSIDE the top-N still maps to Yahoo (no over-reach)."""
    preferred = okx_native_preferred_bases()
    outside = next(b for b in sorted(_OKX_YAHOO_VERIFIED_BASES) if b not in preferred)
    assert map_to_yahoo_ticker("okx", f"{outside}-USDT", "crypto") == f"{outside}-USD"


def test_top_n_zero_disables_routing(monkeypatch: Any) -> None:
    """🚨 POLARIS_OKX_NATIVE_TOP_N=0 = instant #21 rollback (Yahoo for all)."""
    monkeypatch.setenv("POLARIS_OKX_NATIVE_TOP_N", "0")
    ybars._refresh_okx_native_preferred()
    assert okx_native_preferred_bases() == frozenset()
    # BTC now maps to Yahoo again (the pre-① behaviour).
    assert map_to_yahoo_ticker("okx", "BTC-USDT", "crypto") == "BTC-USD"


def test_preferred_is_subset_of_verified() -> None:
    """The native-preferred set can NEVER include an unverified (collision) coin."""
    assert okx_native_preferred_bases() <= _OKX_YAHOO_VERIFIED_BASES


def test_non_usd_quote_unaffected() -> None:
    """A non-USD-equiv quote still returns None (unchanged) — not a Yahoo series."""
    assert map_to_yahoo_ticker("okx", "BTC-BTC", "crypto") is None


@pytest.mark.asyncio
async def test_preferred_bypasses_fallback_cooldown(monkeypatch: Any) -> None:
    """🚨 THE TRAP: a preferred OKX symbol fetches the exchange even inside the
    300s fallback cooldown (a naive None-return would starve BTC 1m → freshness
    regression). Non-preferred symbols keep the cooldown (unmapped-tail storm guard).
    """
    from polaris.scripts import _production_bars as pbars

    ybars._FALLBACK_LAST_MONO.clear()

    fetched: list[str] = []

    async def _fake_yahoo(*a: Any, **k: Any) -> list[Any]:
        return []  # Yahoo never supplies a preferred symbol (it returns None ticker)

    async def _fake_okx(inst_id: str, **k: Any) -> list[Any]:
        fetched.append(inst_id)
        return []

    monkeypatch.setattr(pbars, "fetch_yahoo_bars", _fake_yahoo)
    monkeypatch.setattr(pbars, "fetch_okx_bars", _fake_okx)

    # Prime the cooldown so a NON-preferred symbol would be blocked right now.
    pbars.should_fetch_exchange_fallback("okx", "BTC-USDT", 1000.0)

    # A preferred symbol must STILL reach the OKX fetch despite the cooldown.
    await pbars.fetch_bars_one(
        "okx", "BTC-USDT", "crypto", bar_interval="1m",
    )
    assert "BTC-USDT" in fetched


def test_fallback_cooldown_unchanged_for_unmapped() -> None:
    """Sanity: the cooldown helper itself is byte-identical (storm guard intact)."""
    assert should_fetch_exchange_fallback("okx", "ZZZ-USDT", 0.0) is True
    assert should_fetch_exchange_fallback("okx", "ZZZ-USDT", 10.0) is False  # in window
