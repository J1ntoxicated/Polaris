# Polaris v2 Plan — Dual-Track Demo Aggressive (REWRITE 2)

> Source: 4 리서치 + Codex 6 토픽 디베이트 (architecture만 채택, vision/timeline은 Jin mandate 직접 적용)
> Status: Draft for plan mode sign-off
> Mandate (Jin 2026-05-06):
> - **둘 다 DEMO/PAPER 계정 → 실제 자금 손실 0 → MAX AGGRESSIVE 허용**
> - **OKX SPOT + Capital CFD 동시 투트랙 즉시 배포** (Capital 단독 권고 거부)
> - **일 0.5% 즉시 시작** (12주 demo gate 거부)
> - **"묶지 마"** — drawdown cap / daily target hard limit / regime throttle 거부
> - **"막지 마"** — autonomous full, Jin 결과 첨삭만
> - **Hi-risk hi-return** — 보수 논거 점수 감점

---

## 1. Vision

**목표**: 일 0.5%+ net (= 250%/yr compound) — 안 되면 더 공격적으로 튜닝, 묶지 않음.

**Demo aggressive 원칙**:
- OKX paper + Capital demo 둘 다 가상 자금 — 실패해도 학습만 남음
- "Real-money risk" 논거 = 무효 (이 phase 는 demo 만)
- Live 진입 결정은 Jin 단독 권한 — plan 에 자동 promotion 없음

**측정 cadence**:
- 매 trade: open/close, gross/net, slip, fee, hold, reason → SQLite + vault digest
- 매일 (UTC midnight): daily PnL, trade count, win rate, avg gross, avg loss → vault `40_ops/daily/`
- 매주 (월요일): regime / strategy 별 sharpe / PF / trade dist → vault `40_ops/weekly/`
- **Gate** (각 strategy): 코드 작동 + paper trade 실제 발생 + 1주 generic test (no infinite loop, no auth break) → 즉시 다음 strategy 추가. KPI 통과 X 가 다음 phase 진입의 차단막 X.

**Drawdown 정책**: signal-strength-driven, regime-throttle 없음. Demo 자금 0 까지 갈 수도 OK (학습 관점). Jin 이 manual stop 외 자동 stop 없음.

---

## 2. Architecture (T2 재작성: 투트랙 동시)

**Sole production venue**: 없음. **양 트랙 production-equivalent 즉시**:

- **Track A — OKX SPOT** (`us.okx.com`, demo via `x-simulated-trading: 1`): 24/7 crypto, USDT-quote universe top 30, paper $46k+잡코인 정리 후 USDT $79k 기준
- **Track B — Capital.com CFD** (demo base URL): forex/indices/commodity, A$78k AUD 기준, leverage forex 30× / indices 20× / gold 20× / commodity 10×

**OKX shadow 강등 거부** — Jin mandate "투트랙 동시". OKX 가 Capital 보다 마찰비용 높지만 (0.16-0.20% RT vs forex 0.32% RT account friction at 30× lev), 24/7 + universe 친숙도 + crypto regime 노출이 별도 가치.

### 공통 코어 (둘 다 공유)
- Signal generation (strategy 인터페이스)
- Sizing/risk policy (T4 공식)
- Backtest/forward-test harness
- Telemetry schema (SQLite WAL `data/polaris.sqlite`)
- Dashboard
- Vault & harness modes
- Policy engine (Python, hooks 가 호출)

### Per-venue adapters
- `MarketDataProvider` (Capital, OKX)
- `ExecutionProvider` (Capital, OKX)
- `PositionLedger` (정규화 internal model)
- `OrderStateNormalizer` ({pending, partial, filled, cancelled, rejected})

### Order placement defaults
- **OKX SPOT**: `ordType=ioc` + `px=best_bid*1.0005` (5bps slippage cap), `tgtCcy=quote_ccy`, `tdMode=cash`, clOrdId 하이픈 금지 (R2 발견)
- **Capital CFD**: `POST /positions` market 즉시 fill (25-29ms 평균), SL/TP attach 동시, OCO/partial-close 미지원 → 클라이언트 시뮬

### Reconciliation
- 1초 tick: broker positions ↔ local PortfolioStateStore diff
- 5분 cycle: balance + open orders 깊은 비교
- Drift 감지 시: broker = SSOT, local 강제 sync, vault `40_ops/incidents/` 기록

### Monitoring (single alert)
- Demo phase 라 자동 stop / kill switch 없음 (Jin 명시 mandate)
- Sydney workday active ops, overnight unattended 허용
- Auto-safe-mode 만 1개 — auth 깨짐 (CST/X-SECURITY-TOKEN 만료, OKX 401) → paper-fallback fill (R2 권장)

---

## 3. P1 Strategies (T3 재작성: 즉시 시작, KPI gate X)

### Track A (OKX SPOT) — 3 strategies 즉시 활성화

**A1. Volume Burst** (R1 ★★★)
- Universe: 24h volQuote > $50M USDT-quote, top 30 (BTC/ETH/SOL/XRP/ADA/DOGE/AVAX/SUI 등)
- Timeframe: ws tick + 1m candle close
- Triple-confirm signal:
  1. volume z-score (20-period 1m) > 2.5
  2. price 5min close > prior 12-bar high
  3. liquidity floor: book depth > $50k within 0.1% mid
- Fee filter: gross expected move > 3× round-trip fee (R2 0.2% RT → > 0.6% expected)
- Exit: 30min time-stop OR ATR(14)×1.5 trail OR -0.3% hard stop
- Sizing: T4 공식 base_risk_pct = 1.0% (OKX SPOT 1× lev)

**A2. Donchian Breakout** (R1 ★★★ momentum)
- Universe: same top 30
- Timeframe: 1h
- Signal: close > Donchian(40) high
- Filter: ATR(14) percentile > 50th
- Exit: ATR×2.5 trail OR 24h hold cap
- Sizing: T4 base_risk_pct = 1.0%

**A3. TSMOM** (R1 ★★ trend-following baseline)
- Universe: same top 30
- Timeframe: 4h
- Signal: 20-bar return > 0 AND > prior 5-bar avg return
- Filter: NONE (aggressive — 묶지 마)
- Exit: ATR×3.0 trail OR signal flip
- Sizing: T4 base_risk_pct = 0.8%

### Track B (Capital CFD) — 3 strategies 즉시 활성화

**B1. FX Breakout Basket** (forex 30× lev)
- Universe: EURUSD, GBPUSD, AUDUSD, USDJPY, NZDUSD (5-pair basket)
- Timeframe: 4h
- Signal: Donchian 40 break + ADX(14) > 20
- Macro guard: NONE (aggressive — 묶지 마, news-time entry 도 OK)
- Exit: ATR×2.5 trail OR 7d hold cap
- Sizing: T4 base_risk_pct = 2.0%, leverage cap 30×

**B2. XAU/Indices Trend** (gold + indices 20× lev)
- Universe: XAUUSD, US500, US100, GER40
- Timeframe: 4h
- Signal: Donchian 30 break + 20d momentum sign
- Exit: ATR×2.0 trail OR 3-night cap (gold financing drag)
- Sizing: T4 base_risk_pct = 1.5%, leverage cap 20×

**B3. Session Breakout** (US/EU open momentum)
- Universe: US500, NAS100, GER40, UK100
- Timeframe: 5min trigger, 1h hold
- Signal: open ± 30min ATR×1.5 break
- Exit: 1h time-stop OR ATR×1.5 trail
- Sizing: T4 base_risk_pct = 1.5%, leverage cap 20×

### Strategy 인터페이스 (Jesse-style 4-method)
```python
class Strategy(ABC):
    def should_enter(self, market: MarketContext) -> EntrySignal | None
    def compute_size(self, signal, equity, portfolio_state) -> float
    def update_position(self, position, market) -> PositionUpdate
    def should_exit(self, position, market) -> ExitSignal | None
```

### 즉시 시작 + 자율 진화
- 6 strategies 다 P1 day-1 활성화 (3 OKX + 3 Capital)
- 매 trade closed → vault `50_research/strategies/<name>/trades.md` 자동 append
- 매주 strategy 별 sharpe / PF / DD / trade count 측정 → vault `40_ops/weekly/`
- **KPI 미달 = 자동 비활성화 X** (Jin mandate "묶지 마"). Jin 만 manual disable
- 신규 strategy 후보는 codex 디베이트 거치고 추가 — Jin 승인 후

---

## 4. Sizing Formula (T4 — 9-stack 영구 봉쇄, 채택)

```python
def compute_position_size(strategy, signal_strength_pct, account_equity, portfolio_state):
    # === STEP 1: risk budget (signal scalar applied HERE, not after clip) ===
    base_risk_pct = strategy.base_risk_pct  # OKX 0.8-1.0%, Capital FX 2.0%, XAU 1.5%
    signal_scalar = 0.75 + 0.75 * signal_strength_pct  # range 0.75x - 1.50x
    trade_risk_budget = account_equity * base_risk_pct * signal_scalar

    # === STEP 2: streak amplifier ===
    if (streak_3_wins(strategy)
        and strategy.trades_in_20d >= 5):  # min sample, no regime gate
        trade_risk_budget *= 1.5  # max single risk: 2.0% × 1.5 × 1.5 = 4.5% FX

    # === STEP 3: convert risk to notional via stop distance ===
    stop_distance_pct = strategy.stop_distance_atr() / current_price
    candidate_notional = trade_risk_budget / stop_distance_pct

    # === STEP 4: HARD caps (in order, 묶지 마 — minimum gate) ===
    # 4a. Per-symbol leverage cap (Capital retail)
    candidate_notional = min(candidate_notional, account_equity * leverage_cap[symbol])
    # 4b. Per-symbol gross cap (50% notional, was 40% in T4 — 더 공격)
    candidate_notional = min(candidate_notional, account_equity * 0.50)
    # 4c. Available portfolio gross (100% — no throttle)
    portfolio_gross_cap = 1.00  # AGGRESSIVE — no regime throttle (Jin mandate)
    available_gross = (account_equity * portfolio_gross_cap) - portfolio_state.current_gross
    candidate_notional = min(candidate_notional, available_gross)
    # 4d. Total open stop-risk budget (8% — was 6% in T4, 더 공격)
    total_open_risk_cap = account_equity * 0.08
    available_risk = total_open_risk_cap - portfolio_state.current_open_risk
    candidate_notional = min(candidate_notional, available_risk / stop_distance_pct)

    # === STEP 5: binary halts ===
    if portfolio_state.executor_failure: return 0
    if portfolio_state.broker_auth_broken: return 0  # paper-fallback fill 별도

    # === STEP 6: min executable (cost-aware) ===
    expected_loss_cash = candidate_notional * stop_distance_pct
    rt_cost = candidate_notional * strategy.round_trip_cost_pct
    if expected_loss_cash < max(3 * rt_cost, strategy.min_stop_loss_cash):  # k=3 (was 5)
        return 0

    return candidate_notional
```

### Frozen constants
- `base_risk_pct`: OKX strategies 0.8-1.0%, Capital FX 2.0%, XAU/indices 1.5%
- `signal_scalar` range: 0.75 - 1.50
- streak amplifier: 1.5×, gate = 3 wins + ≥5 trades 20d (regime gate 제거 — 묶지 마)
- per-symbol gross cap: 50% (T4 의 40% → 50%)
- portfolio_gross_cap: 100% 항상 (T4 throttle 제거)
- total_open_risk_cap: 8% account (T4 의 6% → 8%)
- min-executable k-multiplier: 3 (T4 의 5 → 3, 더 자주 진입)
- max single-trade risk: 4.5% (Capital FX 2.0% × 1.5 signal × 1.5 streak)

### Anti-collapse (T4 채택)
v1 collapsed because 9 multipliers were all ≤1. v2 has:
- 1 continuous scalar (0.75-1.5×) BEFORE notional clip
- 1 binary amplifier (1.5×)
- All other caps are HARD MAX, never soft dampener
- No multiplier stacking on multiplicative path

---

## 5. Harness (T5 — 채택)

### Modes (4: 3 hard-exclusive + 1 overlay)
- HARD-EXCLUSIVE: `/dev`, `/alpha`, `/forensic` (one per session)
- OVERLAY: `/debate` (cross-cutting)

### Posture (orthogonal axis)
- `posture: aggressive` (default — Polaris 항상 aggressive)
- `posture: standard` 폐기 — Jin mandate "디펜시브 NO"

### Agents (4)
- `analyst` — research/backtest/hypothesis
- `risk-officer` — sizing rule application, hard cap enforcement (NO defensive throttle, only hard min/max)
- `executor` — split into LANES: `executor.okx` + `executor.capital`. Same agent, separated state context.
- `forensicist` — incident investigation, postmortem

### State stores
- **PortfolioStateStore** (single SQLite `data/polaris.sqlite`, namespaced):
  - `positions_okx`, `positions_capital` (segregated)
  - `orders_okx`, `orders_capital`
  - `trade_events`, `signal_events`, `regime_snapshots` (shared)
  - `halt_flags`, `vault_pointers` (shared)
- **VaultPointerStore** — live vault docs index

### MCP set (7 install)
INSTALL NOW:
- `ccxt` (OKX + multi-exchange)
- `sqlite` (state store)
- `duckdb` (analytics, parquet, large fill scans)
- `context7` (library docs lookup)
- `sequential-thinking` (multi-step reasoning)
- `obsidian-mcp` (vault read/write)
- `time` (Sydney AEST consistency)

DEFER P2: filesystem, brave-search, tradingview, perplexity, alpaca

Capital — NO MCP, build thin Python adapter (R3 reference).

### Skills (8 active)
1. `polaris:ingesting-data` — Capital + OKX OHLC + macro calendar
2. `polaris:querying-vault` — search + backlink
3. `polaris:linting-vault` — orphan/stale/contradiction
4. `polaris:running-backtest` — vectorbt + walk-forward
5. `polaris:reviewing-strategy` — codex external delegate (NOT KPI gate — Jin mandate)
6. `polaris:auditing-trade` — single-trade forensics
7. `polaris:syncing-context` — _NOW.md + INDEX.md (SessionStart)
8. `polaris:governing-execution` — policy_engine matrix authoring + tests (NOT enforcement)

### Policy enforcement (T5 BLOCKER fix 채택)
- **`src/polaris/policy_engine.py`** = deterministic Python module called by hooks (NOT skill)
- Skills cannot reliably enforce — code must

### Allowed-action matrix (policy_engine, AGGRESSIVE only)
```
allowed_actions[mode][env][venue] = list[ToolName]

[/alpha][demo][okx]      → backtest, paper-order, vault-write
[/alpha][demo][capital]  → backtest, demo-order, vault-write
[/dev][*][*]             → ANY destructive action requires explicit human confirm + ADR
[/forensic][*][*]        → read-only DB/log access
```

### Hooks (5 critical)
- `SessionStart` → `polaris:syncing-context`, load mode/posture
- `SessionEnd` → state-transition log, vault write enforced ON material context change (digest / decision / incident / ADR / noop with reason)
- `PreToolUse` → `policy_engine.check()` Python call
- `PostToolUse` → async lint
- `UserPromptSubmit` → mode validation

### Cred segregation
- `.env` 통합본 활용 (이미 작성됨, 2026-05-06)
- OKX_DEMO_* (us.okx.com), CAP_API_KEY/EMAIL/PASSWORD
- Cross-venue leakage 방지 = policy_engine

---

## 6. Vault (T6 — 채택, regrets/ 만 폐기)

### Structure (6 top-level dirs + root files)

```
vault/
├── _NOW.md                    # Hybrid hand-written + auto-gen (SessionEnd updates AUTO block)
├── INDEX.md                   # Auto-generated catalog
├── log.md                     # Append-only, 1-line per work-item, NO interpretation
├── 00_charter/                # Constitution + workflow docs
│   ├── north-star.md          # Aggressive bias mandate
│   ├── coding-conventions.md
│   └── karpathy-workflow.md
├── 10_decisions/              # ADRs, CREATION order
├── 20_strategies/             # Strategy specs (6 P1 + future)
├── 30_components/             # Code component docs
├── 40_ops/                    # Operations
│   ├── daily/2026-MM-DD.md
│   ├── incidents/incident-XXX.md
│   └── digests/weekly-NNN.md
├── 50_research/               # Hypotheses, lit-reviews
├── _attachments/              # Binaries
└── ._meta/                    # Hidden auto-generated artifacts
```

(T6 의 `40_ops/regrets/` 폐기 — Jin mandate "묶지 마", aggressive bias 면 regrets 자체가 없음)

### Frontmatter (YAML)
```yaml
---
type: ADR | strategy | component | runtime | research | charter
status: active | superseded | abandoned
related: [[...]]
date_created: 2026-05-06
date_updated: 2026-05-06
---
```

### ADR numbering — CREATION order (T6 채택)
ADR-001 = vault structure 자체. 나머지 mint 순서대로.

### Lint (2-tier, T6 채택)
- **Light** (pre-commit): missing frontmatter, broken wiki-links, no outbound links
- **Heavy** (weekly cron): contradiction detection, stale-note sweep (>90d), orphan recommendations

### Wiki-links / Bases / provenance
- Entities: `[[EURUSD]]`, `[[BTC-USDT]]`, `[[Capital]]`, `[[ATR]]`
- Decisions: `[[ADR-001]]`
- Strategies: `[[volume-burst]]`, `[[fx-breakout-basket]]`
- Bases (Obsidian 1.9+, Dataview 폐기 — 2025-04 dormant) — top-level dir 별 1 base

---

## 7. Phase Plan (재작성: 즉시 시작, 12주 X)

### P0 — Infrastructure (즉시 — 1-3일 estimate)

병렬 deliverables:
1. `vault/` skeleton (T6 6 dirs) + `_NOW.md`/`INDEX.md`/`log.md`/`00_charter/*`
2. ADRs (mint 순서): ADR-001 vault → ADR-002 vision → ADR-003 dual-track → ADR-004 strategies → ADR-005 sizing → ADR-006 harness
3. `data/polaris.sqlite` schema (positions/orders × okx/capital, halt_flags, event_log, signal_events)
4. `src/polaris/` package skeleton:
   - `policy_engine.py` (Python enforcement)
   - `state_store.py` (SQLite WAL)
   - `market_data_provider.py` (interface + okx + capital)
   - `execution_provider.py` (interface + okx + capital)
   - `position_ledger.py`
   - `order_state_normalizer.py`
5. `src/polaris/strategies/` 6 P1 strategies (Jesse-style 4-method)
6. `.claude/skills/` 8 active skills (gerund form, ≤500 lines)
7. `.claude/hooks/` 5 critical hooks
8. `tools/vault_lint.py` (light + heavy)
9. MCP install: 7 servers
10. Capital adapter (thin Python, R3 reference) — auth (CST + X-SEC-TOKEN), 9-min ping, REST poll fills, WS subscribe
11. OKX adapter (`us.okx.com` + `x-simulated-trading: 1`, R2 reference) — python-okx SDK + WS private fills, clOrdId 하이픈 제거
12. Codex external review of P0 critical modules (1 codex round per — sizing/policy_engine/adapters)

**P0 exit gate**: skeleton runs, vault lints clean, 둘 다 adapter 가 1d OHLC fetch + 1 demo order place 성공.

### P1 — 양 트랙 paper trading 활성화 (P0 끝나는 즉시 — 추가 1-2일)

**병렬 활성**:
- Track A (OKX): A1 Volume Burst + A2 Donchian + A3 TSMOM 동시 시작
- Track B (Capital): B1 FX Breakout + B2 XAU/Indices Trend + B3 Session Breakout 동시 시작

**P1 exit gate**: 양 트랙 paper trade 첫 fill 발생 + 1주 fault-free 운영 (auth break X, infinite loop X, dashboard 라이브). KPI 통과 X 가 차단막 X.

### P2 — 학습 + 진화 (P1 + 1주 부터 ongoing)

- 매주 strategy sharpe/PF/DD 리포트 → vault `40_ops/weekly/`
- KPI 부진 strategy = Jin manual disable (자동 X)
- 신규 strategy 후보 codex 디베이트 → Jin sign-off → 추가
- Live 진입 결정은 Jin 단독 권한 (자동 promotion 없음)

### P3 — Beyond (Jin 결정 시점)

- ASIC pro-client 평가 (Jin 결정 시)
- Strategy slot 확장 6 → 10+ (vault patterns 일관성 확인 후)
- 신규 venue (Binance, Hyperliquid 등) 추가는 Jin 승인 후

---

## 8. Risk Register (aggressive only — 보수 mitigation 거부)

| Risk | Mitigation (aggressive 만) |
|------|---------------------------|
| v1 sizing 9-stack collapse 재발 | T4 공식: 1 continuous scalar BEFORE clip + 1 binary amplifier + hard caps. 봉쇄 |
| OKX 401 root cause 재발 | base URL `us.okx.com` 명시, region split memory write 됨 |
| Capital auth idle expire (10분) | 9-min ping anti-idle, auto-reconnect |
| Dual-track infra burden (single dev) | Common core 공유, per-venue adapters thin. 2-track ≠ 2× work |
| Demo paper 결과 ≠ Live | Live 진입은 Jin 단독 권한. 자동 promotion 없음 — 이 plan 책임 영역 밖 |
| Codex 보수 권고 재발 | plan-mode sign-off 통과 안 한 codex 권고 무효 |
| 내부 stealth conservatism (실수로 보수 도입) | regrets log 거부 (그 자체가 보수 위장). aggressive bias self-check 매 commit |

---

## 9. ADRs to mint at P0 start

1. **ADR-001 Vault Structure** (T6) — directory + frontmatter + lint + ADR numbering rule
2. **ADR-002 Vision** — 일 0.5%+ aggressive, demo paper 무한 risk OK, Jin 단독 live 진입 권한
3. **ADR-003 Dual Track** — OKX + Capital 동시 production-equivalent, shared core + per-venue adapters
4. **ADR-004 P1 Strategies** — 6 strategies (3+3) 즉시 활성화, KPI 자동 disable 거부
5. **ADR-005 Sizing Formula** — T4 공식 + 4.5% max single-trade risk + hard caps
6. **ADR-006 Harness Architecture** — 4 modes, posture aggressive only, 4 agents, 7 MCPs, 8 skills, 5 hooks, policy_engine.py

---

## 10. Aggressive-Bias Self-Check

- [x] Drawdown cap 없음 (signal-strength-driven, regime throttle 거부)
- [x] Daily target hard limit 없음 (목표 0.5%, 더 나면 좋고)
- [x] Sizing 9-stack 영구 봉쇄 (signal scalar BEFORE clip + hard caps)
- [x] 12주 demo gate 거부 — P1 즉시 paper trade 시작
- [x] KPI 자동 disable 거부 — Jin 만 manual control
- [x] 투트랙 동시 즉시 (Codex Capital 단독 권고 거부)
- [x] Regrets log 거부 (보수 위장 메커니즘)
- [x] Posture standard 거부 (aggressive 항상)
- [x] News-time entry 허용 (macro-blackout 거부)
- [x] Demo = max risk 허용 (real-money 가정 거부)

---

**END Polaris v2 plan REWRITE 2. Ready for plan mode sign-off.**

**디베이트 산출 채택 부분**: T4 sizing 공식 + T5 harness 골격 + T6 vault 구조
**디베이트 산출 거부 부분**: T1 50%/yr / T2 Capital 단독 / T3 90d gate / Phase 24주 / regrets log
**거부 사유**: Codex 가 demo (가상 자금) 사실 무시하고 real-money 보수논리 적용 → Jin mandate 정면 위반
