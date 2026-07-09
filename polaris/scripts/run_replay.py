"""Thin CLI for the deterministic replay harness (read-only / behaviour 0).

Builds a :class:`ReplayConfig` from CLI args, runs :class:`ReplayEngine` over
the live-seeded sandbox, builds same-bars / same-fee / same-clock baselines,
evaluates the 3-tier benchmark gate, and persists the run + per-tier rows to the
dashboard READ-MODEL (``replay_runs`` / ``benchmark_results``). Never opens a
live trading path; the only live DB contact is the read-only sandbox seed + bar
read inside the engine, and the only write is the display-only read-model.

Example::

    python3 -m polaris.scripts.run_replay \
        --db data/polaris_live.sqlite --interval 1H \
        --instruments okx:BTC-USDT okx:ETH-USDT --trials 7
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from polaris.core.benchmark.baselines import (
    BaselineCurve,
    bollinger_baseline,
    buy_and_hold_baseline,
    tsmom_baseline,
)
from polaris.core.benchmark.gate import GateResult, evaluate_gate
from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine, load_bars
from polaris.core.replay.models import ReplayConfig, ReplayResult
from polaris.storage.schema import init_db

__all__ = [
    "ConfigBundle",
    "build_baselines",
    "build_config",
    "build_gate",
    "main",
    "persist_run",
    "resolve_top_n_active_instruments",
]


def resolve_top_n_active_instruments(db_path: str, n: int) -> list[str]:
    """Top-``n`` most-liquid ACTIVE ``universe`` instruments (read-only).

    Dynamic-universe substitute for a hardcoded instrument default: the
    nightly replay wrapper no longer pins symbols in the script — it asks
    Layer 0's own ``universe`` table (``is_active = 1``, ranked by
    ``vol_24h_usd``) which tickers are live right now. Fail-open: a missing
    DB / table / column degrades to ``[]`` — the caller then falls back to
    replaying ALL instruments for the interval (``--instruments`` empty),
    never a crash and never a stale pinned symbol.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT instrument_id FROM universe WHERE is_active = 1 "
            "ORDER BY vol_24h_usd DESC LIMIT ?",
            (int(n),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


@dataclass(frozen=True, slots=True)
class ConfigBundle:
    """The frozen ReplayConfig + CLI-only gate/persist knobs.

    ``config`` is the reproducible replay identity; ``trials`` / ``read_model_db``
    / ``persist`` are run-time options kept off the frozen config."""

    config: ReplayConfig
    trials: int
    read_model_db: str
    persist: bool


def build_config(argv: list[str]) -> ConfigBundle:
    p = argparse.ArgumentParser(prog="run_replay", description="Deterministic replay + 3-tier gate")
    p.add_argument("--db", default="data/polaris_live.sqlite", help="live DB (read-only sandbox seed)")
    p.add_argument("--interval", default="1H", help="bar interval (e.g. 1H / 15m / 1m)")
    p.add_argument(
        "--instruments", nargs="*", default=[],
        help="instrument_ids (venue:symbol); empty = all for the interval",
    )
    p.add_argument(
        "--top-n-active", type=int, default=0,
        help="when --instruments is empty, replay only the top-N most-liquid "
        "ACTIVE universe instruments (by vol_24h_usd) instead of ALL "
        "instruments for the interval; 0 = disabled (no universe hardcode — "
        "the nightly wrapper drives this from the live DB, never a pinned "
        "symbol list)",
    )
    p.add_argument("--start-ts", type=int, default=None)
    p.add_argument("--end-ts", type=int, default=None)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--trials", type=int, default=1, help="strategy-search trial count (deflated Sharpe)")
    p.add_argument(
        "--read-model-db", default="data/polaris_live.sqlite",
        help="read-model DB for replay_runs/benchmark_results (default = the live "
        "DB the dashboard reads; the prior data/polaris.sqlite default was unread)",
    )
    p.add_argument("--no-persist", action="store_true", help="skip read-model write (print only)")
    args = p.parse_args(argv)
    instrument_ids = list(args.instruments)
    if not instrument_ids and args.top_n_active > 0:
        instrument_ids = resolve_top_n_active_instruments(args.db, args.top_n_active)
    cfg = ReplayConfig(
        instrument_ids=tuple(instrument_ids),
        bar_interval=args.interval,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        starting_equity=args.equity,
        live_db_path=args.db,
    )
    return ConfigBundle(
        config=cfg, trials=int(args.trials),
        read_model_db=args.read_model_db, persist=not args.no_persist,
    )


def build_baselines(
    bars_by_instrument: dict[str, list[Bar]], *, starting_equity: float
) -> dict[str, BaselineCurve]:
    """Same-bars / same-fee / same-clock baselines for the gate.

    Buy&hold + naive TSMOM + naive Bollinger run on the longest-history
    instrument (the market proxy). Empty / single-bar -> empty dict (the gate
    then has no relative comparison and cannot pass tier 1)."""
    if not bars_by_instrument:
        return {}
    inst = max(bars_by_instrument, key=lambda k: len(bars_by_instrument[k]))
    bars = bars_by_instrument[inst]
    if len(bars) < 2:
        return {}
    return {
        "bh_btc": buy_and_hold_baseline(bars, starting_equity=starting_equity),
        "tsmom": tsmom_baseline(bars, starting_equity=starting_equity),
        "bollinger": bollinger_baseline(bars, starting_equity=starting_equity),
    }


def _verdict(gate: GateResult) -> str:
    """Map the gate to a display verdict (honest framing — no time gate).

    PASS = all three tiers significant. EDGE_WIDE_CI = positive point estimate
    but the lower CI bound is not significant ("edge present, CI wide" — NOT a
    pass). FAIL = no edge / loses to a baseline."""
    if gate.passed:
        return "PASS"
    if gate.net_pnl_r_mean > 0.0 and gate.tiers["relative"].passed:
        return "EDGE_WIDE_CI"
    return "FAIL"


def build_gate(
    result: ReplayResult,
    baselines: dict[str, BaselineCurve],
    *,
    trials: int = 1,
    starting_equity: float = 10_000.0,
    is_oos_spread: float = 0.0,
) -> tuple[GateResult, str]:
    """Evaluate the 3-tier gate + derive the display verdict. Pure (no I/O)."""
    gate = evaluate_gate(
        replay=result, baselines=baselines, trials=trials,
        starting_equity=starting_equity, is_oos_spread=is_oos_spread,
    )
    return gate, _verdict(gate)


def persist_run(
    conn: sqlite3.Connection,
    *,
    config: ReplayConfig,
    result: ReplayResult,
    gate: GateResult,
    verdict: str,
    created_ts: int | None = None,
) -> str:
    """Write the run + per-tier rows to the READ-MODEL (display-only).

    Returns the generated ``run_id``. The caller owns the connection; this
    writes ONLY ``replay_runs`` / ``benchmark_results`` (never a trading table).
    """
    run_id = uuid.uuid4().hex
    ts = int(created_ts if created_ts is not None else time.time())
    m = result.metrics
    conn.execute(
        "INSERT INTO replay_runs (run_id, created_ts, bar_interval, start_ts, "
        " end_ts, fee_model, instrument_ids, n_trades, net_pnl_real_fee, sharpe, "
        " max_dd, win_rate, profit_factor, turnover, fee_drag_real_r, psr, "
        " deflated_sharpe, net_pnl_r_lcb, net_pnl_r_ucb, is_oos_spread, verdict) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, ts, config.bar_interval, config.start_ts, config.end_ts,
            config.fee_model, ",".join(config.instrument_ids), len(result.trades),
            float(m.get("net_pnl", 0.0)), float(m.get("sharpe", 0.0)),
            float(m.get("max_dd", 0.0)), float(m.get("win_rate", 0.0)),
            float(m.get("profit_factor", 0.0)), float(m.get("turnover", 0.0)),
            float(m.get("fee_drag_real_r", 0.0)), gate.psr, gate.deflated_sharpe,
            gate.net_pnl_r_ci[0], gate.net_pnl_r_ci[1], gate.is_oos_spread, verdict,
        ),
    )
    rel = gate.tiers["relative"]
    for baseline, spread in gate.sharpe_spreads.items():
        conn.execute(
            "INSERT INTO benchmark_results (run_id, tier, baseline, sharpe_spread, "
            " passed, note) VALUES (?,?,?,?,?,?)",
            (run_id, "relative", baseline, float(spread), int(spread > 0.0), rel.note),
        )
    if not gate.sharpe_spreads:  # no baselines — still record the tier verdict
        conn.execute(
            "INSERT INTO benchmark_results (run_id, tier, baseline, sharpe_spread, "
            " passed, note) VALUES (?,?,?,?,?,?)",
            (run_id, "relative", "", 0.0, int(rel.passed), rel.note),
        )
    for tier in ("risk_adjusted", "statistical"):
        tr = gate.tiers[tier]
        conn.execute(
            "INSERT INTO benchmark_results (run_id, tier, baseline, sharpe_spread, "
            " passed, note) VALUES (?,?,?,?,?,?)",
            (run_id, tier, "", float(gate.bot_sharpe if tier == "risk_adjusted" else 0.0),
             int(tr.passed), tr.note),
        )
    return run_id


def main(argv: list[str] | None = None) -> int:
    bundle = build_config(sys.argv[1:] if argv is None else argv)
    config = bundle.config
    result = ReplayEngine(config).run()
    # Re-load the bars read-only so the baselines share the replay clock.
    ro = sqlite3.connect(f"file:{config.live_db_path}?mode=ro", uri=True)
    try:
        bars = load_bars(
            ro, instrument_ids=config.instrument_ids, bar_interval=config.bar_interval,
            start_ts=config.start_ts, end_ts=config.end_ts,
        )
    finally:
        ro.close()
    baselines = build_baselines(bars, starting_equity=config.starting_equity)
    gate, verdict = build_gate(
        result, baselines, trials=bundle.trials, starting_equity=config.starting_equity
    )
    run_id = ""
    if bundle.persist:
        Path(bundle.read_model_db).parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(bundle.read_model_db)
        try:
            run_id = persist_run(conn, config=config, result=result, gate=gate, verdict=verdict)
        finally:
            conn.close()
    out = {
        "run_id": run_id,
        "verdict": verdict,
        "n_trades": len(result.trades),
        "metrics": result.metrics,
        "gate": {
            "passed": gate.passed,
            "bot_sharpe": gate.bot_sharpe,
            "sharpe_spreads": gate.sharpe_spreads,
            "psr": gate.psr,
            "deflated_sharpe": gate.deflated_sharpe,
            "net_pnl_r_ci": list(gate.net_pnl_r_ci),
            "tiers": {k: {"passed": v.passed, "note": v.note} for k, v in gate.tiers.items()},
        },
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
