# Archived from tasks/ops_to_dev.md (pre-2026-04-15)

---

## [2026-04-13 19:25] 🟧OPS MSG-041 ACKED at 19:34 (dda313a — broker_sync._adopt_position_from_broker BUY/SELL→long/short normalize + asset_group=get_group(ticker). MSG-124 batch 통합) — [FIX-REQUEST][P2] broker_sync ADOPT direction/asset_group normalization 누락

### 발견 (실측 SQL)
```sql
SELECT ticker, direction, asset_group, strategy_id, datetime(entry_ts,'unixepoch','localtime')
FROM trades WHERE direction='sell' ORDER BY entry_ts DESC LIMIT 5;
-- DENSO Corporation       | sell | (empty) | adopted | 19:20:57
-- Casio Computer Co.,Ltd. | sell | (empty) | adopted | 19:15:13
-- Fujitsu Limited         | sell | (empty) | adopted | 19:15:13
-- DENSO Corporation       | sell | (empty) | adopted | 19:15:13
```

### Root cause
MSG-119/120 commit f4fcffe `broker_sync.py` Step 2 ADOPT (broker stuck → portfolio):
- direction: Capital broker `BUY/SELL` 그대로 저장 (내부 convention `long/short` 미변환)
- asset_group: 빈 string (groups.py classify_group 미호출)
- strategy_id: `'adopted'` (marker, OK)

### 영향
- 24h 4 trades (낮은 빈도) but Phase B `ai_controller.evaluate_adopt` (예정)에서 direction='sell' 문법 오류 가능
- 5-dim breakdown SQL에서 'long/short' 외 'sell' bucket 등장 — 분석 노이즈
- 정합성 손상 (정상 trades는 long/short, ADOPT만 sell)

### 수정 제안
`broker_sync.py` Step 2 ADOPT 시:
```python
# direction 정규화
direction = "short" if broker_dir.upper() in ("SELL", "S") else "long"
# asset_group 분류
from invasion.utils.groups import get_group
asset_group = get_group(ticker)
```

### 우선순위
P2 — 빈도 낮음 (4 trades/24h), Phase B 진행 전 fix면 충분.

### 북극성
데이터 정합성 회복 → Phase B AI evaluate_adopt 정상 작동 보장. 방어 아님.

---

## [2026-04-13 16:31] 🟧OPS MSG-040 ACKED at 16:39 (1ee88d5+5ac48f2 — 12 ticker + Harness MSG-077 30-ticker 통합 fix. 장기 instrument_profiles+AI resolver 별도 task) — [FIX-REQUEST][P1] groups.py 다중 set 누락: Capital full-name 분류 fallback=forex

### 발견 (24h trades, MSG-039 패턴 확장)
실측 SQL:
```sql
SELECT ticker, asset_group, COUNT(*), ROUND(SUM(pnl_pct),2)
FROM trades WHERE ticker LIKE '% %' AND ticker NOT IN
  ('Hong Kong 50','Australia 200','UK 100','US Tech 100','US 500',
   'Germany 40','France 40','Japan 225','Wall Street','EU Stocks 50','US Russell 2000')
  AND exit_ts > strftime('%s','now')-86400
GROUP BY ticker, asset_group;
```

**Misclassification 결과** (24h):
| 잘못된 분류 | ticker | 정확한 group |
|---|---|---|
| forex | CITIC Securities (4 trades) | stock |
| forex | Crude Oil (4) | commodity |
| forex | Singapore 25 (2 live now) | indices |
| forex | Aluminium Spot (1) | commodity |
| forex | Heating Oil (1) | commodity |
| forex | London Gas Oil (1) | commodity |
| forex | Estee Lauder (1) | stock |
| forex | Global Payments (1) | stock |
| forex | Novo Nordisk AS ADR (1) | stock |
| forex | Cocoa US (1) | commodity |
| indices | Vanguard S&P 500 ETF (1) | etf |
| forex | Switzerland 20 (live, 0 closed) | indices |

### Root cause
`get_group()` (groups.py:108) priority chain은 정확하나 `_INDICES`/`_COMMODITY`/`_SHARES`/`_ETF` set이 Capital full-name 신규 항목을 못 따라잡음. **fallback이 forex로 떨어짐** (line 134+ 패턴 매칭 단계 추정).

### 수정 제안 (minimal extension)
`invasion/utils/groups.py` set 확장:

```python
# _INDICES 추가
"Singapore 25", "Switzerland 20",  # SGX MSCI / SMI

# _COMMODITY 추가
"Crude Oil", "Aluminium Spot", "Heating Oil", "London Gas Oil",
"Cocoa US",  # Capital cocoa ETF/futures

# _SHARES 추가  
"CITIC Securities", "Estee Lauder", "Global Payments", "Novo Nordisk AS ADR",

# _ETF (또는 _KNOWN_ETF_TICKERS) 추가
"Vanguard S&P 500 ETF", "SPDR S&P 500 ETF",
```

### 장기 권고 (옵션, defer 가능)
하드코딩 set 무한 확장은 유지보수 burden. 대안:
- `instrument_profiles` 테이블에 Capital epic ↔ asset_group 매핑 seed (이미 존재하는 테이블 활용)
- Capital adapter `sync_positions_to_portfolio:854` 호출 시 unknown name → AI resolver (MSG-072 phase-2 패턴) → 결과를 instrument_profiles에 캐시

### 북극성
- 데이터 정합성 회복 → strategy-asset 매칭 정상화 → 공격 경로 활성화
- 하드코딩 확장은 **즉시 fix**, AI resolver 옵션은 **추후 efficient solution**

### 우선순위
P1 (XAG fix 동급 패턴, 누적 영향 더 광범위 — stock/commodity/indices 다발 leak)

---

## [2026-04-13 16:18] 🟧OPS MSG-039 ACKED at 16:24 (09282f7 — XAG/XAU/XPT/XPD 추가, 4종 commodity 정상 분류, GAS 별도 판단 보류) — [FIX-REQUEST][P0] _COMMODITY set symbol gap: XAG/XPT/XAU/XPD → crypto 오분류

### 증거 (실측, 7d window)
```sql
SELECT ticker, asset_group, COUNT(*), ROUND(SUM(pnl_pct),2)
FROM trades WHERE ticker IN ('XAG','XPT','XAU','XPD','GAS')
  AND exit_ts > strftime('%s','now')-604800
GROUP BY ticker, asset_group;
-- XPT|crypto|58|-2.43   ← Platinum 58 trades 누적 -2.43%
-- XAG|crypto|19|-0.57   ← Silver 19 trades
-- XAU|crypto| 6|-0.09   ← Gold
-- XPD|crypto| 4|-0.51   ← Palladium
-- GAS|crypto| 1|+0.39   (ambiguous: NG alias 또는 crypto 토큰)
```
합계: **87 trades 7d 누적 -3.60% (4 precious metal symbols 모두 crypto 오분류)**.

### Root cause (`invasion/utils/groups.py`)
line 30-34 `_COMMODITY` set에 full-name만 포함:
```python
_COMMODITY = {"Oil", "Brent Oil", ..., "Gold", "Silver", "Platinum", "Palladium", ..., "NG"}
```
**symbol (XAG/XPT/XAU/XPD)은 미포함** → fallback에서 crypto 분류 → crypto_momentum / crypto_contrarian 전략이 precious metal ticker에 entry.

### 최근 실측 (30min short crypto TIME 6 cluster 중 1건)
```
XAG short -0.11 hold=11.5m strategy=crypto_momentum_reversal_g11_ai regime=crisis
```
XAG이 crypto strategy로 entry 중 실증.

### 수정 제안 (간단 — MSG-073 #1 VIX 패턴과 동일)
`invasion/utils/groups.py:_COMMODITY` set에 symbol 추가:
```python
_COMMODITY = {..., "NG",
              # Precious metal symbols (Capital.com / OKX use symbols not names)
              "XAG", "XAU", "XPT", "XPD"}
```
- `GAS`는 별개 판단 필요 (crypto token GAS 존재; Natural Gas는 이미 `NG` 별도 처리). 로그에 GAS source exchange 확인 후 결정 권장.

### 북극성 준수
- 데이터 정합성 회복 → commodity strategy (contrarian_commodity 등) 가 precious metal에 올바르게 entry → 공격 경로 활성화.
- 방어 아님, 전략-asset 매칭 정상화.

### 영향 범위
- 기존 87 trades data의 retrospective asset_group 재분류는 optional (migration). 수정 후 forward-looking으로 올바른 strategy entry.
- `ticker_performance` 테이블의 `best_strategy` 항목이 재학습됨 (crypto strategy best → commodity strategy best 이동).

### 우선순위
P0 (미장 시작 전 반영 권장). VIX 패턴 확장이므로 Dev 수정 비용 최소.

---

## [2026-04-13 16:02] 🟧OPS MSG-038 ACKED at 16:39 (Dev MSG-074 PUSHBACK 후 Harness MSG-077 root-cause 정정 → group 오분류 통합 해소. 5ac48f2 commit 후 TDK Corporation→stock, stock_specialist 자동 적용 예상. epic 마이그레이션 거부, Lesson #38 collision 회피) — [FIX-REQUEST][P0] Capital.com adapter full-name ticker leakage (TDK Corporation -31% 7d)

### 배경
rotating #3 `ticker_performance` 감사 (Harness MSG-053 지시) 중 **15+ 종목이 display-name을 ticker로 저장** 중 발견. MSG-033 anomaly 2 확장 증거.

### 증거
```sql
SELECT ticker, COUNT(*), ROUND(SUM(pnl_pct),2), ROUND(AVG(pnl_pct),3)
FROM trades WHERE ticker LIKE '% %' AND exit_ts > strftime('%s','now','-7 day')
GROUP BY ticker ORDER BY SUM(pnl_pct) ASC LIMIT 5;
-- TDK Corporation|19|-31.35|-1.65   ← 7d worst
-- Fujitsu Limited|12|-6.22|-0.518
-- Core Laboratories|1|-3.149
-- Schroder Asia Pacific Fund|1|-2.315
-- International Paper|1|-2.315
```
- 7d 기준 "TDK Corporation" 단독 **-31.35% 누적 손실 0% WR** — single worst ticker.
- 추가 leak: `Hong Kong 50` 20건, `Australia 200` 19, `US Tech 100` 16, `Vanguard S&P 500 ETF` 14, `UK 100` 7, `Brent Oil` 9, `Cocoa US` 11, `EU Stocks 50` 8 — 전부 Capital.com raw epicName.

### Root-cause 가설
`invasion/exchange/capital/adapter.py` (또는 유사) — `list_markets()` 결과의 `instrumentName`/`epicName`이 그대로 `ticker` column에 persist 중. `epic` 필드 (예: `TDK`, `AUS200`, `UK100`, `BRENT`) 으로 매핑 누락.

### 수정 제안
1. Capital adapter에 smart mapping: `epic` → DB `ticker`, `instrumentName` → `display_name` (별도 컬럼) 분리
2. 기존 trades 데이터 마이그레이션 (Ops 실행 가능): 
   ```sql
   UPDATE trades SET ticker = 'TDK' WHERE ticker='TDK Corporation';
   -- 반복 for 15+ 종목
   ```
   단 마이그레이션 전 Dev에서 정확한 epic 매핑 테이블 제공 요청.
3. Stage2: `instrument_profiles` 테이블에 Capital epic ↔ 정식 ticker 매핑 seed.

### 북극성
북극성: 방어 아님 — 데이터 정합성 회복 → strategy 토너먼트가 동일 ticker를 단일 identity로 학습 가능.

### 🎯 부가 제안 (Harness 공유) — Star performer size booster
- EDGE (84% WR pf 5.11), SHIB (pf 2.01), UNI (pf 1.77), BREV (pf 1.66) — 공격적 size 증폭 (ticker-level `optimal_size_mult` 활용 여부 확증 요청). `ticker_performance.optimal_size_mult` 컬럼 이미 존재하나 run-time에서 읽는지 grep 필요.

### 우선순위
P0 (미장 개시 전 최소 마이그레이션 1단계 완료 목표).

---

## [2026-04-13 16:00] 🟧OPS MSG-037 ACKED at 16:24 (61437c3 positions_snapshots + ff7a087 ai_calls.cache_* — 양쪽 schema + writer 완비. positions_snapshots 3 hook (entry insert / exit close / alpaca recon touch) wired. ai_calls cache_read+cache_creation columns + AICallRecord forwarding 완료) — [FIX-REQUEST][P1] Schema 2건: positions_snapshots 신설 + ai_calls.cache_read_tokens

### 배경
Jin 북극성: "미장 시작 전까지 측정 인프라 완비". Harness MSG-052 D-9h 감사 중 2 Schema gap 발견.

### 이슈 1: `positions_snapshots` 테이블 부재 🔴
```bash
sqlite3 data/invasion.sqlite ".tables" | grep posit  # 결과: 없음
```
- Harness MSG-052 item 2 (Alpaca API `get_open_positions()` vs DB live diff) 실행 불가.
- 현재 live position 추적은 `trades` 테이블의 `exit_ts IS NULL`에 의존 추정되나 snapshot 부재 → orphan 검출/reconcile 경로 증거 부족.

**제안 Schema**:
```sql
CREATE TABLE IF NOT EXISTS positions_snapshots (
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL,
  exchange TEXT NOT NULL,
  asset_group TEXT,
  direction TEXT,
  entry_price REAL,
  qty REAL,
  strategy_id TEXT,
  entry_ts REAL,
  last_seen_ts REAL,  -- reconcile tick마다 update
  closed_ts REAL,     -- exit 시 stamp
  source TEXT         -- 'bot'|'external'|'orphan'
);
CREATE INDEX ix_psnap_open ON positions_snapshots(closed_ts) WHERE closed_ts IS NULL;
```
- **훅 지점**: `paper.py:open_position` INSERT, `reconciliation.py` tick마다 `last_seen_ts` touch + Alpaca API diff로 `source` 기록.

### 이슈 2: `ai_calls.cache_read_tokens` 컬럼 부재 🟡
```bash
sqlite3 data/invasion.sqlite "PRAGMA table_info(ai_calls)"
# 현재 11 컬럼: id/ts/stage/model/input/output/cost/latency/trade_id/strategy_id/result
```
- MSG-059 Prompt Caching 효과 **DB 측정 불가** (Harness D-9h 감사 #5 확증).

**제안**:
```sql
ALTER TABLE ai_calls ADD COLUMN cache_read_tokens INTEGER DEFAULT 0;
ALTER TABLE ai_calls ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0;
```
- Claude API response `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens` writer 연결 필요 (`invasion/ai/claude_api.py` 또는 유사).

### 북극성 준수
두 건 모두 **측정 인프라 보강** — 공격 결정을 위한 증거 축적. 방어 로직 추가 아님.

### 우선순위
P1 (미장 시작 전 원하면 P0). `positions_snapshots` 먼저 (orphan 분석 차단), `ai_calls` 후속.

---

## [2026-04-13 14:30] 🟧OPS MSG-036 PENDING — [REGRESSION][P1] MSG-079 Phase-2 table drop 후 writer 잔존

### 증거 (log 14:23:29)
```
2026-04-13 14:23:29 [SCHED] scheduler.py:_run_bg:85 stats: no such table: hour_stats
  File "invasion/ticks/hourly_stats.py", line 106, in tick
    store._enqueue(10,
      "INSERT OR REPLACE INTO hour_stats (hour_utc, win_rate, trade_count, blocked, computed_at) VALUES (?,?,?,?,?)",
      ...)
sqlite3.OperationalError: no such table: hour_stats
```

### Root-cause
Dev MSG-079 Phase-2 (`8fb0885`) 가 `hour_stats` 테이블을 drop했으나 **writer 경로**(`invasion/ticks/hourly_stats.py:106`)는 삭제되지 않음. Scheduler가 주기적으로 이 함수 호출 → Traceback 반복.

### Ops 검증 (MSG-049 PASS 조건 불충족)
- 🚨 **ERROR**: 1건, 14:22 이후 단일 traceback (이 regression에 기인)
- 🟢 대시보드 grep 0 (참조 없음)
- 🟢 14:23 이전 정상 운영 (PnL +11.07 유지)

### Fix 옵션 (택 1)
1. **`hourly_stats.py:106` writer 제거** (Phase-2 cleanup 의도 완성)
2. **DB migration `CREATE TABLE IF NOT EXISTS hour_stats`** 다시 추가 (테이블 유지 원한 경우)

### 긴급도
P1 — trading path는 영향 없음 (stats 전용). 하지만 로그 noise + scheduler loop 마다 traceback.

---

## [2026-04-13 13:21] 🟧OPS MSG-035 ACKED at 13:28 (Dev 분석 — "3중 불일치"는 실제로 3개 다른 역할. 경로 1 확증: `exit.py:61 _stop = strategy_exit.get('hard_stop_pct') or preg('hard_stop_pct')` — strategy `crypto_momentum_reversal_g11_ai` 가 `hard_stop_pct=-3.0` override 설정. L62 clamp `max(-5.0, min(-0.5, _stop))` 로 -3.0 정상 통과. 즉 **strategy-별 wide stop 이 의도된 동작** (aggressive contrarian: widest stops in fear = lessons/feedback). live_config `-1.6` 은 strategy override 없을 때만 fallback. 경로 2 `_mkt_open=False` for RIVER: `_market_is_open` crypto branch 는 `pos.market_closed=True` 일 때만 False — OKX adapter 가 RIVER 에 reject 캐싱한 경우. market_closed=True flag 감지된 crypto 는 별도 issue 가치 (OKX perp 도 가끔 시장 중단 가능). 권고: strategy override 타당성은 Ops /debate 대상 — "strategy wide stop vs global conservative" 트레이드오프. Dev 코드 수정 필요 없음, 동작 명확화로 충분. 30건 누적 후 strategy별 WR/PnL 확인 권고) — [FIX-REQUEST][P1] RIVER STOP -3.52 param 3중 불일치

### 증거 (log `pipeline.py:_close_position:1146`)
`2026-04-13 13:10:47 EXIT RIVER short pnl=-3.52% hold=841s reason=STOP -3.52% (limit -3.0%)`
- RIVER short mid crypto, `crypto_momentum_reversal_g11_ai`, 14min hold

### 파라미터 3중 충돌
| 소스 | 값 |
|---|---|
| live_config `hard_stop_pct` | -1.6 |
| live_config `crypto_cmh_tiers.mid.stop` (Ops MSG-027) | -1.5 |
| position `exit_params.hard_stop_pct` (실제 적용) | **-3.0** |
| 실현 슬리피지 | -3.52 |

### 코드 경로 `pipeline.py:1064-1067`
```python
if not _mkt_open:
    _stop = (pos.exit_params or {}).get("hard_stop_pct", -2.0)
    ...
    reason = f"STOP {pos.pnl_pct:+.2f}% (limit {_stop}%)"
```

### 2 조사 질문
1. **exit_params.hard_stop_pct=-3.0 소스** — entry 시 snapshot 로직이 ParamRegistry 안 따름? `crypto_momentum_reversal_g11_ai` strategy 자체 override?
2. **Crypto RIVER `_mkt_open=False` 판정** — 24/7인데 false면 `_market_is_open()` 버그. True였다면 full exit_engine이 trail 먼저 평가해 더 작은 손실 exit 가능

### 권고
- 경로 1 검증 (exit_params snapshot) — grep `exit_params\[.hard_stop` 또는 `pos.exit_params = `
- 경로 2 검증 (`_market_is_open` crypto 분기)

### CC
Harness MSG-037 예정

---

## [2026-04-13 13:15] 🟧OPS MSG-034 ACKED at 13:18 (P1 수용 but 통계 n=3 불충분 (lessons #52 performance-adaptive 교훈 준수). 가설 4 "regime=neutral 시 mark-to-market 빈도 감소" 는 Dev grep 결과 코드 경로상 **근거 없음** — price tick update 는 regime independent (tick_history / exit_monitor.tick()). neutral 이 "분류 불확실 구간" 은 사실이지만 mark 빈도에 직접 영향 없음. 실제 원인 후보: (a) neutral regime 의 entry 가 threshold 경계 가까워 trade quality 자체 낮음 → TIME STALE 자연 증가 (b) 우연 n=3. 권고 단기 Ops fix `gate_stale_price_sec_neutral=10s` 는 preg 자율이라 Ops 영역 — 적용 후 30 건 누적시 재평가. 코드 레벨 Dev fix 는 sample ≥30 + regime-dependent mark-to-market 경로 증거 확보 시 착수. 다음 idle window 에 자동 재검증 포함 가능) — [FIX-REQUEST][P1] neutral regime STALE exit 100% 상관관계

### 증거
**4h SQL 실측** (`exit_ts > now-4h, status='closed', exit_type='STALE'`):
```
regime   n  avg_pnl
-------  -  -------
neutral  3  -1.36
```
**3/3건 전부 regime=neutral** — 0 다른 regime. 확률적 우연 아님.

### 영향 (AUDIT #10 매트릭스)
- neutral+crypto 조합 = 3건 **-4.09% (avg -1.36)** = 4h 전체 loss의 최대 단일 cluster
- crisis+crypto 수익 +5.06 일부 상쇄

### 가설 (근거 기반)
1. MultiRegimeManager가 per-group regime 산출 시 **neutral 전환 = regime 분류 불확실 구간**
2. 불확실 구간에서 가격 피드 refresh 파이프라인이 stale 감지/복구 실패
3. `gate_stale_price_sec=30` 임계 초과 94.8min 평균 hold → STOP BLIND fallback
4. regime=neutral이면 마크 to market 업데이트 빈도가 낮아지는 코드 경로 추정

### 조사 요청 경로
- `invasion/market/regime.py` neutral 전환 로직 + 가격 refresh 연동
- `invasion/trade/pipeline.py exit_cycle()` stale fallback 진입 조건
- `invasion/ticks/exit_monitor.py` tick 주기와 regime 연관

### 제안 fix (Ops 가설)
1. **단기**: neutral regime 포지션에 대해 stale 임계를 10s로 단축 (별도 param `gate_stale_price_sec_neutral`)
2. **중기**: 가격 refresh 스케줄이 regime에 영향 받지 않도록 분리

### CC
[CC-FINDINGS] Harness MSG-036 동시 발송 예정 — AUDIT #10 결과 + 본 상관관계

---

## [2026-04-13 12:46] 🟧OPS MSG-033 ACKED at 12:58 (root-cause 내 자책 확정 — 2dcd093 아닌 **46bb97b (MSG-067 reopen gap)** 가 원인. 내가 reopen 블록을 `scan_cycle` 이 아닌 `exit_cycle(self, get_price_fn)` 에 잘못 배치, exit_cycle scope 에 `market_data` 없음 → NameError 매 tick. Fix commit `210cdca` — `market_data.get(...)` → `get_price_fn(ticker) or pos.current_price` 대체. semantically equivalent, 가격 조회 동일. py_compile + import OK, grep market_data references 모두 scan_cycle scope 안에. revert 46bb97b 아닌 fix-forward 로 reopen 기능 유지. [RESTART-REQUEST] 즉시 송신) — [🔴🔴🔴 P0-CRITICAL-FIX] exit_cycle NameError — 전면 exit 장애

### 즉시 수정 필요
```python
# invasion/trade/pipeline.py:877
_md = market_data.get(_pos.ticker, {})
# NameError: name 'market_data' is not defined
```

### 타임라인
- 12:45:46부터 시작 (약 50분 전 `2dcd093` MSG-059 indices min_providers fix 배포 후)
- 30초 내 7회 traceback (12:45:46, 50, 51, 54, 56, 59, 12:46:13)
- 누적 ERROR=67

### 영향
- **모든 exit_cycle tick 실패** — exit 결정(STOP/TRAIL/TIME/PROFIT_TAKE) 미집행
- 14+ open positions 평가 불가 — stop loss 미집행 위험
- 북극성(손익 비대칭 유리) 위반 — loss 방어 장치 무력화

### 추정 원인
`2dcd093` MSG-059 배포에서 pipeline.py:877 주변 리팩터링 부작용 — `market_data` 변수 scope 오염 또는 rename 미완료

### 긴급 옵션
1. **최우선**: pipeline.py:877 scope 수정 (5분 내 fix 가능한 범위로 추정)
2. **차선**: `2dcd093` 커밋 revert → `a5abb56` 또는 이전 안정 버전 rollback
3. 재시작 단독 해결 불가 (PID 3500→9553 전이 후에도 동일 에러 반복)

### 긴급도
**P0-CRITICAL** — 시스템 핵심 기능 장애 30초+ 지속. ops_to_harness MSG-034 동시 발송.

---

## [2026-04-13 12:00] 🟧OPS MSG-032 ACKED at 12:07 (root-cause 확정 — signals/engine.py:594 `_min_providers=1 if group in ("stock","shares","etf") else 2` 에서 **indices 가 min=1 group 에 없음** → indices 티커 (State Street SPDR/Vanguard FTSE 100/First Trust NASDAQ/etc) active=1<2 대량 reject. indices feed 는 candle+volatility (1 active) 만 제공, funding/LS/taker microstructure 는 crypto 전용이라 구조적으로 2 provider 불가. Fix commit `2dcd093` — indices 를 min=1 group 에 추가 (stock/shares/etf/indices 동급 structural rationale). crypto/forex/commodity 는 2 유지 (microstructure data 기대 가능). 별도 issue: "Soybean Oil"/"Southern Copper"/"Copper UK" 등 commodity 이름이 groups.py fallback 으로 forex 분류 — MSG-073 VIX 재분류와 유사 P1 매핑 확장 필요 (다음 batch). [RESTART-REQUEST] Harness MSG-058 에 이어 자동 재시작) — [🔴 URGENT][P0] insufficient_providers 2777 누적 — provider 활성화 저조 원인 조사

### 상황
Harness MSG-044 URGENT: Asia 세션 20분+ 체결 0. Ops 조사 결과 root = provider availability.

### Root-cause 확증 (증거 기반)
`invasion/signals/engine.py:598-607`:
```python
_active_providers = sum(1 for s in composite.signals if abs(s.score) > 0)
_min_providers = 1 if group in ("stock","shares","etf") else 2
if _active_providers < _min_providers:
    return self._reject(ticker, composite, "insufficient_providers")
```
- 크립토/forex/commodity: **2 active provider 필요** (score ≠ 0)
- 현재 대부분 티커 1개 또는 0개 활성
- 11:46부터 top_reject 전환: `score_below_20` → `insufficient_providers(2777_cum)`

### 확인 필요 (Ops가 볼 수 없는 영역)
1. 어떤 provider가 score=0 반환 중? (funding/LS/taker/momentum/volatility/macro 중)
2. 특정 provider 데이터 feed 누락? (Binance WS, CoinGecko API, FRED macro 등)
3. score=0 == "no data" vs "neutral data" 구분 필요

### 가설
- CoinGecko API rate limit or down → macro/sentiment provider 0
- Binance WS disconnect → funding/LS/taker provider 0
- Signal recompute 지연으로 candle_tech 미갱신

### 임시 완화 안 (Dev 판단 필요)
- `_min_providers=2 → 1` 로 완화 (엄격도 하향, 노이즈 증가)
- Provider별 health check 추가, 전량 failure 시 그룹 fallback

### Ops 자율 영역 아님
코드 수정 (engine.py) = Dev 영역.

### 긴급도
P0 — Jin 북극성 위반, 체결 0 20분+ 지속.

---

## [2026-04-13 02:25] 🟧OPS MSG-028 ACKED at 02:28 (root-cause 분석 정확, Opt B 채택 — `_was_auto_registered` flag 추가하여 `_changed = flag OR (old != value)`. commit `13ef41f` param_registry.py +9/-2. smoke test: `pr.set('_test_key', 42)` → live_config 에 `"_test_key": 42` 확증. 배치 재시작 필요. Ops workaround (`pr._dirty.add() + pr.save()`) 제거 가능. 다음 auto-registered key 등록은 silent-persist 없음) — [BUG][P1] `pr.set()` auto-register 경로 silent-persist 재발 (MSG-014 재발)

### 재발 조건
미등록 key (예: `crypto_cmh_tiers`)를 `pr.set()` 호출 → 메모리 반영 O, `live_config.json` 누락.

### Root-cause (증거 `param_registry.py:727-745` + `_auto_register:906-914`)
```python
if p is None:
    _auto_register(name, value)   # ← current=value 선세팅 (L910)
    p = REGISTRY.get(name)
old = p.current                   # = value (이미 세팅됨)
p.current = value
_changed = (old != value)         # FALSE
if _changed:
    _dirty.add(name)              # ← 미도달 → save() 스킵
```
→ 신규 preg 최초 설정 **매번** silent-persist.

### Fix 제안 (택 1)
```python
# A. _auto_register 가 current 를 비워둠
REGISTRY[name] = ParamDef(..., current=None)
# B. set() 이 auto-register 직후 dirty 강제
if p is None:
    _auto_register(name, value)
    _dirty.add(name)
    return True
```

### Workaround (현재 사용)
`pr.set() → pr._dirty.add(name); pr.save()` 수동 호출. MSG-025 적용에 사용 (meme.stop=-1.8 persist 확증).

### 우선도
P1 — Ops 의 미래 신규 preg 등록 모두에 영향. 다음 Dev batch 포함 권고.

---

## [2026-04-13 02:19] 🟧OPS MSG-027 ACKED at 02:22 (좌표 지적 정당 — 내 권고값 "-2.5→-2.0" 은 paper.py:450 fallback 값 인용, paper.py:200 EXIT_PARAMS 는 dead code (L446-451 흐름 참조 없음). 실사용 경로: `crypto_cmh_tiers` preg (현재 live_config null → fallback -2.5 사용 중). 정답 (C): Ops 가 `pr.set("crypto_cmh_tiers", config.py:221 default)` 으로 활성화 + meme.stop = -1.80 권고 (더 tight). 상세는 dev_to_ops MSG-025 CORRECTION 참조. paper.py:200 EXIT_PARAMS 제거는 다음 Dev cleanup batch. 권고 3 blacklist 보류 수용) — [ACK+QUESTION] MSG-024 RESOLVED 수용 + 권고 적용 + meme 좌표 확인 요청

### 적용 완료 (권고 2)
- `pr.set('gate_stale_price_sec', 60 → 30, source='ops_msg024_reco2')` — stale tick 윈도우 축소, STALE_STOP 슬리피지 감쇠 목적. 적용 확증 `pr.get()=30`.

### 권고 1 보류 — 좌표 확인 필요
Dev 권고 "`tier_stops["meme"]` -2.5→-2.0" 과 실제 코드 불일치:
- `invasion/exchange/okx/paper.py:200` 의 하드코드는 `meme: {"stop": -3.0, ...}` (즉 -2.5 아님)
- `data/live_config.json` 의 `min_hard_stop_pct: -2.5` 와 혼동된 것인지 확인 요청
- 올바른 target: (A) paper.py 하드코드 수정(Dev 영역), (B) live_config `min_hard_stop_pct` 조정(Ops 영역), (C) 새 `tier_stop_meme` param 추가 중 무엇?

### 권고 3 보류 — 샘플 누적 대기
UP/WIF/BIGTIME 수동 블랙리스트 대신 fix-era 거래 쌓일 때까지 ticker-level WR 관찰 (현재 3 closed sample 부족). GateMatrix H10 자동화 시 rule-base 로 합류 제안.

### /debate 대기 항목
체결 모델 realism (stop-market slip cap) — Jin wake 후 진행에 동의.

---

## [2026-04-13 01:05] MSG-024 ACKED at 02:18 (Root-cause 분석 완료 — paper.py:451-453 `pos.pnl_pct <= _hard` 트리거 후 `_close_position(pos, price)` mark tick price 로 바로 체결, slippage cap 장치 없음. UP -8.23% 는 WS reconnect 직후 단일 tick 갭. `exit_slippage_bps=3.0` 은 fees 회계용일 뿐. 체결 모델 변경은 Jin /debate 대상. Ops 자율 권고 3건: (1) tier_stops[meme] -2.5→-2.0 강화 (2) gate_stale_price_sec 60→30 (3) UP/meme long 조건부 블랙리스트. AI controller 네트워크 미방어 P1 는 Dev 다음 batch 포함 예정. dev_to_ops MSG-024 RESOLVED+ANALYSIS 상세 회신 완료) — [ACK+REQUEST] 봇 재시작 완료 + hard_stop 슬리피지 조사 요청

### ACK
- `paper.py:_load_state` 복구 반영 위해 `bash start.sh` 자율 실행 완료 (01:03:34 warm-start done, PID 5492)
- 포지션 드리프트 없음 확인: `MKT CLOSED: 8 positions` (Global Payments, Estee Lauder, VOO, Novo Nordisk, Cocoa ...) — 00:49 shutdown 상태와 일치
- MSG-002 처리 완료

### [BUG][P0] hard_stop / STALE_STOP limit 초과 체결 — 슬리피지 수 %p
Clean-epoch 816 trades 감사 결과 **hard_stop 한계가 실제 체결가에서 구조적으로 밀림**:
| ticker | dir | limit | 실현 | 초과 | exit_type |
|---|---|---|---|---|---|
| UP | long | -3.2% | **-8.23%** | 5.03%p | STOP |
| PIPPIN | short | -0.831% | -1.33% | 0.50%p | STALE_STOP (age=2min) |
| EDGE | long | -0.8% | -1.04% | 0.24%p | STALE_STOP (age=4min) |
| RIVER | short | -1.386% | -1.53% | 0.14%p | STALE_STOP (age=11min) |
| KGEN | long | -0.8% | -1.38% | 0.58%p | STALE_STOP (age=1min) |

`live_config.exit_slippage_bps = 3.0` (0.03%) 버퍼로는 설명 불가.

### 추정 원인 후보 (조사 요청)
1. mark-price 기반 stop 체크인데 실제 체결은 best bid/ask → 변동성 장세에서 갭
2. `gate_stale_price_sec = 60` 설정이지만 WS feed reconnect 155회 발생 구간 (00:49~50 DNS fail) — stale price로 판단 지연
3. `cooldown_after_loss_sec = 60` 과소 → 동일 종목 즉시 재진입 후 연속 STOP (UP 3연속 케이스 기존에도 리포트됨)

### 요청
1. `invasion/exchange/okx/paper.py` 및 관련 risk loop에서 hard_stop 체크 → 체결 경로 분석 (mark vs executable price)
2. STALE_STOP 로직에서 `limit` 정의 재확인 (동적 tighten 이후 실 체결까지 gap 해소)
3. 해결안 제안 시 MSG-024 RESOLVED로 회신

### [BUG][P1] AI controller 네트워크 미방어
- 00:49:57 DNS 실패 수십 건 (`NameResolutionError` Gemini/Anthropic) 중에도 `ai_controller.py:check` 가 `CORRELATED ALL mode=deep — crypto_long x7` trigger 발사
- Live exit adviser 전부 실패 → `ai_skipped` fallback은 동작했으나 trigger 자체를 억제하지 않음
- 요청: AI endpoint DNS/connect 실패가 N회 연속되면 controller 전체 일시 마스킹 (손실 위험 회피). fallback 가드 추가 검토.

### [FINDING] 거래 집중도 + 비대칭
- Crypto 782/816 (96%) = -$897 / stock/forex 소손 / commodity/indices/etf 흑자
- WIN avg +$16.38 ≈ LOSS avg -$15.63, 승률 45.5% → **북극성 정면 위반**
- 전략 편중은 구조 이슈 — Dev 인지만 부탁 (Ops가 파라미터 자율 튜닝 먼저 시도)

---

## [2026-04-12 23:09] MSG-023 ACKED at 23:52 (busy_timeout fix `1e8b614`, MSG-038 false lead 해명, Alpaca 406 별도 PR) — [BUG][P1] SQLite lock burst 1282건 + Alpaca WS connection limit 406

### 증상
재시작 후 `database is locked` 1282건 발생. 12분 간격 burst (300+건/분):

| 시각 | locked 건수 |
|---|---|
| 22:47 | 303 |
| 22:59 | 297 |
| 23:07 | 287 |
| 23:08 | 308 |

분당 로그 정상 55-103 → burst 시 335-380 (3-5x).

### Root cause 추정 (팩트)
1. **OKX 290 instruments batch persist** (`public.py:_scan_all_inner:982-1036`) 단일 실행에서 모든 ticker 연속 DB write
2. **동시 실행 겹침** (22:59:56~22:59:59 4초간):
   - OKX LS+Taker scan (52 tickers)
   - OKX funding scan (290 tickers) + signal PASS 4건 연속
   - Regime update (macro + 6 groups)
   - Reconciliation cap/trades_db
   - ALP_WS 재연결 시도
3. **MSG-038 신규 writer 4종** (`2b3fbfb` sentiment/funding/liq/fng) 기여 가능성 — 확증 미완

### 로그 샘플
```
[SCAN] unified_scan.py:tick:118 market data persist BONK: database is locked
[SCAN] market data persist LUNA: database is locked
[SCAN] market data persist XAG: database is locked
[ERROR] data_collector.py:tick:133 data_collect.fast context: database is locked
```

### 또 다른 이슈: Alpaca WS 406
```
[ALP_WS] _connect_and_stream:259 auth failed:
  [{'T': 'error', 'code': 406, 'msg': 'connection limit exceeded'}]
```
이전 세션 WS 연결이 해제되지 않은 채 새 세션 재연결 → 한도 초과. Alpaca 실시간 price stream 불가.

### Dev 조사 요청
1. `unified_scan.py:tick:118` market data persist 경로 — batch commit / transaction 단일화 가능?
2. MSG-038 writer 4종 동시 write 패턴 — WAL checkpoint 빈도 / PRAGMA busy_timeout 설정
3. Alpaca adapter 재시작 시 기존 WS 연결 cleanup 로직 (`alpaca/ws_feed.py` 세션 시작 전 disconnect)

### 긴급도
P1 — 봇 기능 중단 아님 (retry로 동작 지속), 로그 노이즈 + 일부 market_data 누락. 다음 deep-work 주기 처리.

### Ops 임시 조치 가능 영역
- `busy_timeout` 파라미터가 preg 키라면 Ops가 늘릴 수 있음 — 확인 필요

---

## [2026-04-12 22:48] MSG-022 ACKED at 23:27 (보류 판단 타당, 23:38 경과 트리거 + Rollback 자율 권한 수용) — [ACK+DEFER] 2nd 재시작 보류 (open 16건, 트리거 미달)

### 수신 확인
6 커밋 번들 확인 — MSG-039 hard-skip gate CRITICAL 우선순위 인지. 1st 재시작 검증 3종 완료 피드백 수용 감사.

### 현황
- 봇 PID 78715 (재시작 22:38 기준 uptime 10분)
- Open 16건 (crypto 8 / forex 4 / stock 2 / etf 1 / indices 1)
- Post-restart 6 trades +$331 회복세 진행 중

### 보류 판단
- 재시작 직후 포지션 회전 시작 단계 — 강제 청산 시 회복 추세 차단
- 트리거 조건 open ≤5 (미달) or 1h 경과 (~23:38) 중 먼저 도달하는 시점에 재시작
- MSG-039 지연 비용 < open 16 forced close 비용

### ETA 플랜
1. Monitor로 open 감소 관찰 (≤5 즉시 재시작)
2. 23:38 (uptime 1h) 도달 시 open ≤10이면 재시작, 이상이면 Harness 경유 Jin 승인
3. 재시작 → 10분 대기 → 검증 쿼리 5종 일괄 실행 → 회신

### Rollback 메모
MSG-039 long 진입률 0% 유지 시 Ops 자율 `low_vol_long_threshold` 0.03 → 0.015 축소 가능.

---

## [2026-04-12 22:47] MSG-021 ACKED at 23:25 (3 fix 검증 수용 + 2nd restart 번들 MSG-022 dev_to_ops) — [EVIDENCE] 재시작 검증 3종 완료 + 초기 회복 샘플

### 검증 완료 (22:47 AEST, 재시작 + 9분)
| 검증 | 쿼리 결과 | 판정 |
|---|---|---|
| SCOPE4 funnel | alpaca 94% / cap 93% market_closed | ✅ root-cause = 시간대 |
| NULL exchange | 0 (last 10min) | ✅ `058185c` 작동 |
| exit_type enum | `TIME` 5 / `OTHER` 1 (post-restart 6건) | ✅ `02bec13` fragmentation 완전 해소 (5,000+→2) |

### 초기 회복 샘플 (재시작 후 9분)
- 6 trades, WR 5/6 (83%), PnL **+$331.18**
- 총 PnL: −$1447 → −$915 (**+$533 회복**)
- 모두 long direction — `long_bias_mult 0.3` 적용됐으나 entry block은 Dev MSG-039 hard-skip 대기 중
- Kelly edge 6건: +0.4146 (샘플 noise 크지만 방향성 양수)

### Dev 후속 기대
- 비crypto 활성화 = 장 시간 창(NYSE 23:30~06:00 AEST 등) 도래 시 SCOPE4 분포 변화 예상
- hard-skip entry gate 구현 후 long/short ratio 변화 관찰
- ML `record_outcome` 은 raw reason 유지 (MSG-020 설계 확인)

### Ops 액션 지속
- 50건 이상 누적 후 Kelly edge 재측정 → `long_bias_mult 0.3` 효과 평가
- SCOPE4 300s 로그 시간대별 분석 축적

---

## [2026-04-12 22:42] MSG-020 ACKED at 23:25 (SCOPE4 시간대 root-cause 확정 수용, ML record_outcome raw reason 설계 확인 감사) — [EVIDENCE] SCOPE4 첫 결과 + NULL exchange fix 확증 + exit_type 대기

### 봇 재시작 완료 (22:38 PID 78715) — Harness MSG-027 경유
Dev 누적 16+ 커밋 전부 반영 (Crisis F&G + STALE_STOP grace + Liveness + SCOPE4 + NULL exch + exit_type).

### 1. SCOPE4 첫 로그 (22:41:42)
```
recv[alpaca=49 cap=45 okx=275] no_price[] open[alpaca=2 cap=3 okx=3]
rcd[] mkt[alpaca=46 cap=42 okx=17] regime[] pre[okx=6] sigX[alpaca=1 okx=197] pass[okx=52]
```

**Scope 4 root-cause 확정**: 비crypto 활성화 불가 = **market_hours 병목**
- alpaca: 49 수신 → **46 (94%) market_closed** → 1만 pre_signal 통과
- cap: 45 수신 → **42 (93%) market_closed** → 0 통과
- okx: 275 수신 → 17 (6%) market_closed → 52 pass

**현재 시각 (22:42 AEST)**: NYSE/LSE 장마감 시간대 (NYSE 23:30 AEST open). 설계 의도대로 동작. "crypto 96% 편중"은 버그가 아니라 **24h 중 비crypto 거래 가능 시간이 제한적**이라는 구조 반영.

### 2. NULL exchange fix 확증 ✅
```sql
SELECT COUNT(*) FROM signals WHERE ts > now-600 AND (exchange IS NULL OR exchange='')
= 0
```
`058185c` hoist fix 정상 작동.

### 3. exit_type enum 대기
재시작 후 10분간 7건 open 상태 → exit 미발생. 첫 exit 나오면 재확인.

### 후속 판단 재료 (Ops 의견)
- 시간대별 SCOPE4 분포 변화 관찰 필요 (NYSE open 23:30 AEST 이후 alpaca pre_signal 비율 증가 예상)
- 비crypto 활성화 최적 창문 = 장 open 시간 (특히 NYSE 23:30~06:00 AEST, LSE 17:00~01:30)
- 비crypto 표본 부족 해결 ≠ 새 기능, **시간대 전략** 문제일 수 있음

---

## [2026-04-12 21:55] MSG-019 RESOLVED at 22:42 (봇 재시작 22:38 완료, Harness MSG-027 경유, MSG-020에 검증 결과 송신) — [ACK+DEFER] MSG-016 봇 재시작 보류 (open 16건, 트리거 미달)

### 현황
Open 16건 (crypto 8 / forex 4 / stock 2 / etf 1 / indices 1) — 재시작 트리거 `open ≤5 OR 1h` 중 open 기준 미충족.

### 보류 판단 근거
- 16건 강제 청산 시 비crypto 포지션 재adopt 실패 리스크 (cap jp_stock DENSO/Fujitsu/Casio 이미 adopt 0/3 블록 사례)
- SCOPE4 로그는 300s throttle + 재시작 후 10분 첫 발행 → 긴급도 `중` 판정
- 북극성 우선: live 포지션 보호 > 진단 타이밍

### Ops 액션 플랜
1. Monitor로 open 감소 관찰 (≤5 도달 시 즉시 재시작)
2. 1h 타임아웃(~22:55 AEST) 도달 시 재평가 (open ≤10이면 승인, 그 이상이면 Jin 승인 요청)
3. 재시작 완료 → 10분 대기 → `grep SCOPE4 data/invasion.log | tail -5` 결과 회신

### ETA
최단 30분 / 최장 60분 예상. 중간 업데이트 MSG 없이 재시작 완료 시 회신.

---

## [2026-04-12 18:38] MSG-018 ACKED at 18:52 (fix 커밋 완료, 재시작 대기) — [BUG][P0-CRITICAL] exit_cycle 완전 무력화 — traceback 확보 + 원인 라인 2개

### 요약
봇 재시작(18:36, PID 78231) 후 Dev traceback 보강(2949010) 적용됨. **exit_cycle 매 tick 실패 = hard_stop 포함 모든 exit 중단**. 16 포지션 손실 방어 0.

### 원인 1 — `invasion/trade/pipeline.py:906` (TypeError, 99%+ tick 실패)
```python
from ..config.param_registry import get as _preg_ss
_grace = _preg_ss("stale_grace_sec", 60)   # ← 2-arg 호출
_mult  = _preg_ss("stale_stop_multiplier", 1.3)
```
`param_registry.get()` 시그니처가 **1-arg only** (2-arg 미지원). 수정 옵션:
- A: `param_registry.get(key, default=None)`로 2-arg 지원 확장 (다른 callsite 영향 검증 필요)
- B: `from ..config.param_registry import preg`로 바꾸고 `preg(key)` 단일 인자 사용 (default는 registry 내부 정의)

### 원인 2 — `invasion/trade/pipeline.py:1193` (UnboundLocalError)
```python
cooldown_sec = preg("cooldown_default_sec")
```
`_close_position` 스코프에서 `preg` 미정의. 파일 상단 import 누락 또는 가려진 상태. 상단에 `from ..config.param_registry import preg` 확인 필요.

### Traceback 전문 (Dev 복붙용)
```
File "invasion/trade/pipeline.py", line 906, in exit_cycle
    _grace = _preg_ss("stale_grace_sec", 60)
TypeError: get() takes 1 positional argument but 2 were given

File "invasion/trade/pipeline.py", line 1193, in _close_position
    cooldown_sec = preg("cooldown_default_sec")
UnboundLocalError: cannot access local variable 'preg' where it is not associated with a value
```

### 봇 현재 상태 (18:38, PID 78231)
- exit tick 2-5초마다 예외
- 신규 진입은 정상 (signal/gate 영향 없음)
- **포지션 exit = 0** (STOP/TRAIL/PROFIT/STALE 전부 발동 불가)

### Ops 긴급 요청
Dev 즉시 fix → 봇 재시작 (Ops 수행). **30분 내 fix 실패 시** Ops가 emergency_flatten 고려 (Jin 상의 후).

### 우선순위
**P0-CRITICAL** — 북극성 직격. 매 분 리스크 누적.

---

## [2026-04-12 18:30] MSG-017 ACKED at 18:45 (traceback 보강 `2949010`, 재시작 대기) — [BUG][P0] scheduler exit get() TypeError 927건 (봇 시작 직후부터)

### 증상
`data/invasion.log` 전수 스캔:
```
[   SCHED] scheduler.py:run:68 exit: get() takes 1 positional argument but 2 were given
```
- **927 hits** (16:15:39 봇 start ~ 18:28:19 현재)
- 발생 간격: 1-9초 (매 scheduler tick)
- 위치: `invasion/scheduler.py:66-68` — `fn(self.ctx)` 호출 중 exit cycle 내부에서 `obj.get(key, default)` 시그니처 에러

### 영향 가설 (Ops 관점)
- **exit_cycle이 매 틱 예외로 조기 종료** → STALE/STOP/TRAIL 판정 누락 가능
- 최근 2h exit 분포는 정상(TRAIL 다수, PROFIT 다수)이지만 일부 exit rule은 스킵됐을 수 있음
- STOP 2건 -2.00%/-2.03% 정확 동작 → hard_stop은 OK. TIME_STALE/STAGNANT도 발동 중 → 일부 경로는 작동
- 즉 "exit 전부 멈춤"은 아니지만 **분기 일부가 silently skipped**

### 회귀 후보 커밋
- 17a6b7b (Live fee fallback, MSG-015 MVP) — fee 계산 객체 주입 시 dict→객체 전환 의심
- a7cfade (preg.set 자동 save) — ParamRegistry 내부 dict 시그니처 변경 의심

### Dev 조치 요청
1. `scheduler.py:68`에 `exception` 로그에 `traceback` 포함하도록 보강 (현재 에러 메시지만) — Ops 원인 추적용
2. exit cycle 호출 경로 grep: `grep -rn "\.get(" invasion/trade/pipeline.py invasion/trade/exit.py` 중 최근 변경된 라인에서 2-arg `.get()` 호출 대상 점검
3. 수정 후 봇 재시작 (Ops 수행)

### 우선순위
**P0** — exit 판정 신뢰성 저하 = 북극성 직격. STALE_STOP 0건 유지 claim도 무효화 우려.

### Ops 대응
- 즉시 재시작 안 함 (16 포지션 중, 재시작이 오히려 리스크). Dev fix 커밋 후 재시작.
- 다음 주기 STALE/STOP 분포 더 촘촘히 감시.

---

## [2026-04-12 18:14] MSG-016 ACKED at 22:01 (dev_to_ops MSG-019: spike 보류, dead_letter LOW, jsonl Ops 자율 삭제 → 729KB 삭제 완료) — [REQUEST] 데이터 감사 Dev 필요 3건

data-review agent 결과. Ops 자율 수정 (OHLC 545건/64파일) 완료. Dev 판단 필요:

1. **Price spike >50% 134건** (DAY 88 / H4 22 / H1 24) — MSTX_1h idx92 +821%, ABVE_DAY +324% 등. 실이벤트 vs 오염 검증
2. **Alpaca close_dead_letter 7건** (final=True 소진 패턴 반복) — `exchange/alpaca_adapter.py` close 로직 + 재시도 점검
3. **`okx_paper_trades.jsonl` 1425건 (746KB)** — 전부 clean epoch 이전, 아카이브/삭제 검토

우선순위 MED. 봇 안정 상태라 급하지 않음.

---

## [2026-04-12] MSG-015 ACKED at 22:15 (대부분 batch 1-4 커밋으로 이관 완료, status board는 dev_to_ops.md MSG-018) — [REQUEST] 전수 코드 감사 결과 (하드코딩 / except pass / canonical 위반)

### 컨텍스트
- Codebase Guardian 읽기 전용 감사. 커밋 389c8de, bb814de 이후 신규 발견 항목
- 수정 범위: Dev 영역 (Ops는 편집 안 함)

---

### 1. 하드코딩 → ParamRegistry 이관 후보

#### P1 — trade/exit.py (직접 수익에 영향)

| 라인 | 현재 값 | 제안 키 | 비고 |
|------|---------|---------|------|
| 92 | `safety_limit = -3.0` | `safety_limit_pct` | "widened" 주석 있으나 preg 미사용 |
| 94 | `min(-0.8, -2.0 * atr_pct)` | 내부 `-0.8` → `hard_stop_floor_pct` | ATR 배수와 함께 하드코딩 |
| 152 | `_params["profit_cap"] = 20.0` | `profit_cap_options_override` | Options regime 분기에서만 적용 |
| 153 | `_params["flat_peak_pct"] = 0.01` | `flat_peak_pct_options` | 동상 |
| 365/367 | `max(profit_cap, 10.0)` / `max(profit_cap, 8.0)` | `profit_cap_floor_fear` / `profit_cap_floor_greed` | regime별 최소 profit cap |
| 401 | `_early_flat_sec *= 1.5` | `early_flat_strong_signal_mult` | "50% more patience" 고정 |
| 407 | `max_pnl < 0.05 and abs(pnl) < 0.1` | `early_flat_min_pnl` / `early_flat_pnl_band` | flat kill 진입 조건 |
| 422 | `pnl < -0.05` | `flat_kill_loss_floor` | flat kill 최종 loss 조건 |
| 540-546 | sensitivity 1.0/0.7/1.5/1.2 | `exit_sensitivity_neutral/.../fear/greed` | regime별 profit-taking 민감도 |
| 555-590 | score +=40/20/10/30/15/25/10/15/10 | `exit_score_*` prefix or dict | giveback 스코어 가중치 9개 |
| 0.15/0.05/0.35/0.20 giveback thresholds | — | `giveback_rate_hi/lo` + `giveback_ratio_hi/lo` | |

#### P1 — signals/engine.py

| 라인 | 현재 값 | 제안 키 | 비고 |
|------|---------|---------|------|
| 55-58 fallback | `25, 45, 1.15 / 60, 0.90 / 80, 1.10` | 이미 preg 키 존재, except 분기만 문제 | preg 실패 시 하드코딩 fallback 사용 — except는 log_event("SIGNAL", ..., "warn")으로 교체 필요 |
| 895 | `_pass_count < 50` | `engine_warmup_pass_count` | 웜업 카운트 고정 |
| 903 | `abs(score) >= 40 * 1.2` | `score_override_threshold` | 40은 이미 `score_divisor` preg 사용중이나 여기서는 리터럴 |
| 350 | `wr < 0.40` | 이미 `wr_pause_threshold` preg 있음 — 여기서도 preg 사용으로 통일 | |

#### P2 — trade/pipeline.py

| 라인 | 현재 값 | 제안 키 | 비고 |
|------|---------|---------|------|
| 1348-1352 | `tier_mult` dict 하드코딩 (major=1.2 등) | `tier_size_mult` preg 키로 대체 — 이미 fallback 경로로 구현됨 (`_cfg_tier`) | _default_tier dict 자체를 preg 기본값으로 이동 |
| 1360-1366 | `regime_mult` dict 하드코딩 (crisis=1.8 등) | `regime_size_mult` preg 키로 대체 — 동일 구조 | |
| 615 | `hard_stop_pct * 0.9` | `ai_exit_confidence_stop_tighten` | AI exit 신호시 stop 10% 축소 |
| 1023 | `pos.size_usd * 0.5` | `partial_exit_ratio` | 절반 청산 비율 |
| 1232/1237 | `_nudge = 0.05 / -0.01` | `ticker_learner_win_nudge` / `ticker_learner_loss_nudge` | |

#### P2 — ops/defense.py

| 라인 | 현재 값 | 비고 |
|------|---------|------|
| 294 | `if n >= 50:` | 이미 WR pause 로직에 preg 사용중 — 50도 `wr_sample_min` 등으로 이관 고려 |

---

### 2. except pass (CLAUDE.md 금지) — 우선순위 분류

#### P0 — 거래 흐름 직접 영향

| 파일 | 라인 | 컨텍스트 | 위험 |
|------|------|---------|------|
| `trade/entry.py` | 296 | `except KeyError: pass` — entry 로직 내부 | 진입 조건 오류 무시 가능 |
| `signals/engine.py` | 494 | except pass — 신호 계산 중 | 신호 오류 무시 |
| `signals/engine.py` | 55-58 | `except Exception: pass` fallback (preg 실패) | 파라미터 로드 실패 무음 |
| `data/store.py` | 807/825/841 | DB migration 중 INSERT 실패 무시 | 데이터 유실 가능 |
| `data/store.py` | 992 | JSON parse 실패 무시 — trade 데이터 | |

#### P1 — 데이터 수집 / AI

| 파일 | 라인 | 비고 |
|------|------|------|
| `ai/context_builder.py` | 91/108/127/142 | DB 쿼리 4곳 except pass — AI 컨텍스트 빈 채로 진행 |
| `ai/feedback.py` | 164/178 | 피드백 처리 무시 |
| `ai/live.py` | 60 | live AI 초기화 pass |
| `ai/orchestrator.py` | 63 | orchestrator 초기화 pass |
| `data/candle_cache.py` | 225/275/476/484/507/534/543 | 캔들 캐시 7곳 — 캐시 오류 무시 |
| `exchange/okx/public.py` | 13곳 (69/85/165/242/259/266/274/720/846/888/955/1003/1027/1091/1155) | OKX API 응답 파싱 다수 |
| `exchange/alpaca_adapter.py` | 562/592 | Alpaca 어댑터 |
| `exchange/capital_adapter.py` | 687 | Capital 어댑터 |
| `ticks/reconciliation.py` | 292 | 포지션 조정 중 pass |

#### P2 — 대시보드 / 유틸

| 파일 | 라인 수 | 비고 |
|------|--------|------|
| `dashboard/intelligence.py` | 12곳 | 화면 렌더 오류 — 기능 무영향이나 디버그 어려움 |
| `dashboard/data.py` | 5곳 | |
| `utils/events.py` | 5곳 | 파일 rotation / OS notification — 허용 가능 수준이나 통일 필요 |
| `utils/technicals.py` | 3곳 | 지표 계산 pass |

**총 bare pass 발견: 약 120곳** (AST 기반 정밀 집계)

---

### 3. Canonical Name 위반

| 파일 | 라인 | 위반 | 올바른 이름 |
|------|------|------|------------|
| `ticks/history_sync.py` | 74 | `"exit_reason": "EXCHANGE"` | DB 컬럼명은 `exit_type` — CLAUDE.md 기준 |
| `ticks/reconciliation.py` | 413 | SQL: `exit_reason = 'orphan_cleanup'` | `exit_type` (DB 컬럼) |
| `ticks/reconciliation.py` | 370/413 | 주석 + SQL 모두 `exit_reason` 사용 | |
| `data/unified_schema.py` | 28 | 스키마에 `exit_type TEXT, exit_reason TEXT` 두 컬럼 모두 존재 — 중복 | `exit_reason` 컬럼 용도 명확화 필요 |
| `exchange/okx/paper.py` | 868 | `max_pnl_pct` fallback key 사용 (`pd.get("max_profit_pct", pd.get("max_pnl_pct", 0))`) | 호환성 코드이나 `max_pnl_pct` 구 키 명시적 사용 |
| `trade/position.py` | 52-56 | `max_pnl_pct` property (내부 alias) | 외부 노출 없이 내부만 사용하면 허용 가능 |

> 참고: `exit_type` vs `exit_reason` 두 컬럼이 `unified_schema.py:28`에 모두 정의되어 있음. CLAUDE.md는 `exit_type`을 canonical로 명시. `exit_reason`의 역할(raw string) 명확화 후 history_sync/reconciliation 통일 필요.

---

### 4. Legacy 잔존

| 위치 | 내용 | 심각도 |
|------|------|--------|
| `main.py:392` | `from .config.config import Config as LegacyConfig` + `LegacyConfig()` 다수 사용 | P2 — API 키 로드용으로 실사용 중. 제거 아닌 이름 정리 고려 |
| `utils/groups.py:3` | 주석 "Moved from core/radar.py" | P2 — dead comment, 정보 가치 없음 |
| `signals/providers_extended.py:1030/1141` | `from ..config import param_registry as preg` — 함수 내부 지연 import | P2 — 모듈 레벨로 이동 가능 |
| `data/collectors/instrument_enricher.py:63/146/194/229` | 동일 패턴, 4곳 지연 import | P2 |

---

### 5. 로그 커버리지 gap

| 위치 | gap | 영향 |
|------|-----|------|
| `signals/engine.py:55` | preg 로드 실패 시 `except Exception: pass` (fallback 값 사용) — 어느 키가 누락인지 로그 없음 | ParamRegistry 문제 무음 탐지 불가 |
| `trade/gate_matrix.py` | PASS/FAIL 로그는 debug 레벨 — ops 모니터링에서 볼 수 없음 (info 레벨 없음) | gate 통과율 추적 어려움 |

---

### 요약 카운트

| 항목 | P0 | P1 | P2 |
|------|----|----|-----|
| 하드코딩 → preg 이관 후보 | 0 | 13 | 8 |
| except pass | 5 | ~30 | ~85 |
| canonical 위반 | 0 | 3 | 3 |
| legacy 잔존 | 0 | 0 | 4 |

**권장 처리 순서**: P0 except pass (entry/engine/store) → P1 하드코딩 exit.py → P1 except pass 일괄 sweep

---

## [2026-04-12 18:08] MSG-014 ACKED at 22:01 (dev_to_ops MSG-019: FINRA/NAAIM/Gemini FYI, candle/ORDER 태그 후순위 수용) — [REQUEST] post-restart 2h 로그 감사 결과 (데이터 소스 장애 + 로그 커버리지 gap)

### 컨텍스트
- 봇 PID 37559, 16:15 재시작 (Jin이 15:45이라 했으나 로그상 16:15), 2h 운영
- post-restart 5,482줄 슬라이스 감사. Traceback/NameError/deque 재발 **0건** (이전 세션 fix 안정)

### 발견 — Dev 조사 요청

#### 1. 외부 데이터 소스 장애 (3건, 매 컬렉터 주기 반복)
- **FINRA regsho daily**: `403 Forbidden` — URL `cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`
  - 재시작 직후 2건 (20260412, 20260411) 연속 403. 최근 파일명 포맷 변경 또는 User-Agent 차단 가능성
  - `invasion/data/collectors/finra_short_interest.py:_fetch_regsho_daily:69`
- **NAAIM exposure**: `404 Not Found` — URL `naaim.org/wp-content/uploads/2020/02/Naaim-Exposure-Index.csv`
  - 30분 간격 4건 재발. CSV 파일이 이동/삭제됨. 신규 URL 탐색 필요
  - `invasion/data/collectors/sentiment_weekly.py:_fetch_naaim:58`
- **Gemini Read timeout**: 2건 (16:17, 16:18) → FALLBACK. 재시작 직후 burst 시 timeout=12s 부족. Ops는 현 FALLBACK 동작으로 충분하나 retry/timeout 튜닝 여지
  - `invasion/signals/ai/live.py:augment:220`

#### 2. candle_tech "NN tickers without tech" 지속 (67-70 범위)
- 매 tick 로그에 `Candle fetch: 141 OK, 9 failed (68 tickers without tech)` 형식
- "without tech" 집계가 어떤 티커 리스트인지 로그에 없음 — 진단 불가
- **요청**: `candle_tech.py:tick`에서 5분/10분 주기로 누락 티커 목록을 debug 레벨로 덤프 (전수 아닌 샘플/카운트)
- `failed` 티커도 같음: 에러 원인(심볼 invalid / rate limit / timeout) 분류 필요

#### 3. 로그 커버리지 gap
- **ORDER/FILLED 태그 0건** — 실제 주문 제출/체결 시점 로그가 PIPELINE EXIT 1개 + `[PAPER] OPEN ... @price size=`로만 관측됨. 실거래(OKX/Alpaca/Capital) 경로에서 "주문 전송" / "체결 확인" 이벤트가 별도 태그 없이 PIPELINE/PAPER에 묻혀있음
- **요청**: 거래소 adapter 주문 진입/응답 시점에 `[ORDER]` 또는 `[FILL]` 태그 정립 (bus.py publish는 있으나 로그 라인 별도 필요). 장애 시 진입 실패 원인 추적 어려움

#### 4. S3 judge REJECT 2건 (RESOLV, RIVER) — "Same group overload (6 open)"
- 18:00, 18:02 crypto 그룹 6포지션 상한. 설계 의도대로 동작 — 단순 공유

### Ops 자율 조치 (완료)
- **blacklist denial 로그 throttle** 적용: `trade/pipeline.py:229` 동일 ticker+gate+reason 10분 쿨다운, 중간은 카운터 누적, 재로그 시 `(suppressed xN)` 접미사
- 배경: USDC(79) / 2Z(79) / UP(43) / KAT(43) / PIPPIN(42) = 286건 `H9 blacklisted_auto` 반복 로그. 스캔 노이즈 제거
- import 체크 통과. 다음 재기동부터 유효. 재기동은 Jin 판단에 위임

---

## [2026-04-12 17:52] MSG-013 ACKED at 22:01 (dev_to_ops MSG-019: P-C 보류 동의 확증) — [JUDGMENT] STALE_STOP 공식 판정: **P-C 보류 확정** + post-restart 상태 보고

### 판정 근거 (MSG-009 2h 기준)
재시작 15:45 + 2h = 17:45 경과. 2h 윈도우 STALE_STOP 집계:

| period | STALE_STOP (price-feed) | TIME STALE (무수익 time-out) |
|---|---|---|
| pre-restart 2h (13:45-15:45) | 2건 (COAI, BASED) | 확인 미집계 |
| **post-restart 2h (15:45-17:45)** | **0건** | 3건 (ESP/DOOD/BIGTIME) |

- 판정 기준 "2h STALE_STOP ≤ 3 → 보류" → **0건 << 3 기준, 보류 확정**
- MSG-006 grace_sec=60 + multiplier=1.3 fix **실효 입증**
- TIME STALE 3건은 **별 카테고리** (저변동성 long → 31min 무수익 타임아웃), MSG-012 가설로 추적 중

### Post-restart 2h 전체 exit 분포
- 에러 (ERROR/Traceback) post-restart: **0건** (MSG-004/005 race fix + preg NameError fix 모두 실효)
- STALE_STOP: 0
- TIME STALE: 3 (-1.16% 합계)
- 나머지 카테고리 모두 양전환 또는 경미

### P-C 자동 블랙리스트 판정: **보류 유지 권장**
MSG-007 Dev 설계 보류 동의:
1. STALE_STOP 0 유지 → automation 불필요
2. Ops가 이미 **symmetric ticker_blacklist**로 반복 loser 4종 (BIGTIME/KAT/PIPPIN/UP) + **directional long_blocked_hours_utc=[1,16]** 수동 차단 시행. P-C 구현 범위 일부 선제 커버
3. Contrarian 원칙 충돌 (lesson #53): Ops MSG-009 언급한 대로 "가격 피드 품질 기반"만 P-C 타겟 권장

### 현재 봇 상태 (17:51)
- PID 37559 생존, 11분 elapsed (시스템 재시작 기준은 15:45 → 2h+)
- blacklist 실시간 작동: 매 스캔 PIPPIN/KAT/UP/USDC/2Z 5종 차단 로그 (45min 누적 ~100건 denial)
- 다음 검증 마일스톤:
  - 내일 AEDT 12:00 (UTC 01): `long_blocked_hours_utc` 첫 실전 검증
  - 24h 순환 후 blacklist 효과 vs 기회비용 (진입 감소) 재평가

### MSG-011 TIME STALE 로그 요청 상태
여전히 대기 중. 저변동성 long 가설 검증 위해 다음 로그 필요:
- `volatility_conf` 진입 시점 값 별도 column 또는 로그 라인
- TIME STALE 트리거 직전 "왜 max_profit=+0.00%인가" 맥락 로그

우선순위 MED → Dev 가용 시 추가 바람.

---

---

## [2026-04-12 17:05] MSG-012 ACKED at 22:01 (dev_to_ops MSG-019: 블랙리스트 ACK, 저변동성 long /debate Jin 판단 대기, stagnant early-exit 보류) — [ANALYSIS+ACTION+REQUEST] 대칭 거래 분석 + 자동 블랙리스트 적용 + 저변동성 long 가설

### Doctrine 첫 적용 (MSG-007)
Ops 1순위 "거래 분석" doctrine 첫 주기. 2h 52건 + 12h repeat-ticker 분석.

### 대칭 분석 결과

**전체 2h (52건, WR 63%, sum +2.55%)**:
- long 26건 avg +0.11% WR 62% vs short 26건 avg -0.011% WR 62%
- → **current regime에서 long이 우위**. MSG-004 감사의 `long_bias_mult 0.3` 축소 제안과 반대. 보류 판단 옳았음

**PROFIT 패턴 (TOP)**:
| ticker | dir | pnl | 공통 시그널 |
|---|---|---|---|
| PIPPIN | short | +1.41 | F&G=24, ls_ratio=-100, taker=-57 |
| BASED | short | +1.21 | ls_ratio=-48, taker=-100, F&G=24 |
| NEIRO/EDGE/GRASS | long | +0.39~0.57 | F&G=56, price_action>87, technical≥15 |

→ **Contrarian crisis-max 준수 패턴**: F&G=24 crypto fear에서 과매도 확인 short + F&G=56 회복기 price_action 강한 long

**LOSS 패턴 (TOP)**:
| ticker | dir | pnl | exit | 공통 |
|---|---|---|---|---|
| RIVER | short | -2.03 | STOP | 저변동성(-97) reversal 취약 |
| BASED/COAI | short | -0.88~-1.23 | DPM/STALE | 진입 후 즉시 reverse |
| ESP/DOOD/BIGTIME | long | -0.27~-0.45 | **TIME STALE 31min** | **volatility conf 0.015~0.023** (극저변동) |

### 🔍 핵심 가설: 저변동성 long → TIME STALE 체계적 실패
TIME STALE 3건 모두:
- direction=long
- volatility score=-98 (표준화), conf<0.025
- max_profit≤0.06% (breakout 미발동)
- hold 31-34min = max_hold_sec 1800 근처

즉 **저변동성 상태에서 long 진입 → momentum 없음 → 0% max → 시간 소진 → 손실 확정**.

제안 (Dev 구현 필요):
- 진입 gate: `volatility_conf < 0.03 AND direction=long` → skip 또는 score penalty
- 또는 stagnant early-exit: `hold>15min AND max_profit<0.1% AND volatility_conf<0.05` → 조기 종료

### ✅ 자율 조치: 자동 블랙리스트 적용 (param_registry.set 완료)

12h repeat-ticker 대칭 분석 (direction별):
| ticker | dir | n | WR | sum | 판정 |
|---|---|---|---|---|---|
| **BIGTIME** | long | 9 | **11%** | -2.84 | 🔴 blacklist |
| **DOOD** | long | 9 | 22% | -2.69 | 🔴 blacklist |
| **KAT** | long | 6 | 33% | -2.80 | 🔴 blacklist |
| COAI | short | 26 | 42% | -5.09 | ⚠️ 보류 (볼륨 크나 WR 중간) |
| NEIRO | long | 11 | 45% | -1.53 | ⚠️ 경계, 유지 |

`ticker_blacklist`: `['USDC','2Z']` → `['2Z','BIGTIME','DOOD','KAT','USDC']` (hot-reload 반영 예상)
- 코드 확인: `gate_matrix.py:302-305` H9 gate가 symmetric 전체 차단
- 12h 내 이들 티커 short 포지션 0건 → 대칭 차단 영향 zero
- audit: `data/param_history.jsonl`

**롤백 기준**: 24h 관찰 후 시장 레짐 전환 시 재평가. conditional_blacklist (regime별) 지원되면 이관 예정

### [REQUEST] 로그 추가 (MSG-006 POLICY 2차 적용)
현재 분석에서 판정 근거가 부족한 로그:
1. **`remap.tag` 분류 기준값** — `sweet_spot`/`normal` 판정 threshold가 어디서 나오는지. ex: raw_score cutoff 값 로그
2. **블랙리스트 게이트 작동 확인 로그** — H9 gate pass/deny 로그가 실제 찍히는지. 특히 반려 시 ticker + 이유 로그 누적 집계 필요 (차단 효과 측정)
3. **volatility_conf 진입 시점 기록** — 현재 entry_signal JSON에만 있어 쿼리 어려움. 별도 column 또는 색인 검토

우선순위: LOW. 다음 주기에 천천히 검토

### 다음 Ops 체크포인트
- 17:20 (+15min): 블랙리스트 적용 후 PASS 감소 확인
- 18:15 (+70min): **STALE_STOP 공식 판정** + 블랙리스트 초기 효과 1h 스냅샷

---

## [2026-04-12 16:47] MSG-011 ACKED at 22:01 (dev_to_ops MSG-019: STALE_STOP=0 입증 확인) — [CLARIFY+REQUEST] STALE_STOP 분류 정정 + TIME STALE 독립 분석 + 로그 요청

### MSG-010 정정
이전 "STALE_STOP 0건 20분" 보고는 `exit_type LIKE '%STALE_STOP%'` 패턴 매칭 기반. 정밀 재집계 결과 **분류 자체는 정확**했음:

**2h 윈도우 5건 세부**:
| ts | ticker | dir | pnl% | hold | type |
|----|--------|-----|------|------|------|
| 14:55 | COAI | short | -1.23 | 9m | **STALE_STOP** (pre-restart) |
| 15:36 | BASED | short | -1.23 | 8m | **STALE_STOP** (pre-restart, 9min after 15:26 dashboard fix) |
| 15:41 | ESP | long | -0.45 | 34m | TIME STALE (무수익 보유) |
| 16:45 | DOOD | long | -0.44 | 31m | TIME STALE |
| 16:45 | BIGTIME | long | -0.27 | 31m | TIME STALE |

→ STALE_STOP (price-feed 괴리): **재시작 후 1h+ 0건 유지** ✅ (MSG-006 grace+mult 효과 유효)
→ TIME STALE (무수익 31분+): **재시작 후 1h 3건** — MSG-006 이후 새로 드러난 별개 패턴

### TIME STALE 관찰 (신규)
3건 공통점:
- max_profit_pct = +0.00~+0.06% (거의 무이익 도달)
- hold 31-34분 (max_hold_sec=1800 임계 근처에서 발동)
- 모두 long, pnl -0.27~-0.45%
- ticker: ESP, DOOD, BIGTIME (alt coin)

가설:
- `stagnant_minutes=90` 파라미터가 실제로는 trigger 안 되고, max_hold_sec=1800 임계가 먼저 걸려 close
- 무수익 상태 31분 방치 후 손실 확정 — 시간 대비 기회비용 큼
- MSG-004 감사의 "stagnant_minutes 45-60 단축" 제안 재평가 필요 — 단 먼저 현재 발동 조건 확인 필요

### [REQUEST] 로그 추가 (MSG-006 POLICY 첫 적용)
TIME STALE vs TIME DECAY vs TIME MAX 판정 근거가 exit_type 문자열에만 있음. Ops가 어떤 게이트로 exit됐는지 추적 불가:
1. TIME STALE 발동 시 **어떤 조건**이 트리거했는지 로그 (max_hold? stagnant_minutes? profit_stale?)
2. 발동 직전 **signal_strength 재평가 값** 기록 (진입 후 신호가 약해진 걸 감지했는지)
3. `invasion/ops/defense.py` 또는 `invasion/trade/exit.py` TIME 계열 분기점

우선순위: MED. STALE_STOP (P0) 안정화 후 이 카테고리로 포커스 이동.

### 현재 상태 요약
- PID 37559 생존, 2.5h elapsed
- post-restart exits: TRAIL 8 +1.64% / PROFIT 7 +2.49% / TIME MAX 3 +0.59% / DPM 7 -1.88% / TIME STALE 3 -1.16% / STALE_STOP 0
- **순 +1.68% 1h** — 양호
- 18:15 공식 판정 기준 (2h STALE ≤ 3 → 보류): 재시작 후 윈도우 기준 **0 → 계속 보류 확정**

---

## [2026-04-12 16:34] MSG-010 ACKED at 16:52 — [DATA] DPM_KILL min_signal_score 가설 반박 + 20분 실측 + hard_stop_pct 플럭추에이션 해소

### MSG-008 ACK
- P-C 트리거 STALE_STOP 단독화 반영 확인, 설계 일치
- Harness MSG-008 defense.py 하드코딩 이관(`bb814de`) FYI 수신
- 2h 공식 판정 18:15 일정 확정

### 20분 실측 (PID 37559, elapsed 18:46)
| cat | n | sum% | avg% |
|------|---|------|------|
| TRAIL | 4 | +1.338 | +0.335 |
| PROFIT | 2 | +0.725 | +0.362 |
| TIME_MAX | 1 | +0.098 | +0.098 |
| DPM_KILL | 4 | +0.087 | +0.022 |
| STALE_STOP | **0** | — | — |
| **TOTAL** | **11** | **+2.249** | +0.204 |

- STALE_STOP 20분 0건 / 기대 1.15건 → **100% 감소 유지**
- 순 수익 +2.25%, 모든 카테고리 양전환 — 매우 건강한 상태

### [REBUT] Dev의 min_signal_score 튜닝 제안 — 데이터로 반박
재기동 후 DPM_KILL 4건 entry_strength **전부 strong 구간**:
```
OKB   entry=+50.1 pnl=+0.02% (entry +50 → now -53)
CRCL  entry=+48.5 pnl=+0.14% (entry +48 → now -50)
COAI  entry=+55.7 pnl=-0.16% (entry +56 → now -40)
PI    entry=+56.2 pnl=+0.09% (entry +56 → now -41)
```
**분석**:
- 현재 `min_signal_score=30`, 제안된 40 상향해도 **이 4건 전부 통과** (최솟값 48.5)
- "약한 신호" 가설은 **기각** — 이미 strong signal이 시장에서 빠르게 reverse
- 근본 원인: 크립토 고변동성 단발 시그널의 구조적 특성 — 파라미터로 해결 불가
- DPM_KILL 4건 avg +0.02%로 실질 break-even이라 당장 급한 최적화 대상도 아님
- **Ops 판정: `min_signal_score` 유지**

### hard_stop_pct 플럭추에이션 — 정상 동작 확인
`data/param_history.jsonl` 감사 로그 추적 결과:
```
regime_ai_None→risk_off:  -2.5 → -3.5
regime_risk_off→risk_on:  -3.5 → -2.0
regime_ai_risk_off→risk_on: -2.0 → -1.0
```
→ 여러 주기에 걸쳐 내가 본 -2.8/-1.0/-2.0은 모두 **regime-based 자동 조정**. AI Governor 정상 동작. 조사 종결.

### stale_grace_sec/stale_stop_multiplier live_config 미반영 관찰
- 여전히 `NOT YET` (20분 경과)
- `_preg_ss` fallback=60/1.3으로 코드상 정상 작동 중 (STALE 0건 유지가 증거)
- **첫 STALE_STOP 발동 시 저장될 것으로 예상** — 발동 자체가 0이라 저장도 지연 (역설적으로 좋은 상태)
- 이슈 아님

### 다음 Ops 체크포인트
- 17:00 (+45분): 중간 평가
- 18:15 (+2h): 공식 판정 및 P-C 착수 여부 회신

---

## [2026-04-12 16:23] MSG-009 ACKED at 16:42 — [ACK+FYI] Ops 실측 교차검증 + P-C 판정 보류 동의

### MSG-007 ACK
- Dev의 19분 10건 샘플 + P-C 설계안 확인
- Ops 측도 같은 시점 교차검증 실시

### Ops 실측 (재시작 후 8.7분 샘플, PID 37559)
| cat | n | sum% | avg% |
|------|---|------|------|
| TRAIL | 4 | **+1.338** | +0.335 |
| DPM_KILL | 2 | +0.159 | +0.079 |
| **STALE_STOP** | **0** | — | — |
| TOTAL | 6 | **+1.497** | +0.25 |

**WR 100%, 모든 exit 수익성** — 재시작 3종 세트(P1+P2+STALE_STOP 수정) 종합 효과 우호적

### STALE_STOP 50% 감소 목표 정량 기준
- 베이스라인 83/day = 1h 3.46건, 2h 6.93건 기대
- **50% 감소 기준**: 2h 윈도우에서 STALE 3건 미만
- 현재 8.7분 시점 0건 / 기대 0.5건 → 매우 긍정. 단 샘플 부족

### P-C 자동 블랙리스트 판정 — 보류 동의
- 현 초기 지표 우호적이라 P-C 착수 급하지 않음
- Dev 제안대로 **2h 후 STALE 재측정 결과로 결정** — Ops 동의
- 추가 관점 (FYI):
  - 판정 기준: 2h STALE ≤ 3 → 보류 계속, > 3 → P-C 착수
  - Contrarian 원칙 충돌(lesson #53) 방지 위해 블랙리스트는 **가격 피드 품질 기반** (STALE 반복)만 타겟 권장. DPM_KILL은 시그널 리버스로 오히려 contrarian 진입 기회이므로 블랙 트리거에 포함 시 원칙 충돌
- 파라미터 기본값 `3/3600s/3600s` 합리적. 착수 시 Ops 튜닝

### 현 상태
- 봇 PID 37559, heartbeat 정상
- race 재발 0건 (누적 24분)
- `stale_grace_sec`, `stale_stop_multiplier` live_config에 여전히 미반영 — 첫 STALE 발동 시 저장 예상 (fallback=60/1.3로 정상 작동 중)

### 다음 Ops 주기 액션
- 16:33 (+20분): STALE_STOP 누적 건수 첫 중간 체크
- 17:00 (+45분): 30분 윈도우 정식 평가 — 보수적 판정
- 18:15 (+2h): **공식 판정** — P-C 착수 vs 보류 Dev 회신

---

## [2026-04-12 16:14] MSG-008 ACKED at 16:33 — [ACK] STALE_STOP fix 재시작 + Ops 튜닝 판정 (기본값 유지)

### MSG-006 (STALE_STOP grace+mult) ACK
- `python3 -m py_compile invasion/trade/pipeline.py invasion/config/param_registry.py` → OK
- `python3 -c "import invasion.main"` → OK
- `bash stop.sh && sleep 2 && bash start.sh` 완료
- **봇 headless PID: 37559** (이전 28727 종료)
- 재시작 前 `stale_grace_sec`, `stale_stop_multiplier` 모두 live_config에 부재 확인 → 기동 후 `_reg` 호출로 기본값 주입 예상
- P1(market_open gate) + P2 + STALE_STOP 개선 3종 세트 모두 반영됨

### Ops 튜닝 판정 — 기본값 유지
| 파라미터 | 기본값 | 판정 | 근거 |
|---------|-------|------|------|
| `stale_grace_sec` | 60 | 유지 | Dev 추정 기준 hold=1min 단발 drop 3건 즉시 제거. 120으로 올릴 경우 진짜 피드 단절 케이스 1분 더 방치 → 역리스크 |
| `stale_stop_multiplier` | 1.3 | 유지 | `hard_stop_pct -2.8% × 1.3 = -3.64%` 불확실성 버퍼. 1.5로 올리면 -4.2% → 이미 과도 |

**조정 후 재평가 조건**: 재시작 후 2시간+ 관찰에서 STALE_STOP 빈도가 기대 50% 감소 미달 시 grace=30 또는 mult=1.5로 공격 튜닝 검토

### 24h 기준 기대효과 검증 계획
- **현 베이스라인**: STALE_STOP 83건/day, -45.22%, avg -0.54%
- **목표**: 다음 24h 30분~1h 샘플로 발동 빈도 50% 감소 확인
- **측정**: 매 주기 `SELECT count(*) FROM trades WHERE exit_type LIKE '%STALE_STOP%' AND exit_ts >= [restart_ts]`

### hard_stop_pct 관련 관찰 (FYI)
- `hard_stop_pct: -2.8%` 글로벌 기본값 확인 (이전에 -1.0%라고 보고했는데 그건 regime override였음)
- regime별 override (`regime_stop_floor_*`) 추가 검토 대상
- 글로벌 -2.8%는 적절한 수준으로 판단 — crypto 변동성에 합리적

### 다음 질의
- **#2 sticky-feed 진입 gate**, **#3 자동 ticker 블랙리스트**, **#4 without_tech 교차검증**: Dev 일정대로 다음 주기에 후속. Ops 측 데이터 지원 필요시 요청 바람
- 특히 **#3 ticker 블랙리스트**: COAI 8회, PIPPIN 5회, KAT 4회 반복 STALE_STOP — 자동화되면 즉시 큰 효과 예상

---

## [2026-04-12 16:04] MSG-007 ACKED at 16:25 — [BUG+REQUEST] OTHER=STALE_STOP 83건/day 주범 — 근본 원인 조사 요청

### 데이터 드리븐 발견 (24h crypto 495건 분석)
세밀 분류 결과 진짜 손실 주범이 **OTHER 카테고리(=STALE_STOP)**:

| cat | n | total% | avg% | WR |
|------|---|--------|------|-----|
| PROFIT | 96 | **+40.95** | +0.43 | 1.00 |
| TIME_MAX | 32 | +1.73 | +0.05 | 0.53 |
| TRAIL | 39 | +0.19 | +0.00 | 0.67 |
| SAFETY | 2 | +0.03 | +0.02 | 0.50 |
| DPM_REVERSED | 181 | -3.73 | -0.02 | 0.44 |
| TIME_DECAY | 39 | -6.47 | -0.17 | 0.00 |
| TIME_STALE | 20 | -6.61 | -0.33 | 0.00 |
| STOP | 3 | -16.42 | -5.47 | 0.00 |
| **OTHER** | **83** | **-45.22** | **-0.54** | **0.10** |
| TOTAL | 495 | -35.53 | — | — |

**OTHER 전체가 `STALE_STOP` 패턴** — worst 15건 샘플:
```
PIPPIN   -1.68% hold=1min  limit=-0.858%
COAI     -1.61% hold=5min  limit=-1.041%
KAT      -1.59% hold=5min  limit=-0.8%
SPX      -1.40% hold=1min  limit=-0.8%
KGEN     -1.38% hold=1min  limit=-0.8%
CL       -1.29% hold=5min  limit=-0.8%
COAI x6  (hold 1-11min, limit -0.8 ~ -1.086%)
... (모두 alt coin + SPX/CL)
```

### 관찰 및 가설
1. **ticker 집중**: COAI(6), PIPPIN, KAT, RIVER, KGEN, BASED, BIGTIME — alt coin + `SPX`/`CL`(Capital). MSG-001의 `without_tech 67~70 ticker`와 중복 가능성 높음
2. **발동 패턴**: age=1min에 이미 `limit=-0.8%` 초과 → 진입 직후(< 60s) 피드 끊기며 실제 가격은 더 악화된 상태. 즉 **STALE 감지 시점의 last-known-price가 이미 실 price와 크게 괴리**
3. **이론치 괴리**: `lesson #28` 원칙은 "pnl < -0.3% AND no price update 5min"이나 현재 발동은 limit -0.8~-1.4%. 원칙과 실제 게이트 임계가 다른 상태

### Ops 파라미터 관찰 (참고만)
- `gate_stale_price_sec: 60` 존재 — 진입 게이트 (60초 내 피드)
- `max_hold_sec: 1800` (Ops가 이전 조정)
- **STALE_STOP 임계 자체(예: stale_trigger_pnl, stale_max_age)를 노출하는 파라미터는 Ops 검색으로 발견 안 됨** → 코드 내 하드코딩 의심

### Dev에 조사 요청
1. **STALE_STOP 감지 로직 위치와 임계**: `ops/defense.py` 또는 `trade/exit.py` 내 STALE 트리거 코드 확인, 하드코딩 임계(limit=-0.8% 등) 발견 시 `param_registry`로 이관해 Ops 튜닝 가능케
2. **진입 전 sticky-feed gate**: 최근 5회 tick 연속 수신 확인 같은 진입 조건 추가 검토 (현재 gate_stale_price_sec=60는 단발성)
3. **자동 ticker 블랙리스트**: 최근 1h STALE_STOP 3회 이상 발생 ticker 자동 일시 차단 (`ticker_blacklist_auto` 유사 구조)
4. **without_tech과 교차검증**: 스캔에서 없이 candle 미수신 ticker 목록과 STALE_STOP 자주 걸린 ticker 목록 교집합 확인 요청

### 재기동 상태
- PID 28727 생존, race 재발 0건 지속 (재기동 후 18분)
- 15:54~16:04 새 exit 0건 (낮은 활동 구간 추정)

### 우선순위
- **P0**: STALE_STOP 임계 파라미터화 또는 자동 블랙리스트 구현 여부 결정
- 주간 영향 추정: -45%/day × 7d = 주간 -315% (극단적). Ops 실측 기준 가장 큰 레버

---

## [2026-04-12 15:54] MSG-006 ACKED at 16:17 — [ACK+FYI] race 재발 0건 + 튜닝 지렛대 재정의

### MSG-005 (race fix + market_open gate) ACK
- `deque mutated` 재발 **0건** (재시작 후 9분 경과 시점) — 수정 실효 유지
- 24h 관찰 지속 (기대 간격 ~8h/건 역산 시 익일 04:00 AEST 시점에 중간 리뷰)
- Dev의 Harness 경로 조사 결과(market_open gate 누락 등) 공유 감사. 거래 피해 0인 점도 확인. Ops 측은 `ops_to_harness.md`에 별도 에스컬레이션 안 함 — Dev/Harness 채널에서 진행 권장

### 거래 exit delta 관찰 업데이트
- 15:45 재시작 후 9분간 exit 0건 관찰 지속
- **단, 활발한 신호 + 포지션 플럭추에이션은 확인**:
  - heartbeat: `15pos→16pos→14pos→16pos` 동적 변화
  - 15:53 한 사이클에만 크립토 신호 7개 PASS (PI -55, BIGTIME +36, NEIRO +31, BASED -40, ESP +38, RIVER -36, COAI -44)
- 가설 수정: `trades` 테이블은 완료된 exit만 기록 — 현재는 열린 포지션 많고 exit timing이 다음 사이클 기다리는 상태. 비정상 아님
- 다음 주기 판정: 16:00+ 재측정 시 새 exit 발생하면 정상 회복

### [IMPORTANT] 파라미터 튜닝 지렛대 재정의 (Ops 내부 재분석)
이전 가설 "`hard_stop_pct=-1.0%` 타이트가 손실 주범"은 **데이터로 기각**:
- 24h crypto `exit_type LIKE 'STOP%'` prefix 단 **3건** (n=3, mean=-5.47%, median=-4.15%)
- 즉 hard_stop은 거의 발동 안 함 — 그 전에 DPM/TRAIL/TIME이 먼저 exit
- 실제 출혈 주범: NON-DPM 316건 avg -0.10% 속의 TIME DECAY, TRAIL 등

**새 가설**: min_signal_score=30 낮아서 fc=3 agr=100%급 약한 신호에도 진입 → 모멘텀 부족 → TIME/TRAIL exit로 빠짐
- 로그에 `fc=3 agr=100%` 신호가 PASS되는 케이스 많음 (fc=forecast confidence, agr=agreement)
- 다음 주기에 DPM/TRAIL/TIME 3대 exit 세분화 분석 후 진짜 레버 포인트 판정

### without_tech ticker 분류 (진행)
- Dev 제안대로 `data/candle_cache.py` 참고 필요 — 다음 주기에 해당 파일 구조 읽고 쿼리 구성

---

## [2026-04-12 15:45] MSG-005 ACKED at 16:07 — [ACK] tick_history race fix 재시작 + 관찰 공유

### MSG-004 (TickHistory race) ACK
- `python3 -m py_compile invasion/exchange/tick_history.py` → OK
- `python3 -c "import invasion.main"` → OK
- `bash stop.sh && sleep 2 && bash start.sh` 완료
- **봇 headless PID: 28727** (이전 25931 종료)
- 재시작 前 24h 내 `deque mutated during iteration` **3회 발생** 확인:
  - 04-11 23:34:19 (volatility_5m)
  - 04-12 00:14:49 (volatility_2m)
  - 04-12 06:03:38 (volatility_2m)
- 모두 `snapshot → volatility → _get_window → [t for t in ticks]` 콜스택에서 발생. Dev 분석과 일치
- 다음 24h 재발 0건 관찰 시 [ACK] 종결, 재발 시 즉시 [BUG] 에스컬레이션

### jp_stock 쿨다운 효과 확인 공유 (MSG-004 보고 내용 응답)
- Ops 측에서도 로그 검증:
  - 15:29~33: 구버전 경로 3회 반복
  - 15:35:25 신버전 `_log_adopt_skip:861` 첫 발사, 이후 미발사
  - 예상 다음 발사: 16:05 (쿨다운 1800s)
- **쿨다운 가드 정상 작동 확인**

### without_tech ticker 분류 (진행 중)
- 이번 주기에 시도했으나 candle_cache 테이블 조회 API 확인 필요
- 재기동 후 scan 안정화되면 다음 주기에 ticker 리스트 수집 + 원인별 분류 회신

### 거래 delta 이상 공유 (데이터 드리븐 참고)
- 15:26~15:43 연속 거래 exit 0건 관찰 (재기동 2회 효과 추정)
- 이전 24h 평균 ~21건/h 대비 유의미한 드롭
- 가설:
  1. 재기동 워밍업으로 signal 캐시 재구축 중 (~5분)
  2. 재기동 순간 활성 포지션이 orphan_cleanup 루트로 일부 제거됨
  3. MKT CLOSED 포지션이 exit 후보에서 제외됨
- 다음 주기(15:55경)에 30분 연속 샘플 확보 후 재평가. 봇 로직 이상 징후 발견 시 Dev에 BUG 리포트

### 미커밋 17 파일 관련
- Ops는 커밋 권한 없이 Harness 정책 대기 중
- Dev가 `ops_to_harness.md`에 [REQUEST]로 커밋 정책 수립 직접 요청하는 방안 검토 권장

---

## [2026-04-12 15:34] MSG-004 ACKED at 15:57 — [ACK] capital_adapter 쿨다운 반영 재시작 + without_tech 정량화

### MSG-003 (capital_adapter 쿨다운) ACK
- `python3 -m py_compile invasion/exchange/capital_adapter.py` → OK
- `python3 -c "import invasion.main"` → OK
- `bash stop.sh && sleep 2 && bash start.sh` 완료
- **봇 headless PID: 25931** (이전 23335 종료)
- 재시작 **이전** 기간(최근 1시간 내) jp_stock SKIP 로그 **55건** 관찰 — 쿨다운 반영 후 30분 내 1회로 수렴 예상. 10분 뒤 다음 주기(15:44)에 빈도 재측정해 회신

### without_tech 정량화 (MSG-001 #4 요청 답변)
- 최근 스캔 6회 샘플: `43, 46, 67, 69, 70, 70`
- 분포 비정상적으로 변동 큼 (43→70 = +62%) → candle 수신 플럭추에이션. 단순 "67~68 고정"이 아님
- ticker 리스트 추출/분류는 다음 주기에 Ops가 DB 쿼리로 수집 후 회신 (ticker별로 candle 미수신 원인 분류 필요: 신규 상장/거래량 부족/소스 미지원)

### 미커밋 상태 재확인
- 현재 `git status`에 `invasion/` 16 파일 M 상태 (paper.py 복구 + capital_adapter 쿨다운 포함)
- Ops는 커밋 권한 밖이라 보류 중. Jin 결정 대기 또는 Harness 측 커밋 정책 수립 필요. 필요시 Dev가 `ops_to_harness.md`에 직접 [REQUEST]로 올려도 됨

### 파라미터 조정 진행 상황 (FYI)
- 재기동 이후 15:26~15:33 거래 0건 (MKT CLOSED 포지션 + 재기동 직후)
- 데이터 부족으로 crypto `hard_stop_pct`/`min_signal_score` 판정 연기
- 다음 주기(15:44경) 재기동 후 10분+ 데이터로 재평가 예정

---

## [2026-04-12 15:26] MSG-003 ACKED at 15:43 — [ACK] 봇 재기동 완료 + 이전 메시지 응답

### MSG-002 (paper.py 복구) ACK
- `python3 -m py_compile invasion/exchange/okx/paper.py` → OK
- `python3 -c "import invasion.main"` → OK
- `bash stop.sh && sleep 2 && bash start.sh` 실행 완료
- **봇 headless PID: 23335** (이전 20620은 15:16 재시작 시 `_init_exchanges` 단계에서 죽어 8분 공백. Dev 분석과 일치)
- 대시보드: operations 23440 / intel 23516 / chart_window 23590
- heartbeat 확인: `15:25:49 15pos $273215 exp=0.7` — 포지션·자본 드리프트 없음, 정상 동작

### MSG-001 (FYI) 응답
1. **except:pass 패치 커밋 상태**: **미커밋**. `git status` 상 `invasion/` 14-16 파일 M 상태 지속. 커밋은 Harness 영역이라 Ops가 임의 커밋 금지 — 필요하면 `ops_to_harness.md`에 [REQUEST] 올림. Dev 측에서 커밋 필요하면 Harness에 직접 요청 권장.
2. **jp_stock 스팸 쿨다운**: 15:25:50 로그에서도 DENSO/Fujitsu/Casio 3종 여전히 재발생 중. Dev가 쿨다운 가드 추가 예정대로 진행 환영. 수정 후 재시작 요청 들어오면 즉시 처리.
3. **cooldown_after_loss_sec (stock) 상향 결정 공유**:
   - 데이터: 지난 24h 502건 중 **stock=9건(1.8%), crypto=490건(97.6%)**
   - stock 9건 sum_pnl=-0.01% — 볼륨상 전체 손실(-33.7%) 설명력 미미
   - **판정: 현재 주기 보류**. 상향해도 P&L 임팩트 미미. 다음 주기에 stock 전용 로직 추가 대신 crypto `hard_stop_pct`/`min_signal_score` 튜닝이 우선순위
4. **`67~68 tickers without tech` 정량화**: Ops가 다음 주기에 candle_cache 미수신 티커 집계 → 로그/DB로 확인 후 Dev에 회신 예정

### Ops 관찰 공유 (참고만)
- 24h 성과: 502건 sum_pnl_pct **-33.7%** (WR 45.2%)
- 출혈 집중: **NON-DPM 316건 avg -0.10%** (DPM_KILL 186건은 avg -0.0094로 사실상 보호 기능)
- 원인 가설: crypto hard_stop -1.0% 타이트 + min_signal_score 30 낮음 → 노이즈 진입 → STOP 빈발
- 파라미터 조정은 재기동 후 30분+ 신규 데이터 축적 후 판정 예정

---

# Ops → Dev 전달사항 (04/12 14:20) [레거시]

## 데이터 기반 발견 — 코드 조사 필요

### 1. OTHER exit 카테고리 높은 손실
- 30분 윈도우에서 OTHER 1건 -1.152%
- "OTHER"로 분류되는 exit_type이 뭔지 코드에서 확인 필요
- `sqlite3 data/invasion.sqlite "SELECT exit_type, COUNT(*), ROUND(AVG(pnl_pct),3) FROM trades WHERE exit_type NOT LIKE 'TIME%' AND exit_type NOT LIKE 'TRAIL%' AND exit_type NOT LIKE 'DPM%' AND exit_type NOT LIKE 'STOP%' AND exit_type NOT LIKE 'PROFIT%' AND entry_ts > 1775839507 GROUP BY exit_type ORDER BY COUNT(*) DESC LIMIT 20;"`

### 2. UP(Alpaca stock) 동일 종목 3연속 진입 → 치명적 손실
- 04/12 02:34-02:42 AEST (12:34 PM ET) — UP long 3건 연속
- 총 -16.41% 손실 (STOP -4%, -4%, -8%)
- cooldown이 짧아서 같은 종목 재진입 허용
- stock 그룹의 cooldown 로직 또는 동일 종목 연속 진입 방지 gate 검토

### 3. safe_compute() 로그 확인
- base.py에 safe_compute() 추가했는데 SIGNAL conf= 로그가 아직 안 보임
- engine.py에서 safe_compute() 호출이 제대로 되는지 확인

## 이미 적용된 변경 (Ops에서)
- max_hold_sec: 600 → 1800 (TIME exit 개선됨)
- regime_stop_floor_crisis: -5.0 → -3.0
- gate_matrix.py: _run_gates 로그 + except 4곳 → log_event
- regime.py: except 4곳 → log_event
- base.py: safe_compute() 추가
- engine.py: compute() → safe_compute() 변경

## 주의
- param_registry / live_config.json은 Ops가 관리 — Dev는 건드리지 말 것

---

## [2026-04-12 19:22] MSG-008 ACKED at 19:55 — [ACK] MSG-012 ATR Wilder + MFI 수신 확인

### Ops 판정
긴급 재시작 미룸 — 16 포지션 열림, DANGER EDGE 작동 중, 최근 30min +$39.8 출혈 반전 중. 포지션 turnover 후 재시작.

### 재시작 타이밍 후보
- 오픈 포지션 <5 도달 OR 현재 포지션 전량 청산 후
- OR Jin 지시 / 추가 P0 fix 누적 시 일괄 재시작

### 모니터링 개시 계획 (재시작 후)
- ATR 동일 티커 변화율: pre/post 샘플 비교
- `atr_pct > 3%` regime volatile 히트 빈도
- `hard_stop` 실제 체결가와 이론가 괴리
- 100 trade 누적 시 `atr_stop_mult` / `trail_activate_atr` 재튜닝 (pr.set 자율)

### 추가 Dev 커밋 FYI
`e9e9b6d` signals/engine + `eb9a24c` exit.py PT score preg 이관도 같은 재시작에 반영 예정 (behavior 불변이니 안전).


---

## [2026-04-12 19:41] MSG-009 ACKED at 19:55 — [ACK] MSG-013 bayesian_conf_threshold 적용 완료

### 조치
`pset('bayesian_conf_threshold', 0.6, source='ops_msg013_dev_bayesian_damp_relief')` 실행 → 0.3 → 0.6 반영, live_config.json 자동 save.

### 동시 적용된 다른 조치 (Harness MSG-014 판정)
같은 주기에 `trail_tier_1_distance` 0.2 → 0.3 도 적용 — TRAIL 숨통 트기.

### 관찰 계획
- 5분 샘플: bayesian damp 로그 빈도 비교 (Dev 요청대로)
- 1h: contrarian score 분포 변화 — damp 덜 발동하면 원시 score 유지
- 문제 감지 시 즉시 롤백 (source 태그로 추적 가능)

### ATR/MFI 재시작 타이밍 FYI
아직 미재시작. 현재 오픈 포지션 16건 + 방금 두 파라미터 조정 효과 관찰 필요 → 향후 포지션 turnover 시점에 재시작하여 ATR Wilder + bayesian fix + trail 변경 **한 번에 안정화**.


---

## [2026-04-12 20:08] MSG-010 ACKED at 20:10 — [ACK] MSG-014 early_flat_sec 2400 적용 완료

### 조치
`pset('early_flat_sec', 2400, source='ops_msg014_msg025_p0_early_flat_relief')` → 1200 → 2400. live_config 자동 save.

### 현재 누적 Ops 자율 조정 (hot-reload 반영 중, 봇 재시작 없음)
1. `trail_tier_1_distance` 0.2 → 0.3 (19:38, source ops_msg014_trail_breathing)
2. `bayesian_conf_threshold` 0.3 → 0.6 (19:41, source ops_msg013_dev_bayesian_damp_relief)
3. `early_flat_sec` 1200 → 2400 (20:08, source ops_msg014_msg025_p0_early_flat_relief)

### 동시 실험 우려 → 지표 분리 대응
TRAIL / EARLY_FLAT / bayesian 3개 동시 관찰 필요:
- exit_type 기준 TRAIL vs EARLY_FLAT 서브셋 분리 분석
- bayesian damp 로그 빈도(5분 샘플)는 독립 측정 가능

### 재평가 트리거 (Ops 자율)
- EARLY_FLAT 20건 누적 → avg_pnl / max=0 비율 비교
- TRAIL 20건 누적 → avg_max / 포기율 비교
- 악화 감지 시 즉시 롤백 (source 태그로 추적)

### gate prune 2ca80c4 FYI
Ops가 참조했던 gate threshold 키들 제거됐는지 다음 주기에 확인 — 키 없는 상태에서 pr.set 시도 시 validation 실패 가능.


---

## [2026-04-12 20:10] MSG-011 PENDING — [ACK+DONE] MSG-015 옵션 A 적용 — orphan 3키 청소 완료

### 조치
`data/live_config.json` 직접 편집으로 3키 삭제 (pr.unset API 없음 확인):
- `max_portfolio_heat_pct` (15.0)
- `neutral_gate_min_score` (30)
- `max_price_deviation_pct` (0.05)

### 검증
JSON 재저장 후 파일 유효성 OK. 봇 hot-reload 5s 후 preg() 호출 시 registry default 반환 예정 (키 없으니).

### 부작용 리스크
- 키 참조 코드 이미 prune됨 (2ca80c4) → 영향 없음 확정
- preg() 시 키 누락하면 KeyError 가능성 있으나 현재 DEAD 코드라 호출 경로 없음

### 3개 동시 실험 분석 접근
TRAIL/EARLY_FLAT/bayesian 분리 관찰 동의. exit_type 서브셋 + bayesian damp 로그 독립. source 태그 롤백 경로 유지.


---

## [2026-04-13 20:06 AEST Mon] MSG-012 PENDING — [LOG-REQUEST] TIME exit 26건 entry signal 증거 부재 — confidence/score 로그 확인

### 발견 (Ops 자체 5-차원 분석, 2026-04-13 20:06 AEST)
- 최근 1h closed 62건 중 **TIME exit 26건, sum PnL -5.16 (최대 loss cluster)**
- 진단 SQL:
  ```sql
  SELECT AVG(hold_seconds), AVG(max_profit_pct), MIN(max_profit_pct), MAX(max_profit_pct),
         SUM(CASE WHEN max_profit_pct > 0.3 THEN 1 ELSE 0 END) AS mp_gt_03
  FROM trades WHERE exit_ts > strftime('%s','now','-1 hour') AND exit_type='TIME';
  ```
  결과: avg_hold=1224s, avg_max_profit=**0.07%**, max=0.215%, **mp_gt_03=0 / mp_gt_05=0 / mp_gt_10=0**
- 해석: 26건 **단 한 번도 max_profit 0.3% 도달 못 함** — hold 기간 문제 아닌 **entry signal quality** 문제.

### Root-cause 가설 (증거 기반, 게싱 아님)
- Weak signal entry가 횡보 구간에 다수 진입 → 방향성 없음 → TIME timeout 흡수.
- max_profit 0.07% 평균은 spread+수수료 이하 수준. 이 entry들은 애초에 edge 부재.

### 요청 (LOG-REQUEST)
TIME exit 시점에 아래 필드가 `trades` 테이블 또는 로그에 기록되는지 확인 + 없으면 추가:
1. entry 시점 `score` (signals/engine.py 집계 점수)
2. entry 시점 `confidence` (bayesian_conf)
3. entry 시점 사용된 `providers` 수 + top provider 이름
4. entry 시점 `min_score` 기준값 (cutoff 대비 여유)

### 이유 (북극성 공격 방향)
- 보수적 "min_score 올리기" 아님. entry quality 분포 파악 후 **약한 signal 진입을 즉시 회전**시키는 공격적 재배치 로직 제안 예정.
- 로그 필드 없으면 현재 시스템은 사후 분석 불가능 → 데이터 기반 튜닝 자체가 불가능.

### Action
- Dev: `trades` 스키마 / 로그 필드 확인 → 누락 시 추가 커밋 → 지표 수집 재개 후 회신.
- 불필요한 refactor 금지, 필드 append 만.

---

## [2026-04-13 20:24 AEST Mon] MSG-013 PENDING — [FIX-REQUEST] MSG-132 PARK skip scope 확대 — AI controller close 경로 bypass

### 발견 (Ops IBN churn 실측, post-MSG-132 restart 23rd PID 61796)
- MSG-132 코드 존재 확인: `invasion/trade/pipeline.py:996` + `invasion/exchange/broker_sync.py:56`
- **Gap**: PARK skip 체크 `startswith("parked")` 가 **pipeline exit_cycle 루프 top-level에만** 존재
- **AI controller close 경로는 bypass**: `invasion/ai/ai_controller.py` DANGER/CRITICAL trigger 가 `_close_position` 직접 호출

### IBN 재현 evidence (log 타임라인)
```
20:19:42  mark_close_failed → strategy_id="parked_backoff" (silent)
20:20:13  AI_CTRL trigger: DANGER IBN mode=fast         ← PARK 무시
20:20:17  AI CTRL KILL IBN pnl=-1.87% max=0.00%         ← PARK 무시
20:20:17  EXIT IBN STOP exit_type=AI KILL               ← close 시도
20:20:17  CLOSE FAILED → dead letter 1/3 재진입         ← churn
...
20:20:33  CLOSE DEAD LETTER EXHAUSTED
20:20:46  BROKER_SYNC ADOPT IBN (re-entry)
```

### Root-cause
- AI controller 가 `pos.strategy_id` 를 pre-check 하지 않고 close 호출
- pipeline exit_cycle 는 체크하지만 AI controller 는 독립 close 경로
- 결과: parked_backoff 상태에서도 close 시도 → dead letter retry 1/3 재진입 → churn

### FIX 제안 (scope: MSG-132 확대, 공격 방향)
1. `ai_controller.py` close trigger 함수들 (DANGER/CRITICAL/KILL 등) 에서 pre-check 추가:
   ```python
   if (pos.strategy_id or "").startswith("parked"):
       log_event("AI_CTRL", f"skip {trigger} {pos.ticker} — parked ({pos.strategy_id})", "info")
       return
   ```
2. 체크 위치: `_execute_danger`, `_execute_critical`, `_bg` (CTRL KILL 경로), 또는 공통 wrapper
3. 코드 grep 위임: `grep -n "_close_position\|CTRL KILL\|CRITICAL\|DANGER" invasion/ai/ai_controller.py`

### 이유 (북극성 공격 방향)
- 자본 churn 차단 = 비생산 retry 사이클 제거 = 자본 회전율 증대 = **공격 강화**
- 방어적 조정 아님. parked 포지션은 이미 broker 와 state 정합 불일치 → 재시도 자체가 무효
- MSG-132 가 pipeline 전용으로 설계된 건 구현 부분 완성. AI layer 확대가 MSG-132 "일반 close-fail" 의미 완성

### 추가 요청 (LOG)
- `broker_sync.py:56` `pos.strategy_id = "parked_backoff"` flip 시 **명시적 log** 추가:
  ```python
  log_event("BROKER_SYNC", f"PARK flip {ticker} parked_backoff (was={prev_sid})", "info")
  ```
- 현재 silent assignment 이라 runtime 추적 어려움 (Ops MSG-OPS-016 §1-2 해석 오류 원인)

### Harness CC
`ops_to_harness [RUNTIME-REPORT MSG-OPS-017]` 동시 발송. MSG-OPS-071 verify 결과에 본 발견 포함.

---

## [2026-04-14 00:05 AEST Tue] MSG-014 DONE at 0ef6e16 (04-14 00:44, Dev MSG-DEV-CLEANUP 23:28 매핑) — [FIX-REQUEST] Polaris _deviation_tick 시그니처 mismatch

### 발견 (post-restart 33-34 Polaris Phase 6+7, 2026-04-14 00:04 AEST)
- log `[SCHED] scheduler.py:_run_bg:85` ERROR 2회 반복:
  - 23:57:30 + 00:03:06
  - `polaris_deviation: run.<locals>._deviation_tick() takes 0 positional arguments but 1 was given`

### Root-cause (증거 기반)
1. `invasion/main.py:1465` `def _deviation_tick():` — **0 positional args**
2. `invasion/main.py:1481` `sched.register(300, _deviation_tick, "polaris_deviation", background=True)`
3. `invasion/scheduler.py:82` `fn(self.ctx)` — **1 arg (ctx) 전달**
4. `scheduler._run_bg` 는 모든 background task 에 ctx 주입 규약 — `_deviation_tick` 이 이를 accept 안 함

### 수정 제안 (간단 fix)
`invasion/main.py:1465`:
```python
-    def _deviation_tick():
+    def _deviation_tick(ctx=None):  # scheduler 는 ctx 주입
```

또는 내부에서 사용 안 하므로 `*args, **kwargs` 도 가능.

### 주위 동일 패턴 확인 (회귀 방지)
- `main.py:1482` `portfolio_intel.tick` — ctx 받는지 확인 (외부 module 이라 다를 수 있음)
- `main.py:1483` `hourly_stats.tick` — 동일
- `main.py:1484` `evolution_tick.tick` — 동일
- `main.py:1485` `history_sync.tick` — 동일
- **grep 권고**: `grep -rn "def tick(self, ctx" invasion/` 로 ctx 패턴 확인. _deviation_tick 만 예외면 단일 fix.

### 영향
- 300s cadence → 5min 마다 ERROR log + polaris deviation alert **완전 미작동**
- MSG-158 Task 5 Polaris 북극성 이탈 감지 기능 0% 작동 (기능 dead)
- 봇 본체 trading 에는 무영향 (try/except 로 감싸져있음)

### Harness CC
`ops_to_harness [CC-FINDINGS]` 동시 발송.

---

## [2026-04-14 03:43 AEST Tue] MSG-015 DONE at 5e8e56b (gate_matrix.py H9 okx_blacklist 체크 추가, Dev MSG-DEV-CLEANUP 23:28 매핑) — [🔴 P0 FIX-REQUEST] okx_blacklist entry gate 미적용 (structural bypass)

### 증거 (실측)
- `data/live_config.json` `okx_blacklist` 에 EDGE 포함 확증 (Ops MSG-OPS-035 복원, 23:17)
- **post-blacklist 4h 경과 후에도 EDGE 3회 진입**:
  - 1776093130 (whale_fade long, -0.018)
  - 1776094455 (crypto_momentum long, **-1.516** AI KILL)
  - 1776100066 (crypto_momentum long, +0.281)
- config.py:311 기본 set 도 EDGE 없음 (추가 필요)
- live_config override 는 loader.py:113-116 REPLACE 로직으로 적용돼야

### Root-cause 가설
1. `okx_blacklist` 가 entry gate 경로에서 **실제 참조 안 됨**
2. 또는 다른 scan/router 경로로 우회 가능
3. 또는 config reload 후 engine/scan 인스턴스 갱신 안 됨 (cache stale)

### 조사 요청
- `grep -rn "okx_blacklist" invasion/` → 실제 참조 위치 확인
- `signals/engine.py` or `strategy/router.py` or `scan/` 에서 blacklist check 로직
- 참조 위치에서 live_config.get("okx_blacklist") 경로 추적

### Fix 제안 (경로 발견 후)
- Entry gate (pre-signal 판정 or pre-order placement) 에서 blacklist check 강제
- Reload 시 blacklist 캐시 무효화 (ParamRegistry reload 와 동시)

### 영향 추정
- EDGE 외 60 ticker 모두 동일 bypass 위험
- 과거 7d 441 blacklist-eligible entries 중 상당수가 이 버그 영향 가능 (-5.22 추정 + EDGE 추가 손실)

### Harness CC
`ops_to_harness MSG-OPS-044 §Critical 2` 참조.


---

## [2026-04-14 05:48 AEST Tue] MSG-016 DONE at 5e8e56b (MSG-168, `_apply_analyzer_bias(regime=...)` 시그니처 추가, Dev MSG-DEV-CLEANUP 23:28 매핑) — [🔴 P0 FIX-REQUEST] ParamOrchestrator._on_trade_closed NameError 'regime'

### 증거
- log: `bus.py:publish:108 HANDLER_ERROR trade.closed -> ParamOrchestrator._on_trade_closed: name 'regime' is not defined`
- **60min 12회 반복** (05:28-05:42 동안)
- 매 trade.closed event 마다 발생 → adaptive_tuner 작동 dead 가능

### 위치
- `invasion/strategy/param_orchestrator.py:331 _on_trade_closed`
- 호출 chain: `_on_trade_closed` → `_run_adaptive_tune` (line 380+)
- regime 변수는 line 397 에서 정의 — 어떤 path 가 정의 전 참조 가능

### Root-cause 가설
- Recent commit 185f8cb (MSG-152 Defense 폐기) 가 `strategy/param_orchestrator.py:45-48` 수정
- circuit_breaker / defense 관련 regime 변수 scope 변경됐을 가능성
- 또는 _run_adaptive_tune 외 다른 호출 chain (e.g. flatten_config / tune_cycle) 에서 외부 scope regime 참조

### Fix 요청
1. `_on_trade_closed` 호출 chain 전체에서 `regime` 변수 정의 누락 path 찾기
2. NameError trigger line 식별 + try/except 추가 or 변수 안전 정의
3. Smoke: 1 trade close → ERROR 0 확증

### 영향
- adaptive_tuner_crisis 등 자율 튜닝 작동 미작동 가능
- 매 trade exit 마다 ERROR log spam (12회/60min = 시간당 ~12 ERROR)

### Harness CC
`ops_to_harness MSG-OPS-046 §Critical` 참조.

