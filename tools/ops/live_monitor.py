#!/usr/bin/env python3
"""Live monitor — executions / profit-calc / gate activity / health.

Jin 2026-06-23: "거래체결 되는거랑 프로핏 계산하는거 좀 계속 모니터링 + 게이트 엑티비티".
Run anytime: `python3 tools/ops/live_monitor.py`. Also folded into the autonomous loop.
Read-only. Flags anomalies (churn, profit-calc mismatch, PF<1, errors).
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import urllib.request

DB = "data/polaris_live.sqlite"
LOG = "data/paper/polaris_runtime.log"
SNAP = "http://localhost:8770/api/snapshot"


def _cols(c: sqlite3.Connection, t: str) -> list[str]:
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


def main() -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    fc = _cols(c, "fills")
    pid = "contribution_id" if "contribution_id" in fc else "position_id"
    tsc = next((x for x in ("ts_ms", "ts", "created_ts", "filled_ts") if x in fc), "ts_ms")

    reset = None
    with contextlib.suppress(sqlite3.Error):
        reset = c.execute(
            "SELECT reset_ts,label FROM measurement_resets ORDER BY reset_ts DESC LIMIT 1"
        ).fetchone()
    rt = reset["reset_ts"] if reset else 0
    now = c.execute("SELECT MAX(closed_ts) m FROM positions").fetchone()["m"] or 0
    scale = 1000 if tsc == "ts_ms" else 1
    win = 900 * scale
    flags: list[str] = []

    print(f"===== LIVE MONITOR  (reset: {reset['label'] if reset else 'none'}) =====")

    print("⚡ 체결 (15분):")
    okx_closes = 0
    for r in c.execute(
        f"SELECT p.venue v,SUM(CASE WHEN f.is_close=0 THEN 1 ELSE 0 END) o,"
        f"SUM(CASE WHEN f.is_close=1 THEN 1 ELSE 0 END) cl FROM fills f "
        f"JOIN positions p ON p.position_id=f.{pid} WHERE f.{tsc}>=? GROUP BY p.venue",
        (now * scale - win,),
    ):
        print(f"   {r['v']:8} 진입 {r['o'] or 0:>3} 청산 {r['cl'] or 0:>3}")
        if r["v"] == "okx":
            okx_closes = r["cl"] or 0
    if okx_closes > 15:
        flags.append(f"OKX churn 높음: 15분간 청산 {okx_closes}건")

    r = c.execute(
        f"SELECT COUNT(DISTINCT p.position_id) n,COALESCE(SUM(f.pnl_usd),0) net,"
        f"COALESCE(SUM(ABS(f.fee_usd)),0) fees,SUM(CASE WHEN f.pnl_usd>0 THEN 1 ELSE 0 END) w "
        f"FROM positions p JOIN fills f ON f.{pid}=p.position_id AND f.is_close=1 "
        f"WHERE p.opened_ts>=? AND p.status='closed'",
        (rt,),
    ).fetchone()
    rec = c.execute(
        "SELECT COUNT(*) n FROM positions WHERE opened_ts>=? AND status='reconciled'", (rt,)
    ).fetchone()["n"]
    n, net, fees, w = r["n"], r["net"], r["fees"], r["w"] or 0
    wr = 100 * w / max(n, 1)
    print(
        f"\U0001f4b0 프로핇 (리셋 후): 정상 {n}건 | gross ${net + fees:+.2f} | "
        f"net ${net:+.2f} (fee ${fees:.2f}) | 승률 {wr:.0f}% | reconciled제외 {rec}"
    )

    # 경제 상시 감사 — 전략별 gross vs fee vs net, 수수료가 가격엣지 먹는 누수 자동 플래그.
    print("\U0001f4ca 전략별 (리셋 후, gross/fee/net):")
    for r in c.execute(
        f"SELECT p.strategy_id s,COUNT(DISTINCT p.position_id) n,"
        f"COALESCE(SUM(f.pnl_usd),0) net,COALESCE(SUM(ABS(f.fee_usd)),0) fee "
        f"FROM positions p JOIN fills f ON f.{pid}=p.position_id AND f.is_close=1 "
        f"WHERE p.opened_ts>=? AND p.status='closed' GROUP BY p.strategy_id "
        f"HAVING n>=2 ORDER BY net ASC",
        (rt,),
    ):
        gross = r["net"] + r["fee"]
        tag = ""
        if r["fee"] > abs(gross) and r["fee"] > 2:
            tag = " ⚠️수수료>엣지"
            flags.append(f"{r['s']} 수수료출혈: fee ${r['fee']:.1f} > gross ${gross:+.1f} (maker 진입 검토)")
        print(
            f"   {r['s']:18} n={r['n']:>2} gross ${gross:+.1f} "
            f"fee ${r['fee']:.1f} net ${r['net']:+.1f}{tag}"
        )

    direct_net = net
    try:
        d = json.load(urllib.request.urlopen(SNAP, timeout=4))  # noqa: S310
        print("\U0001f6aa 게이트:")
        for g in d.get("gate_decisions") or []:
            print(f"   G{g['gate_id']} {g['label']:10} {g['headline']}")
        sr = d.get("since_reset")
        if sr:
            pf = sr.get("pf", 0)
            print(
                f"   since_reset n={sr['n']} pf={pf:.2f} "
                f"net=${sr.get('net_usd', 0):.1f} win={sr.get('win_pct', 0):.0f}%"
            )
            if abs(sr.get("net_usd", 0) - direct_net) > 5:
                flags.append(
                    f"프로핇 불일치: dashboard ${sr.get('net_usd', 0):.1f} "
                    f"vs 직접 ${direct_net:.1f} (대시보드 재기동 필요?)"
                )
            if sr["n"] >= 20 and pf < 1.0:
                flags.append(f"PF<1 (적자): pf={pf:.2f}")
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"\U0001f6aa 게이트: 스냅샷 unavailable ({type(e).__name__})")

    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            tail = f.readlines()[-300:]
        errs = sum(1 for ln in tail if ("Traceback" in ln or "CRITICAL" in ln or "rejected code" in ln))
        if errs:
            flags.append(f"로그 에러/reject {errs}건 (최근 300줄)")
    except OSError:
        pass

    # 구조 상시 감사 — 500 LOC 규율 부채를 매 iteration 가시화(누적 방지).
    over = []
    for root, _, files in os.walk("polaris"):
        if "test" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with contextlib.suppress(OSError), open(
                os.path.join(root, fn), encoding="utf-8", errors="ignore"
            ) as fh:
                ln = sum(1 for _ in fh)
                if ln > 500:
                    over.append((ln, fn))
    over.sort(reverse=True)
    top = ", ".join(f"{fn}:{ln}" for ln, fn in over[:3])
    print(f"\U0001f3d7️ 구조: 500 LOC 초과 {len(over)}개" + (f" (최대 {top})" if over else " ✅"))
    egregious = sum(1 for ln, _ in over if ln > 800)
    if egregious:
        flags.append(f"모듈화 부채: {egregious}개 파일 >800 LOC")

    # 용량 상시 감사 — DB/WAL(retention=daily prune, WAL=재기동 시 reclaim, 무한증식 차단).
    db_mb = wal_mb = 0.0
    with contextlib.suppress(OSError):
        db_mb = os.path.getsize(DB) / 1e6
    with contextlib.suppress(OSError):
        wal_mb = os.path.getsize(DB + "-wal") / 1e6
    print(f"\U0001f4be 용량: DB {db_mb:.0f}MB + WAL {wal_mb:.0f}MB")
    if wal_mb > 200:
        flags.append(f"WAL creep {wal_mb:.0f}MB (>200 — 재기동 체크포인트 필요)")
    if db_mb > 900:
        flags.append(f"DB {db_mb:.0f}MB (>900 — retention 윈도우/VACUUM 검토)")

    if flags:
        print("⚠️ 이상:")
        for fl in flags:
            print(f"   - {fl}")
    else:
        print("✅ 이상 없음")


if __name__ == "__main__":
    main()
