# Polaris v2 Plan — REWRITE 3 (Round 2 codex debate 합의)

> 작성: 2026-05-06 (Sydney) · 방법: 6 topic codex debate (T1-T6, gpt-5.4) · 절대 컨텍스트: **DEMO unlock**
>
> Round 1 채택 (변경 X): T4 sizing 공식, T5 harness 골격, T6 vault 6 dirs · Round 2 합의 = aggressive bias preserved + demo unlock + Polaris 원칙 정합

---

## ⚠️ 절대 컨텍스트 (모든 phase)

**DEMO / PAPER / SANDBOX ONLY**:
- OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) — 가상 USDT $79,000
- Capital.com CFD demo (`demo-api-capital.backend-capital.com`) — 가상 AUD $78,000
- 합산 ≈ USD $130,000 가상 자금. **실제 자금 손실 = 0**.

**거부된 전제** (등장 시 무효):
- 12주/60d/90d demo gate · KPI auto-disable · regulatory cap · capital protection
- Posture defensive · macro guard 60m blackout · regime auto-throttle
- regrets/ 디렉토리 · anti-stealth-conservatism log
- Live 진입 결정 = 본 plan 책임 영역 밖 (Jin 단독, 별도 phase)

**Stop 권한**: Jin manual only. demo 자금 0 도달 시 → DB reset → restart.

**Aggressive bias preserved**: "안전" 논거 = 점수 감점, "Hi-risk hi-return" = 가산.

---

## 1. Vision (T1)

### Targets
- **Primary**: 일 평균 +0.75% (compounding 252 → +560%/yr; $130k → $865k 가상)
- **Stretch**: 일 평균 +1.25% (~5,400%/yr 이론치)
- **Daily intraday band**:
  - soft: ±5% (정상 운영)
  - stretch: ±8% (고출력 구간)
  - ±8%+ : 태깅만, 실행 차단 X (이후 sizing/allocation 조정 근거)

### ~~Escalation policy (periodic cadence, codex round 1)~~ [SUPERSEDED]

> ⚠️ **SUPERSEDED (2026-06-22)**: 이 단계적 escalation 래더(주기적 실현률 게이트)는 **폐기**. 현 방향 = 측정 정직화 + 안정화 + /debate 후 단계적 적용([[system_design_audit_2026-06-22]], loop_state.md M→S→D→R). aggressive·flow_not_block 유지 — 주기적 자동 게이트/스로틀로 회귀하지 않음. 아래는 역사 기록.

1. ~~**Step 1** — sizing scalar +0.25, single-strategy cap +20%~~
2. ~~**Step 2** — 자금 reallocate (under→over venue)~~
3. ~~**Step 3** — leverage step-up (Capital 30 → venue ceiling)~~
4. ~~**Step 4** — venue gap > 0.2%/day → strategy add (codex debate 후)~~

순서(역사): `sizing → reallocate → leverage → strategy_add`

### Drawdown checkpoint snapshot (실행 차단 X, 데이터 가치만)
- intraday -8% : snapshot + 원인 태깅
- rolling 5d -20% : feature dump + freeze-copy
- venue equity -35% : full position state freeze

### Auto-stop = 없음. Jin manual only.

---

## 2. Architecture — Dual-Track Concurrent (T2)

### 공통 코어 + 얇은 adapter (codex 4-point 압축)

#### Layer 1 — Canonical market model
`data_adapter → canonical bar/event → signal_engine` (1 layer 추가, codex)
- OKX 1m bar / Capital tick 모두 canonical OHLCV+event stream 으로 정규화
- `polaris/core/data/{canonical.py, adapter_base.py}`

#### Layer 2 — Unified SQLite schema with venue column
namespaced tables 폐기. 단일 schema, `venue` 컬럼 포함:
```sql
CREATE TABLE positions(venue TEXT, symbol TEXT, ...);
CREATE TABLE fills(venue TEXT, ...);
CREATE TABLE orders(venue TEXT, ...);
CREATE TABLE signals(strategy_id, signal_id, correlation_group, ...);
CREATE TABLE events(timestamp, type, agent, mode, payload_json);
```

#### Layer 3 — Common sizing + venue constraint translation
- Common: `target_risk_budget`, `notional`, `stop_distance` 산출
- Venue: cash (OKX SPOT) / margin·leverage·liquidation (Capital CFD) 변환은 venue 어댑터 책임
- `polaris/venues/{okx,capital}/constraint_translator.py`

#### Layer 4 — REST polling 우선 P0
- WebSocket = P1 후반 도입
- P0 strategy = 30s+ timeframe (1m bar OK)

### Portfolio
- **운영**: venue별 분리. cross-venue netting X.
- **Dashboard**: USD-equivalent aggregate (read model only). 별도 진실원장 X. venue snapshot 에서 파생.
- **Reallocate decision** (T1 step2): aggregate 기반.

### Strategy capability metadata (cross-venue 미래 대비)
```python
capability = {
  "venues": ["okx", "capital"],
  "asset_classes": ["crypto", "fx", "gold", "indices"],
  "correlation_group_id": "spot_intraday_event"
}
```
연결 키: `strategy_instance_id`, `portfolio_group_id` schema 에 미리 박음.

### 3rd venue 정책
- P0/P1 = 2 venue (OKX + Capital). 3rd 거부.
- P2 후반 후보: **Alpaca** (자산군/세션 다름). Bybit/Binance 거부 (OKX 와 중복).

---

## 3. Track A — OKX SPOT ($79k USDT)

### Constraints
- Endpoint: `us.okx.com` + `x-simulated-trading: 1`
- Long-only, no leverage
- Fee: 0.1% maker/taker (demo)
- Symbol universe: top 50 USDT-quote by 24h vol

### Strategies (3 P1)
| # | Strategy | Type | Timeframe | Per-strategy cap |
|---|---|---|---|---|
| 1 | Volume Burst | event-driven | 1m bar | 24% |
| 2 | TSMOM 20-bar | cross-sectional momentum | 1H rebalance | 32% (basket) |
| 3 | RSI-BB Pullback | mean-reversion on trend | 15m bar | 18% |

### Track A gross cap: 60% · daily venue risk: 8%

---

## 4. Track B — Capital CFD (A$78k)

### Constraints
- Endpoint: `demo-api-capital.backend-capital.com`
- Long/short, leverage native
- Leverage ceilings: forex 30× / indices 20× / gold 20× / commodity 10×
- Fee: spread (built-in)

### Strategies (3 P1)
| # | Strategy | Symbols | Lev | Per-strategy cap |
|---|---|---|---|---|
| 4 | FX Breakout Basket | EURUSD, GBPUSD, AUDUSD, USDJPY, USDCAD | 30× | 12%/pair × 5 = 36% |
| 5 | XAU/Indices Trend | XAUUSD, US500, US100, GER40 | 20× | 16%/sym × 4 = 40% |
| 6 | Session Breakout | US500, US100, EURUSD, GBPUSD | 20× | 10%/trade × 20% concurrent |

### Track B gross cap: 80% · daily venue risk: 9%

### macro guard / news blackout = **거부** (Jin "묶지 마")

---

## 5. Sizing & Risk Engine (T4)

### 기본 공식 (round 1 채택, anti-collapse)
```
notional = base_notional 
         × continuous_scalar(strength)   # 0.75 ~ 1.5×
         × binary_amplifier(streak)      # 1.0× or 1.5×
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```
- 1 continuous scalar (0.75-1.5×) BEFORE notional clip
- 1 binary amplifier (1.5×)
- All other = HARD MAX (소프트 dampener X)
- v1 9-stack collapse 영구 봉쇄

### Hard caps (codex round 1, demo 강화)
| Param | 값 |
|---|---|
| per-symbol cap (spot, OKX) | **50%** |
| per-symbol cap (CFD, Capital) | **35%** (margin 효과) |
| per-symbol absolute ceiling | **50%** |
| Track A gross cap | 60% |
| Track B gross cap | 80% |
| Track A daily venue risk | **8%** |
| Track B daily venue risk | **9%** |
| Total daily risk absolute ceiling | **10%** |
| max single-trade risk (default) | **4%** |
| max single-trade risk (amplifier on) | **5%** |
| single-trade absolute ceiling | **5%** |
| Kelly fractional k | **0.33** |

### Streak amplifier (sample-only, 4중 guard 거부)
- **Trigger**: n ≥ 15 samples AND hit-rate ≥ 70%
- 백업: n = 10 AND hit-rate ≥ 80%
- regime/gross/sample 4중 guard 거부 (Jin "묶지 마") — sample stat-significance only
- Effect: 1.5× binary amplifier

### ATR Stop/TP (per-strategy, Strategy.metadata)
| Strategy | Stop ATR | TP ATR | Window |
|---|---|---|---|
| Volume Burst | 1.8 | 2.5 | 10 |
| TSMOM | 2.5 | 4.0 | 14 |
| RSI-BB Pullback | 2.0 | 3.0 | 14 |
| FX Breakout | 2.0 | 3.5 | 14 |
| XAU/Indices | 2.5 | 4.0 | 14 |
| Session Breakout | 1.0 | 3.0 | 10 |

### P1 ramp-up (T3 합의)
- **P1.0** (P0 직후): Volume Burst + TSMOM + FX Breakout (3 strategy, venue/패턴/시간축 분산)
- **P1.1** (≥40 trades OR ≥3 days): + RSI-BB Pullback
- **P1.2** (≥40 trades OR ≥3 days): + XAU/Indices + Session Breakout (전체 6 활성)
- 트리거: 시간 X, **표본 = 각 단계 ≥ 40 trades OR ≥ 3 trading days (늦은 쪽)**
- Jin manual override = 언제든 가능

---

## 6. Strategy Interface Lock (T3)

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
    strength: float            # 0-1, signal quality
    sizing_hint: float         # 0-1, suggested portion
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

### Correlation groups (max concurrent, codex 차등)
| Group | Strategy | Max concurrent |
|---|---|---|
| spot_intraday_event | Volume Burst | 3 |
| spot_cross_sectional_momo | TSMOM | 5 |
| spot_mean_reversion | RSI-BB Pullback | 4 |
| cfd_fx_trend | FX Breakout | 5 |
| cfd_index_commodity_trend | XAU/Indices | 4 |
| cfd_session_event | Session Breakout | 2 |

---

## 7. Harness (T5)

### 4 modes
- `/dev`, `/alpha`, `/forensic` (hard-exclusive)
- `/debate` (overlay)

### Posture 단일화
- DEMO = aggressive only.
- policy_engine.py 내 reserved field (`posture: Literal["aggressive"] = "aggressive"`), enforcement X.

### 5 agents
- `analyst` / `risk_officer` / `executor_okx` / `executor_capital` / `forensicist`

### policy_engine.py (3-layer, codex)
```python
# Layer 1: Matrix (mode × agent × action_class)
MATRIX = {...}  # see table below

# Layer 2: Per-action target validator
VALIDATORS = {
    "write_research": vault_path_predicate,
    "read_data": db_scope_predicate,
    "place_order": venue_symbol_predicate,
    ...
}

# Layer 3: Event log schema (모든 policy 결정 이벤트화)
@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    event_id: str

def check(mode, agent, action_class, target, ctx) -> PolicyDecision: ...
```

### Allowed-action matrix (DEMO 단순)
| mode \ action | read_data | write_research | write_now | write_log | write_decision | write_strategy | write_code | place_order | cancel_order | emergency_halt |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | ALL | ALL | analyst,risk | ALL | analyst,risk | analyst | analyst | NO | NO | risk |
| alpha | ALL | ALL | risk,executor* | ALL | risk | NO | NO | executor_okx,executor_capital | executor_*,risk | risk |
| forensic | ALL | forensicist | forensicist | forensicist | forensicist | NO | NO | NO | forensicist (close only) | risk |

### `is_live` flag
- `ExecutionContext` 의 일부 (단순 bool 보유 X — 함수 시그니처/로그 포맷에 자연 통합)
- Order/Fill/Audit 인터페이스 = demo/live 공용 port-adapter 패턴
- 분기 X (P0). live 도입 = 미래 별도 ADR.

### 6 hooks
1. `SessionStart` — vault `_NOW.md` + `INDEX.md` mandatory read
2. `SessionEnd` — vault append (digest/insight/ADR/lesson)
3. `PreToolUse` — policy_engine matrix check
4. `PostToolUse` — state_store write log
5. `UserPromptSubmit` — mode parse (`/dev`, `/alpha`, etc.)
6. **`PreStateTransition`** (codex 신규, 일반화) — checkpoint/strategy ramp/order state machine 모두 cover

### Event schema (codex: hook 수보다 event 먼저 lock)
- `signal_generated`, `order_intent_created`, `order_placed`, `fill_received`
- `position_state_transition`, `policy_decision`, `checkpoint_snapshot`
- `strategy_ramp_promotion`, `drawdown_threshold_crossed`
- 모든 event = SQLite `events` table

### 9 skills (gerund form, ≤500L, codex +1 reconciling-portfolio)
1. `running-paper-loop` — main paper trade loop kicker
2. `signaling-strategies` — signal gen orchestration
3. `sizing-positions` — T4 공식 invocation
4. `executing-orders` — venue adapter call
5. `auditing-fills` — reconciliation
6. `analyzing-pnl` — daily/weekly P&L breakdown
7. `reviewing-strategies` — Strategy lifecycle (ramp-up, periodic strategy review)
8. `governing-risk` — checkpoint snapshot, hard cap enforcement
9. `reconciling-portfolio` (codex 신규) — position/cash/exposure state 합치기

### 7 MCP servers (round 1 lock)
- `ccxt`, `sqlite`, `duckdb`, `context7`, `sequential-thinking`, `obsidian-mcp`, `time`

---

## 8. Vault (T6)

### 6 dirs (round 1 lock)
- `00_charter` — 북극성, 원칙, vision
- `10_decisions` — ADR (creation order numbering)
- `20_strategies` — strategy specs (per Strategy 1 file)
- `30_components` — code module docs (sizing, risk, harness, etc.)
- `40_ops` — runtime, ops digests, schedules
- `50_research` — forensic, exploration, debate logs

### Root files
- `_NOW.md` — hybrid auto+manual live state (Tier 0, mandatory read)
- `INDEX.md` — catalog (auto-generated)
- `log.md` — chronological 1-line append

### regrets/ = 폐기 (Jin Goodhart 우려, codex 합의)
- 감정형 폴더 X
- 보수성 측정 메타-감시 X
- 손실 회고 서술형 X

### 대체 메커니즘 = B + D + C(제한)

#### B — Monthly Strategy Review (기본 운영면)
- vault path: `40_ops/monthly_review_YYYY_MM.md`
- 내용: 요약 통계 + 배치 결정 (서술형 반성 X, codex)
- 허용 결정 (4 lever): `keep / increase / reallocate / add`
- **금지**: `reduce` (단 D forensic 결과 있을 때만 예외)

#### D — Forensic on checkpoint trigger (사건 분석면)
- 발동 조건:
  - T1 -8% / -20% / -35% checkpoint snapshot
  - **예외 트리거**: 동일 strategy + 동일 correlation_group, 7일 내 ≥3 stop-loss
- vault path: `50_research/forensic_<event_id>_<date>.md` 또는 `pattern_repeat_<group>_<date>.md`
- 항상 활성 X (codex: "운영 리듬을 무겁게")

#### C — Winner-only ELO (보조 증폭기, cap-bound)
- 매 trade 종료 시 strategy_evolution agent ELO update
- **Loser 자동 감점 X** (defensive 거부)
- Winner 증액 재원 = **유휴 현금에서만**, 총 cap 초과 금지 (codex)
- 증액 룰: winner sizing scalar `+0.05 / month` (max 1.5×, T4 ceiling)

### 2-tier lint
- Light pre-commit (orphan, frontmatter)
- Heavy weekly cron (stale, contradictions, link integrity)

### Frontmatter YAML, hash 폐기
- git immutability 가 hash 대체

### Bases (Obsidian 1.9+, Dataview 폐기)

---

## 9. Phase Plan

### P0 — Infrastructure (1-3일 sprint)
**Scope** (codex 압축, ~3550 LOC):
- Canonical market model + REST polling + unified schema (~1500 LOC)
- 2 venue REST adapter (OKX SPOT + Capital CFD), market+limit only (~1200 LOC)
- Strategy 1개 paper loop (Volume Burst) (~250 LOC)
- 최소 sizing engine + state store (~600 LOC)

**검증**:
- smoke test only (full test suite = P1 후반)
- codex 외부 review 의무 (작성 agent ≠ 리뷰 agent, Jin 원칙)

### P1 — Strategy Activation (P0 직후 즉시)
**P1.0** (day 1): Volume Burst + TSMOM + FX Breakout 3 strategy paper trade 동시
**P1.1** (≥40 trades OR ≥3 days): + RSI-BB Pullback
**P1.2** (≥40 trades OR ≥3 days): + XAU/Indices + Session Breakout
- harness skills 8개 + reconciling-portfolio 9번째 추가
- WebSocket 도입 (P1 후반)
- 12주/24주 phase plan = **거부**

### P2 — Ongoing (deferred to actual P1 결과 기반)
- Cross-venue arbitrage (correlation_group_id 활용)
- 3rd venue 후보 평가 (Alpaca, 자산군 다름)
- ELO winner-only sizing 증액 (cap-bound, monthly)
- live 진입 = 별도 ADR (본 plan 책임 X)

### Stop conditions
- **Auto-stop = 없음**. Jin manual only.
- Demo 자금 0 도달 시 → DB reset → restart (학습 데이터 archive)

---

## 10. Key Decisions Summary (round 2 합의)

| 영역 | 결정 | 근거 |
|---|---|---|
| Daily target | 0.75% primary / 1.25% stretch | codex: "0.5%/day는 공격적이라기보다 기본선" |
| Architecture | dual-track 동시 + canonical layer | codex: "data_adapter → canonical event/bar → signal_engine" |
| Strategies | 6개 (Spot Donchian 제거, RSI-BB 추가) | codex: "Track A 전부 breakout/trend 기울어 다양성 약함" |
| Ramp-up | 3→4→6, ≥40 trades OR ≥3 days trigger | codex: "표본 확보가 시간 고정보다 중요" |
| Sizing | 1 scalar + 1 amplifier + hard MAX (round 1 채택) | T4 round 1 anti-collapse |
| Hard caps | spot 50%/CFD 35%/A 8%/B 9%/single 4-5% | codex: "k=0.33 자체가 이미 공격적, 6%면 venue risk 8%와 정면충돌" |
| Streak amplifier | n≥15 hit≥70% sample-only | codex: "n=10 60%은 표본 너무 작음" |
| Posture | aggressive only (reserved field) | Jin "디펜시브 NO" + codex retrofit cost |
| policy_engine | 3-layer (matrix + validator + event log) | codex: "target predicate 안 받으면 예외 규칙 새어 나옴" |
| Hooks | 5 + PreStateTransition (일반화) | codex: "PostStrategyAdd 같은 특수화 거부" |
| Skills | 8 + reconciling-portfolio | codex: "auditing-fills만으로는 position/cash/exposure 합치기 비어 보임" |
| regrets/ | 폐기 (B+D+C 대체) | Jin Goodhart + codex: "감정형 폴더 폐기" |
| Phase | P0 1-3일 / P1 즉시 ramp-up / P2 ongoing | 12주/24주 거부 |

---

## 11. 위반 키워드 sweep (round 2 종결 검증)

본 plan 등장 여부 검사:
- `12주`, `90d Sharpe`, `60d gate` → 0건 ✓
- `regulatory cap`, `professional risk`, `capital protection`, `fund mandate` → 0건 ✓
- `regrets/`, `anti-stealth-conservatism` → 0건 (폐기 명기 only) ✓
- `auto-disable`, `auto drawdown stop`, `regime auto-throttle` → 0건 ✓
- `posture defensive`, `posture standard` → 0건 (단일화 명기 only) ✓
- `macro guard 60m`, `news blackout` → 0건 (거부 명기 only) ✓

**모든 보수 위장 패턴 제거 완료**.

---

## 12. 다음 액션

1. 본 plan Jin review · 승인 후 P0 sprint kickoff
2. `polaris/` 패키지 골격 생성 (core/venues/strategies/harness/)
3. canonical market model + REST polling + unified schema 작성 (P0 day 1)
4. OKX adapter + Capital adapter (P0 day 2-3)
5. Volume Burst strategy + paper loop smoke test (P0 day 3)
6. P1.0 즉시 ignition: Volume Burst + TSMOM + FX Breakout
7. vault `00_charter/` `_NOW.md` `INDEX.md` 초기화 (P0 와 병행)

**Codex 외부 review = 모든 신규 코드 의무** (Jin `feedback_code_review_codex_external`).

---

*Generated 2026-05-06 · Polaris v2 plan REWRITE 3 · Round 2 codex debate consensus (T1-T6, 7 codex calls)*
