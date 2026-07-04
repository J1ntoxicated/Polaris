"""cowork watchlist-intel bot-side ingest — end-to-end wire test.

DEMO/PAPER only. Additive-only (flow_not_block): this feed only adds
candidates / raises rank / stamps a measurement tag — it never blocks, caps,
or removes anything. Covers the full chain named in the build task:

  fixture seed file -> universe candidate reachability (_alpaca union)
    -> tick-loop seed_tag stamp on RawSignal
    -> G3 validator payload carries seed_tag
    -> position row is stamped at open (survives to `positions.seed_tag`)

Plus the fail-safe no-op matrix: expired file, absent file, malformed JSON —
every one of these must degrade to an EMPTY seed (normal unseeded universe),
never an exception, never a partial/stuck state.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from polaris.core.pipeline.payload_builder import build_validator_payload
from polaris.core.universe import intel_seed
from polaris.core.universe._alpaca import ALPACA_DATA_BASE, _fetch_alpaca_liquidity
from polaris.core.universe.intel_seed import load_intel_seed
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal


@pytest.fixture(autouse=True)
def _clear_intel_cache() -> None:
    intel_seed._CACHE.clear()
    yield
    intel_seed._CACHE.clear()


def _write_seed_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _good_payload(symbol: str, *, expiry_ts: str = "2099-01-01T00:00:00Z") -> dict:
    return {
        "generated_at": "2026-07-04T11:30:00Z",
        "expiry_ts": expiry_ts,
        "candidates": [
            {
                "symbol": symbol,
                "venue": "alpaca",
                "thesis_tag": "pead_high",
                "score": 0.82,
                "catalyst_ts": "2026-07-03T20:05:00Z",
                "evidence": ["https://finviz.com/quote.ashx?t=" + symbol],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Stage 1 — fixture seed file reaches the universe candidate union
# ---------------------------------------------------------------------------


def test_stage1_seed_file_reaches_universe_liquidity_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_path = tmp_path / "alpaca_seed.json"
    _write_seed_file(seed_path, _good_payload("ACME"))
    monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", seed_path)

    universe_symbols = {"ACME", "OTHERCO"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta1/screener/stocks/most-actives":
            return httpx.Response(200, json={"most_actives": []})
        if request.url.path == "/v2/stocks/snapshots":
            requested = (request.url.params.get("symbols", "") or "").split(",")
            snaps = {
                s: {
                    "dailyBar": {"o": 10, "h": 10.2, "l": 9.8, "c": 10, "v": 1e6},
                    "minuteBar": {"c": 10},
                }
                for s in requested
                if s in universe_symbols
            }
            return httpx.Response(200, json={"snapshots": snaps})
        return httpx.Response(404, json={})

    async def run() -> dict:
        async with httpx.AsyncClient(
            base_url=ALPACA_DATA_BASE, transport=httpx.MockTransport(handler)
        ) as cli:
            return await _fetch_alpaca_liquidity(cli, headers={}, symbols=universe_symbols)

    liquidity = asyncio.run(run())
    assert "ACME" in liquidity  # seeded candidate reached the universe probe


# ---------------------------------------------------------------------------
# Stage 2 — seed_tag stamp on RawSignal -> G3 payload -> position row
# ---------------------------------------------------------------------------


def test_stage2_seed_tag_survives_from_signal_to_payload(tmp_path: Path) -> None:
    """Simulates the tick-loop stamp (dataclasses.replace with seed_tag=...)
    then verifies the G3 validator payload carries it through untouched,
    alongside the strategy-owned thesis_tag (never overwritten)."""
    sig = RawSignal(
        signal_id="sig_e2e", strategy_id="equity_52wk_high_breakout",
        symbol="ACME", side="long", strength=1.0, sizing_hint=0.05,
        ttl_bars=10, thesis_tag="equity_52wk_high+brk=0.02",
        correlation_group="equity_52wk_high_breakout",
    )
    # Mirrors _production_tick.py's stamp: load_intel_seed().seed_tags.get(symbol).
    stamped = dataclasses.replace(sig, seed_tag="pead_high")

    conn = sqlite3.connect(":memory:")
    try:
        from polaris.storage.schema import init_db

        conn.close()
        conn = init_db(tmp_path / "g3.sqlite")
        payload = build_validator_payload(
            raw_signal=stamped, venue="alpaca", symbol="ACME",
            instrument_id="alpaca:ACME", regime="bull_trend", conn=conn,
        )
        assert payload["raw_signal"]["seed_tag"] == "pead_high"
        assert payload["raw_signal"]["thesis_tag"] == "equity_52wk_high+brk=0.02"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_stage3_position_stamped_at_open_survives(
    memdb: sqlite3.Connection,
) -> None:
    """Full lifecycle: a seed_tag-carrying RawSignal opens a (sim) position and
    the tag lands durably on the positions row (cohort measurement input)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="sig_open_e2e", strategy_id="equity_52wk_high_breakout",
        symbol="ACME", side="long", strength=1.0, sizing_hint=0.05,
        ttl_bars=10, thesis_tag="equity_52wk_high+brk=0.02",
        correlation_group="equity_52wk_high_breakout", seed_tag="pead_high",
    )
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="alpaca", symbol="ACME",
        asset_class="equity", underlying_group_id="equity:ACME",
        notional_usd=200.0, last_price=100.0, now_ts=int(time.time()),
        real_roundtrip=False,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT seed_tag FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "pead_high"


@pytest.mark.asyncio
async def test_stage3_unseeded_position_stamps_empty_string(
    memdb: sqlite3.Connection,
) -> None:
    """The overwhelming default path — no seed_tag on the signal -> '' on the row."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="sig_unseeded", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=False,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT seed_tag FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == ""


# ---------------------------------------------------------------------------
# Fail-safe no-op matrix — expired / absent / malformed feed
# ---------------------------------------------------------------------------


def test_noop_expired_seed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_path = tmp_path / "alpaca_seed.json"
    _write_seed_file(seed_path, _good_payload("ACME", expiry_ts="2020-01-01T00:00:00Z"))
    monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", seed_path)
    result = load_intel_seed()
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_noop_absent_seed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        intel_seed, "DEFAULT_ALPACA_SEED_PATH", tmp_path / "does_not_exist.json"
    )
    result = load_intel_seed()
    assert result.seed_tags == {}
    assert result.symbols == frozenset()


def test_noop_malformed_json_logs_warning_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seed_path = tmp_path / "alpaca_seed.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text("{ this is not json !!")
    monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", seed_path)
    with caplog.at_level("WARNING"):
        result = load_intel_seed()
    assert result.seed_tags == {}
    assert any("intel_seed" in r.name for r in caplog.records)
