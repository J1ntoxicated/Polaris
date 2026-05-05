"""Live readiness audit — Paper vs Live PnL divergence analysis.

Sources:
- LESSON-002 paper vs live divergence (모태)
- INSIGHT-007 OKX fee 함정
- INSIGHT-032 OKX fee 0.7%/side paper assumption
- Current paper results (lifetime PnL, win rate, avg edge per strategy)

Output:
- Paper EV vs Live EV difference estimate
- Live slippage model (taker 0.07% × 2 sides + market impact)
- Live readiness score (0-100)
- Top 3 risk factors

Usage:
    python3 scripts/live_readiness_audit.py
    python3 scripts/live_readiness_audit.py --json   # machine-readable output
"""
from __future__ import annotations

import glob
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


# ─── Live reality constants (OKX SPOT, INSIGHT-032 + LESSON-002) ────────────
# Phase 6 (slippage_model.py): paper engine now internalizes spread cross +
# orderbook walk in fill price → friction delta drops from 0.08% → 0.02%
# (residual: latency, partial fills, dark liquidity).
#
# NOTE: legacy paper trades (pre-2026-05-05) used last_price for fills, so the
# total friction estimate is conservative until new trades dominate the sample.
PAPER_FEE_ROUND_TRIP: float = 0.0014     # taker 0.07% × 2 (LV3+)
LIVE_FEE_ROUND_TRIP: float = 0.0014     # same (OKX LV3 assumed)
SLIPPAGE_PER_SIDE: float = 0.00005      # 0.005% residual after Phase 6 (was 0.03%)
SLIPPAGE_ROUND_TRIP: float = SLIPPAGE_PER_SIDE * 2  # 0.01% total
SPREAD_DRAG_PER_SIDE: float = 0.00005   # 0.005% residual (paper now crosses spread)
SPREAD_DRAG_ROUND_TRIP: float = SPREAD_DRAG_PER_SIDE * 2

# Total live friction delta vs paper (paper now accounts for slippage in fill)
LIVE_FRICTION_DELTA: float = SLIPPAGE_ROUND_TRIP + SPREAD_DRAG_ROUND_TRIP  # 0.02% residual

# Thresholds for readiness scoring
MIN_TRADES_FOR_CONFIDENCE: int = 30
MIN_WIN_RATE_FOR_LIVE: float = 0.40
MIN_EV_PCT_FOR_LIVE: float = 0.10          # EV must exceed 0.10% per trade (>2× live friction delta)
MAX_LOSS_PCT_FOR_LIVE: float = -5.0        # lifetime PnL > -5% of capital considered ok for test
SHARPE_TARGET: float = 0.5                  # ADR-009v2 perp threshold (applies to live too)


@dataclass
class StrategyAudit:
    """Per-strategy live readiness summary."""
    name: str
    n_trades: int
    win_rate: float
    avg_size_usd: float
    paper_pnl_usd: float
    paper_ev_pct: float          # avg net PnL per trade as % of size
    estimated_live_ev_pct: float  # paper EV minus live friction delta
    slippage_drag_usd: float     # total estimated slippage cost over n_trades
    live_pnl_estimate_usd: float
    ready_for_live: bool
    score: int                   # 0-100
    notes: list[str]


@dataclass
class AuditResult:
    """Aggregate audit result."""
    total_trades: int
    total_paper_pnl_usd: float
    total_live_pnl_estimate_usd: float
    total_slippage_drag_usd: float
    overall_win_rate: float
    overall_ev_pct: float
    live_readiness_score: int    # 0-100
    top_risks: list[str]
    strategies: list[dict]
    summary_line: str


def estimate_live_pnl(paper_pnl: float, n_trades: int, avg_size: float) -> dict:
    """Estimate live PnL adjusting for slippage + spread drag.

    Paper assumptions:
        fee 0.0014 round-trip (LV3+ taker × 2)

    Live realities (LESSON-002):
        fee 0.0014 — same (OKX LV3 assumed, no change)
        slippage 0.0006 round-trip (0.03% × 2 sides market impact)
        spread drag 0.0002 round-trip (half-spread cost for taker crossing BBO)
        total friction delta: +0.0008 per trade on top of paper

    Returns dict with paper/live comparison metrics.
    """
    slippage_drag = LIVE_FRICTION_DELTA * avg_size * n_trades
    live_pnl = paper_pnl - slippage_drag
    div_pct = (live_pnl - paper_pnl) / abs(paper_pnl) * 100 if paper_pnl != 0 else 0.0
    return {
        "paper_pnl": paper_pnl,
        "estimated_live_pnl": live_pnl,
        "divergence_pct": div_pct,
        "slippage_drag": slippage_drag,
    }


def _load_paper_stats() -> dict[str, dict]:
    """Load all paper state JSONs → aggregate per-strategy stats."""
    state_dir = Path(__file__).resolve().parent.parent / "data" / "paper"
    files = sorted(glob.glob(str(state_dir / "paper_state_*.json")))

    per_strategy: dict[str, dict] = {}
    for fpath in files:
        try:
            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
        except Exception:
            continue

        closed = data.get("closed_positions", []) or []
        if not closed:
            continue

        fname = Path(fpath).stem.replace("paper_state_", "")
        # strategy name = everything after first ticker_ part
        # format: <ticker>_<strategy_name>
        parts = fname.split("_", 1)
        strat = parts[1] if len(parts) > 1 else fname

        if strat not in per_strategy:
            per_strategy[strat] = {"pnl": 0.0, "n": 0, "wins": 0, "size_sum": 0.0}

        for pos in closed:
            ep = pos.get("entry_price") or 0.0
            xp = pos.get("exit_price") or 0.0
            sz = pos.get("size_usd") or 0.0
            fee = pos.get("fee_round_trip") or PAPER_FEE_ROUND_TRIP
            if not (ep and xp and sz):
                continue
            gross = (xp - ep) / ep * sz
            net = gross - fee * sz
            per_strategy[strat]["pnl"] += net
            per_strategy[strat]["n"] += 1
            per_strategy[strat]["wins"] += 1 if net > 0 else 0
            per_strategy[strat]["size_sum"] += sz

    return per_strategy


def _score_strategy(s: StrategyAudit) -> int:
    """Compute 0-100 live readiness score for a single strategy."""
    score = 0
    # Sample adequacy (max 20 pts)
    if s.n_trades >= MIN_TRADES_FOR_CONFIDENCE:
        score += 20
    elif s.n_trades >= 10:
        score += 10
    elif s.n_trades >= 5:
        score += 5

    # Win rate (max 25 pts)
    if s.win_rate >= 0.55:
        score += 25
    elif s.win_rate >= MIN_WIN_RATE_FOR_LIVE:
        score += 15
    elif s.win_rate >= 0.30:
        score += 5

    # Paper EV (max 30 pts)
    if s.paper_ev_pct >= 0.5:
        score += 30
    elif s.paper_ev_pct >= MIN_EV_PCT_FOR_LIVE:
        score += 20
    elif s.paper_ev_pct >= 0.0:
        score += 5

    # Live EV positive after friction (max 25 pts)
    if s.estimated_live_ev_pct >= MIN_EV_PCT_FOR_LIVE:
        score += 25
    elif s.estimated_live_ev_pct >= 0.0:
        score += 10

    return min(score, 100)


def audit_strategies(per_strategy: dict[str, dict]) -> list[StrategyAudit]:
    """Build per-strategy StrategyAudit objects."""
    results = []
    for strat, v in per_strategy.items():
        n = v["n"]
        if n == 0:
            continue
        avg_sz = v["size_sum"] / n
        win_rate = v["wins"] / n
        paper_pnl = v["pnl"]
        paper_ev_pct = (paper_pnl / v["size_sum"]) * 100 if v["size_sum"] > 0 else 0.0

        live_est = estimate_live_pnl(paper_pnl, n, avg_sz)
        live_ev_pct = paper_ev_pct - (LIVE_FRICTION_DELTA * 100)  # subtract friction %

        notes = []
        if n < MIN_TRADES_FOR_CONFIDENCE:
            notes.append(f"low sample n={n} (need {MIN_TRADES_FOR_CONFIDENCE})")
        if win_rate < MIN_WIN_RATE_FOR_LIVE:
            notes.append(f"win rate {win_rate:.0%} < {MIN_WIN_RATE_FOR_LIVE:.0%} threshold")
        if paper_ev_pct < 0:
            notes.append(f"paper EV negative ({paper_ev_pct:+.3f}%/trade)")
        if live_ev_pct < 0:
            notes.append(f"live EV negative after friction ({live_ev_pct:+.3f}%/trade)")
        if live_ev_pct < 0 and paper_ev_pct > 0:
            notes.append("friction destroys positive paper EV — not viable live")

        s = StrategyAudit(
            name=strat,
            n_trades=n,
            win_rate=win_rate,
            avg_size_usd=avg_sz,
            paper_pnl_usd=paper_pnl,
            paper_ev_pct=paper_ev_pct,
            estimated_live_ev_pct=live_ev_pct,
            slippage_drag_usd=live_est["slippage_drag"],
            live_pnl_estimate_usd=live_est["estimated_live_pnl"],
            ready_for_live=False,  # set below
            score=0,
            notes=notes,
        )
        s.score = _score_strategy(s)
        s.ready_for_live = (
            s.score >= 60
            and s.n_trades >= MIN_TRADES_FOR_CONFIDENCE
            and s.estimated_live_ev_pct >= MIN_EV_PCT_FOR_LIVE
        )
        results.append(s)
    return sorted(results, key=lambda x: x.score, reverse=True)


def compute_overall_score(strategies: list[StrategyAudit], total_trades: int) -> int:
    """Overall live readiness score — weighted by trade volume."""
    if not strategies:
        return 0
    # Weight by n_trades
    weighted = sum(s.score * s.n_trades for s in strategies)
    total_n = sum(s.n_trades for s in strategies)
    base = weighted / total_n if total_n else 0
    # Sample penalty if total < 100 trades
    if total_trades < 50:
        base *= 0.5
    elif total_trades < 100:
        base *= 0.75
    return int(min(base, 100))


def build_top_risks(strategies: list[StrategyAudit], overall_score: int) -> list[str]:
    """Identify top 3 live risks from audit."""
    risks = []

    # Risk 1: dominant loser strategies
    losers = [s for s in strategies if s.paper_pnl_usd < -10 and s.n_trades >= 20]
    if losers:
        worst = losers[-1]  # lowest score first... sorted desc, so last is worst pnl
        worst_by_pnl = sorted(strategies, key=lambda x: x.paper_pnl_usd)[0]
        risks.append(
            f"Heavy paper loss concentration: {worst_by_pnl.name} "
            f"n={worst_by_pnl.n_trades}, pnl=${worst_by_pnl.paper_pnl_usd:+.2f}, "
            f"win={worst_by_pnl.win_rate:.0%} — live will add slippage drag "
            f"(${worst_by_pnl.slippage_drag_usd:.2f} additional)"
        )

    # Risk 2: friction dominance (low EV strategies)
    friction_killed = [s for s in strategies if -0.08 < s.paper_ev_pct < 0.15 and s.n_trades >= 5]
    if friction_killed:
        risks.append(
            f"{len(friction_killed)} strategies with paper EV < 0.15%/trade — "
            f"live friction (+{LIVE_FRICTION_DELTA*100:.2f}% = ${LIVE_FRICTION_DELTA*100:.0f}bps) "
            f"eliminates thin margins: {[s.name for s in friction_killed[:3]]}"
        )

    # Risk 3: low sample / signal frequency
    low_sample = [s for s in strategies if s.n_trades < 10 and s.score > 50]
    if low_sample:
        risks.append(
            f"{len(low_sample)} high-score strategies have n<10 trades — "
            f"score is unreliable (survivorship bias in small sample): "
            f"{[s.name for s in low_sample]}"
        )

    # Risk 4: market impact at 50k capital
    large_size_strategies = [s for s in strategies if s.avg_size_usd > 2000]
    if large_size_strategies:
        risks.append(
            f"With 50k capital, avg sizes will scale 10x. "
            f"Strategies with avg_sz > $2000 face meaningful market impact on illiquid pairs. "
            f"Affected: {[s.name for s in large_size_strategies]}"
        )

    if overall_score < 40:
        risks.append(
            f"Overall live readiness score {overall_score}/100 is BELOW 40 — "
            f"SPOT paper trading portfolio is NOT ready for live. "
            f"Need: n>=30 per strategy, win_rate>=40%, EV>=0.10%/trade after friction."
        )

    return risks[:3] if len(risks) >= 3 else risks


def run_audit(output_json: bool = False) -> AuditResult:
    """Main audit entry point."""
    per_strategy = _load_paper_stats()
    strategies = audit_strategies(per_strategy)

    total_paper_pnl = sum(s.paper_pnl_usd for s in strategies)
    total_live_pnl = sum(s.live_pnl_estimate_usd for s in strategies)
    total_slippage = sum(s.slippage_drag_usd for s in strategies)
    total_trades = sum(s.n_trades for s in strategies)
    total_wins = sum(int(s.win_rate * s.n_trades) for s in strategies)
    overall_wr = total_wins / total_trades if total_trades else 0.0
    total_size = sum(s.avg_size_usd * s.n_trades for s in strategies)
    overall_ev_pct = (total_paper_pnl / total_size * 100) if total_size else 0.0

    overall_score = compute_overall_score(strategies, total_trades)
    top_risks = build_top_risks(strategies, overall_score)

    ready_count = sum(1 for s in strategies if s.ready_for_live)
    summary = (
        f"Live Readiness: {overall_score}/100 | "
        f"{ready_count}/{len(strategies)} strategies ready | "
        f"Paper PnL ${total_paper_pnl:+.2f} → Live est. ${total_live_pnl:+.2f} | "
        f"Slippage drag ${total_slippage:.2f} | "
        f"Overall EV {overall_ev_pct:+.3f}%/trade ({total_trades} trades)"
    )

    return AuditResult(
        total_trades=total_trades,
        total_paper_pnl_usd=total_paper_pnl,
        total_live_pnl_estimate_usd=total_live_pnl,
        total_slippage_drag_usd=total_slippage,
        overall_win_rate=overall_wr,
        overall_ev_pct=overall_ev_pct,
        live_readiness_score=overall_score,
        top_risks=top_risks,
        strategies=[asdict(s) for s in strategies],
        summary_line=summary,
    )


def _print_audit(result: AuditResult) -> None:
    print()
    print("=" * 72)
    print("  POLARIS — LIVE READINESS AUDIT")
    print("=" * 72)
    print(f"  Total closed trades  : {result.total_trades}")
    print(f"  Paper PnL            : ${result.total_paper_pnl_usd:+.2f}")
    print(f"  Live PnL estimate    : ${result.total_live_pnl_estimate_usd:+.2f}")
    print(f"  Slippage drag (total): ${result.total_slippage_drag_usd:.2f}")
    print(f"  Overall win rate     : {result.overall_win_rate:.1%}")
    print(f"  Overall EV/trade     : {result.overall_ev_pct:+.3f}%")
    print(f"  Live friction delta  : +{LIVE_FRICTION_DELTA*100:.2f}bps per trade")
    print(f"  Live readiness score : {result.live_readiness_score}/100")
    print()

    # Score gauge
    score = result.live_readiness_score
    gauge_filled = int(score / 5)
    gauge = "[" + "#" * gauge_filled + "-" * (20 - gauge_filled) + "]"
    verdict = "NOT READY" if score < 40 else ("MARGINAL" if score < 60 else "READY")
    print(f"  {gauge} {score}/100 — {verdict}")
    print()

    print("  TOP RISKS:")
    for i, risk in enumerate(result.top_risks, 1):
        # word-wrap at 68 chars
        words = risk.split()
        lines = []
        current = f"  {i}. "
        for w in words:
            if len(current) + len(w) + 1 > 72:
                lines.append(current)
                current = "     " + w
            else:
                current += " " + w if current.strip() else current + w
        lines.append(current)
        print("\n".join(lines))
        print()

    print("-" * 72)
    print("  PER-STRATEGY BREAKDOWN (sorted by score desc):")
    print(f"  {'Strategy':<36} {'n':>4} {'win':>5} {'EV%':>7} {'LiveEV%':>8} {'Score':>6} {'Ready':>6}")
    print(f"  {'-'*36} {'-'*4} {'-'*5} {'-'*7} {'-'*8} {'-'*6} {'-'*6}")
    for s in result.strategies:
        ready_mark = "YES" if s["ready_for_live"] else "no"
        ev = s["paper_ev_pct"]
        lev = s["estimated_live_ev_pct"]
        print(
            f"  {s['name']:<36} {s['n_trades']:>4} {s['win_rate']:>4.0%}  "
            f"{ev:>+6.3f}% {lev:>+7.3f}% {s['score']:>6}   {ready_mark:>4}"
        )
        if s["notes"]:
            for note in s["notes"]:
                print(f"    -> {note}")
    print()
    print(f"  {result.summary_line}")
    print("=" * 72)
    print()


def main() -> int:
    output_json = "--json" in sys.argv
    result = run_audit(output_json=output_json)
    if output_json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        _print_audit(result)
    return 0 if result.live_readiness_score >= 60 else 1


if __name__ == "__main__":
    sys.exit(main())
