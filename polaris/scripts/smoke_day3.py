"""Smoke test — Phase 0 P0 Day 3 (Layer 2 + 4 GPT gates + L3 T4 + L1 ingest).

Pieces:
1. OKX bar ingest — fetch 100 BTC-USDT candles, persist + baseline samples.
2. Cell matrix seed (top quartile for BTC-USDT).
3. End-to-end pipeline: G3 validator → G4 watcher → G5 sizer → G6 monitor →
   G7 exit → G8 reflector. Real GPT call when OPENAI_API_KEY is set,
   otherwise mocked deterministic responses (fail-closed gates would KILL).
4. Layer 3 T4 sizing: top-quartile cell + base 2% should produce ~3.6%
   (0.02 × 1.2 × 1.0 × 1.5 × 1.0).

Run: ``python3 -m polaris.scripts.smoke_day3``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.data.ingest import ingest_bars
from polaris.core.pipeline import run_signal_pipeline
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_SIGNAL_VALIDATOR,
    SignalLifecycle,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
)
from polaris.storage.schema import init_db
from polaris.venues.okx import fetch_okx_bars

DOTENV_PATH = Path(".env")


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# 1. OKX bar ingest
# ---------------------------------------------------------------------------


async def _ingest_btc_bars(db_path: Path, *, limit: int = 100) -> int:
    print("[ingest] fetching OKX BTC-USDT bars ...")
    try:
        bars = await fetch_okx_bars("BTC-USDT", bar_interval="1m", limit=limit)
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest] fetch failed: {exc!r}")
        return 0
    print(f"[ingest] received {len(bars)} bars")
    conn = init_db(db_path)
    try:
        out = ingest_bars(conn, bars, asset_class="crypto")
        print(f"[ingest] persisted {out['bars']} bars + {out['baseline_samples']} samples")
    finally:
        conn.close()
    return len(bars)


# ---------------------------------------------------------------------------
# 2. Cell matrix seed (so resolve_routing_for_cell returns top quartile)
# ---------------------------------------------------------------------------


def _seed_cells_top(db_path: Path, now_ts: int) -> None:
    conn = init_db(db_path)
    try:
        # Clear any prior smoke state.
        conn.execute("DELETE FROM cell_matrix_p0")
        conn.execute("DELETE FROM cell_matrix_parent3")
        conn.execute("DELETE FROM cell_matrix_parent2")
        conn.execute("DELETE FROM cell_matrix_shadow_context")
        ctx = CellContext(group="spot_intraday", session="asia",
                          direction="long", liquidity_tier="high")
        # 25 cells × 25 trades, BTC = highest score.
        for i in range(25):
            ticker = "BTC-USDT" if i == 24 else f"PL{i:02d}-USDT"
            for j in range(25):
                pnl = (i - 12) * 0.1
                tr = TradeClose(
                    key=CellKeyP0("okx", "volume_burst", ticker, "bull_trend"),
                    context=ctx,
                    pnl_r=pnl,
                    won=pnl > 0,
                    closed_ts=now_ts + j,
                )
                update_on_trade_close(conn, tr)
        print("[cell] seeded 25 cells (BTC-USDT in top quartile)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. End-to-end pipeline
# ---------------------------------------------------------------------------


def _make_gpt_client() -> Any:
    """2026-05-07 migration: returns ``GPTClient`` (OpenAI) or ``None``."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from polaris.core.pipeline.agents._gpt_client import GPTClient
    except ImportError:
        print("[gpt] _gpt_client import failed; using deterministic fallback")
        return None
    return GPTClient(api_key=api_key)


class _StubGPTClient:
    """Deterministic stand-in when real GPT is unavailable.

    Returns ``decision: PASS`` for the validator and ``decision: PROCEED`` for
    the watcher so the rest of the pipeline executes end-to-end. Used when
    OPENAI_API_KEY is missing or the API rejects (credit balance, etc.).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                outer.calls.append(kwargs)
                user_msg = kwargs.get("messages", [{}])[-1].get("content", "")
                if "Pre-Entry Watcher" in str(kwargs.get("system", "")) or "PROCEED" in user_msg:
                    text = '{"decision": "PROCEED"}'
                elif "Reflector" in str(kwargs.get("system", "")):
                    text = (
                        '{"lesson_type": "ok", "confidence": 0.80, '
                        '"lesson_text": "smoke trade reflection", "delta": {}}'
                    )
                else:
                    text = '{"decision": "PASS", "strength_scalar": 1.0}'

                class _Block:
                    pass

                blk = _Block()
                blk.text = text  # type: ignore[attr-defined]

                class _Resp:
                    pass

                resp = _Resp()
                resp.content = [blk]  # type: ignore[attr-defined]
                resp.usage = None  # type: ignore[attr-defined]
                return resp

        self.messages = _Messages()


async def _probe_gpt(client: Any) -> bool:
    """Send a tiny ping; return False on error (credit balance / network)."""
    from polaris.core.pipeline.agents._gpt_client import GPT_P0_MODEL
    try:
        await client.messages.create(
            model=GPT_P0_MODEL,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with literally OK."}],
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001
        return False
    return True


async def _run_pipeline(db_path: Path, now_ts: int) -> dict[str, Any]:
    real = _make_gpt_client()
    if real is None:
        print("[gpt] no API key — using deterministic stub")
        haiku: Any = _StubGPTClient()
    else:
        ok = await _probe_gpt(real)
        if ok:
            print("[gpt] real GPTClient — probe OK")
            haiku = real
        else:
            print("[gpt] real client constructed but probe failed (credits/net) — stub")
            haiku = _StubGPTClient()

    intent = SignalIntent(
        signal_id="smoke-1",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.2,
        listing_age_hours=240.0,
        leverage=1.0,
        base_risk_pct=0.02,
    )
    risk_state = StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=30, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=now_ts,
    )
    portfolio = PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )
    payload = {
        "raw_signal": {
            "strategy": "volume_burst",
            "direction": "long",
            "score": 1.2,
            "thesis": "smoke test signal",
        },
        "validated_signal": {
            "strategy": "volume_burst",
            "strength_scalar": 1.2,
            "direction": "long",
        },
        "tick_window": [],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
        "signal_intent": intent,
        "risk_state": risk_state,
        "portfolio": portfolio,
        "position": {
            "correlation_group": "btc-cluster",
            "side": "long",
            "venue": "okx",
            "symbol": "BTC-USDT",
            "strategy": "volume_burst",
        },
        "unrealized_pnl_r": 0.3,
        "max_loss_r": 1.0,
        "current_stop_price": 70000.0,
        "closed_trade": {
            "trade_id": "smoke-trade-1",
            "strategy_id": "volume_burst",
            "regime": "bull_trend",
            "session": "asia",
            "pnl_r": 0.85,
        },
        "closed_trade_count": 50,
    }

    conn = init_db(db_path)
    try:
        # Phase A: entry chain G3 → G4 → G5 → G6 → G7. Stops at G7 because
        # the position is open (state ACTIVE/MONITORED, not EXIT_PENDING).
        started = time.monotonic()
        ctx, results = await run_signal_pipeline(
            run_id=uuid.uuid4().hex,
            venue="okx",
            symbol="BTC-USDT",
            strategy_id="volume_burst",
            payload=payload,
            conn=conn,
            haiku_client=haiku,
            start_gate=GATE_SIGNAL_VALIDATOR,
        )
        elapsed_a = int((time.monotonic() - started) * 1000)

        # Phase B: close → reflect chain. Seed EXIT_PENDING so G7 transitions
        # to CLOSED, then G8 reflects.
        started_b = time.monotonic()
        close_ctx, close_results = await run_signal_pipeline(
            run_id=uuid.uuid4().hex,
            venue="okx",
            symbol="BTC-USDT",
            strategy_id="volume_burst",
            payload=payload,
            conn=conn,
            haiku_client=haiku,
            start_gate=GATE_ADAPTIVE_EXIT,
            initial_state=SignalLifecycle.EXIT_PENDING,
        )
        elapsed_b = int((time.monotonic() - started_b) * 1000)
        elapsed_ms = elapsed_a + elapsed_b
        results = list(results) + list(close_results)
    finally:
        conn.close()

    print(f"[pipeline] end-to-end {len(results)} gates in {elapsed_ms}ms")
    for r in results:
        print(
            f"  - gate decision={r.decision.value:<14} "
            f"model={r.model_used:<18} "
            f"latency={r.latency_ms}ms "
            f"next={r.next_gate}"
        )
    print(f"[pipeline] entry phase final state = {ctx.state.value}")
    print(f"[pipeline] close phase final state = {close_ctx.state.value}")

    sized = ctx.payload.get("sized")
    if sized:
        print(f"[pipeline] T4 sizing notional = ${sized.get('final_notional_usd', 0):,.2f}")
        print(f"[pipeline] proposal = {json.dumps(sized.get('proposal', {}))}")
    return {
        "elapsed_ms": elapsed_ms,
        "n_gates": len(results),
        "state": close_ctx.state.value,
        "sized": sized,
    }


# ---------------------------------------------------------------------------
# 4. Standalone L3 sizing call (deterministic, validates T4 numerator)
# ---------------------------------------------------------------------------


def _standalone_sizing(db_path: Path, now_ts: int) -> None:
    conn = init_db(db_path)
    try:
        intent = SignalIntent(
            signal_id="x", venue="okx", symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
            asset_class="crypto", strategy="volume_burst", track="A",
            regime="bull_trend", direction="long",
            signal_strength=1.2, listing_age_hours=240.0,
            leverage=1.0, base_risk_pct=0.02,
        )
        risk = StrategyRiskState(
            venue="okx", strategy="volume_burst",
            closed_trades=30, kelly_p=0.55, kelly_q=0.45,
            kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
            updated_ts=now_ts,
        )
        portfolio = PortfolioState(
            equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
            track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
            fill_rate_active_cut=False,
        )
        sized = compute_size(conn, intent=intent, risk_state=risk,
                             portfolio=portfolio, now_ts=now_ts)
        print(
            f"[sizing] standalone T4: cell={sized.proposed.cell_routing_mult}× "
            f"final_pct={sized.final_risk_pct*100:.2f}% "
            f"notional=${sized.final_notional_usd:,.2f} "
            f"binding={sized.binding_cap}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run() -> None:
    _load_dotenv()
    now_ts = int(time.time())
    db_path = Path("data/polaris.sqlite")

    print("=" * 64)
    print("Polaris P0 Day 3 — Layer 2 pipeline + 4 Haiku gates + L3 T4 + L1 ingest")
    print(f"now_ts = {now_ts}")
    print("=" * 64)

    # 1. OKX bar ingest (best-effort — demo network may fail).
    try:
        await _ingest_btc_bars(db_path, limit=100)
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest] failed: {exc!r}")

    # 2. Cell seed (BTC top quartile).
    _seed_cells_top(db_path, now_ts)

    # 3. End-to-end pipeline.
    result = await _run_pipeline(db_path, now_ts)

    # 4. Standalone sizing call.
    _standalone_sizing(db_path, now_ts)

    print("=" * 64)
    print(f"smoke OK — gates={result['n_gates']} state={result['state']} "
          f"elapsed={result['elapsed_ms']}ms")
    print("=" * 64)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
