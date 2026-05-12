# Polaris v2 Plan — FINAL (Round 3 codex debate + Jin MED/LOW sign-off)

> 작성: 2026-05-06 (Sydney) · 방법: Round 1 (T1-T6 ✗ ROLLBACK) → Round 2 (demo unlock) → Round 3 (4 critical/high) → Internal review (12 items, 6 lens) → Jin sign-off (8 MED/LOW)
> 절대 컨텍스트: **DEMO unlock**

---

## ⚠️ 절대 컨텍스트 (모든 phase)

**DEMO / PAPER / SANDBOX ONLY**:
- OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) — 가상 USDT $79,000
- Capital.com CFD demo (`demo-api-capital.backend-capital.com`) — 가상 AUD $78,000
- 합산 ≈ USD $130,000 가상 자금. **실제 자금 손실 = 0**

**거부된 전제** (등장 시 무효):
- 12주/60d/90d demo gate · KPI auto-disable · regulatory cap · capital protection
- Posture defensive · macro guard · regime auto-throttle
- regrets/ 디렉토리 · anti-stealth-conservatism log
- monthly review · 30일 lock-in
- 표본 부족 risk · real-money safety · fractional Kelly is too aggressive in practice

**Stop 권한**: Jin manual only. demo 자금 0 도달 시 → DB reset → restart
**Aggressive bias preserved**: "안전" 논거 = 점수 감점, "Hi-risk hi-return" = 가산

---

## 1. Vision

### Targets
- **Primary**: 일 평균 +0.75% (compounding 252 → +560%/yr; $130k → $865k 가상)
- **Stretch**: 일 평균 +1.25% (~5,400%/yr 이론치)
- **Daily intraday band**:
  - soft: ±5% (정상)
  - stretch: ±8% (고출력)
  - ±8%+: 태깅만, 실행 차단 X

### Miss escalation policy (continuous trade-driven, 시간 X)

**Trigger evaluation = 매 closed trade 직후**.
**Lever 변경 발동 조건 = 4개 동시 충족**:
1. `rolling 100-trade portfolio expectancy < 0`
2. `rolling 40-trade expectancy < rolling 100-trade expectancy` (열화 가속)
3. 직전 lever 변경 후 ≥ 20 closed trades 경과 (cooldown)
4. 손상 지표 ≥ 2개 동시 훼손: `win-rate`, `avg R`, `profit factor`, `MFE/MAE ratio`

### 4 lever 순서 + skip 정책

**순서**:
1. `reallocate` (자본 이동 — 가장 빠른 unlock)
2. `strategy_add` (새 edge 추가)
3. `sizing` (생존 sleeve 강화 only — 실패 sleeve 업사이즈 금지)
4. `leverage` (마지막, gate 별도)

**Skip 규칙**:
- `localized miss`: `reallocate → sizing → strategy_add`
- `broad miss` (다수 동반 둔화): `reallocate` 생략, `strategy_add` 직행
- 2회 연속 lever 변경 후에도 rolling 40-trade 개선 없음 → `strategy_add` 점프
- `leverage` gate: rolling 100-trade expectancy 양수 복귀 전 unlock 금지
- 신규 추가 sleeve = 기존 상위 2 sleeve 와 상관 낮은 쪽 우선

### Drawdown checkpoint (실행 차단 X, 데이터 가치만)
- intraday -8% : snapshot + 원인 태깅
- rolling 5d -20% : feature dump + freeze-copy
- venue equity -35% : full position state freeze

### Auto-stop = 없음. Jin manual only.

---

## 2. Architecture — Dual-Track Concurrent

### 5-layer 공통 코어

#### Layer 1 — Canonical market model
`data_adapter → canonical bar/event → signal_engine`
- OKX 1m bar / Capital tick → canonical OHLCV+event stream 정규화
- `polaris/core/data/{canonical.py, adapter_base.py}`

#### Layer 2 — Unified SQLite schema with venue column
namespaced tables 폐기. 단일 schema, `venue` 컬럼 포함:
```sql
CREATE TABLE positions(venue TEXT, symbol TEXT, strategy_id TEXT, ...);
CREATE TABLE fills(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE orders(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE signals(strategy_id, signal_id, correlation_group, ...);
CREATE TABLE events(timestamp, type, agent, mode, payload_json);
```

#### Layer 3 — Common sizing + venue constraint translation
- Common: `target_risk_budget`, `notional`, `stop_distance`
- Venue: cash (OKX SPOT) / margin·leverage·liquidation (Capital CFD) 변환은 venue 어댑터
- `polaris/venues/{okx,capital}/constraint_translator.py`

#### Layer 4 — REST polling 우선 P0
- WebSocket = P1 후반
- P0 strategy = 30s+ timeframe (1m bar OK)
- OKX rate limit 20 req/2s → 30 ticker 1m polling 안전

#### Layer 5 — Strategy isolation primitives (7 mechanisms)
**7 strategy 동시 활성 (P1.0 day 1) 의 segregation**:
1. **Per-strategy process boundary** — strategy 마다 독립 worker/process (메모리/event loop/retry queue 공유 X)
2. **Per-strategy state namespace** — `positions/{sid}`, `orders/{sid}`, `risk/{sid}`, `signals/{sid}` 경로 분리
3. **Immutable portfolio inputs** — strategy = read-only account snapshot, 주문 의도만 emit. 실제 sizing/submit = 중앙 execution agent 검증 후 반영
4. **Strategy-scoped circuit breaker** — 예외/order reject storm/NaN sizing/stale market data → strategy_id `HALT`, 나머지 6개 continue
5. **Global allocator hard fence** — T4 scalar/amplifier = strategy 별, `hard MAX` = 중앙 allocator 최종 강제
6. **Idempotent order keys** — `strategy_id + symbol + timeframe + signal_ts` dedupe
7. **Kill-switch granularity** — default `kill(strategy_id)`, global stop = exchange/session 장애 only

**파일**: `polaris/core/isolation/{worker.py, namespace.py, circuit_breaker.py, allocator_fence.py}`

### Portfolio
- venue 별 분리, cross-venue netting X
- Dashboard: USD-equivalent aggregate (read model only)
- Reallocate decision (§1 lever 1): aggregate 기반

### 3rd venue
- P0/P1 = 2 venue (OKX + Capital). 3rd 거부
- P2 후반 후보: Alpaca (자산군 다름). Bybit/Binance 거부

---

## 3. Track A — OKX SPOT ($79k USDT)

### Constraints
- Endpoint: `us.okx.com` + `x-simulated-trading: 1`
- Long-only, no leverage
- Fee: 0.1% maker/taker (demo)
- Symbol universe: top 50 USDT-quote by 24h vol

### Strategies (4)
| # | Strategy | Type | Timeframe | Per-strategy cap |
|---|---|---|---|---|
| 1 | Volume Burst | event-driven | 1m bar | 24% |
| 2 | TSMOM 20-bar | cross-sectional momentum | 1H rebalance | 32% (basket) |
| 3 | RSI-BB Pullback | mean-reversion on trend | 15m bar | 18% |
| 4 | Spot Donchian Breakout | momentum/breakout | 1H | 20% |

### Track A gross cap: 60% · daily venue risk: 8%

---

## 4. Track B — Capital CFD (A$78k)

### Constraints
- Endpoint: `demo-api-capital.backend-capital.com`
- Long/short, leverage native
- Leverage ceilings: forex 30× / indices 20× / gold 20× / commodity 10×
- Fee: spread (built-in)

### Strategies (3)
| # | Strategy | Symbols | Lev | Per-strategy cap |
|---|---|---|---|---|
| 5 | FX Breakout Basket | EURUSD, GBPUSD, AUDUSD, USDJPY, USDCAD | 30× | 12%/pair × 5 = 36% |
| 6 | XAU/Indices Trend | XAUUSD, US500, US100, GER40 | 20× | 16%/sym × 4 = 40% |
| 7 | Session Breakout | US500, US100, EURUSD, GBPUSD | 20× | 10%/trade × 20% concurrent |

### Track B gross cap: 80% · daily venue risk: 9%
### macro guard / news blackout = **거부** (Jin "묶지 마")

**Total: 7 strategies (4 OKX + 3 Capital), 동시 활성**

---

## 5. Sizing & Risk Engine

### 기본 공식 (anti-collapse + tier amplifier)
```
notional = base_notional 
         × continuous_scalar(strength)   # 0.75 ~ 1.5×
         × tier_amplifier(streak)        # 1.0× / 1.5× / 2.0× / 3.0×
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```
- 1 continuous scalar BEFORE notional clip
- 1 tier amplifier (1.5/2.0/3.0×)
- All other = HARD MAX (소프트 dampener X)
- v1 9-stack collapse 영구 봉쇄

### Hard caps (demo aggressive)
| Param | 값 |
|---|---|
| per-symbol cap (spot, OKX) | 50% |
| per-symbol cap (CFD, Capital) | 35% |
| per-symbol absolute ceiling | 50% |
| Track A gross cap | 60% |
| Track B gross cap | 80% |
| Track A daily venue risk | 8% |
| Track B daily venue risk | 9% |
| Total daily risk absolute ceiling | 10% |
| max single-trade risk (default) | **8%** |
| max single-trade risk (amplifier on) | **9%** |
| single-trade absolute ceiling | **9%** |
| Kelly fractional k | **0.50** |

**Priority**: hard MAX > Kelly. Kelly 산출치가 single cap 초과 시 무조건 절단

### Cold start (CS-3 bootstrap)
demo 첫 trades = historical p/q 입력 부재:
- `n < 20` (closed trades per strategy): Kelly off, single-trade risk = **6% default / 7% amplifier on**
- `n >= 20`: Kelly on, 본 cap (8%/9%) 적용
- Kelly 입력 = 전략별 rolling estimator + clamp (급변폭 제한)

### Streak amplifier (tier)
**Tier table** (loss=R1 reset):
| Streak | Amplifier |
|---|---|
| 3 wins | 1.5× |
| 5 wins | 2.0× |
| 8+ wins | 3.0× |
| 1 loss | reset → 1.0× (binary) |

**Trigger gate**:
- `n < 8` → amplifier off
- `n = 8~9` AND `hit-rate ≥ 75%` → 최대 `1.5×` 만
- `n ≥ 10` AND `hit-rate ≥ 70%` → full tier (1.5/2.0/3.0×)
- `2.0×` 이상 = 반드시 `n ≥ 10`

**9% cap 정합 예시**:
| Base | 1.5× | 2.0× | 3.0× | 절단 |
|---|---|---|---|---|
| 2% | 3% | 4% | 6% | 미도달 |
| 3% | 4.5% | 6% | 9% | 3.0× 정확히 |
| 4% | 6% | 8% | 9% (12→9) | 3.0× 절단 |
| 5% | 7.5% | 9% (10→9) | 9% (15→9) | 2.0×+ 절단 |

→ amplifier = 작은-base 전략의 추격 장치

### Symbol-cluster cap
중앙 allocator 가 strategy cap 차감 **이전에** symbol-cluster cap 먼저 차감:
- `BTC/ETH cluster` (spot 동시 노출): 합산 한도 = 40%
- `XAU/indices cluster` (CFD 동시 노출): 합산 한도 = 50%
- `FX majors cluster` (CFD 동시 노출): 합산 한도 = 60%

### Risk budget fill-rate weak-signal cut
venue daily risk ceiling (8%/9%) 빠른 도달 시:
- `risk budget fill-rate` = 현재까지 사용한 risk / daily ceiling
- fill-rate ≥ 70% 도달 시 가장 약한 signal_strength 부터 즉시 컷
- 손익 무관

### ATR Stop/TP (per-strategy)
| Strategy | Stop ATR | TP ATR | Window |
|---|---|---|---|
| Volume Burst | 1.8 | 2.5 | 10 |
| TSMOM | 2.5 | 4.0 | 14 |
| RSI-BB Pullback | 2.0 | 3.0 | 14 |
| Spot Donchian | 2.5 | 4.0 | 14 |
| FX Breakout | 2.0 | 3.5 | 14 |
| XAU/Indices | 2.5 | 4.0 | 14 |
| Session Breakout | 1.0 | 3.0 | 10 |

### P1 ramp-up (즉시 7 동시 활성)
- **P1.0** (P0 직후 day 1): **7 strategy 전부 동시 활성**
- **첫 24h**: watchdog focus = segregation/wiring 검증 (성능 평가 X). 5 agents 중 1개 = watchdog 전용. 격리 결함 → strategy_id HALT + hotfix → 즉시 재투입
- **이후**: 자연 운영, lever 변경 = §1 trigger 충족 시
- 시간 기반 ramp 게이트 = 폐기
- Jin manual override = 언제든 가능

---

## 6. Strategy Interface Lock

```python
@dataclass
class StrategyMetadata:
    timeframe: str
    warmup_bars: int
    max_positions: int
    gross_cap: float
    per_symbol_cap: float
    expected_holding_bars: int
    asset_class: str
    venue: str
    correlation_group_id: str

class Strategy(ABC):
    metadata: StrategyMetadata
    def generate_signals(market_view, portfolio_state) -> list[Signal]: ...
    def manage_position(position, market_view, portfolio_state) -> list[OrderIntent]: ...

@dataclass
class Signal:
    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    entry_type: Literal["market", "limit"]
    strength: float            # 0-1
    sizing_hint: float         # 0-1
    ttl_bars: int
    thesis_tag: str
    correlation_group: str
    venue_constraints: dict
    created_at_bar: int
    invalidate_reason: Optional[str] = None
    cooldown_bars: int = 0
    tags: dict[str, str] = field(default_factory=dict)

@dataclass
class OrderIntent:
    signal_id: str
    execution_policy: dict     # post_only, reduce_only, slippage_tier, time_in_force
    state: Literal["candidate", "pending_entry", "active", "exit_pending", "closed"]
    venue_override: dict
```

### Correlation groups
| Group | Strategy | Max concurrent |
|---|---|---|
| spot_intraday_event | Volume Burst | 3 |
| spot_cross_sectional_momo | TSMOM | 5 |
| spot_mean_reversion | RSI-BB Pullback | 4 |
| spot_breakout | Spot Donchian | 3 |
| cfd_fx_trend | FX Breakout | 5 |
| cfd_index_commodity_trend | XAU/Indices | 4 |
| cfd_session_event | Session Breakout | 2 |

---

## 7. Harness

### 4 modes
- `/dev`, `/alpha`, `/forensic` (hard-exclusive)
- `/debate` (overlay)

### Posture 단일화
- DEMO = aggressive only
- policy_engine.py 내 reserved field (`posture: Literal["aggressive"] = "aggressive"`), enforcement X

### 5 agents
- `analyst` / `risk_officer` / `executor_okx` / `executor_capital` / `forensicist`
- P1.0 day 1 첫 24h = 1 agent 를 `watchdog` 으로 전용

### policy_engine.py (3-layer)
```python
# Layer 1: Matrix (mode × agent × action_class)
MATRIX = {...}

# Layer 2: Per-action target validator
VALIDATORS = {
    "write_research": vault_path_predicate,
    "read_data": db_scope_predicate,
    "place_order": venue_symbol_predicate,
    "destructive_op": confirm_required_predicate,  # P0: confirm 요구
    ...
}

# Layer 3: Event log schema
@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    event_id: str

def check(mode, agent, action_class, target, ctx) -> PolicyDecision: ...
```

### Allowed-action matrix (DEMO 단순)
| mode \ action | read_data | write_research | write_now | write_log | write_decision | write_strategy | write_code | place_order | cancel_order | emergency_halt | destructive_op |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | ALL | ALL | analyst,risk | ALL | analyst,risk | analyst | analyst | NO | NO | risk | confirm |
| alpha | ALL | ALL | risk,executor* | ALL | risk | NO | NO | executor_okx,executor_capital | executor_*,risk | risk | confirm |
| forensic | ALL | forensicist | forensicist | forensicist | forensicist | NO | NO | NO | forensicist (close only) | risk | confirm |

`destructive_op` (drop tables / rm -rf data /...) = 모든 mode 에서 explicit human confirm + ADR mint 의무 (P0 신중)

### `is_live` flag
- `ExecutionContext` 의 일부 (단순 bool 보유 X)
- Order/Fill/Audit = demo/live 공용 port-adapter
- 분기 X (P0). live 도입 = 미래 별도 ADR

### 6 hooks
1. `SessionStart` — vault `_NOW.md` + `INDEX.md` mandatory read
2. `SessionEnd` — vault append **on material change only** (code edit / decision / incident / new strategy / trade event). material change 없으면 skip
3. `PreToolUse` — policy_engine matrix check
4. `PostToolUse` — state_store write log
5. `UserPromptSubmit` — mode parse (`/dev`, `/alpha`, etc.)
6. `PreStateTransition` — **stub P0 (logging only)**, full impl P1 후반 (checkpoint/strategy ramp/order state machine 일반화)

### Event schema
- `signal_generated`, `order_intent_created`, `order_placed`, `fill_received`
- `position_state_transition`, `policy_decision`, `checkpoint_snapshot`
- `strategy_halt`, `strategy_resume`, `lever_change`
- `drawdown_threshold_crossed`
- 모든 event = SQLite `events` table

### Skills (9 total — 6 P0 + 3 P1)

**P0 (6 — sprint 핵심)**:
1. `running-paper-loop` — main paper trade loop kicker
2. `signaling-strategies` — signal gen orchestration
3. `sizing-positions` — T4 공식 invocation (Kelly + tier amplifier + symbol-cluster cap)
4. `executing-orders` — venue adapter call
5. `governing-risk` — hard cap enforcement, symbol-cluster cap, fill-rate weak-signal cut
6. `reconciling-portfolio` — position/cash/exposure state 합치기

**P1 (3 — paper trading 활성화 후 추가)**:
7. `auditing-fills` — slippage/fee reconciliation (live trade 후 의미 있음)
8. `analyzing-pnl` — daily/weekly P&L breakdown
9. `reviewing-strategies` — Strategy lifecycle (continuous trade-driven escalation)

### 7 MCP servers
- `ccxt`, `sqlite`, `duckdb`, `context7`, `sequential-thinking`, `obsidian-mcp`, `time`

---

## 8. Vault

### 6 dirs
- `00_charter` — 북극성, 원칙, vision
- `10_decisions` — ADR (creation order numbering)
- `20_strategies` — strategy specs (per Strategy 1 file, 7 files at P1.0)
- `30_components` — code module docs (sizing, risk, harness, isolation)
- `40_ops` — runtime, lever_change logs, schedules
- `50_research` — forensic, exploration, debate logs

### Root files
- `_NOW.md` — hybrid auto+manual live state (Tier 0, mandatory read)
- `INDEX.md` — catalog (auto-generated)
- `log.md` — chronological 1-line append

### regrets/ = 폐기

### 대체 메커니즘 = B' + D + C(제한)

#### B' — Continuous lever-change log
- vault path: `40_ops/lever_change_<event_id>_<date>.md`
- 트리거: §1 4-조건 충족
- 내용: rolling expectancy 통계 + 발동 lever + skip 결정 사유
- 허용 결정: `reallocate / strategy_add / sizing(survivors only) / leverage(gated)`
- **금지**: failing sleeve upward sizing, leverage gate skip

#### D — Forensic on checkpoint trigger
- 발동 조건:
  - drawdown checkpoint snapshot (-8% / -20% / -35%)
  - 동일 strategy + 동일 correlation_group, 7일 내 ≥3 stop-loss
  - strategy circuit breaker HALT 발동 시
- vault path: `50_research/forensic_<event_id>_<date>.md`
- 항상 활성 X

#### C — Winner-only ELO (cap-bound, max 3.0×)
- 매 trade 종료 시 strategy_evolution agent ELO update
- **Loser 자동 감점 X**
- Winner 증액 재원 = **유휴 현금에서만**, 총 cap 초과 금지
- 증액 룰: winner sizing scalar `+0.05 / 100 trades` (max **3.0×**, T4 tier amplifier 와 통일)

### 2-tier lint
- Light pre-commit (orphan, frontmatter, broken wiki-links)
- Heavy weekly cron (stale, contradictions, link integrity)

### Frontmatter YAML, hash 폐기 (git immutability)
### Bases (Obsidian 1.9+, Dataview 폐기 — 4/2025 dormant)

---

## 9. Phase Plan

### P0 — Infrastructure (1-3일 sprint)

**Scope (~3950 LOC)**:
- Canonical market model + REST polling + unified schema (~1500 LOC)
- 2 venue REST adapter (OKX SPOT + Capital CFD), market+limit only (~1200 LOC)
- Strategy 1개 paper loop smoke (Volume Burst) (~250 LOC)
- 최소 sizing engine + state store (~600 LOC)
- Strategy isolation primitives (~400 LOC) — per-strategy worker, namespace, circuit breaker, allocator fence
- 6 P0 skills (≤500L each, ~3000L docs)
- 6 hooks (PreStateTransition stub)
- Vault skeleton (6 dirs + 3 root files)
- ADR-001 ~ ADR-007 mint

**검증**:
- smoke test only
- codex 외부 review 의무 (작성 agent ≠ 리뷰 agent)

### P1 — Strategy Activation (P0 직후 즉시)

**P1.0** (day 1): **7 strategy 전부 동시 활성**
- Track A 4: Volume Burst + TSMOM + RSI-BB Pullback + Spot Donchian
- Track B 3: FX Breakout + XAU/Indices + Session Breakout
- 첫 24h: watchdog focus = segregation/wiring (성능 평가 X)
- 격리 결함 → strategy_id HALT + hotfix → 즉시 재투입

**P1.1+**: 자연 운영, lever 변경 = §1 trigger 충족 시
- P1 skills 3개 추가 (auditing-fills / analyzing-pnl / reviewing-strategies)
- WebSocket 도입 (P1 후반)
- PreStateTransition full impl (P1 후반)
- 시간 기반 phase plan = 거부

### P2 — Ongoing
- Cross-venue arbitrage (correlation_group_id 활용)
- 3rd venue 후보 평가 (Alpaca, 자산군 다름)
- ELO winner-only sizing 증액 (cap-bound, trade-count)
- live 진입 = 별도 ADR (본 plan 책임 X)

### Stop conditions
- **Auto-stop = 없음**. Jin manual only
- Demo 자금 0 도달 시 → DB reset → restart (학습 데이터 archive)

---

## 10. Key Decisions Summary

| 영역 | 결정 |
|---|---|
| Daily target | 0.75% primary / 1.25% stretch |
| Architecture | dual-track concurrent + 5-layer (canonical / unified schema / common sizing / REST P0 / isolation primitives) |
| Strategies | **7개** (Track A 4: VolumeBurst+TSMOM+RSI-BB+Spot Donchian / Track B 3: FX+XAU+Session) |
| Ramp-up | P1.0 day 1 즉시 7 동시 활성 + 24h watchdog focus |
| Sizing 공식 | 1 scalar (0.75-1.5×) + tier amplifier (1.5/2/3×) + hard MAX, anti-collapse |
| Hard caps | k=0.5, single 8%/9%, per-symbol spot 50% / CFD 35%, Track A 8% / B 9% / total 10% |
| Cold start (CS-3) | n<20 Kelly off + single 6%/7%, n≥20 Kelly on + 8%/9% |
| Streak amplifier | tier 1.5/2/3×, n≥10 hit≥70% (n=8-9 fast hit≥75% max 1.5×), R1 loss reset |
| Symbol-cluster cap | BTC/ETH 40% / XAU/indices 50% / FX majors 60% |
| Risk-budget fill-rate cut | ≥70% → weak-signal 컷 |
| Escalation trigger | continuous trade-driven (rolling 100 + 40 + 20 cooldown + 2 지표) |
| 4 lever 순서 | reallocate → strategy_add → sizing(survivors) → leverage(gated) + skip |
| Posture | aggressive only (reserved field) |
| policy_engine | 3-layer (matrix + validator + event log) |
| Hooks | 6 (SessionStart / SessionEnd material-only / PreToolUse / PostToolUse / UserPromptSubmit / PreStateTransition stub P0) |
| Skills | 9 total = **6 P0** + 3 P1 |
| Destructive ops | confirm 요구 (모든 mode, ADR mint) |
| regrets/ | 폐기 (B' continuous lever_change + D forensic checkpoint + C winner-only ELO max 3.0×) |
| Phase | P0 1-3일 (~3950 LOC) / P1.0 day 1 즉시 7 동시 / P2 ongoing |

---

## 11. 위반 키워드 sweep

본 plan 등장 검사:
- `12주`, `90d`, `60d` → 0건 ✓
- `regulatory cap`, `professional risk`, `capital protection`, `fund mandate` → 0건 ✓
- `regrets/`, `anti-stealth-conservatism` → 0건 (폐기 명기 only) ✓
- `auto-disable`, `auto drawdown stop`, `regime auto-throttle` → 0건 ✓
- `posture defensive`, `posture standard` → 0건 ✓
- `macro guard 60m`, `news blackout` → 0건 (거부 명기 only) ✓
- `monthly review`, `30일 lock-in` → 0건 ✓
- `표본 부족 risk`, `real-money safety` → 0건 ✓
- `fractional Kelly is too aggressive in practice` → 0건 ✓

**모든 보수 위장 패턴 제거 완료**

---

## 12. 다음 액션 — P0 sprint kickoff

1. 본 plan Jin sign-off (plan mode)
2. Vault 신설: `vault/` 6 dirs + 3 root files + `00_charter/north-star.md` + `00_charter/aggressive-bias.md`
3. ADR mint 순서:
   - ADR-001 Vault Structure
   - ADR-002 Vision (0.75% / 1.25% + continuous escalation)
   - ADR-003 Dual-Track Architecture
   - ADR-004 7 P1 Strategies
   - ADR-005 Sizing Formula (k=0.5, single 8%/9%, tier amplifier)
   - ADR-006 Harness (4 modes, 5 agents, 6 hooks, 9 skills, 7 MCPs, policy_engine 3-layer)
   - ADR-007 Cold Start CS-3 + Symbol-cluster cap + Fill-rate cut
4. `polaris/` 패키지 골격: `core/{data,isolation,sizing}` + `venues/{okx,capital}` + `strategies/` + `harness/`
5. P0 sprint:
   - Day 1: canonical market model + unified schema + isolation primitives + Volume Burst smoke
   - Day 2: OKX adapter + Capital adapter (REST) + sizing engine
   - Day 3: state store + 6 hooks + 6 P0 skills + paper loop smoke
6. Codex 외부 review = 모든 신규 코드 의무
7. P1.0 ignition: 7 strategy 동시 활성 + 24h watchdog

---

## 13. 합의 출처

- **Round 1 (T1-T6)**: ROLLBACK (demo context 누락 → real-money 보수 권고) `/tmp/polaris_debate/`
- **Round 2 (T1-T6 demo unlock)**: `/tmp/polaris_debate_round2/` · 7 codex calls · plan_round2.md
- **Round 3 (D1-D4 critical/high)**: `/tmp/polaris_debate_round3/` · 4 codex calls · plan_round3.md
- **Internal review** (12 items, 6 lens): sequential-thinking + brainstorming, 14 issues
- **Jin sign-off** (8 MED/LOW): 7 strategies / ELO 3.0× / REST P0 / 3-layer validator / confirm destructive / SessionEnd material-only / PreStateTransition stub-P0 / Skills 6 P0 + 3 P1

---

*Generated 2026-05-06 · Polaris v2 Plan FINAL · Round 1-3 codex consensus + internal review + Jin sign-off*
