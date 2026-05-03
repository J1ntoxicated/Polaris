# SPOT Scalp Paper Bot — Design Spec

- **Date**: 2026-04-30
- **Author**: Claude (Opus 4.7) under Jin mandate
- **Status**: Draft (pending Jin review)
- **Related**: `[[INSIGHT-024]]` `[[INSIGHT-028]]` `[[INSIGHT-029]]` `[[INSIGHT-030]]` `PROPOSAL-okx-cash-cow-first-2026-04-29`

---

## 1. 동기 (Why)

### 24h actual (2026-04-29 측정)
| 영역 | n | NET | WR |
|---|---|---|---|
| OKX crypto (SWAP) | 2562 | -$550 | 57.1% |
| cap commodity (CFD) | 161 | -$1944 | 19.3% |
| alpaca stock (spot) | 388 | -$1169 | 36.1% |
| 그 외 | 222 | -$185 | 30~41% |

비-OKX 합계 -$3320. CFD 손실 + 결함 (INSIGHT-024/028/029) 이 drag 의 main source.

### Jin Mandate
> "캐시 카우 하나 24/7 먼저 구축을 하는게 맞을듯."
> "테크니컬 실시간 가지고 85% WR 가능?"
> "전용 페이퍼 어카운트 만들면 되는거 아니야?"
> "케피탈만 cfd, 분기 가능?"

### 결론
**SPOT 전용 scalp paper 봇 신규** — Product type 분기 (SPOT vs CFD) 의 첫 검증.
1. OKX SPOT only Phase 1 (24/7 cash cow MVP).
2. 메인 봇 패키지 (`invasion/`) 무손상 — additive 패키지 (`invasion/spot/`) + 별도 sqlite. **단 dashboard tools (visualizer / intel / operations) 는 additive 변경 발생** (두 봇 동시 관찰 위해).
3. Tight scalp / maker-rebate 우선 — 기존 봇과 시그널/exit 완전 다름.
4. WR 75% (1차) → 85% (stretch). Expectancy 양수 필수.

---

## 2. 비목표 (Non-goals, YAGNI)

- 메인 봇 코드 수정 (Phase 1 무손상)
- Capital / forex / indices / commodity (CFD 영역 — 별도 결정)
- Alpaca SPOT (Phase 2 검토)
- Short selling (long-only baseline)
- 자동 fund 실거래 전환 (paper only)
- AI gate 통합 (Phase 1 순수 technical)
- Tournament evolver / mutation (Phase 1 fixed signal set)

---

## 3. 아키텍처

### 위치 (additive)
```
invasion/
├── (기존 그대로)
│   exchange/, strategy/, trade/, ticks/, data/, ai/, regime/, ...
│
├── spot/                       ← 신규 parallel 패키지
│   ├── __main__.py             (`python3 -m invasion.spot --headless`)
│   ├── runtime.py              (1초 tick 메인 루프)
│   ├── ws_feed_spot.py         (OKX SPOT WS 3채널)
│   ├── signal_scalp.py         (5 sub-signal AND-gate)
│   ├── router_spot.py          (maker post_only + taker fallback)
│   ├── exit_spot.py            (TP/TRAIL/TIME/HARD_STOP/SIGNAL_FADE)
│   ├── cell_resolve_spot.py    (5-dim slim cell)
│   └── store_spot.py           (자체 sqlite)
│
└── data/
    ├── invasion.sqlite         (기존 — 메인 봇)
    └── invasion_spot.sqlite    (신규 — SPOT 봇)
```

### 프로세스 모델
```
Process A (기존):  python3 -m invasion --headless        → invasion.sqlite
Process B (신규):  python3 -m invasion.spot --headless   → invasion_spot.sqlite
```
완전 격리. 같은 OKX public API read-only 공유. 쓰기 없음.

### 재사용 (read-only import)
| 기존 모듈 | SPOT 봇 사용 | 수정? |
|---|---|---|
| `invasion.exchange.okx.public.OKXPublic` | SPOT price fetch | ❌ |
| `invasion.utils.technicals` | BB/RSI/ATR/MACD/SAR/CCI/Donchian | ❌ |
| `invasion.regime.*` | crypto regime state | ❌ |
| `invasion.data.unified_schema` | 컬럼 정의 참고 | ❌ |
| `invasion.exchange.okx.public_funding` | SWAP funding (basis 계산) | ❌ |

### DB Schema (`invasion_spot.sqlite`)
```sql
CREATE TABLE trades (
  id INTEGER PRIMARY KEY,
  ticker TEXT, inst_id TEXT,         -- BTC-USDT (SPOT)
  side TEXT DEFAULT 'buy',           -- long-only
  entry_ts INTEGER, exit_ts INTEGER,
  entry_px REAL, exit_px REAL,
  qty REAL, size_usd REAL,
  net_pnl_usd REAL, pnl_pct REAL,
  fee_paid REAL,                     -- maker rebate 음수 가능
  fill_type TEXT,                    -- maker / taker / partial
  queue_pos INTEGER,                 -- 큐 위치 추정
  exit_type TEXT,
  strategy_id TEXT,
  status TEXT,                       -- pending_fill / open / closed / abandoned
  signal_meta TEXT,                  -- JSON (active_signals, score 등)
  cell_key TEXT,                     -- 5-dim composite
  exit_lock_ts INTEGER               -- race 보호
);

CREATE TABLE cell_matrix_spot (
  ticker TEXT, session TEXT, regime TEXT,
  strategy_id TEXT, direction TEXT DEFAULT 'long',
  optimal_tp_pct REAL,
  optimal_trail_giveback_pct REAL,
  optimal_max_hold_sec INTEGER,
  optimal_hard_stop_pct REAL,
  optimal_signal_fade_count INTEGER,
  exit_optim_n_samples INTEGER DEFAULT 0,
  total_pnl_usd REAL DEFAULT 0,
  win_count INTEGER DEFAULT 0,
  loss_count INTEGER DEFAULT 0,
  updated_ts INTEGER,
  PRIMARY KEY (ticker, session, regime, strategy_id, direction)
);

CREATE TABLE signals (
  id INTEGER PRIMARY KEY,
  ts INTEGER, ticker TEXT,
  active_signals TEXT,               -- JSON list
  score REAL,
  decision TEXT,                     -- enter / skip / signal_fade
  expected_tp_pct REAL,
  trade_id INTEGER                   -- link to trades.id (NULL if skipped)
);
```

### 시각화/대시보드 통합
- `tools/visualizer/snapshot.py`: 두 sqlite 모두 read → SPOT 노드 별도 cluster (`spot_data`, tier 12, lime green).
- `tools/visualizer/static/sphere-render.js`: `SPOT_TIER=12`, `SPOT_COLOR=0x00ff88`. 기존 노드 무영향.
- `intel.py`: `[SPOT BOT]` 신규 panel — Process status / WR / NET / maker fill rate / top signal.
- `operations.py`: 봇 process 라인 추가 (`✓ invasion.spot PID xxxx uptime ...`).

---

## 4. 컴포넌트 디테일

### 4.1 `runtime.py` (~250 line)
- 책임: 1초 tick 메인 루프. boot → loop → graceful shutdown.
- 의존: 모든 SPOT 모듈 + read-only import.
- 인터페이스: `def run(headless: bool = True) -> None`.
- 작업: ws_feed start, universe load, per-ticker signal eval → entry → store, exit loop, state.json heartbeat 30s.

### 4.2 `ws_feed_spot.py` (~200 line)
- 책임: OKX SPOT 3채널 동시 subscribe → in-memory state.
- 채널: `tickers` (price), `books5` (호가 5단), `trades` (체결 stream).
- 인터페이스:
  ```python
  class OKXSpotWSFeed:
      state: dict[str, dict]
      async def run(self) -> None
      def get_book(self, ticker) -> dict | None
      def get_taker_flow(self, ticker, window_s=60) -> dict
  ```

### 4.3 `signal_scalp.py` (~350 line) — 핵심
- 5 sub-signal (long-only):
  1. `bb_extreme_revert` — BB±2.5σ + RSI(14)<25
  2. `microstructure_imbalance` — bid/ask depth 비율 ≥1.5 + 60s taker buy/sell ratio ≥1.3
  3. `queue_position_advantage` — own limit price queue depth ≤30%
  4. `volatility_compression_burst` — ATR(14) / median(ATR, 100) <0.6 + 1m breakout
  5. `funding_decoupling` — abs(SPOT-SWAP)/SPOT >0.05% + SPOT 저평가
- Gate: 5중 3+ true AND `regime != 'crisis_high'` → enter.
- 인터페이스: `evaluate(ticker, ws_state, candles_1m, swap_price) -> dict`.

### 4.4 `router_spot.py` (~250 line)
- Maker only default: `post_only` limit at best bid + offset.
- Reprice loop 200ms: 미체결 시 cancel + re-quote.
- Taker fallback: signal score >0.85 AND maker 5초 미체결 → market.
- OKX demo header: `x-simulated-trading: 1`. `tdMode=cash`.
- 인터페이스: `place_entry`, `place_exit`, `reprice_loop`.

### 4.5 `exit_spot.py` (~180 line)
- Priority: TP → TRAIL → TIME → HARD_STOP → SIGNAL_FADE.
- Cell-aware override: `optimal_tp_pct`, `optimal_max_hold_sec`, etc.
- Long-only: liquidation 검사 X. pnl = (exit_px - entry_px) / entry_px.
- 인터페이스: `evaluate_exit(position, ws_state, cell_thresholds) -> str | None`.

### 4.6 `cell_resolve_spot.py` (~150 line)
- 5-dim: `(ticker, session, regime, strategy_id, direction='long')`.
- UPSERT learning (메인 봇 INSIGHT-027 패턴 차용).
- 인터페이스: `resolve`, `upsert_learning`, `get_thresholds`.

### 4.7 `store_spot.py` (~200 line)
- WAL mode sqlite3.
- CRUD: trades, cell_matrix_spot, signals.
- migration: schema bootstrap on first run.

---

## 5. 데이터 흐름

### Trade lifecycle
```
WS push → ws_feed state 갱신
  ↓ (1s tick)
runtime: per-ticker signal_scalp.evaluate
  ↓ (3+/5 true AND regime != crisis_high)
cell_resolve_spot.resolve → thresholds
  ↓
router_spot.place_entry (post_only limit)
  ↓
store_spot.insert_trade (status='pending_fill')
  ↓ (200ms reprice loop)
fill 또는 5초 후 taker fallback
  ↓
store update (status='open', fill_type, fee)
  ↓ (1s exit loop)
exit_spot.evaluate_exit → exit_reason
  ↓
router_spot.place_exit
  ↓
store update (status='closed', net_pnl_usd, exit_type)
  ↓
cell_resolve_spot.upsert_learning
```

### State stores
| Store | 누가 쓰나 | 누가 읽나 |
|---|---|---|
| `data/invasion_spot.sqlite` | runtime/router/exit/cell_resolve | visualizer, intel.py, audit |
| `data/spot_state.json` | runtime (30s heartbeat) | restart recovery |
| `ws_feed_spot.state` (in-memory) | ws_feed_spot | signal_scalp, exit_spot |

### Tick 빈도
| Loop | 주기 |
|---|---|
| runtime main | 1초 |
| reprice | 200ms |
| exit | 1초 |
| state.json snapshot | 30초 |
| visualizer refresh | 5분 |

---

## 6. 에러 처리

### 6.1 외부 의존성 (OKX API)
| 시나리오 | 대응 |
|---|---|
| 5xx | exponential backoff (1/2/4/8s, max 30s) |
| 429 (rate limit) | 30s pause + WARN log |
| 401 (auth fail) | 즉시 종료 + alert |
| WS 단절 | 3s backoff reconnect, 단절 동안 신호 skip |
| demo 환경 down | 5분 backoff |
| post_only 거부 (cross 발생) | 한 tick skip |

### 6.2 데이터 무결성 (silent-fail 0%)
- books5 stale >5s → `queue_position_advantage = False`
- 1m candle 누락 → REST fallback, 실패 시 신호 skip + WARN
- WS vs REST 0.5%+ 괴리 → 신호 skip 1분 + ERROR
- SPOT-SWAP basis >5% → `funding_decoupling = False` (extreme 의심)

### 6.3 주문 lifecycle (INSIGHT-029 교훈 — 5분 reconcile 의무)
| 시나리오 | 대응 |
|---|---|
| pending_fill age >5min | OKX `/orders-history` poll → fill / abandoned |
| Position 있는데 open trade 없음 | broker reconcile → manual flag |
| Open trade 있는데 잔고 없음 | `zombie_cleanup` exit_type |
| Partial fill 후 cancel | 체결분만 trade 기록 |

### 6.4 핵심 원칙 (CLAUDE.md 정합)
- `try/except: pass` 절대 금지 — 최소 `log_event`.
- 5분 reconciliation loop 의무 (INSIGHT-029 재발 차단).
- WARN 이상 → `data/spot_alerts.jsonl` + intel.py panel.

### 6.5 결함 metric 추적
- `okx_api_5xx_count_1h`
- `ws_disconnect_count_1h`
- `pending_fill_zombie_count_24h`
- `signal_eval_skip_rate`
- `maker_fill_rate` (목표 ≥80%)
- `taker_fallback_count`

---

## 7. 테스트 + 검증 KPI

### 7.1 단위 테스트 (`tests/spot/`)
- `signal_scalp`: 5 sub-signal true/false 경계 (fixture 캔들/호가)
- `cell_resolve_spot`: INSERT, UPSERT 누적, threshold 조회
- `exit_spot`: 5 priority 순서
- `router_spot`: post_only 거부 → re-quote, 5초 → taker
- `store_spot`: WAL, migration, query consistency

### 7.2 통합 테스트
1. 1 ticker 단일 trade lifecycle
2. WS 강제 단절 후 reconnect
3. OKX demo 429 → backoff/recovery
4. Reconcile zombie (open vs broker 0)
5. 메인 봇 SIGTERM → SPOT 봇 무영향 (격리)
6. 50 ticker 동시 signal — per-ticker lock

### 7.3 Phase 5 운영 KPI (1주)

#### 합격선 (필수)
- Daily NET >0 (5/7일 양수)
- Expectancy/trade >$0
- Reconcile zombie 0건
- Maker fill rate ≥80%
- WS uptime ≥99%

#### Stretch
- WR ≥75% (1차) → 85% (stretch)
- Daily n ≥200
- Total NET 7일 >+$200

#### 실패 트리거 (즉시 중단)
- 3일 연속 NET 음수
- Reconcile zombie 5+건
- Maker fill rate <50%
- Crash uptime <95%

#### 회색지대 (50~79% maker fill, WR 50~74%, NET 약양수) — 1주 추가 튜닝 후 재평가

### 7.4 Pivot 결정 frame (1주 후)
| 결과 | 다음 |
|---|---|
| 합격선 + WR ≥75% | 확장 (Phase 2 Alpaca SPOT + capital up) |
| 합격선 + WR <75% | 튜닝 (signal threshold) 1주 더 |
| 합격선 일부 미달 | root-cause 분석 |
| 실패 트리거 | 중단 + post-mortem INSIGHT |

---

## 8. 일정

| Phase | 기간 | 산출물 |
|---|---|---|
| Phase 0: spec → plan | 오늘 | 이 문서 + writing-plans |
| Phase 1: 골격 (runtime + WS + store) | 1~2일 | 데이터 흐르는 빈 봇 |
| Phase 2: 시그널 + router | 2~3일 | OKX demo 진입 가능 |
| Phase 3: exit + cell learning | 1~2일 | 완전 lifecycle |
| Phase 4: 시각화 + 대시보드 | 1일 | 두 봇 동시 관찰 |
| Phase 5: 1주 운영 + KPI | 7일 | WR/expectancy/maker fill |

총 ~2주 + 운영 1주 = **3주 후 cash cow 검증 결정**.

---

## 9. 작업량

| 작업 | line |
|---|---|
| 8 신규 모듈 (runtime~store_spot) | ~1900 |
| WS 새 채널 + 데이터 fetch helper | ~420 |
| 시각화/대시보드 통합 | ~320 |
| sqlite schema + migration | ~150 |
| 단위/통합 테스트 | ~600 |
| **총** | **~3400 line** |

기존 `invasion/` ~50,000 line 대비 ~6.8%.

---

## 10. 위험 + 완화

| 위험 | 가능성 | 영향 | 완화 |
|---|---|---|---|
| OKX demo 환경 불안정 | 중 | 중 | 5분 backoff + alert |
| Maker fill rate 낮음 (<50%) | 중 | 높음 | tick offset 튜닝, taker fallback |
| WR 75% 못 달성 | 높음 | 중 | signal threshold 튜닝, Phase 5 ramp |
| 격리 실패 (메인 봇 영향) | 낮음 | 매우 높음 | read-only import 강제, 통합 테스트 #5 |
| Reconcile 결함 재발 (INSIGHT-029) | 낮음 | 높음 | 5분 reconcile 의무 + zombie cleanup |
| OKX rate limit 초과 | 낮음 | 중 | 분당 60 req 한도 안 운영 |
| Cell sparse 학습 못 채움 | 중 | 중 | 5-dim slim 으로 dense 화 (8-dim 대비 4x) |

---

## 11. 확정 default (변경 가능)

- **Universe size**: **Top 10 liquidity** (BTC/ETH/SOL/XRP/DOGE/ADA/DOT/LINK/AVAX/LTC). 안정 후 50 확장.
- **Capital size (paper)**: **$10k** (queue position 보존, 실 maker rebate tier 가까움).
- **Maker rebate tier**: 보수적 **0.0%** 가정 후 실측. preg `okx_spot_maker_fee_pct=0.0`, `okx_spot_taker_fee_pct=0.10`.
- **WS subscriber**: **단일 subscriber 10 instruments 묶기** (기존 OKX SWAP WS 패턴).
- **OKX demo 인증**: env `OKX_DEMO_API_KEY` / `OKX_DEMO_SECRET` / `OKX_DEMO_PASSPHRASE` 별도 (메인 봇 OKX_API_KEY 와 분리).
- **Cell sparse target**: Phase 5 종료 시 ≥**40%** (1주 운영 기준, 5-dim 슬림 + Top 10 universe 로 자연 dense 화).

---

## 12. 정합성 (북극성 self-review)

- ✅ `feedback_aggressive_always_profit` — 손실 source (CFD) 제거 + 수익 source (OKX SPOT) 강화.
- ✅ `feedback_loss_profit_asymmetry` — long-only + tight TP + tight SL = symmetric, expectancy 양수 추구.
- ✅ `feedback_no_quick_patch_ever` — Phase 분리 + 1주 검증 + pivot frame.
- ✅ `feedback_no_block_filter_architecture` — 메인 봇 차단 X. 추가 trace.
- ✅ `feedback_no_defensive_param_dampen` — defense X, scalp = aggressive maker queue 캡처.
- ✅ `feedback_overhaul_over_incremental` — 전체 신규 패키지 (incremental if-branch 회피).
- ✅ `feedback_sequential_superpowers_vault_organic` — sequential thinking + brainstorming + vault grounding.

---

## 13. Approval

- [ ] Jin reviews this spec
- [ ] Jin approves
- [ ] Transition to writing-plans skill for implementation plan
