"""Layer 0 — Alpaca universe ∪ cowork intel seed (additive union) tests.

DEMO/PAPER only. The cowork watchlist-intel feed (``data/intel/alpaca_seed.json``)
must widen the bounded liquidity-probe candidate set the SAME way the curated
``LIQUID_SEED_SYMBOLS`` already does — additive-only (flow_not_block): a seeded
symbol already outside ``symbols`` (not in the tradable universe) is never
added, and nothing already probed is ever removed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from polaris.core.universe import intel_seed
from polaris.core.universe._alpaca import ALPACA_DATA_BASE, _fetch_alpaca_liquidity

NOW = 1_783_000_000


def _seed_file(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-04T11:30:00Z",
                "expiry_ts": "2099-01-01T00:00:00Z",
                "candidates": [
                    {
                        "symbol": s,
                        "venue": "alpaca",
                        "thesis_tag": "pead_high",
                        "score": 0.7,
                        "catalyst_ts": None,
                        "evidence": ["https://example.com"],
                    }
                    for s in symbols
                ],
            }
        )
    )


def _snapshots_handler(known_symbols: set[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta1/screener/stocks/most-actives":
            return httpx.Response(200, json={"most_actives": []})
        if request.url.path == "/v2/stocks/snapshots":
            symbols_param = request.url.params.get("symbols", "")
            requested = symbols_param.split(",") if symbols_param else []
            snaps = {
                s: {
                    "dailyBar": {"o": 10, "h": 10.2, "l": 9.8, "c": 10, "v": 1_000_000},
                    "minuteBar": {"c": 10},
                }
                for s in requested
                if s in known_symbols
            }
            return httpx.Response(200, json={"snapshots": snaps})
        return httpx.Response(404, json={"path": request.url.path})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clear_intel_cache() -> None:
    intel_seed._CACHE.clear()
    yield
    intel_seed._CACHE.clear()


def test_intel_seed_symbol_in_universe_is_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A seeded symbol inside the tradable universe joins the probe candidates."""
    seed_path = tmp_path / "alpaca_seed.json"
    _seed_file(seed_path, ["ACME"])
    monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", seed_path)

    universe_symbols = {"ACME", "OTHERCO"}
    transport = _snapshots_handler(universe_symbols)

    async def run() -> dict[str, dict[str, float]]:
        async with httpx.AsyncClient(base_url=ALPACA_DATA_BASE, transport=transport) as cli:
            return await _fetch_alpaca_liquidity(
                cli, headers={}, symbols=universe_symbols
            )

    liquidity = asyncio.run(run())
    assert "ACME" in liquidity  # seeded name got a real snapshot, not skipped


def test_intel_seed_symbol_outside_universe_never_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """∩ universe preserved — a seeded symbol NOT in the tradable universe is dropped."""
    seed_path = tmp_path / "alpaca_seed.json"
    _seed_file(seed_path, ["NOTINUNIVERSE"])
    monkeypatch.setattr(intel_seed, "DEFAULT_ALPACA_SEED_PATH", seed_path)

    universe_symbols = {"ACME"}
    transport = _snapshots_handler(universe_symbols)

    async def run() -> dict[str, dict[str, float]]:
        async with httpx.AsyncClient(base_url=ALPACA_DATA_BASE, transport=transport) as cli:
            return await _fetch_alpaca_liquidity(
                cli, headers={}, symbols=universe_symbols
            )

    liquidity = asyncio.run(run())
    assert "NOTINUNIVERSE" not in liquidity


def test_missing_intel_seed_file_does_not_break_existing_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No seed file at all → existing LIQUID_SEED_SYMBOLS/actives flow untouched."""
    monkeypatch.setattr(
        intel_seed, "DEFAULT_ALPACA_SEED_PATH", tmp_path / "absent.json"
    )
    universe_symbols = {"AAPL"}
    transport = _snapshots_handler(universe_symbols)

    async def run() -> dict[str, dict[str, float]]:
        async with httpx.AsyncClient(base_url=ALPACA_DATA_BASE, transport=transport) as cli:
            return await _fetch_alpaca_liquidity(
                cli, headers={}, symbols=universe_symbols
            )

    liquidity = asyncio.run(run())
    assert "AAPL" in liquidity  # curated LIQUID_SEED_SYMBOLS path unaffected
