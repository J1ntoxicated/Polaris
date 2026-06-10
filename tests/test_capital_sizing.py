"""Bug C — T4 notional → per-epic Capital lot wiring (DEMO/PAPER paper bot).

The translation EXPRESSES the T4 output at the venue (floor-to-step ≤ T4 ask;
a sub-min ask ceils to the venue minimum — floor expression, never a skip) and
every degraded path keeps the CURRENT legacy 1-lot behaviour so entries always
flow (flow_not_block). No sizing multiplier is added anywhere (9-stack intact).

Constraint fixtures are REAL Capital demo payload captures
(tests/fixtures/capital_markets — RO GET probe 2026-06-11, no orders): they pin
``size`` = underlying units (lotSize=1, valueOfOnePip absent), per-epic
minDealSize (GOLD 0.01 / J225 0.1 / EURUSD 100) and the quote ccy living in
``instrument.currency`` (J225 = JPY).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from polaris.core.data.fill_normalizer import normalize_capital_confirm
from polaris.scripts._production_capital_sizing import (
    CapitalConstraintCache,
    capital_close_contract_factor,
    maybe_evict_on_reject,
    translate_capital_order,
)
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies.base import RawSignal
from polaris.venues.capital.constraint_translator import (
    CapitalMarketConstraint,
    payload_to_constraint,
    size_usd_to_lots,
    unit_notional_usd,
)

FIXTURES = Path(__file__).parent / "fixtures" / "capital_markets"


def _fixture_body(epic: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{epic}.json").read_text())


def _fixture_constraint(epic: str) -> CapitalMarketConstraint:
    return payload_to_constraint(epic, _fixture_body(epic))


def _constraint(**overrides: Any) -> CapitalMarketConstraint:
    base: dict[str, Any] = {
        "epic": "X", "instrument_type": "COMMODITIES", "min_deal_size": 0.01,
        "step_size": 0.01, "decimal_places": 2, "leverage": 20.0,
        "pip_value_usd": 0.0, "base_ccy": "", "quote_ccy": "USD",
    }
    base.update(overrides)
    return CapitalMarketConstraint(**base)


class _StubSession:
    """Path-aware stub of ``CapitalSession.request`` (GET /markets/{epic})."""

    def __init__(
        self,
        bodies: dict[str, dict[str, Any]] | None = None,
        *,
        exc: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.bodies = bodies or {}
        self.exc = exc
        self.gate = gate
        self.calls = 0

    async def request(self, method: str, path: str, **_kw: Any) -> Any:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.exc is not None:
            raise self.exc
        body = self.bodies.get(path.rsplit("/", 1)[-1])
        if body is None:
            return SimpleNamespace(status_code=404, json=lambda: {})
        return SimpleNamespace(status_code=200, json=lambda: body)


def _seed_bar(
    conn: sqlite3.Connection, *, instrument_id: str, close: float, ts: int
) -> None:
    venue, symbol = instrument_id.split(":", 1)
    conn.execute(
        "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
        " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
        " notional_usd, trade_count, vwap, bid_close, ask_close, "
        " spread_bps_close, source) "
        "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 1.0, 1.0, 0, 0, 0, 0, 0, 't')",
        (instrument_id, f"fx:{symbol}", venue, symbol, ts, close, close, close,
         close),
    )


# ---------------------------------------------------------------------------
# Real-payload parsing (fixtures pin the venue semantics).
# ---------------------------------------------------------------------------


def test_real_payload_parse_gold() -> None:
    c = _fixture_constraint("GOLD")
    assert c.min_deal_size == pytest.approx(0.01)
    assert c.step_size == pytest.approx(0.01)
    assert c.quote_ccy == "USD"
    assert c.lot_size == pytest.approx(1.0)
    snap = _fixture_body("GOLD")["snapshot"]
    assert c.snapshot_mid == pytest.approx((snap["bid"] + snap["offer"]) / 2.0)


def test_real_payload_parse_j225_jpy_quote() -> None:
    c = _fixture_constraint("J225")
    assert c.quote_ccy == "JPY"
    assert c.min_deal_size == pytest.approx(0.1)
    assert c.lot_size == pytest.approx(1.0)


def test_real_payload_parse_eurusd_min_deal_above_one() -> None:
    # min_deal_size > 1.0 epic — the prior hardcoded 1.0-lot submit was BELOW
    # the venue minimum here (every order venue-rejected, never filled).
    c = _fixture_constraint("EURUSD")
    assert c.min_deal_size == pytest.approx(100.0)
    assert c.quote_ccy == "USD"


# ---------------------------------------------------------------------------
# step_size = dealingRules.minSizeIncrement (the SIZE increment) — NOT
# minStepDistance, which is a stop/limit PRICE distance. The two diverge on
# 5/6 live captures (GOLD is the only coincidental match, which is why a
# GOLD-only integration test could not see this). Blocker round 1.
# ---------------------------------------------------------------------------

# (epic, dealingRules.minSizeIncrement, dealingRules.minStepDistance)
_SIZE_INCREMENTS: list[tuple[str, float, float]] = [
    ("EURUSD", 100.0, 1e-05),
    ("GOLD", 0.01, 0.01),
    ("J225", 0.01, 0.1),
    ("NATURALGAS", 0.1, 0.0001),
    ("OIL_CRUDE", 0.1, 0.001),
    ("US500", 0.01, 0.1),
]


@pytest.mark.parametrize(("epic", "increment", "_distance"), _SIZE_INCREMENTS)
def test_step_size_is_min_size_increment(
    epic: str, increment: float, _distance: float
) -> None:
    body = _fixture_body(epic)
    # Guard the fixture itself: both keys present, value pinned.
    assert body["dealingRules"]["minSizeIncrement"]["value"] == pytest.approx(
        increment
    )
    assert _fixture_constraint(epic).step_size == pytest.approx(increment)


def test_step_size_fallback_chain_without_min_size_increment() -> None:
    # Payloads that omit ``minSizeIncrement`` keep the existing fallback
    # chain: minStepDistance, then instrument.lotSize.
    body = _fixture_body("GOLD")
    del body["dealingRules"]["minSizeIncrement"]
    body["dealingRules"]["minStepDistance"]["value"] = 0.5
    assert payload_to_constraint("GOLD", body).step_size == pytest.approx(0.5)
    del body["dealingRules"]["minStepDistance"]
    body["instrument"]["lotSize"] = 2.0
    assert payload_to_constraint("GOLD", body).step_size == pytest.approx(2.0)


def _fixture_mid(epic: str) -> float:
    snap = _fixture_body(epic)["snapshot"]
    return (float(snap["bid"]) + float(snap["offer"])) / 2.0


@pytest.mark.parametrize(("epic", "increment", "_distance"), _SIZE_INCREMENTS)
def test_lots_align_to_min_size_increment_all_fixtures(
    epic: str, increment: float, _distance: float
) -> None:
    """T4 $1,743 at the captured snapshot mid → lots land ON the venue size
    grid (exact multiple of minSizeIncrement) for every probed epic."""
    c = _fixture_constraint(epic)
    rate = 1.0 / 160.0 if c.quote_ccy == "JPY" else 1.0
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid(epic),
        quote_usd_rate=rate,
    )
    assert lots > 0.0
    from decimal import Decimal

    assert Decimal(str(lots)) % Decimal(str(increment)) == 0
    assert lots >= c.min_deal_size


# ---------------------------------------------------------------------------
# unit_notional_usd — exposure of one size unit; leverage is inert.
# ---------------------------------------------------------------------------


def test_unit_notional_usd_quote_and_leverage_inert() -> None:
    c = _fixture_constraint("GOLD")
    base = unit_notional_usd(constraint=c, last_price=4000.0)
    assert base == pytest.approx(4000.0)
    juiced = replace(c, leverage=500.0)
    assert unit_notional_usd(constraint=juiced, last_price=4000.0) == pytest.approx(
        base
    )


def test_unit_notional_non_usd_quote() -> None:
    c = _fixture_constraint("J225")
    unit = unit_notional_usd(
        constraint=c, last_price=64_000.0, quote_usd_rate=1.0 / 160.0
    )
    assert unit == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# size_usd_to_lots v2 — floor-to-step / min-deal ceil / payload-defect edges.
# ---------------------------------------------------------------------------


def test_lots_floor_to_step_never_exceeds_t4() -> None:
    c = _fixture_constraint("GOLD")  # min 0.01 / step 0.01
    lots = size_usd_to_lots(constraint=c, notional_usd=1743.0, last_price=4164.0)
    assert lots == pytest.approx(0.41)
    assert lots * 4164.0 <= 1743.0


def test_lots_sub_min_ceils_to_min() -> None:
    c = _fixture_constraint("GOLD")
    lots = size_usd_to_lots(constraint=c, notional_usd=20.0, last_price=4000.0)
    assert lots == pytest.approx(0.01)  # raw 0.005 → venue floor expression


def test_lots_floor_below_min_resurfaces_at_min() -> None:
    # Misaligned step: raw 0.35 → floor(0.25) < min 0.3 → ceil-to-step(min)=0.5.
    c = _constraint(min_deal_size=0.3, step_size=0.25)
    lots = size_usd_to_lots(constraint=c, notional_usd=35.0, last_price=100.0)
    assert lots == pytest.approx(0.5)


def test_lots_min_zero_floor_zero_takes_one_step() -> None:
    c = _constraint(min_deal_size=0.0, step_size=0.1)
    lots = size_usd_to_lots(constraint=c, notional_usd=4.0, last_price=100.0)
    assert lots == pytest.approx(0.1)


def test_lots_step_zero_passes_raw() -> None:
    c = _constraint(min_deal_size=1.0, step_size=0.0)
    lots = size_usd_to_lots(constraint=c, notional_usd=270.0, last_price=100.0)
    assert lots == pytest.approx(2.7)


def test_lots_huge_notional() -> None:
    c = _fixture_constraint("EURUSD")  # min 100 / step 100 (minSizeIncrement)
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1_000_000.0, last_price=1.25
    )
    assert lots == pytest.approx(800_000.0)


# Blocker round 1 — pinned reproductions: with step mis-parsed from
# minStepDistance these came out OFF the venue size grid (EURUSD 1508.10509 /
# OIL_CRUDE 19.676 / NATURALGAS 536.3241 → above-min venue rejects), while
# J225/US500 were over-coarse (step 0.1 instead of 0.01 → up to one full step
# of T4 under-expression, ~$739 on US500).


def test_lots_eurusd_misaligned_repro_floors_to_100() -> None:
    c = _fixture_constraint("EURUSD")
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid("EURUSD")
    )
    assert lots == pytest.approx(1500.0)  # raw 1508.105 → 100-grid floor


def test_lots_oil_crude_misaligned_repro_floors_to_tenth() -> None:
    c = _fixture_constraint("OIL_CRUDE")
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid("OIL_CRUDE")
    )
    assert lots == pytest.approx(19.6)  # raw 19.6767 → 0.1-grid floor


def test_lots_naturalgas_misaligned_repro_floors_to_tenth() -> None:
    c = _fixture_constraint("NATURALGAS")
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid("NATURALGAS")
    )
    assert lots == pytest.approx(536.3)  # raw 536.3242 → 0.1-grid floor


def test_lots_us500_finer_step_reduces_under_expression() -> None:
    c = _fixture_constraint("US500")
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid("US500")
    )
    assert lots == pytest.approx(0.23)  # raw 0.23587; old 0.1 step gave 0.2


def test_lots_j225_finer_step_reduces_under_expression() -> None:
    c = _fixture_constraint("J225")
    lots = size_usd_to_lots(
        constraint=c, notional_usd=1743.0, last_price=_fixture_mid("J225"),
        quote_usd_rate=1.0 / 160.0,
    )
    assert lots == pytest.approx(4.31)  # raw 4.31419; old 0.1 step gave 4.3


# ---------------------------------------------------------------------------
# normalize_capital_confirm — contract_factor path vs legacy byte-identity.
# ---------------------------------------------------------------------------


def _confirm_payload(size: float, level: float) -> dict[str, Any]:
    return {
        "epic": "GOLD", "direction": "BUY", "level": level, "size": size,
        "status": "OPEN", "dealStatus": "ACCEPTED", "dealId": "d1",
        "date": "2026-06-11T00:00:00Z",
    }


def test_normalize_contract_factor_excludes_leverage() -> None:
    fill = normalize_capital_confirm(
        _confirm_payload(0.41, 4164.0), strategy_id="s", pip_value_usd=10.0,
        leverage=20.0, contract_factor_usd=1.0,
    )
    assert fill.size_usd == pytest.approx(0.41 * 4164.0)
    assert fill.base_qty == pytest.approx(0.41)


def test_normalize_without_factor_keeps_legacy_formula() -> None:
    fill = normalize_capital_confirm(
        _confirm_payload(1.0, 4164.0), strategy_id="s", pip_value_usd=10.0,
        leverage=20.0,
    )
    assert fill.size_usd == pytest.approx(200.0)  # the live $200.00 reproduction


# ---------------------------------------------------------------------------
# CapitalConstraintCache — TTL / stale-serve / cooldown / single-flight / evict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_ttl_reuse_no_refetch() -> None:
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    cache = CapitalConstraintCache()
    c1 = await cache.get(sess, "GOLD")
    c2 = await cache.get(sess, "GOLD")
    assert c1 is not None and c2 is c1
    assert sess.calls == 1


@pytest.mark.asyncio
async def test_cache_stale_serve_on_refresh_failure() -> None:
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    cache = CapitalConstraintCache()
    c1 = await cache.get(sess, "GOLD")
    assert c1 is not None
    # Expire the entry, then make the venue fail → STALE is served.
    constraint, _ = cache._entries["GOLD"]
    cache._entries["GOLD"] = (constraint, time.monotonic() - 7200.0)
    fail = _StubSession(exc=RuntimeError("boom"))
    assert await cache.get(fail, "GOLD") is c1
    assert fail.calls == 1
    # The failure also arms the cooldown — the next order does NOT re-fetch.
    assert await cache.get(fail, "GOLD") is c1
    assert fail.calls == 1


@pytest.mark.asyncio
async def test_cache_miss_failure_none_plus_cooldown() -> None:
    fail = _StubSession(exc=RuntimeError("boom"))
    cache = CapitalConstraintCache()
    assert await cache.get(fail, "GOLD") is None
    assert await cache.get(fail, "GOLD") is None
    assert fail.calls == 1  # 120 s negative cooldown — no venue hammering


@pytest.mark.asyncio
async def test_cache_single_flight_concurrent_one_fetch() -> None:
    gate = asyncio.Event()
    sess = _StubSession({"GOLD": _fixture_body("GOLD")}, gate=gate)
    cache = CapitalConstraintCache()
    t1 = asyncio.create_task(cache.get(sess, "GOLD"))
    t2 = asyncio.create_task(cache.get(sess, "GOLD"))
    await asyncio.sleep(0.01)
    gate.set()
    c1, c2 = await asyncio.gather(t1, t2)
    assert c1 is not None and c2 is c1
    assert sess.calls == 1


@pytest.mark.asyncio
async def test_reject_evict_bypasses_cooldown_and_refetches() -> None:
    state = ProdLoopState()
    cache = state.capital_constraints
    fail = _StubSession(exc=RuntimeError("boom"))
    assert await cache.get(fail, "GOLD") is None  # arms the 120 s cooldown
    assert maybe_evict_on_reject(
        state, epic="GOLD", reject_code="MINIMUM_DEAL_SIZE", reject_msg=None
    )
    good = _StubSession({"GOLD": _fixture_body("GOLD")})
    assert await cache.get(good, "GOLD") is not None
    assert good.calls == 1  # cooldown bypassed — forced re-fetch succeeded


def test_size_reject_evicts_cached_constraint() -> None:
    state = ProdLoopState()
    state.capital_constraints._entries["GOLD"] = (
        _fixture_constraint("GOLD"), time.monotonic(),
    )
    assert maybe_evict_on_reject(
        state, epic="GOLD", reject_code=None, reject_msg="invalid deal step"
    )
    assert state.capital_constraints.peek("GOLD") is None


def test_non_size_reject_does_not_evict() -> None:
    state = ProdLoopState()
    state.capital_constraints._entries["GOLD"] = (
        _fixture_constraint("GOLD"), time.monotonic(),
    )
    assert not maybe_evict_on_reject(
        state, epic="GOLD", reject_code="MARKET_CLOSED", reject_msg="market closed"
    )
    assert state.capital_constraints.peek("GOLD") is not None


# ---------------------------------------------------------------------------
# translate_capital_order — quote→USD wiring + degraded fallbacks.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_jpy_quote_via_bars(memdb: sqlite3.Connection) -> None:
    state = ProdLoopState()
    _seed_bar(
        memdb, instrument_id="capital:USDJPY", close=160.0, ts=int(time.time())
    )
    sess = _StubSession({"J225": _fixture_body("J225")})
    plan = await translate_capital_order(
        state=state, conn=memdb, capital_session=sess, epic="J225",
        notional_usd=1600.0, last_price=64_000.0,
    )
    # unit = 64,000 JPY / 160 = $400 → raw 4.0 lots → step 0.01 floor → 4.0.
    assert plan is not None
    assert plan.lots == pytest.approx(4.0)
    assert plan.effective_notional_usd == pytest.approx(1600.0)
    assert plan.contract_factor_usd == pytest.approx(1.0 / 160.0)
    assert state.capital_constraint_fallbacks == 0


@pytest.mark.asyncio
async def test_translate_jpy_rate_via_cached_snapshot(
    memdb: sqlite3.Connection,
) -> None:
    # No USDJPY bars → the rate resolves from the venue snapshot mid through
    # the SAME constraint cache (1 extra GET, then cached for the TTL).
    state = ProdLoopState()
    usdjpy_body = {
        "instrument": {"type": "CURRENCIES", "lotSize": 1, "currency": "JPY"},
        "dealingRules": {"minDealSize": {"value": 100}},
        "snapshot": {"bid": 159.99, "offer": 160.01, "decimalPlacesFactor": 3},
    }
    sess = _StubSession(
        {"J225": _fixture_body("J225"), "USDJPY": usdjpy_body}
    )
    plan = await translate_capital_order(
        state=state, conn=memdb, capital_session=sess, epic="J225",
        notional_usd=1600.0, last_price=64_000.0,
    )
    assert plan is not None
    assert plan.contract_factor_usd == pytest.approx(1.0 / 160.0)
    assert sess.calls == 2  # J225 + USDJPY, each once


@pytest.mark.asyncio
async def test_translate_jpy_rate_unavailable_degrades_to_none(
    memdb: sqlite3.Connection,
) -> None:
    # No bars AND no USDJPY market (404): recording a 1.0-rated JPY exposure
    # would be ~160× wrong — degrade to the legacy path instead (entry flows).
    state = ProdLoopState()
    sess = _StubSession({"J225": _fixture_body("J225")})
    plan = await translate_capital_order(
        state=state, conn=memdb, capital_session=sess, epic="J225",
        notional_usd=1600.0, last_price=64_000.0,
    )
    assert plan is None
    assert state.capital_constraint_fallbacks == 1


@pytest.mark.asyncio
async def test_translate_constraint_fetch_failure_degrades_to_none(
    memdb: sqlite3.Connection,
) -> None:
    state = ProdLoopState()
    sess = _StubSession(exc=RuntimeError("venue 500"))
    plan = await translate_capital_order(
        state=state, conn=memdb, capital_session=sess, epic="GOLD",
        notional_usd=1743.0, last_price=4164.0,
    )
    assert plan is None
    assert state.capital_constraint_fallbacks == 1


# ---------------------------------------------------------------------------
# capital_close_contract_factor — peek-only (no I/O).
# ---------------------------------------------------------------------------


def test_close_contract_factor_warm_cache(memdb: sqlite3.Connection) -> None:
    state = ProdLoopState()
    state.capital_constraints._entries["GOLD"] = (
        _fixture_constraint("GOLD"), time.monotonic(),
    )
    factor = capital_close_contract_factor(state=state, conn=memdb, epic="GOLD")
    assert factor == pytest.approx(1.0)  # USD quote, lotSize 1
    assert state.capital_constraint_fallbacks == 0


def test_close_contract_factor_cache_miss_legacy(
    memdb: sqlite3.Connection,
) -> None:
    state = ProdLoopState()
    assert capital_close_contract_factor(
        state=state, conn=memdb, epic="GOLD"
    ) is None
    assert state.capital_constraint_fallbacks == 1


# ===========================================================================
# Integration — reserve_and_submit Capital branch (network legs stubbed).
# ===========================================================================


def _make_fake_adapter(
    *, level: float, fail_status: str | None = None
) -> type:
    """Fresh fake CapitalAdapter class per test (class-level capture)."""

    class _FakeCapAdapter:
        open_kwargs: dict[str, Any] = {}
        fail: str | None = fail_status

        def __init__(self, _session: Any) -> None: ...

        async def open_position(self, **kw: Any) -> Any:
            type(self).open_kwargs = kw
            if type(self).fail:
                return SimpleNamespace(
                    ok=False, deal_reference=None, deal_id=None,
                    status=type(self).fail, reason="venue reject",
                )
            return SimpleNamespace(
                ok=True, deal_reference="ref1", deal_id="d_top",
                status="OK", reason="",
            )

        async def confirm(self, _ref: str) -> dict[str, Any]:
            kw = type(self).open_kwargs
            return {
                "epic": kw.get("epic", "GOLD"),
                "direction": kw.get("direction", "BUY"),
                "level": level, "size": kw.get("size", 1.0),
                "dealStatus": "ACCEPTED", "status": "OPEN", "dealId": "d_top",
                "affectedDeals": [{"dealId": "d_pos_1"}],
                "date": "2026-06-11T00:00:00Z",
            }

    return _FakeCapAdapter


def _cap_sig(signal_id: str) -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="volume_burst", symbol="GOLD",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="cfd_intraday",
    )


async def _submit(
    memdb: sqlite3.Connection,
    state: ProdLoopState,
    sess: Any,
    *,
    signal_id: str,
    notional_usd: float,
    now_ts: int | None = None,
) -> Any:
    from polaris.scripts._production_pipeline import reserve_and_submit

    return await reserve_and_submit(
        conn=memdb, state=state, sig=_cap_sig(signal_id), venue="capital",
        symbol="GOLD", asset_class="commodity",
        underlying_group_id="commodity:GOLD", notional_usd=notional_usd,
        last_price=4164.0, now_ts=now_ts or int(time.time()),
        real_roundtrip=True, capital_session=sess,
    )


@pytest.mark.asyncio
async def test_reserve_and_submit_capital_expresses_t4_notional(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """요건 3+4: requested == submitted == recorded (fills + risk row)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe

    reset_process_fence()
    fake = _make_fake_adapter(level=4164.0)
    monkeypatch.setattr(pipe, "CapitalAdapter", fake)
    state = ProdLoopState()
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    trade = await _submit(
        memdb, state, sess, signal_id="cap_t4", notional_usd=1743.0
    )
    assert trade is not None
    # T4 $1,743 @ 4164 → 0.41 lots — NOT the 1.0-lot hardcode.
    assert fake.open_kwargs["size"] == pytest.approx(0.41)
    expected = 0.41 * 4164.0
    res = memdb.execute(
        "SELECT requested_notional FROM allocator_reservations"
    ).fetchone()
    assert float(res[0]) == pytest.approx(expected)
    fill = memdb.execute(
        "SELECT size_usd, base_qty FROM fills WHERE is_close = 0"
    ).fetchone()
    assert float(fill[0]) == pytest.approx(expected)
    assert float(fill[1]) == pytest.approx(0.41)
    prs = memdb.execute(
        "SELECT notional_usd FROM position_risk_state"
    ).fetchone()
    assert float(prs[0]) == pytest.approx(expected)
    assert state.capital_constraint_fallbacks == 0


@pytest.mark.asyncio
async def test_reserve_and_submit_capital_sub_min_bumps_and_logs(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sub-min T4 ask → venue min submitted AND reserved (one observability log)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe

    reset_process_fence()
    fake = _make_fake_adapter(level=4164.0)
    monkeypatch.setattr(pipe, "CapitalAdapter", fake)
    state = ProdLoopState()
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    with caplog.at_level(logging.INFO, logger="polaris.scripts._production_capital_sizing"):
        trade = await _submit(
            memdb, state, sess, signal_id="cap_min", notional_usd=30.0
        )
    assert trade is not None
    assert fake.open_kwargs["size"] == pytest.approx(0.01)  # venue min
    expected = 0.01 * 4164.0
    res = memdb.execute(
        "SELECT requested_notional FROM allocator_reservations"
    ).fetchone()
    assert float(res[0]) == pytest.approx(expected)  # 예약 == 제출
    assert any("min-deal" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_reserve_and_submit_capital_degraded_keeps_current_behaviour(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constraint unavailable → EXACTLY the current live behaviour: 1.0 lot,
    T4 notional reserved, legacy $200 fill maths, fallback counter visible."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe

    reset_process_fence()
    fake = _make_fake_adapter(level=4164.0)
    monkeypatch.setattr(pipe, "CapitalAdapter", fake)
    state = ProdLoopState()
    sess = _StubSession()  # every market detail GET → 404
    trade = await _submit(
        memdb, state, sess, signal_id="cap_degraded", notional_usd=1743.0
    )
    assert trade is not None
    assert fake.open_kwargs["size"] == pytest.approx(1.0)  # MIN_CAPITAL_LOT
    res = memdb.execute(
        "SELECT requested_notional FROM allocator_reservations"
    ).fetchone()
    assert float(res[0]) == pytest.approx(1743.0)  # T4 원값 예약 (현행과 동일)
    fill = memdb.execute(
        "SELECT size_usd FROM fills WHERE is_close = 0"
    ).fetchone()
    # Legacy formula: 1.0 lot × pip-default 10 × commodity leverage 20 = $200.
    assert float(fill[0]) == pytest.approx(200.0)
    assert state.capital_constraint_fallbacks >= 1


@pytest.mark.asyncio
async def test_reserve_and_submit_size_reject_evicts_and_refetches(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """거부 → evict → 재조회 → 신규 step으로 성공 (review blocker scenario)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe

    reset_process_fence()
    fake = _make_fake_adapter(level=4164.0, fail_status="ERROR_INVALID_DEAL_SIZE")
    monkeypatch.setattr(pipe, "CapitalAdapter", fake)
    state = ProdLoopState()
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    now_ts = int(time.time())
    trade = await _submit(
        memdb, state, sess, signal_id="cap_rej", notional_usd=1743.0,
        now_ts=now_ts,
    )
    assert trade is None
    assert sess.calls == 1
    # The size-shaped reject evicted the constraint (cooldown bypassed)...
    assert state.capital_constraints.peek("GOLD") is None
    # ...and the venue moved its dealing rules: minDealSize is now 0.5.
    body_v2 = _fixture_body("GOLD")
    body_v2["dealingRules"]["minDealSize"]["value"] = 0.5
    sess.bodies["GOLD"] = body_v2
    fake.fail = None
    trade = await _submit(
        memdb, state, sess, signal_id="cap_rej2", notional_usd=1743.0,
        now_ts=now_ts + 60,
    )
    assert trade is not None
    assert sess.calls == 2  # forced re-fetch picked up the new rules
    # raw 0.4186 < new min 0.5 → ceil-to-step(min) = 0.5 lots.
    assert fake.open_kwargs["size"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Close path — warm cache → close fill records REAL exposure; PnL keys off the
# corrected ENTRY size_usd (변동% × 노출); the wave-1 slice maths (base_qty
# fractions) is untouched.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_close_records_real_exposure_and_pnl(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.scripts import _production_close as close_mod
    from polaris.scripts._production_close import close_specific_position
    from polaris.scripts._smoke_fills import SimulatedTrade
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_cap_close"
    now_ts = int(time.time())
    entry_size_usd = 0.41 * 4164.0
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count, deal_id) "
        "VALUES (?, 'capital', 'GOLD', 'commodity:GOLD', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.41, 'open', ?, 0, "
        "        'd_pos_1')",
        (pos_id, now_ts),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_cap', 'capital', 'capital:GOLD', 'volume_burst', 'buy', "
        "        ?, 4164.0, 0.1, 0.0, ?, 'd_pos_1', ?, 0.0, 0, 0.41, ?, "
        "        'filled')",
        (entry_size_usd, now_ts * 1000, pos_id, entry_size_usd),
    )

    class _FakeCloseAdapter:
        def __init__(self, _session: Any) -> None: ...

        async def close_position(self, _deal_id: str) -> Any:
            return SimpleNamespace(
                ok=True, deal_reference="cref1", status="OK", reason=""
            )

        async def confirm(self, _ref: str) -> dict[str, Any]:
            return {
                "epic": "GOLD", "direction": "SELL", "level": 4205.64,
                "size": 0.41, "dealStatus": "ACCEPTED", "dealId": "d_close",
                "date": "2026-06-11T01:00:00Z",
            }

    monkeypatch.setattr(close_mod, "CapitalAdapter", _FakeCloseAdapter)
    state = ProdLoopState()
    state.capital_constraints._entries["GOLD"] = (
        _fixture_constraint("GOLD"), time.monotonic(),
    )
    trade = SimulatedTrade(
        signal_id="t_cap", venue="capital", symbol="GOLD",
        strategy_id="volume_burst", side="long", entry_price=4164.0,
        notional_usd=entry_size_usd, open_ts=now_ts, position_id=pos_id,
        base_qty=0.41,
    )
    trade.deal_id = "d_pos_1"
    state.open_trades.append(trade)
    closed = await close_specific_position(
        memdb, state=state, position_id=pos_id, now_ts=now_ts + 60,
        lookup_regime=_lookup_regime, real_roundtrip=True,
        capital_session=object(),
    )
    assert closed is True
    row = memdb.execute(
        "SELECT size_usd, pnl_usd FROM fills "
        "WHERE contribution_id = ? AND is_close = 1",
        (pos_id,),
    ).fetchone()
    # Close fill size_usd = lots × close level × factor (REAL exposure).
    assert float(row[0]) == pytest.approx(0.41 * 4205.64)
    # pnl_usd = price move % × corrected ENTRY exposure (+1% × $1,707.24).
    move_pct = (4205.64 - 4164.0) / 4164.0
    assert float(row[1]) == pytest.approx(move_pct * entry_size_usd, rel=1e-6)


# ---------------------------------------------------------------------------
# Tick-engine seam — the SAME reserve_and_submit expresses the T4 notional.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_engine_entry_uses_translated_lots(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.ticks.config import TickEngineConfig
    from polaris.core.ticks.signals import TickIntent
    from polaris.scripts import _production_pipeline as pipe
    from polaris.scripts import _production_tick_engine as tick_mod
    from polaris.scripts._production_tick_engine import TickEngineState, _try_open

    reset_process_fence()
    fake = _make_fake_adapter(level=4164.0)
    monkeypatch.setattr(pipe, "CapitalAdapter", fake)
    # Pin the EXISTING T4 sizer output (compute_size itself is out of scope).
    monkeypatch.setattr(
        tick_mod, "_sized_notional", lambda *a, **kw: 1743.0
    )
    state = ProdLoopState()
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    intent = TickIntent(
        venue="capital", symbol="GOLD", side="long", conviction=0.8,
        signal_id="micro_reversion", signal_family="reversion",
        ref_price=4164.0,
    )
    sess = _StubSession({"GOLD": _fixture_body("GOLD")})
    placed = await _try_open(
        memdb, state, eng, intent=intent, asset_class="commodity",
        underlying_group_id="commodity:GOLD", regime="bull_trend",
        now_ts=int(time.time()), now_mono=time.monotonic(),
        okx_adapter=None, capital_session=sess, alpaca_adapter=None,
        real_roundtrip=True,
    )
    assert placed is True
    assert fake.open_kwargs["size"] == pytest.approx(0.41)
    fill = memdb.execute(
        "SELECT size_usd FROM fills WHERE is_close = 0"
    ).fetchone()
    assert float(fill[0]) == pytest.approx(0.41 * 4164.0)
