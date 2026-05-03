# Harness Hourly Audit Log

자동 1h 주기 감사 (Jin 지시 2026-04-21 01:25 AEST).
긴급 임계: bot dead / 1h DD > -$500 / ERROR spike / new structural leak.

---

## AUDIT-10 · 2026-04-21 11:06 AEST (Tokyo 10:06) — Phase wire 발동 0회 + ATR scale 의문
- **Bot**: PID 12678 alive (uptime 49s post ATR amp restart @11:00)
- **1h trades**: 4 / WR 50% / -$21 (cap 2 / okx 2)
- **Phase wire activity**: ATR amp 0 / cell_matrix_skip 0 / conviction 0 → 모두 **미발동**
- **원인 분석**:
  - ATR: 3% threshold 가 실제 scale 과 mismatch 가능성 (trades.atr_at_entry raw 평균 113 / max 10189)
  - Cell: 대부분 n<10 sparse → None fallback (정상, Phase A 데이터 아직 얕음)
  - Conviction: 같은 cycle 에 multi-strategy agree 없음 (자연발생 대기)
- **STOP BLIND (10min)**: 2 / ERROR 0
- **Alerts**: 14 → archive
- **Status**: 🟡 기능 배포 OK but trigger 미발동 — 다음 tick 에 데이터 더 관측, ATR threshold 재검토 필요
- **Next**: 12:06 AEST (Jin 점심 결정 타이밍)

---

## AUDIT-09 · 2026-04-21 10:03 AEST (Tokyo 09:03 JST 개장) — Phase B 조용히 작동
- **Bot**: PID 96710 alive (10m35s post Asia 8-band restart @09:35)
- **1h trades**: 2 / WR **100%** / **+$19** (OKX only, quiet)
- **Phase B**: skip/mult 로그 0건 — sparse fallback (n<10) 정상 동작. 버그 아님
- **Cell matrix reviewer**: **2 runs** ✓ (437 cells, findings 0 drift)
- **Asia session**: Tokyo 막 개장, 아직 flow 미미. asia_tokyo_open 데이터 축적 대기.
- **STOP BLIND/ERROR (10min)**: 1 (minor) / 0
- **Alerts**: 10 → archive
- **Status**: 🟢 Phase B 안정 / Asia 세션 flow 기다림
- **Next**: 11:00 AEST

---

## AUDIT-08 · 2026-04-21 08:58 AEST (Mon 18:58 EDT) — Phase A 배포 후
- **Bot**: PID 79711 alive (uptime 9m23s from Phase A restart @08:55)
- **1h trades**: 3 / WR 0% / -$150 (CAP only, 매우 quiet)
- **원인**: VIX long (strategy_id 비어있음, adopted 포지션) 75min -$143 단독 ← engine 경로 밖
  - AUD/USD short forex_g195_struct -$5 / Copper long commodity_g193 -$2 (ITEM-021 retired 의 pre-restart open)
- **Cell matrix**: 437 cells DB 저장 ✓ (reviewer 1h tick 아직 — 다음 주기에 첫 finding)
- **STOP BLIND/ERROR**: 0
- **Alerts**: 19 → archive
- **Status**: 🟢 quiet, VIX -$143 은 adopted 로 engine 수정 불가
- **Next**: 09:58 AEST (Tokyo 개장 직후 첫 asia_early cell 관측 기대)

---

## AUDIT-07 · 2026-04-21 07:39 AEST (Mon 17:39 EDT) — ITEM-020/021 검증
- **Bot**: PID 48876 alive (uptime 7m47s from ITEM-021 restart @06:47)
- **1h trades**: 4 / WR 0% / -$31 (CAP only, NY 마감 후 quiet)
- **Kill 검증 ✓**:
  - `crypto_momentum_reversal_g11_ai`: **status=retired** (tournament 자동 + kill list 이중)
  - `crypto_specialist_g193 × short`: 3 REJECT 로그 작동 중
  - `indices/commodity_g193 × long`: signal fire 없음 (52min)
- **STOP BLIND/ERROR**: 0
- **Alerts**: 13 → archive
- **Status**: 🟢 Jin 조사 요청 완결, 모든 retire 정상 작동
- **Next**: 08:40 AEST

---

## AUDIT-06 · 2026-04-21 06:38 AEST (Mon 16:38 EDT) 🎉 대폭 회복
- **Bot**: PID 16970 alive (uptime 18m55s, CPU 20.8%)
- **1h trades**: 24 / WR **79.2%** / **+$1,152.75** (AUDIT-04 -$628 → +$1,153 완전 반전)
- **Exit mix**: **TIME 22/+$1,216** (NY 16:00 EDT 마감 직후 EOD 대량 청산 positive 확정) · TRAIL 1/-$24 · BEP 1/-$40
- **Post-restart ENTRY (d9a1ed93)**: crypto/stock g193 × short **0** ✓ (kill 완전 정상)
- **Kill REJECT (10min)**: 10건 작동 중 / **STOP BLIND 0 / ERROR 0**
- **Alerts**: 15 → archive
- **Status**: 🟢 복구 완료 + 누적 cohort positive
- **Next**: 07:38 AEST

---

## AUDIT-05 · 2026-04-21 05:34 AEST (Mon 15:34 EDT) — 회복 확인
- **Bot**: PID 16970 alive (uptime 9m25s post-restart, CPU 9.9%)
- **1h trades (exit_ts rolling)**: 14 / WR 14.3% / -$423 (pre-restart entry closed 포함)
- **Post-restart ENTRY 실제**: 7건 — etf_g193×(L/S), forex g104/g107/g53, indices_g193×L. 총 ~-$4 (미결 4 open)
- **Kill list 검증**: ✓ `strategy_direction_killed` REJECT **132건** post-restart (crypto_g193×short / stock_g193×short/long 전부 차단 확인)
- **오판 정정**: 이전 AUDIT-05 초기 진단 "10 trades -$744" 은 pre-restart entry 의 rolling window 포함. 실제 신규 kill bypass 없음.
- **STOP BLIND/ERROR**: 0
- **Alerts**: 30 → archive
- **Status**: 🟢 회복 — 1h rolling window 만 pre-restart cohort 잔재. 다음 audit 로 정상화 확인
- **Next**: 06:34 AEST

---

## AUDIT-04 · 2026-04-21 04:30 AEST (Mon 14:30 EDT) 🚨 EMERGENCY
- **Bot**: PID 71207→**16970** (emergency restart 04:35)
- **1h trades**: 18 / WR **0.0%** / **-$628.42** (threshold -$500 초과)
- **Exit mix**: TIME 13/-$584 · BEP 4/-$29 · SIGNAL 1/-$16
- **원흉**: `_g193` cluster 폭발
  - crypto_specialist_g193 × short 5/-$398 (TRB -$250 size $13K anomaly)
  - stock_specialist_g193 × short 5/-$168 (VFC -$102, MRVL -$41)
- **Action**: commit `d9a1ed93` — 2건 structural retire 추가 (kill list 7→9)
  - Long legs 보존 (crypto_g193 long = +$139 기존)
- **Restart**: `bash start.sh` 04:35 AEST → PID 16970 alive
- **Status**: 🔴 → 🟡 대응 완료 (다음 audit 로 회복 확인)
- **Next**: 05:30 AEST

---

## AUDIT-03 · 2026-04-21 03:28 AEST (Mon 13:28 EDT)
- **Bot**: PID 71207 alive (CPU 6.2%, uptime 16m40s)
- **1h trades**: 30 / WR **40.0%** / **-$102.59** (15% → 26% → 40% 연속 개선)
- **Exit mix**: **TRAIL 15/+$198** · TIME 8/-$240 · BEP 6/-$30 · SIGNAL 1/-$30
- **STOP BLIND (10min)**: 0 ✓ (P0-3 지속)
- **ERROR (10min)**: 0
- **Alerts**: 15 → archive (이미 이전 batch 에 포함)
- **Status**: 🟢 OK — TRAIL 우세 (+$198), net leak $103 감소
- **Action**: None (TIME -$240/h lingering 관찰, 긴급 아님)
- **Next**: 04:28 AEST

---

## AUDIT-02 · 2026-04-21 02:27 AEST (Mon 12:27 EDT)
- **Bot**: PID 71207 alive (CPU 5.6%, uptime 8m39s — 안정)
- **1h trades**: 23 / WR 26.1% / **-$142.45** (직전 -$342 대비 개선)
- **Exit mix**: TIME 11/-$251 · BEP 6/-$42 · TRAIL 5/+$119 · SIGNAL 1/+$32
- **STOP BLIND (10min)**: **0** ✓ (P0-3 resubscribe 효과 확인)
- **ERROR/CRITICAL (10min)**: 0
- **Alerts**: 30 (wr_1h 15 + subsystem 14 + loss_streak 2 + dd_1h 1) → batch archive
- **Status**: 🟢 OK — leak 지속하나 개선 추세, 긴급 없음
- **Action**: None (WR 26%는 threshold 30% 근접, TIME 계속 -$251/h 관찰)
- **Next**: 03:27 AEST

---

## AUDIT-01 · 2026-04-21 01:25 AEST (Mon 11:25 EDT)
- **Bot**: PID 71207 alive (CPU 18.7%, uptime 44s post-restart `136b35e1`)
- **1h trades**: 52 / WR 15.4% / **-$341.72**
- **Exit mix**: BEP 22/-$225 · TIME 19/-$372 · TRAIL 6/+$127 · SIGNAL 3/+$10 · TP 2/+$118
- **New alerts**: 10 (4 wr_1h + 6 subsystem)
- **STOP BLIND**: 7 (USD/CHF 191min · NVO 35min · BAC 26min · USD-Index 20min · GBP/USD 17min) — P0-3 resubscribe 60s 주기, 첫 cycle 대기
- **Defensive REJECT**: 0 ✓ (T9 sweep 정상)
- **Status**: 🟡 WATCH — WR low (NY open noise), TIME/BEP leak continues, no action needed (<$500 threshold)
- **Next**: 02:25 AEST
