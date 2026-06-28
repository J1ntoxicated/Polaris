"""Run the 4h-trend backtest matrix + report. DEMO/PAPER research.

flow_not_block: validating edge, honest net. If fee-fatal, say fee-fatal.
"""
from __future__ import annotations

import sqlite3

from _4h_backtest import collect_returns, summarize

DB = "/Users/jinyoon/Projects/Polaris/data/research_4h.sqlite"


def symbols() -> list[str]:
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT symbol FROM c WHERE bar='4H'").fetchall()
    db.close()
    return [r[0] for r in rows]


def time_split() -> tuple[int, int]:
    db = sqlite3.connect(DB)
    lo, hi = db.execute("SELECT MIN(ts),MAX(ts) FROM c WHERE bar='4H'").fetchone()
    db.close()
    return lo, lo + (hi - lo) // 2  # midpoint


def fmt(r) -> str:
    return (
        f"  n={r.n:5d} win={r.win_rate*100:5.1f}% "
        f"gross={r.gross_bps:+7.1f}bps "
        f"net_taker={r.net_taker_bps:+7.1f} net_maker={r.net_maker_bps:+7.1f} "
        f"cont={r.cont_rate*100:4.1f}% med={r.fwd_median_bps:+6.1f}"
    )


def main() -> None:
    syms = symbols()
    lo, mid = time_split()
    span_days = 180
    print(f"# 4h-trend backtest | {len(syms)} OKX USDT majors | ~{span_days}d "
          f"native 4H | fees: taker 70bps/leg, maker 8bps/leg\n")

    # Horizons in 4H bars: 3=12h, 6=24h(1d), 12=48h(2d), 18=72h(3d)
    horizons = [3, 6, 12, 18]
    variants = [
        ("tsmom",      {"n": 6}),    # 6-bar (24h) momentum cross
        ("tsmom",      {"n": 12}),   # 12-bar (48h) momentum cross
        ("ma_cross",   {"fast": 10, "slow": 30}),
        ("ma_cross",   {"fast": 20, "slow": 50}),
        ("donchian",   {"d": 20}),   # 20-bar (~3.3d) breakout
        ("donchian",   {"d": 40}),   # 40-bar (~6.7d) breakout
        ("confluence", {"d": 20, "r": 20}),  # 4H D20 breakout + 1D>SMA20
        ("confluence", {"d": 40, "r": 20}),
    ]

    for variant, params in variants:
        label = f"{variant}{params}"
        print(f"## {label}")
        for h in horizons:
            full = collect_returns(syms, variant, h, params, lo, 10**13)
            r_full = summarize(f"H={h}", full)
            # OOS split
            ins = collect_returns(syms, variant, h, params, lo, mid)
            oos = collect_returns(syms, variant, h, params, mid, 10**13)
            r_ins = summarize("IS", ins)
            r_oos = summarize("OOS", oos)
            print(f" H={h:2d}bar({h*4:2d}h) FULL{fmt(r_full)}")
            print(f"            IS  {fmt(r_ins)}")
            print(f"            OOS {fmt(r_oos)}")
        print()

    # Slippage stress on best-looking config (donchian d40 / confluence d40)
    print("## slippage stress (per-leg bps added) — donchian d40 & confluence d40, H=6")
    for variant, params in (("donchian", {"d": 40}),
                            ("confluence", {"d": 40, "r": 20})):
        fwd = collect_returns(syms, variant, 6, params, lo, 10**13)
        print(f" {variant}{params}:")
        for slip in (0, 10, 15, 20):
            r = summarize(f"slip{slip}", fwd, slippage_bps=slip)
            print(f"   slip={slip:2d}bps net_taker={r.net_taker_bps:+7.1f} "
                  f"net_maker={r.net_maker_bps:+7.1f} (n={r.n})")

    # Firing frequency
    print("\n## firing frequency (signals/day across all 30 symbols)")
    for variant, params in variants:
        cnt = len(collect_returns(syms, variant, 1, params, lo, 10**13))
        print(f" {variant}{params}: {cnt} signals / {span_days}d "
              f"= {cnt/span_days:.2f}/day total, "
              f"{cnt/span_days/len(syms):.3f}/day/symbol")


if __name__ == "__main__":
    main()
