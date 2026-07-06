"""OKX venue-resting conditional-stop arming (largest unaddressed loss hole).

The software stop fires only on the next ~5s recalc poll; an OKX SPOT alt can
gap through it in that window → a polled market close fills far below → the
-34..-100R orphan. ``arm_okx_venue_stop`` rests the FSM-computed stop AT the
venue so OKX triggers it the instant the trigger crosses (inter-tick gap closed).

PRECISE-EXIT loss-defence, NEVER a throttle: size + entry side untouched, this
only mirrors the per-position stop the FSM already computed. Fully fail-open —
a venue/algo failure keeps the existing software stop as the backstop.

All tests inject a fake adapter; no real venue network call ever happens.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from polaris.scripts._production_recalc_exit import arm_okx_venue_stop
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.venues.okx.adapter import OKXAlgoOrderResponse

NOW = 1_780_000_000


def _trade(position_id: str = "pos-1") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="ETH-USDT",
        strategy_id="vb", side="long", entry_price=100.0, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, base_qty=0.8,
    )


class _FakeOKX:
    """Records conditional-stop placements + cancels. ``ok``/``algo_id`` control
    the response; ``raise_exc`` simulates a transport blip."""

    def __init__(
        self, *, ok: bool = True, algo_id: str | None = "algo-1",
        raise_exc: bool = False,
    ) -> None:
        self._ok = ok
        self._algo_id = algo_id
        self._raise = raise_exc
        self.placements: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        self._n = 0

    async def place_conditional_stop(self, **kwargs: Any) -> OKXAlgoOrderResponse:
        if self._raise:
            raise RuntimeError("boom")
        self.placements.append(kwargs)
        self._n += 1
        aid = (
            None if not self._ok or self._algo_id is None
            else f"{self._algo_id}-{self._n}"
        )
        return OKXAlgoOrderResponse(
            ok=self._ok and aid is not None, algo_id=aid,
            client_order_id="cl", code="0" if self._ok else "51280", msg="",
            raw={},
        )

    async def cancel_algo_order(self, **kwargs: Any) -> dict[str, Any]:
        self.cancels.append(kwargs)
        return {"code": "0", "data": []}


@pytest.mark.asyncio
async def test_arm_places_resting_stop_on_stop_set() -> None:
    """First non-None stop → one resting conditional SELL placed at the trigger;
    the algoId + trigger px are tracked on the trade."""
    trade = _trade()
    okx = _FakeOKX()
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
    )
    assert len(okx.placements) == 1
    p = okx.placements[0]
    assert p["inst_id"] == "ETH-USDT"
    assert p["side"] == "sell"  # long position → protective stop is a SELL
    assert p["base_qty"] == pytest.approx(0.8)
    assert p["trigger_px"] == pytest.approx(98.5)
    assert trade.okx_stop_algo_id == "algo-1-1"
    assert trade.okx_stop_px == pytest.approx(98.5)


@pytest.mark.asyncio
async def test_arm_no_op_when_stop_unchanged() -> None:
    """A steady tick (stop did not tighten) is a no-op — no churn, no re-place."""
    trade = _trade()
    okx = _FakeOKX()
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert len(okx.placements) == 1  # second call no-ops
    assert len(okx.cancels) == 0


@pytest.mark.asyncio
async def test_arm_replaces_when_stop_tightens() -> None:
    """A materially-tightened stop (long → up) cancels the old resting order and
    places the new one (ratchet mirrored at the venue)."""
    trade = _trade()
    okx = _FakeOKX()
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    # Tighten well above the 5bps replace epsilon.
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=99.4)
    assert len(okx.placements) == 2
    assert len(okx.cancels) == 1
    assert okx.cancels[0]["algo_id"] == "algo-1-1"
    assert trade.okx_stop_algo_id == "algo-1-2"
    assert trade.okx_stop_px == pytest.approx(99.4)


@pytest.mark.asyncio
async def test_arm_fallback_when_algo_unavailable_keeps_software_stop() -> None:
    """Algo endpoint unavailable (ok=False) → no algoId tracked; the software
    stop remains the backstop (flow_not_block — never blocks the trade)."""
    trade = _trade()
    okx = _FakeOKX(ok=False, algo_id=None)
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert len(okx.placements) == 1
    assert trade.okx_stop_algo_id is None
    assert trade.okx_stop_px is None


@pytest.mark.asyncio
async def test_arm_unavailable_caches_and_skips_next_tick() -> None:
    """Algo unavailable for a symbol → the result is cached; subsequent ticks
    within the cooldown do NOT re-attempt the place (no per-tick arm spam). The
    software stop remains the backstop the whole time (fail-open unchanged)."""
    trade = _trade()
    okx = _FakeOKX(ok=False, algo_id=None)
    clock = {"t": 1000.0}
    # First tick: arms once → unavailable → caches.
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    # Many subsequent ticks within the cooldown (stop keeps tightening so the
    # tighten-gate would otherwise let each one through) — all skipped.
    for i in range(10):
        clock["t"] += 0.5  # ~2 ticks/sec, the observed spam cadence
        await arm_okx_venue_stop(
            trade=trade, okx_adapter=okx, side="long", stop_price=98.5 + i,
            now_monotonic=lambda: clock["t"],
        )
    assert len(okx.placements) == 1  # armed exactly once, no re-attempt spam
    assert trade.okx_stop_algo_id is None  # software stop still the backstop


@pytest.mark.asyncio
async def test_arm_retries_once_after_cooldown_expires() -> None:
    """After the cooldown window elapses the arm retries once (the algo endpoint
    may have recovered) — backoff, not a permanent give-up."""
    trade = _trade()
    okx = _FakeOKX(ok=False, algo_id=None)
    clock = {"t": 1000.0}
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    # Within cooldown → skipped.
    clock["t"] += 30.0
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=99.5,
        now_monotonic=lambda: clock["t"],
    )
    assert len(okx.placements) == 1
    # Past cooldown → retries once.
    clock["t"] += 10_000.0
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=99.5,
        now_monotonic=lambda: clock["t"],
    )
    assert len(okx.placements) == 2


@pytest.mark.asyncio
async def test_arm_cooldown_is_per_symbol_fresh_symbol_still_arms() -> None:
    """A fresh position/symbol carries no cooldown — it still arms (the cache is
    scoped to the trade whose algo was unavailable, not global)."""
    okx = _FakeOKX(ok=False, algo_id=None)
    clock = {"t": 1000.0}
    stale = _trade(position_id="pos-stale")
    await arm_okx_venue_stop(
        trade=stale, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    clock["t"] += 0.5
    fresh = _trade(position_id="pos-fresh")
    await arm_okx_venue_stop(
        trade=fresh, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    assert len(okx.placements) == 2  # fresh symbol still attempted


@pytest.mark.asyncio
async def test_arm_available_symbol_unaffected_by_cooldown() -> None:
    """An available symbol arms + replaces normally — the cooldown only gates the
    unavailable path, never the happy path (no behavior change for working algos)."""
    trade = _trade()
    okx = _FakeOKX()  # ok=True
    clock = {"t": 1000.0}
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    clock["t"] += 0.5
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=99.4,
        now_monotonic=lambda: clock["t"],
    )
    assert len(okx.placements) == 2  # tighten replace unaffected
    assert trade.okx_stop_algo_id == "algo-1-2"


@pytest.mark.asyncio
async def test_arm_clears_cooldown_after_success() -> None:
    """If the algo endpoint recovers and a place succeeds, the cached unavailable
    marker is cleared so the symbol returns to the normal tighten-driven path."""
    trade = _trade()
    okx = _FakeOKX(ok=False, algo_id=None)
    clock = {"t": 1000.0}
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: clock["t"],
    )
    # Endpoint recovers; advance past cooldown so the retry fires and succeeds.
    okx._ok = True
    okx._algo_id = "algo-1"
    clock["t"] += 10_000.0
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=99.5,
        now_monotonic=lambda: clock["t"],
    )
    assert trade.okx_stop_algo_id == "algo-1-2"  # 1st place was the unavail probe
    assert trade.okx_stop_px == pytest.approx(99.5)
    assert getattr(trade, "okx_stop_unavail_until", None) is None


@pytest.mark.asyncio
async def test_arm_transport_error_never_raises() -> None:
    """A venue/transport blip is swallowed (best-effort) — software stop holds."""
    trade = _trade()
    okx = _FakeOKX(raise_exc=True)
    # Must NOT raise.
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert trade.okx_stop_algo_id is None


@pytest.mark.asyncio
async def test_arm_skips_when_no_adapter_or_no_stop() -> None:
    """No adapter / no stop yet / non-okx venue → no-op (guards)."""
    trade = _trade()
    okx = _FakeOKX()
    await arm_okx_venue_stop(trade=trade, okx_adapter=None, side="long", stop_price=98.5)
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=None)
    cap_trade = _trade()
    cap_trade.venue = "capital"
    await arm_okx_venue_stop(trade=cap_trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert len(okx.placements) == 0


# ---------------------------------------------------------------------------
# 51068 orphan adoption — a restart leaves the prior run's stop resting on OKX
# with the SAME deterministic algoClOrdId, so the re-arm POST collides (51068)
# and the in-mem algoId was lost. Without recovery the arm loops 51068 forever
# and the venue stop stays FROZEN at the stale prior-run trigger, defeating the
# just-enabled G7 tighten rail. The arm must recover + ADOPT the resting algoId
# so the next tighten cancel-then-replaces it. Fail-open throughout.
# ---------------------------------------------------------------------------


class _FakeOKXOrphan:
    """A prior-run stop already rests on OKX (deterministic algoClOrdId). The
    first place COLLIDES (51068); a GET recovers the resting order; once the
    orphan is cancelled a later place succeeds (the self-heal path)."""

    def __init__(
        self, *, orphan_state: str = "live", fetch_ok: bool = True,
        orphan_px: str = "97.0",
    ) -> None:
        self.orphan_present = True
        self._orphan_state = orphan_state
        self._fetch_ok = fetch_ok
        self._orphan_px = orphan_px
        self.placements: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        self.fetches: list[dict[str, Any]] = []
        self._n = 0

    async def place_conditional_stop(self, **kwargs: Any) -> OKXAlgoOrderResponse:
        self.placements.append(kwargs)
        if self.orphan_present:
            return OKXAlgoOrderResponse(
                ok=False, algo_id=None, client_order_id="cl",
                code="51068", msg="algoClOrdId already exists", raw={},
            )
        self._n += 1
        return OKXAlgoOrderResponse(
            ok=True, algo_id=f"fresh-{self._n}", client_order_id="cl",
            code="0", msg="", raw={},
        )

    async def fetch_algo_order(self, **kwargs: Any) -> OKXAlgoOrderResponse:
        self.fetches.append(kwargs)
        if not self._fetch_ok or not self.orphan_present:
            return OKXAlgoOrderResponse(
                ok=False, algo_id=None, client_order_id=None,
                code="fetch_failed", msg="", raw={},
            )
        return OKXAlgoOrderResponse(
            ok=True, algo_id="orphan-9", client_order_id="cl", code="0", msg="",
            raw={"data": [{
                "algoId": "orphan-9", "slTriggerPx": self._orphan_px,
                "state": self._orphan_state,
            }]},
        )

    async def cancel_algo_order(self, **kwargs: Any) -> dict[str, Any]:
        self.cancels.append(kwargs)
        self.orphan_present = False  # cancelling the orphan clears the collision
        return {"code": "0", "data": []}


@pytest.mark.asyncio
async def test_arm_adopts_orphan_stop_on_51068() -> None:
    """A 51068 collision → recover the resting algoId via GET and ADOPT it (algoId
    + the orphan's actual trigger px), NOT back off to a cooldown."""
    trade = _trade()
    okx = _FakeOKXOrphan()
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert len(okx.placements) == 1       # the colliding place
    assert len(okx.fetches) == 1          # the recovery GET fired
    assert trade.okx_stop_algo_id == "orphan-9"       # resting algoId adopted
    assert trade.okx_stop_px == pytest.approx(97.0)   # orphan's real trigger adopted
    assert getattr(trade, "okx_stop_unavail_until", None) is None  # NOT backed off


@pytest.mark.asyncio
async def test_arm_51068_self_heals_on_next_tighten() -> None:
    """THE regression: after adopting, a tightened stop cancel-then-replaces the
    orphan — the 51068 loop is broken and the venue stop tracks the ratchet."""
    trade = _trade()
    okx = _FakeOKXOrphan()
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=98.5)
    assert trade.okx_stop_algo_id == "orphan-9"  # adopted at 97.0
    # Software stop tightens well above 97.0 → cancel orphan, place fresh (no collision).
    await arm_okx_venue_stop(trade=trade, okx_adapter=okx, side="long", stop_price=99.4)
    assert okx.cancels and okx.cancels[0]["algo_id"] == "orphan-9"
    assert trade.okx_stop_algo_id == "fresh-1"        # tighter stop now rests
    assert trade.okx_stop_px == pytest.approx(99.4)


@pytest.mark.asyncio
async def test_arm_51068_orphan_not_live_falls_back_to_software_stop() -> None:
    """A recovered order that is NOT 'live' (already canceled/triggered) is never
    adopted — back off + keep the software stop (never adopt a dead algoId)."""
    trade = _trade()
    okx = _FakeOKXOrphan(orphan_state="canceled")
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: 1000.0,
    )
    assert trade.okx_stop_algo_id is None
    assert getattr(trade, "okx_stop_unavail_until", None) == pytest.approx(1300.0)


@pytest.mark.asyncio
async def test_arm_51068_fetch_failure_falls_back_to_software_stop() -> None:
    """A failed recovery GET (fail-open) → no adoption, software stop backstop."""
    trade = _trade()
    okx = _FakeOKXOrphan(fetch_ok=False)
    await arm_okx_venue_stop(
        trade=trade, okx_adapter=okx, side="long", stop_price=98.5,
        now_monotonic=lambda: 1000.0,
    )
    assert trade.okx_stop_algo_id is None
    assert getattr(trade, "okx_stop_unavail_until", None) == pytest.approx(1300.0)
