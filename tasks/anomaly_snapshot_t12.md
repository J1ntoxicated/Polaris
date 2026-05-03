# Anomaly Snapshot — T12 관찰 전용 (코드/포지션 무개입)

목적: 구조적 결함 데이터 증거 수집. T13 에서 한번에 근본 fix.
실행: `sqlite3 data/invasion.sqlite ".read tasks/anomaly_detection_t12.sql"`
기록: hourly tick 에 snapshot 추가 (cron `f4a007a8` 에 integrated)

---

## 01:30 AEST 초기 snapshot

### Open 포지션 개요
| exchange | group | n | avg_age_min | max_age_min |
|---|---|---|---|---|
| alpaca | crypto | 9 | 1558 | 1562 |
| alpaca | stock | 88 | 1498 | 1562 |
| cap | commodity | 4 | 1420 | 1549 |
| cap | indices | 43 | 1214 | 1536 |
| cap | forex | 34 | 820 | 1642 |
| okx | crypto | 80 | 636 | 1588 |
| alpaca | etf | 11 | 40 | 121 |

### Anomaly Classification (total 269)
| 유형 | 건수 | 비율 |
|---|---|---|
| total_open | 269 | 100% |
| **age_gt_6h** | 221 | **82%** |
| **age_gt_12h** | 183 | **68%** |
| **age_gt_24h** | 120 | **45%** 🔴 |
| cap_age_gt_180m (max_hold 초과) | 77 | — |
| okx_age_gt_60m | 71 | — |
| alpaca_age_gt_240m | 97 | — |

### 발견된 구조적 결함 (3가지)

#### 결함 1: `peak_pct_stored = 0.0` 전부 (DB live update 누락)
- alpaca/cap/okx 모든 open 포지션 `max_profit_pct = 0`
- 실제 live pnl 은 메모리에만, DB 에 flush 안 됨
- 결과: restart 시 peak 정보 소실 → TIME 면책 로직 재계산 불가
- T13 Part K.10 Position Live Monitoring 절실한 증거

#### 결함 2: TIME→TRAIL_PROTECTED 무한 suppression
- 로그: `TIME DECAY 36min max=+0.28% pnl=-0.21% — routing to TRAIL floor (suppressed)`
- peak 0.10% 만 넘으면 TIME exit 영구 면책
- TRAIL 이 발동 안 하면 loss 포지션 무한 hold
- T13 Part 2.5 PHS 가 근본 해결 (맥락 기반 판단)

#### 결함 3: alpaca 에 `asset_group='crypto'` 9건
- BZ, ALLO, BEAT, SATS, DASH, HUMA, WAL, ZETA, ALLO(중복) — alpaca 인데 group=crypto
- Alpaca 는 stock/etf 만이어야 함. 분류 오류
- strategy_id 에 `crypto_momentum_reversal` 포함 — 전략이 alpaca ticker 에 crypto 태그 붙이는 버그 의심
- T13 Phase 1.3 Signal hygiene 에서 group 정규화 포함 필요

### 초장기 open 포지션 상위 (age > 1500m = 25h)
**USD/CHF cap forex short 1642m (27.4h)** — 최장
TAO okx crypto long 1588m
TRUMP okx crypto short 1586m / 1549m (중복)
ALLY BMBL BZ TRVI ALLO AUR BOX GD KMB LCID LION MSFT PAGP PEGA RLMD SCHI — alpaca stock 다수 1561m

### T13 에서 해결해야 할 것 (정리)
1. **결함 1** → Phase 2.5 PHS + K.10 Position Live Monitoring (DB flush 포함)
2. **결함 2** → Exit cycle 재설계 (suppression 조건을 cell 기반 맥락 판단으로)
3. **결함 3** → signal hygiene 의 asset_group 정규화

---

## Snapshot 기록 방식
- Hourly tick (`f4a007a8`) 에 `sqlite3 ... anomaly_detection_t12.sql` 실행 결과 요약 append
- 오래된 포지션이 어떻게 진화하는지 추적 (age / peak / status 변화)
- 결함 패턴 반복 여부 확인 (신규 진입 포지션도 같은 문제 재현?)
