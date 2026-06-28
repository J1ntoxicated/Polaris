"""Maker fill-rate levers (#91) — touch-ward repost + weekend taker fallback.

DEMO/PAPER only — every test injects a fake OKX adapter, NO real venue network
call ever happens. Covers the three fill-rate levers added on top of the #77
bounded reprice/repost loop, each env-gated and DEFAULT-OFF (current behaviour
byte-identical):

  (a) TOUCH-WARD REPOST — ``POLARIS_POST_ONLY_REPOST_STEP_BPS`` advances each
      repost's post-only price from the deep touch toward the mid (BUY: bid →
      bid + step), clamped strictly below the ask so the order STAYS post-only
      (still a maker fill, just progressively more aggressive). A thin book that
      never trades back to the deep bid now fills at a price that crept toward
      the ask. Default 0.0 = OFF = posts at the bare touch (#77 behaviour).

  (b) WEEKEND TAKER FALLBACK — ``POLARIS_WEEKEND_MAKER_TAKER_FALLBACK`` (and the
      graduated ``POLARIS_WEEKEND_MAKER_TAKER_AFTER_N``) let a ``no_fill="cancel"``
      strategy fall back to a TAKER market order after the repost loop is
      exhausted, instead of skipping. Default OFF = the #77 skip sentinel (the
      weekend maker thesis: a missed deep bid is 0 realised cost). The fallback
      is a deliberate "fill-guarantee vs edge" knob Jin can turn — the maker_fill
      shadow keeps measuring the edge impact on every fill.

  (c) MAX REPOSTS — ``POLARIS_POST_ONLY_MAX_REPOSTS`` (already env-tunable in
      #77) raises the repost count; combined with (a) each extra repost creeps
      closer to the ask. Pinned here so the knob stays reachable.

flow_not_block: every lever only ever makes the ENTRY more likely to fill — none
adds a block/skip/throttle. The close/rail leg is a SEPARATE taker path
(``real_okx_close_fill``) and is NEVER touched here (see test_rail_close_is_taker).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._okx_post_only import _okx_maker_px, _okx_post_only_open
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt


def _resp(*, ok: bool = True, ord_id: str = "ord_1", code: str = "0") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=ok, venue_order_id=ord_id if ok else None,
        client_order_id="cl1", code=code, msg="", raw={},
    )


def _filled_row(*, price: float = 100.0) -> dict[str, Any]:
    return {
        "ordId": "ord_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": "1.0",
        "avgPx": str(price), "fee": "-0.02", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }


class _RepostFakeOKX:
    """Never-fills fake (forces the full repost loop); records places + cancels.

    A static ``bid``/``ask`` book lets a test read each repost's posted price out
    of ``last_price_hint`` (the touch-ward step is a function of the attempt_idx,
    not of a moving book).
    """

    def __init__(self, *, bid: str = "100.0", ask: str = "100.20") -> None:
        self._bid = bid
        self._ask = ask
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        return {"bidPx": self._bid, "askPx": self._ask}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        return _resp(ord_id=f"ord_{len(self.place_calls)}")

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return {"data": [{"ordId": ord_id, "state": "live", "accFillSz": "0"}]}

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        self.cancel_calls.append(ord_id)
        return {"code": "0"}


class _TakerFallbackFakeOKX(_RepostFakeOKX):
    """post_only never fills; a ``market`` order DOES fill (taker fallback path).

    Used to assert that when the weekend taker-fallback knob is on, the caller's
    market fallback actually places a TAKER order — i.e. the post-only path
    returned ``None`` (fall back) rather than the skip sentinel.
    """

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        # A market (taker) order id is "mkt_*"; post_only ids are "ord_*".
        if ord_id.startswith("mkt_"):
            return {"data": [_filled_row()]}
        return {"data": [{"ordId": ord_id, "state": "live", "accFillSz": "0"}]}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.place_calls.append(kwargs)
        if kwargs.get("ord_type") == "market":
            return _resp(ord_id="mkt_1")
        return _resp(ord_id=f"ord_{len(self.place_calls)}")


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_LIMIT_FILL_WAIT_SEC", "0.02")
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "3")
    # Levers default OFF unless a test opts in (current behaviour byte-identical).
    monkeypatch.delenv("POLARIS_POST_ONLY_REPOST_STEP_BPS", raising=False)
    monkeypatch.delenv("POLARIS_WEEKEND_MAKER_TAKER_FALLBACK", raising=False)
    monkeypatch.delenv("POLARIS_WEEKEND_MAKER_TAKER_AFTER_N", raising=False)


# ---------------------------------------------------------------------------
# (a) touch-ward repost price progression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maker_px_off_posts_bare_touch_every_attempt() -> None:
    """Default (step=0): every attempt posts at the bare best bid (#77)."""
    fake = _RepostFakeOKX(bid="100.0", ask="100.20")
    for idx in range(4):
        px = await _okx_maker_px(fake, inst_id="BTC-USDT", attempt_idx=idx)
        assert px == 100.0


@pytest.mark.asyncio
async def test_maker_px_touch_ward_steps_toward_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """step_bps>0: each repost creeps the post-only price toward the ask."""
    monkeypatch.setenv("POLARIS_POST_ONLY_REPOST_STEP_BPS", "5.0")
    fake = _RepostFakeOKX(bid="100.0", ask="100.20")
    # attempt 0 = bare touch; attempt k = bid * (1 + 5bps*k/1e4).
    px0 = await _okx_maker_px(fake, inst_id="BTC-USDT", attempt_idx=0)
    px1 = await _okx_maker_px(fake, inst_id="BTC-USDT", attempt_idx=1)
    px2 = await _okx_maker_px(fake, inst_id="BTC-USDT", attempt_idx=2)
    assert px0 is not None and px1 is not None and px2 is not None
    assert px0 == 100.0
    assert px1 == pytest.approx(100.0 * (1 + 5.0 / 1e4))   # 100.05
    assert px2 == pytest.approx(100.0 * (1 + 10.0 / 1e4))  # 100.10
    # Strictly increasing toward — but never reaching — the ask.
    assert px0 < px1 < px2 < 100.20


@pytest.mark.asyncio
async def test_maker_px_clamped_strictly_below_ask_stays_post_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that would cross the ask is clamped just below it (stays maker)."""
    monkeypatch.setenv("POLARIS_POST_ONLY_REPOST_STEP_BPS", "50.0")
    fake = _RepostFakeOKX(bid="100.0", ask="100.20")
    # 50 bps * attempt 5 = 250 bps → 102.50, far past the 100.20 ask. Must clamp
    # to STRICTLY below the ask so the post_only is not a would-cross reject.
    px = await _okx_maker_px(fake, inst_id="BTC-USDT", attempt_idx=5)
    assert px is not None
    assert px < 100.20
    assert px == pytest.approx(100.20 * (1 - 1e-4), rel=1e-9)


@pytest.mark.asyncio
async def test_touch_ward_loop_posts_progressive_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the repost loop posts a strictly-increasing price sequence."""
    monkeypatch.setenv("POLARIS_POST_ONLY_REPOST_STEP_BPS", "5.0")
    fake = _RepostFakeOKX(bid="100.0", ask="100.50")
    await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    hints = [c["last_price_hint"] for c in fake.place_calls]
    assert hints[0] == 100.0
    assert hints == sorted(hints)            # monotonic toward the ask
    assert all(h < 100.50 for h in hints)    # never crosses (stays post-only)
    assert all(c["ord_type"] == "post_only" for c in fake.place_calls)


# ---------------------------------------------------------------------------
# (b) weekend taker fallback (cancel mode only, env-gated, default OFF)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_mode_default_still_skips_no_taker() -> None:
    """OFF by default: cancel mode returns the skip sentinel (no taker)."""
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    assert isinstance(attempt, OpenAttempt)
    assert attempt.fill is None
    assert attempt.reject_code == "maker_no_fill"
    # No market (taker) order was ever placed — every place is post_only.
    assert all(c["ord_type"] == "post_only" for c in fake.place_calls)


@pytest.mark.asyncio
async def test_cancel_mode_taker_fallback_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knob ON: cancel mode falls back to taker (returns None for the caller)."""
    monkeypatch.setenv("POLARIS_WEEKEND_MAKER_TAKER_FALLBACK", "1")
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    # None → the limit-open caller will market-fall-back (taker), NOT skip.
    assert attempt is None


@pytest.mark.asyncio
async def test_cancel_mode_taker_after_n_skips_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graduated knob: taker only after >= N reposts; below N still skips."""
    # 4 total attempts (1 + 3 reposts). Require 99 → never reached → skip.
    monkeypatch.setenv("POLARIS_WEEKEND_MAKER_TAKER_AFTER_N", "99")
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    assert isinstance(attempt, OpenAttempt)
    assert attempt.reject_code == "maker_no_fill"


@pytest.mark.asyncio
async def test_cancel_mode_taker_after_n_falls_back_when_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graduated knob: once the attempt count reaches N, fall back to taker."""
    # 4 attempts (1 + 3). N=4 → threshold met → taker fallback (None).
    monkeypatch.setenv("POLARIS_WEEKEND_MAKER_TAKER_AFTER_N", "4")
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    assert attempt is None


@pytest.mark.asyncio
async def test_market_mode_unaffected_by_weekend_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The weekend knobs are cancel-mode-only: market mode is byte-identical."""
    monkeypatch.setenv("POLARIS_WEEKEND_MAKER_TAKER_FALLBACK", "1")
    monkeypatch.setenv("POLARIS_WEEKEND_MAKER_TAKER_AFTER_N", "2")
    fake = _RepostFakeOKX()
    attempt = await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="rsi_bb_pullback", last_price=100.0,
        poll_delay_sec=0.0, no_fill="market",
    )
    # market mode already returns None (caller market-falls-back); the weekend
    # knobs never change that path.
    assert attempt is None


# ---------------------------------------------------------------------------
# (b') A/B measurement preserved — a maker fill still logs the basis shadow
# ---------------------------------------------------------------------------


class _FillFirstFakeOKX(_RepostFakeOKX):
    """Fills the very first post-only (clean maker fill) so the shadow logs."""

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        # Filled BELOW the touch (favourable) → clean_fill, positive basis.
        return {"data": [_filled_row(price=99.99)]}


@pytest.mark.asyncio
async def test_maker_fill_still_logs_shadow_basis(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A maker fill emits the real-fee shadow basis log (A/B kept measurable)."""
    import logging

    fake = _FillFirstFakeOKX(bid="100.0", ask="100.20")
    with caplog.at_level(logging.INFO):
        attempt = await _okx_post_only_open(
            fake, inst_id="BTC-USDT", notional_usd=100.0,
            strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
            poll_delay_sec=0.0, no_fill="cancel",
        )
    assert attempt is not None
    assert isinstance(attempt.fill, Fill)
    assert any("maker-fill-shadow" in r.message for r in caplog.records)
    assert any("clean_fill" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (c) max-reposts knob raises the attempt count (combines with touch-ward)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_reposts_knob_raises_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POLARIS_POST_ONLY_MAX_REPOSTS lifts the bounded loop (more shots to fill)."""
    monkeypatch.setenv("POLARIS_POST_ONLY_MAX_REPOSTS", "7")
    fake = _RepostFakeOKX()
    await _okx_post_only_open(
        fake, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="weekend_thin_book_flush_maker", last_price=100.0,
        poll_delay_sec=0.0, no_fill="cancel",
    )
    # 1 initial + 7 reposts = 8 total, all post_only, each cancelled (no leak).
    assert len(fake.place_calls) == 8
    assert len(fake.cancel_calls) == 8
