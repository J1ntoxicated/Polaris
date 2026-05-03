# Forensic T13 — D11.5 ~ D11.9 (2026-04-24 Fri AEST)

Read-only SQL 5건. DB: `data/invasion.sqlite`. 표기: 관찰=확정, 가설=debate 대상.

---

## D11.5 — OKX 붕괴 timeline (E1)
**SQL**: `trades WHERE exchange='okx' AND asset_group='crypto'` 14d daily + 04-15 hourly/exit_type/ticker.
**관찰**:
- Daily: **04-15 = 2757t -$10,687** (worst). 04-16 -$3,657 (1270t). 04-21 +$7,256 (632t best). 04-22/23 -$316/-$700.
- 04-15 hourly worst: 19h -$2,645 (105t) / 14h -$1,765 / 11h -$1,300 / 16h -$1,139. Worst single -$600.
- Exit_type 04-15: **STOP 434t -$15,108** · TP 470 +$9,009 · TRAIL 407 +$4,440 · **TIME 697 -$6,202** · SIGNAL 748 -$2,827 · DEAD 1 +0.27.
- 184 distinct tickers / 2757t ≈ 15t/ticker/day. Worst: SOON -$1,014 / OP -$764 / LIGHT -$653.

**가설**: Plan "+$720→-$1048 batch exit" 은 특정시각 batch 가 아닌 **04-15 day-wide churn** (Plan E1 재정의와 일치). STOP+TIME -$21k 가 TP+TRAIL +$13.4k 상쇄 → -$10.6k. TIME 697t -$6.2k = hold-timeout 후 강제청산 = asymmetric loss 핵심 경로.
**Plan 연결**: D3 (exit recalc) / D7 (TIME 정책) / E1 재정의.

---

## D11.6 — Stuck 28h 주기 (E3)
**SQL**: `hold_seconds>100000` + status/exit_type.
**관찰**:
- hold>100000 의 exit_type: **broker_removed 49 · TP 1 · BEP 1**. Sample 20건 전원 entry 04-17 20:59 / exit 04-20 13:44 = **64.7h stuck**, 동시 cleanup.
- Cleanup exit 제외 **true live-stuck = 2건만** (USD/CHF TP +$97.73 / BEP -$3.22).
- Status: closed 16364 / open 13 / ghost 380 / quarantined 245.

**가설**: Plan "28h" 는 실측 **64.7h (≈2.7d)**. 04-17 bulk adoption → 04-20 restart 시 broker_removed 일괄 해소 = **restart-gap 패턴**, 28h 주기 아님. E3 원본 문제는 cleanup 으로 이미 해소, 남은 issue = adopted_pending → broker_removed 정상성.
**Plan 연결**: D3.5 (adopted lifecycle) / D9 reconciliation.

---

## D11.7 — CAP direction bias (E4)
**SQL**: `cap` 7d + 24h.
**관찰 7d**: forex-S 270 (wr12.2% -$132) / indices-S 249 (wr4.4% -$260) / indices-L 117 (wr3.4% -$393) / commodity-S 100 (wr8% -$254) / commodity-L 97 (wr2.1% -$101) / forex-L 50 (wr8% -$49). **L 267 : S 619 ≈ 1:2.3**.
**관찰 24h**: forex-S 46 (wr23.9% +$11) / forex-L 11 (wr36.4% +$87.5). **1:4.2**. indices/commodity = closed.

**가설**: Plan "69L/345S ≈ 1:5" → 7d 실측 **1:2.3** (완화), 24h forex **1:4.2** (Plan 근접). Bias 존재 but Plan 수치보다 시점 변동 큼. **전 direction WR 단자리수** → direction bias < signal quality/exit 가 primary. 24h forex only positive = market open 타이밍.
**Plan 연결**: E4 / D3 (universal exit recalc).

---

## D11.8 — Signal acted→entry drop (E5)
**SQL**: `signals` 24h + 3d + exchange.
**관찰**:
- **24h total 122,676 / acted 18,623 (15.2%) / linked 1,212 (0.99%)** → Plan "1%" **정확 매치**.
- 3d: 04-23 109659/17081/1169 (1.07%). 04-24 13558/1620/43 (0.32%).
- Exchange: okx 62855→0.79% / cap 32029→1.42% / alpaca 27793→0.94%.

**가설**: acted 18623 vs linked 1212 gap = **17,411 signal 이 acted=1 but trade_id 없음** → H.1 write 누락 (tasks #20) 유력. 실제 entry 실패 vs write 누락 **linked 만으로 구분 불가**. write 누락이면 entry rate ≈ 15%, gate 차단이면 1% = **15x 범위**. D6 sizing/gate 결정 전 H.1 선결 필수.
**Plan 연결**: D8/H.1 / D6 entry gate.

---

## D11.9 — E2 max_profit_pct=0 실측
**SQL**: 7d zero + exit_type.
**관찰**:
- **7d zero 1,954 / total 4,612 = 42.4%** (Plan 38.6% +3.8pp 증가).
- By exit_type zero%: UNKNOWN_BACKFILL 507/507 (100%) · broker_removed 427/427 (100%) · startup_orphan_cleanup 344/344 (100%) · CLEANUP_STRUCT_DEFECT_T12 170/170 (100%) · TIME 376/1068 (35.2%) · STOP 74/168 (44%) · SIGNAL 40/142 (28%) · **TP 0/623 (0%) · TRAIL 1/1003 (0.1%) · BEP 0/128 (0%)**.
- "정상 exit (TP/TRAIL/BEP/AI)" zero = 1/1754 = **0.06%** → normal path 정상.
- Cleanup path 전체 zero = 1,448 (전체 zero 의 74%).

**가설**: Plan E2 "stale/flush 누락" 은 partially 맞음 — cleanup exit 은 정의상 fill 없음 → track 불가 (noise). **진짜 이상은 TIME 35% · STOP 44% · SIGNAL 28% live path zero** → exit 시점 last_price/max_profit_ts refresh 단절 추정. Core issue = cleanup 제외한 live exit path.
**Plan 연결**: D3 (exit recalc) / E2 재정의 (cleanup 제외 metric).

---

**Summary 3줄**:
1. **04-15 OKX crypto -$10.6k single day** (E1) — STOP+TIME -$21k 가 TP+TRAIL +$13.4k 초과 흡수, batch 가 아닌 day-wide churn.
2. **Stuck 64.7h (Plan 28h 오표기)** — live-stuck 2건만, cleanup 으로 해소 완료. 남은 이슈 = adopted_pending lifecycle 정상성.
3. **signal 1% linked 실측 정확 + max_profit=0 42.4%** — acted-linked gap 17k = H.1 write 누락 vs gate 차단 구분 선결 / zero 는 TIME/STOP live path 35~44% 가 core (cleanup 제외).
