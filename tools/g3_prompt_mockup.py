"""G3 Validator Prompt Mockup Tester (DEMO context fix).

Replays real production G3 inputs against 5 prompt variants and reports
PASS / KILL / MODIFY ratios. Used to diagnose the 44%+ KILL ratio observed
in PID 9417's first hour and pick the variant that best preserves Polaris
aggressive bias under DEMO/paper conditions.

Variants:
  A — Control (current production prompt)
  B — DEMO context explicit
  C — Aggressive bias + KILL criteria explicit
  D — B + C combined
  E — D + few-shot examples (2 PASS + 1 KILL)

Usage:
    OPENAI_API_KEY=... python3 tools/g3_prompt_mockup.py \\
        --db data/polaris.sqlite --n-kill 10 --n-pass 10

Output:
    Per-variant PASS/KILL/MODIFY counts (JSON to stdout) +
    `data/paper/g3_mockup_<ts>.json` for the digest.

Sequential design: 5 variants * 20 samples = 100 calls; gpt-5-mini rate
limit comfortably absorbs serial dispatch. ~$0.10 budget.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

# Make the `polaris` package importable when running from repo root.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from polaris.core.pipeline.agents._gpt_client import (  # noqa: E402
    DEFAULT_TIMEOUT_SEC,
    GPT_P0_MODEL,
    GPTCallResult,
    call_gpt,
    default_gpt_factory,
)

# Per-variant token cap mirrors the live validator (Q6 G3 budget).
VALIDATOR_MAX_TOKENS: Final[int] = 250
RECENT_TRADES_MAX: Final[int] = 5


# ---------------------------------------------------------------------------
# Sample extraction (real production rows joined across G2 / signals / cell / baseline)
# ---------------------------------------------------------------------------


def _extract_signal_samples(
    conn: sqlite3.Connection, *, decision: str, n: int
) -> list[dict[str, Any]]:
    """Pull recent (post-GPT-migration) G3 samples with full input context.

    The `signals` table is best-effort and often empty (P0 design). The
    canonical raw_signal lives in the *previous* gate's payload_json
    (G2 stamp -> orchestrator forwards into G3 ctx). We join G3 -> G2 by
    run_id to recover it.
    """
    rows = conn.execute(
        """
        SELECT g3.run_id, g3.signal_id, g3.created_ts, g2.payload_json
        FROM gate_events g3
        JOIN gate_events g2
          ON g2.run_id = g3.run_id AND g2.gate_id = 2
        WHERE g3.gate_id = 3 AND g3.model_used = 'gpt' AND g3.decision = ?
        ORDER BY g3.created_ts DESC
        LIMIT ?
        """,
        (decision, int(n)),
    ).fetchall()

    samples: list[dict[str, Any]] = []
    for run_id, signal_id, _created_ts, payload_json in rows:
        try:
            sig_payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            continue
        raw_signal = sig_payload.get("raw_signal")
        if not raw_signal:
            continue
        symbol = str(raw_signal.get("symbol") or "")
        strategy = str(raw_signal.get("strategy_id") or "")
        if not symbol or not strategy:
            continue

        instrument_id = f"okx:{symbol}"

        # Cell routing (regime defaults to chop when no row available).
        regime_row = conn.execute(
            "SELECT regime FROM regime_state WHERE underlying_group_id LIKE ? LIMIT 1",
            (f"%{symbol.replace('-USDT', '')}%",),
        ).fetchone()
        regime = str(regime_row[0]) if regime_row else "chop"

        cell_row = conn.execute(
            """
            SELECT n_eff, wins_eff, avg_pnl_r, score, last_closed_ts
            FROM cell_matrix_p0
            WHERE exchange='okx' AND strategy=? AND ticker=? AND regime=?
            """,
            (strategy, symbol, regime),
        ).fetchone()
        if cell_row:
            cell_routing = {
                "quartile": "mid",
                "score": float(cell_row[3]),
                "n_eff": float(cell_row[0]),
                "wins_eff": float(cell_row[1]),
                "avg_pnl_r": float(cell_row[2]),
                "last_closed_ts": int(cell_row[4]),
            }
        else:
            cell_routing = {
                "quartile": "mid", "score": 0.0, "n_eff": 0.0,
                "wins_eff": 0.0, "avg_pnl_r": 0.0,
            }

        # Baseline (atr / size / volume).
        baseline: dict[str, Any] = {}
        for metric in ("atr", "size", "volume"):
            br = conn.execute(
                """
                SELECT baseline_p50, baseline_p75, sample_count, lookback_sec
                FROM ticker_baseline_state
                WHERE instrument_id=? AND metric=?
                """,
                (instrument_id, metric),
            ).fetchone()
            if br:
                baseline[metric] = {
                    "p50": float(br[0]), "p75": float(br[1]),
                    "n": int(br[2]), "lookback_sec": int(br[3]),
                }

        # Recent trades (best-effort).
        rt_rows = conn.execute(
            """
            SELECT ts_ms, side, fill_price, pnl_usd
            FROM fills
            WHERE venue='okx' AND instrument_id LIKE ? AND is_close=1
            ORDER BY ts_ms DESC LIMIT ?
            """,
            (f"%{symbol}%", RECENT_TRADES_MAX),
        ).fetchall()
        recent = [
            {
                "ts": int(r[0]) // 1000,
                "side": r[1],
                "pnl_r": float(r[3]) / 50.0,
                "won": float(r[3]) > 0.0,
            }
            for r in rt_rows
        ]

        samples.append({
            "run_id": run_id,
            "signal_id": signal_id,
            "original_decision": decision,
            "raw_signal": raw_signal,
            "cell_routing": cell_routing,
            "baseline": baseline,
            "recent_trades": recent,
        })
    return samples


# ---------------------------------------------------------------------------
# Prompt variant builders
# ---------------------------------------------------------------------------

PromptBuilder = Callable[[dict[str, Any]], tuple[list[dict[str, Any]], str]]


def _user_prompt_block(sample: dict[str, Any]) -> str:
    rt_lines = "\n".join(
        f"- {t.get('ts')}: pnl_r={t.get('pnl_r')} won={t.get('won')}"
        for t in sample["recent_trades"][:RECENT_TRADES_MAX]
    ) or "(none)"
    return (
        f"# Raw signal\n{sample['raw_signal']}\n"
        f"# Cell routing\n{sample['cell_routing']}\n"
        f"# Baseline\n{sample['baseline']}\n"
        f"# Recent same-symbol\n{rt_lines}\n"
        'Output JSON: {"decision": "PASS|KILL|MODIFY", "strength_scalar": 1.0, '
        '"thesis": "..."}'
    )


def variant_a(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Control — pre-fix production prompt (kept for regression view)."""
    body = (
        "# Role\nPolaris Signal Validator — PASS / KILL / MODIFY only.\n"
        "# Decision enum\nPASS, KILL, MODIFY\n"
        f"# Cell matrix snapshot\n{sample['cell_routing']}\n"
        f"# Ticker baseline\n{sample['baseline']}\n"
        f"# Recent trades\n{sample['recent_trades'][:RECENT_TRADES_MAX]}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}], _user_prompt_block(sample)


def variant_prod(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Mirror current production prompt (after Jin's 2026-05-07 fix).

    Imports the live builders so this stays auto-synced — running the
    mockup re-validates the prompt every time the validator is touched.
    """
    from polaris.core.pipeline.agents._gpt_client import make_system_prefix
    role = (
        "Polaris Signal Validator (G3). Emit PASS / KILL / MODIFY only. "
        "DEFAULT to PASS — KILL only on clear structural violation "
        "(direction strongly contradicts cell routing, baseline data "
        "corruption, or 5+ consecutive losing same-symbol trades). "
        "Cold cells (n_eff < 5), new tickers, sparse baselines => PASS, "
        "not KILL."
    )
    system = make_system_prefix(
        role=role,
        decision_enum=["PASS", "KILL", "MODIFY"],
        cell_summary=str(sample["cell_routing"]),
        baseline_summary=str(sample["baseline"]),
        recent_trades_summary=str(
            sample["recent_trades"][:RECENT_TRADES_MAX]
        ),
    )
    return system, _user_prompt_block(sample)


def variant_b(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """DEMO context explicit."""
    body = (
        "# Role\n"
        "You are a Signal Validator gate in Polaris v2 paper trading system.\n"
        "**This is DEMO/PAPER trading on OKX simulated environment. "
        "Capital is virtual. Real-money safety arguments are INVALID — "
        "false negatives (skipping good trades) cost more than false "
        "positives.**\n"
        "# Decision enum\nPASS, KILL, MODIFY\n"
        f"# Cell matrix snapshot\n{sample['cell_routing']}\n"
        f"# Ticker baseline\n{sample['baseline']}\n"
        f"# Recent trades\n{sample['recent_trades'][:RECENT_TRADES_MAX]}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}], _user_prompt_block(sample)


def variant_c(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Aggressive bias + explicit KILL criteria."""
    body = (
        "# Role\n"
        "Signal Validator. Bias: AGGRESSIVE — DEFAULT to PASS unless clear "
        "structural violation.\n"
        "# KILL criteria (only)\n"
        "- Signal direction strongly contradicts cell_matrix routing "
        "(score < -0.5 same-direction)\n"
        "- Baseline normalize ratio > 3-sigma outlier (data corruption)\n"
        "- Recent same-symbol trades show 5+ consecutive losses same "
        "regime+strategy\n"
        "# PASS default\n"
        "Otherwise PASS. New tickers / cold cells (n_eff < 5) / sparse "
        "data => PASS, not KILL.\n"
        "# Decision enum\nPASS, KILL, MODIFY\n"
        f"# Cell matrix snapshot\n{sample['cell_routing']}\n"
        f"# Ticker baseline\n{sample['baseline']}\n"
        f"# Recent trades\n{sample['recent_trades'][:RECENT_TRADES_MAX]}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}], _user_prompt_block(sample)


def variant_d(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """B + C combined."""
    body = (
        "# Role\n"
        "You are a Signal Validator gate in Polaris v2 paper trading system.\n"
        "**This is DEMO/PAPER trading on OKX simulated environment. "
        "Capital is virtual. Real-money safety arguments are INVALID — "
        "false negatives (skipping good trades) cost more than false "
        "positives.**\n"
        "Bias: AGGRESSIVE — DEFAULT to PASS unless clear structural "
        "violation.\n"
        "# KILL criteria (only)\n"
        "- Signal direction strongly contradicts cell_matrix routing "
        "(score < -0.5 same-direction)\n"
        "- Baseline ratio > 3-sigma outlier (data corruption)\n"
        "- Recent same-symbol trades show 5+ consecutive losses same "
        "regime+strategy\n"
        "# PASS default\n"
        "Otherwise PASS. New tickers / cold cells (n_eff < 5) / sparse "
        "data => PASS, not KILL.\n"
        "# Decision enum\nPASS, KILL, MODIFY\n"
        f"# Cell matrix snapshot\n{sample['cell_routing']}\n"
        f"# Ticker baseline\n{sample['baseline']}\n"
        f"# Recent trades\n{sample['recent_trades'][:RECENT_TRADES_MAX]}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}], _user_prompt_block(sample)


def variant_e(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """D + few-shot examples (2 PASS + 1 KILL)."""
    body = (
        "# Role\n"
        "You are a Signal Validator gate in Polaris v2 paper trading system.\n"
        "**This is DEMO/PAPER trading on OKX simulated environment. "
        "Capital is virtual. Real-money safety arguments are INVALID — "
        "false negatives (skipping good trades) cost more than false "
        "positives.**\n"
        "Bias: AGGRESSIVE — DEFAULT to PASS unless clear structural "
        "violation.\n"
        "# KILL criteria (only)\n"
        "- Signal direction strongly contradicts cell_matrix routing "
        "(score < -0.5 same-direction)\n"
        "- Baseline ratio > 3-sigma outlier (data corruption)\n"
        "- Recent same-symbol trades show 5+ consecutive losses same "
        "regime+strategy\n"
        "# PASS default\n"
        "Otherwise PASS. New tickers / cold cells (n_eff < 5) / sparse "
        "data => PASS, not KILL.\n"
        "# Examples\n"
        "Example 1 (PASS): tsmom long BTC-USDT, cell n_eff=0 cold, "
        'baseline empty -> {"decision":"PASS","strength_scalar":1.0,'
        '"thesis":"cold cell, default aggressive"}\n'
        "Example 2 (PASS): volume_burst long SOL-USDT, cell score=0.2 "
        'mid, recent 1 win 1 loss -> {"decision":"PASS",'
        '"strength_scalar":1.0,"thesis":"no structural violation"}\n'
        "Example 3 (KILL): donchian short ETH-USDT, cell score=-0.7 "
        "same-direction (strong contradiction), recent 5 consecutive "
        'losses -> {"decision":"KILL","strength_scalar":0.0,'
        '"thesis":"structural mismatch + losing streak"}\n'
        "# Decision enum\nPASS, KILL, MODIFY\n"
        f"# Cell matrix snapshot\n{sample['cell_routing']}\n"
        f"# Ticker baseline\n{sample['baseline']}\n"
        f"# Recent trades\n{sample['recent_trades'][:RECENT_TRADES_MAX]}\n"
        "# Output\nReturn ONE JSON object only. No prose."
    )
    return [{"type": "text", "text": body}], _user_prompt_block(sample)


VARIANTS: Final[dict[str, PromptBuilder]] = {
    "A": variant_a,
    "B": variant_b,
    "C": variant_c,
    "D": variant_d,
    "E": variant_e,
    "P": variant_prod,
}


# ---------------------------------------------------------------------------
# Mockup runner
# ---------------------------------------------------------------------------


async def _call_one(
    client: Any, system: list[dict[str, Any]], user: str
) -> str:
    """Single GPT call -> normalized decision token (PASS/KILL/MODIFY/ERR)."""
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=user,
        max_tokens=VALIDATOR_MAX_TOKENS,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
        model=GPT_P0_MODEL,
    )
    if res.error or res.parsed is None:
        return "ERR"
    decision = str(res.parsed.get("decision", "")).upper()
    if decision in {"PASS", "KILL", "MODIFY"}:
        return decision
    return "ERR"


async def run_mockup(
    samples: list[dict[str, Any]], *, client_factory: Callable[[], Any]
) -> dict[str, Any]:
    """Run every (sample, variant) combo and aggregate per-variant counts."""
    results: dict[str, dict[str, int]] = {
        v: {"PASS": 0, "KILL": 0, "MODIFY": 0, "ERR": 0}
        for v in VARIANTS
    }
    # Track per-original-decision split (KILL-vs-PASS regression view).
    by_original: dict[str, dict[str, dict[str, int]]] = {
        v: {
            "KILL": {"PASS": 0, "KILL": 0, "MODIFY": 0, "ERR": 0},
            "PASS": {"PASS": 0, "KILL": 0, "MODIFY": 0, "ERR": 0},
        }
        for v in VARIANTS
    }

    client = client_factory()
    for idx, sample in enumerate(samples):
        for variant_name, builder in VARIANTS.items():
            system, user = builder(sample)
            decision = await _call_one(client, system, user)
            results[variant_name][decision] += 1
            by_original[variant_name][sample["original_decision"]][decision] += 1
        if (idx + 1) % 5 == 0:
            print(f"  ... processed {idx + 1}/{len(samples)} samples", flush=True)

    return {
        "totals": results,
        "by_original": by_original,
        "n_samples": len(samples),
    }


def _format_table(report: dict[str, Any]) -> str:
    out: list[str] = []
    out.append(
        f"\nMockup results (n={report['n_samples']}, model={GPT_P0_MODEL})\n"
    )
    out.append(
        "  variant | PASS  KILL  MODIFY  ERR  | "
        "origKILL: PASS/KILL  | origPASS: PASS/KILL"
    )
    out.append("  " + "-" * 80)
    for v, counts in report["totals"].items():
        ok = report["by_original"][v]["KILL"]
        op = report["by_original"][v]["PASS"]
        out.append(
            f"     {v}    | {counts['PASS']:>4}  {counts['KILL']:>4}  "
            f"{counts['MODIFY']:>5}  {counts['ERR']:>3}  | "
            f"     {ok['PASS']:>3}/{ok['KILL']:<3}     |     "
            f"{op['PASS']:>3}/{op['KILL']:<3}"
        )
    return "\n".join(out)


def _pick_best(report: dict[str, Any]) -> str:
    """Best variant: maximize PASS while keeping >= 30% KILL on origKILL set."""
    n_per_orig = report["n_samples"] // 2
    best = "A"
    best_pass = -1
    for v, counts in report["totals"].items():
        kill_orig_kill = report["by_original"][v]["KILL"]["KILL"]
        kill_ratio_origkill = (
            kill_orig_kill / n_per_orig if n_per_orig else 0.0
        )
        # Discriminator preserved (>= 0.30 keep-rate on originally-KILL set).
        if kill_ratio_origkill < 0.30:
            continue
        if counts["PASS"] > best_pass:
            best_pass = counts["PASS"]
            best = v
    return best


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/polaris.sqlite")
    p.add_argument("--n-kill", type=int, default=10)
    p.add_argument("--n-pass", type=int, default=10)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    print("Extracting samples ...", flush=True)
    kill_samples = _extract_signal_samples(conn, decision="KILL", n=args.n_kill)
    pass_samples = _extract_signal_samples(conn, decision="PASS", n=args.n_pass)
    samples = kill_samples + pass_samples
    print(
        f"  -> {len(kill_samples)} KILL + {len(pass_samples)} PASS = "
        f"{len(samples)} samples"
    )
    if not samples:
        print("ERROR: no samples extracted (DB empty?)", file=sys.stderr)
        return 2

    print(
        f"Running 5 variants x {len(samples)} samples = "
        f"{5 * len(samples)} GPT calls ..."
    )
    report = asyncio.run(
        run_mockup(samples, client_factory=lambda: default_gpt_factory())
    )
    print(_format_table(report))

    best = _pick_best(report)
    print(f"\nBest variant: {best}")
    report["best_variant"] = best

    out_path = args.out or f"data/paper/g3_mockup_{int(time.time())}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2))
    print(f"Report written -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
