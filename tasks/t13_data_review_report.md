# T13 Phase 2 — Data 전수 분석 (t13_data_review_report.md)

> **방법**: `data/invasion.sqlite` 직접 SQL 쿼리. 7d window 기본, E1 경우 14d.
> **원칙**: 관찰 기록. 단정 금지. 값 → 해석 → 확인 필요 3분법.
> **범위**: signal / trade / trade_events / strategy_cell_matrix / ticker_baseline 4축 + E1 forensic.

---

## 0. Schema + Row Counts 스냅샷

| 테이블 | 행 수 | 비고 |
|---|---|---|
| trades | 16,988 | max_profit_pct 컬럼 존재 (DEFAULT 0) |
| signals | 143,647 | **trade_id FK 이미 존재** (Phase 0 agent 오인 보고) |
| trade_events | 210,812 | **Plan H.1 trade_events 테이블 이미 존재** |
| strategy_cell_matrix | 216 | 6-dim cell (T12 확장 반영) |
| ticker_baseline | 126 | `pnl_pct_std` 등 5-metric 확장됨 |

Phase 0 plan_vs_code 재교정 포인트:
- trade_events 테이블 존재 → Plan H.1 은 **스키마 신규 X, 구조화 enrich**.
- signals.trade_id FK 존재 → E7 는 "FK 부재" 아닌 **"linkage 누락 (99%)"** 로 재정의.

---

## 1. E2 — max_profit_pct=0 실측 (7d closed)

| status | n | zero_n | zero% | avg_pnl | avg_max_profit |
|---|---|---|---|---|---|
| closed | 4,386 | 1,691 | **38.6%** | -0.023 | 0.224 |
| quarantined_structural_defect | 170 | 170 | 100% | - | 0 |
| quarantined_noise | 9 | 8 | 88.9% | 11.104 | 13.326 |

**관찰**:
- 7d closed trades 의 **38.6% 가 max_profit_pct=0 으로 기록**. Phase 0 의 "flush 누락 vs peak tracking stuck" 가설이 실측으로 큰 비중 확인.
- avg_max_profit_pct 0.224 는 전체 평균이고 flush 안 된 38.6% 를 빼면 실제 peak 경험자의 평균은 더 높음.
- quarantined_structural_defect (170건) 은 T12 cleanup 로 잡힌 sample — **Pillar 5 Unfillable-Queued 의 원시 prototype 이 이미 운영 중**.

**확인 필요**:
- max_profit_pct=0 은 peak 객체 값 자체가 0 으로 유지된 건가, close 시점에 flush 가 생략된 건가 — `trade/position.py` peak tracking 로직 + close 경로 step-by-step.
- 짧은 hold (<30s) 포지션이 peak 축적 전 청산된 비율 교차.

---

## 2. E16 — Session × Exchange (7d closed, entry_ts 기반)

### OKX
| session | n | WR% | Σpnl ($) |
|---|---|---|---|
| us_late | 544 | **65.3** | +246.4 |
| europe_early | 213 | 62.9 | +148.6 |
| asia_close | 251 | 57.0 | -59.0 |
| asia_core | 211 | 57.8 | -183.7 |
| europe_late | 264 | 53.8 | -295.3 |
| asia_tokyo_open | 355 | 55.8 | -330.4 |
| asia_syd_pre | 272 | 51.8 | -477.4 |
| **us_core** | 348 | 56.3 | **-1808.0** |

### Alpaca
| session | n | WR% | Σpnl ($) |
|---|---|---|---|
| us_core | 271 | 37.3 | +919.4 |
| us_late | 152 | 46.1 | +448.1 |
| europe_late | 276 | 10.1 | -484.7 |
| europe_early | 113 | 0.0 | 0.0 |
| asia_* | 12 | 0.0 | 0.0 |

### CAP
| session | n | WR% | Σpnl ($) |
|---|---|---|---|
| 전 session | 436 | 8-22 | -$1,160+ |

**관찰**:
1. **OKX us_core 348 trades WR 56.3% -$1808** 가 7d 최대 출혈. (직전 T12 관측 "US OKX 74% WR" 는 1h 샘플 값, 7d 누적은 다름.)
2. **OKX us_late 544 trades WR 65.3% +$246** 는 유일한 성공 segment.
3. **Alpaca us_core +$919** vs **Alpaca europe_late -$485** — 같은 exchange 내 session 별 극명한 비대칭.
4. Alpaca europe_late 276 trades 0-10% WR 은 entry_ts 가 UTC 11-13 (AEST 21-23, US premarket 7-9AM). 의심: alpaca paper 계정이 premarket/extended hours 포지션 → 저유동 + wide spread 로 구조적 손실.
5. CAP 전 session 적자 — T12 ITEM-015 retire 이후에도 잔여 손실 지속.

**확인 필요**:
- cell_matrix 의 session 축이 위 분포와 일관되게 계산되는지 (`cell_matrix.py:55-61` `_session_8band` 검증 Phase 0 완료).
- Alpaca premarket/extended hours 포지션 eligibility — config / alpaca adapter 의 시간 gate.
- OKX us_core -$1808 의 ticker/strategy 분해 (E1 forensic 과 교차).

---

## 3. E1 — OKX batch exit forensic (14d, pnl < -$500/h)

| hour (UTC) | n | Σpnl | TIME | TRAIL | STOP |
|---|---|---|---|---|---|
| 2026-04-15 14:00 | 123 | **-3523.5** | 48 | 13 | 11 |
| 2026-04-15 19:00 | 131 | -2664.9 | 44 | 14 | 16 |
| 2026-04-16 05:00 | 146 | -1638.0 | 42 | 17 | 13 |
| 2026-04-15 11:00 | 108 | -1318.0 | 41 | 17 | 13 |
| 2026-04-16 00:00 | 125 | -1285.6 | 37 | 11 | 10 |
| 2026-04-15 16:00 | 129 | -1122.5 | 41 | 15 | 9 |
| 2026-04-16 02:00 | 131 | -959.3 | 55 | 10 | 8 |
| 2026-04-15 22:00 | 125 | -941.9 | 54 | 9 | 8 |
| 2026-04-15 18:00 | 138 | -901.3 | 54 | 17 | 7 |
| 2026-04-12 01:00 | 36 | -879.2 | 3 | 14 | 11 |

**관찰**:
- 2026-04-15 일어난 **연쇄 batch exit** (11:00~22:00 UTC 에만 10+ 시간, 총 -$12,000 급). T12 관측 "+$720 → -$1048" 는 mild subset.
- 모든 batch 에서 TIME 이 TRAIL/STOP 보다 많음 (40-55 vs 10-17).
- 04-12 01:00 은 STOP 11 / TRAIL 14 / TIME 3 구조 — 타 배치와 다른 성격 (샘플 36건).

**04-15 14:00 UTC 최악 배치 ticker 분해**:
- TSLA short (stock_specialist_g18_g24_bayes) 1건 **-$1,392** (단일 trade 재난)
- crypto_momentum_reversal_g11_ai short 여러 ticker (SATS/OP/SHIB/BOME/UNI/SUI/MEW/SOON) 합 -$1,400+
- stock_specialist_g18_g24 long/ai 여러 건 -$200~400

**해석**:
- 주범 전략 2종 (`crypto_momentum_reversal_g11_ai`, `stock_specialist_g18_g24_bayes`) 은 **T12 ITEM-020 / ITEM-016 에서 이미 retire 완료**. 이후 현재까지 재발 흔적 없음 — 봇 생존의 직접 원인.
- 그러나 **TIME exit suppression 해제 타이밍 batch 동기화 구조 결함** 은 여전. 결함 2 (TIME→TRAIL_PROTECTED 고착) 와 별개 layer.

**확인 필요**:
- 04-15 당시 TIME suppression 해제 trigger (peak drop / loss cap) 가 macro 이벤트 (BTC 변동 등) 와 동기화 되었는지.
- 현재 봇 코드에 이 type batch 재발 방지 기제 존재 여부 (TIME exit distribution 축).

---

## 4. 결함 3 — alpaca asset_group=crypto 실증 (7d)

| exchange | asset_group | n | avg_pnl |
|---|---|---|---|
| alpaca | stock | 897 | +0.016 |
| alpaca | etf | 256 | +0.017 |
| alpaca | **crypto** | **19** | **-0.615** |
| cap | (empty) | 3 | +0.741 |

**관찰**:
- **Alpaca crypto 19건 확인**. Phase 0 의 `trade/position.py:337` fallback `"crypto"` 가설 직접 증거.
- avg_pnl -0.615 는 정상 alpaca 거래 (0.016~0.017) 대비 **30~40배 손실**. 분류 오류가 잘못된 sizing/exit 경로 유발.
- CAP 에 asset_group 빈 행 3건 (별도 결함 가능).

**확인 필요**:
- Alpaca crypto 19건 의 ticker list — 실제 crypto 일 수도 있음 (alpaca 는 BTC/ETH 등 crypto 지원). Jin 의도 vs fallback 실수 구분.

---

## 5. E7 + E5 — signals ↔ trades linkage (7d)

| metric | value |
|---|---|
| total signals | 143,680 |
| acted_on=1 | 22,093 |
| trade_id NOT NULL | 1,267 |
| acted% | **15.38%** |
| linked% | **0.88%** |

**관찰** (재정의):
- Phase 0 agent 는 "signals/bus.py 에 trace_id 없음 → forensic 불가" 로 봤으나 실제로는 **signals.trade_id FK 가 schema 에 존재**.
- 문제는 **값 자체가 없다**: acted_on=1 인 22,093 중 trade_id 링크된 것 1,267 (5.7%). 다른 20,826 은 linkage 누락.
- E5 "Signal acted→entry 99% drop" 의 원시 증거가 이것 — "acted 했는데 entry 체결 안 됨" 이 아니라 "**entry 체결됐지만 trade_id 가 signal 로 write-back 안 됨**" 으로 읽을 수도 있음 (확인 필요).

**확인 필요**:
- signals.trade_id write 경로 grep (`acted_on=1` 세팅 site 에서 trade_id 도 같이 update 하는지).
- bus.py publish payload 에 trade_id/signal_seq 함께 실리는지 (H.1 trace_id 는 여전히 필요한가 vs 기존 FK 채우는 것으로 충분한가).

---

## 6. trade_events 구조 (7d)

| event_type | n |
|---|---|
| `trade_event` | 7,095 |
| `TRAIL max=+0.4% now=+0.2% dist=0.2%` | 127 |
| `TRAIL BEP max=+0.2% floor=+0.1% now=+0.1%` | 126 |
| `TIME STAGNANT 43min max=+0.15% pnl=+0.03%` | 94 |
| 외 다수 TIME / TRAIL 변형 | ~수백 |

**관찰**:
- event_type 에 **reason 문자열이 그대로 들어감** — 구조화 안 됨. Plan H.1 의 "trade_events 구조화" 는 스키마 확장이 아닌 **column split** (event_type 고정 enum + details 분리) 이 정확한 표현.
- 같은 의미 (`TIME STAGNANT`) 가 백분율 다르다고 별개 event_type 으로 분리됨 → group-by 불가.

**확인 필요**:
- trade_events 쓰기 site grep (`exit_cycle.py` / `exit_fsm.py` / `close_handler.py`) — reason 문자열을 event_type 으로 쓰는 위치.

---

## 7. 결함 5 — Quarantine 이미 운영 중

| status | exit_type | n |
|---|---|---|
| quarantined_structural_defect | CLEANUP_STRUCT_DEFECT_T12 | 170 |
| quarantined_noise | TIME | 65 |
| quarantined_noise | broker_removed | 5 |
| quarantined_noise | STOP -99.07% (limit -2.5%) | 1 |
| quarantined_noise | TRAIL CAP +99.09% (cap 5.0%) | 1 |

**관찰**:
- trades.status 에 `quarantined_*` 상태 이미 존재 — T12 에서 **cleanup 자동화의 원시 구현**.
- STOP/TRAIL 99% 같은 단위 anomaly 도 quarantine 으로 격리됨 (노이즈 차단 효과).
- Plan Pillar 5 의 Unfillable-Queued 는 이 패턴 확장 — 기존 quarantine 레이어와 통합 설계 필요 (별도 축 만들지 말 것).

**확인 필요**:
- quarantined_* 설정 site + 해제 조건 grep.

---

## 8. Phase 2 종합 판정

### Plan v2.1 수정 트리거 (Phase 2 → Phase 2 plan v2.2 update 대상)
1. **Plan H.1 trade_events "구조화"**: 스키마 신규가 아닌 column split. v2.2 에서 재표현.
2. **Plan E7 "trace_id 부재"**: signals.trade_id FK 는 schema 에 존재하나 값 99% 누락. 선결은 **write 경로 fix** + bus payload trace_id (cross-process correlation).
3. **Plan Pillar 5 Unfillable-Queued**: 기존 quarantined_* 확장으로 재표현.
4. **Plan E1 "batch exit"**: 04-15 의 -$12K+ 재난이 주범 전략 2종 retire (T12 ITEM-020/016) 으로 재발 없음. **PHS 설계 시 retire 의존 vs 구조 재발 방지 근거로 재확인**.

### Phase 3 (Harness) 선결 후보
- profit_target learner `*100` 잔존 (D-A debate) — Phase 3 validator hook 으로 regression 방지
- signals.trade_id write 경로 확정 (E5/E7) — Phase 3 로깅 audit 범위
- Alpaca asset_group=crypto (19건) 교정 (D-D) — Phase 3 pre-commit hook 에서 catch 가능

### T13 debate 에 추가 반영
- **D-G (신규)**: TIME suppression 해제 batch 동기화 원인 — macro 이벤트 corr vs 구조적 (04-15 14:00 forensic 기반).
- **D-H (신규)**: quarantined_structural_defect vs Unfillable-Queued 통합 vs 분리.
- **D-I (신규)**: Alpaca europe_late 276 trades 0-10% WR 처리 — premarket 포지션 eligibility 재정의.

### 다음 (Phase 3) 입력
- 본 파일 + audit 2종 → Harness 감사 시 focus: Plan vs Code gap + Phase 0 신규 debate 9항 (D-A~I) 의 auto-enforce 설계.
