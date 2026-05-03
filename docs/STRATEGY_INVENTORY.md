# INVASION — Strategy & Architecture Inventory

Snapshot: 2026-04-20 AEST · post-commits `f52b8f2b` (16 external provider wire) + `60f28f14` (alerter adopted fix)

## 1. 북극성 (Philosophy)
**Aggressive Contrarian** — crisis = opportunity, max bet on fear. ALL regimes ATTACK (방어 없음). 비대칭 유리 (loss<profit). Block rule 누적 금지. 세부: `.claude/docs/north_star.md`, `MEMORY.md feedback_aggressive_always_profit`.

## 2. Stack
- Python 3.11+ · SQLite WAL (`data/invasion.sqlite`) · Clean epoch 1775839507
- AI: Gemini (primary) + Claude (critical only)
- Exchanges: OKX(crypto perp) · Capital.com(forex/index/commodity CFD) · Alpaca(stock/ETF paper) · Binance(data only)
- Dashboard: 2-window (operations.py LEFT + intel.py RIGHT)

## 3. Layer Architecture
```
EXCHANGE → TICKS → CONFIG(preg 4-Tier) → GATE → SIGNAL(47 providers)
  → TRADE(pipeline: size chain → execute → exit_monitor → ExitEngine)
  → STRATEGY(Elo tournament + 4 mutations) → REGIME(per-group + crisis escalation)
```
4-Tier preg: FROZEN · CONFIG · DYNAMIC(AI hourly) · COMPUTED(realtime).

## 4. Package Tree (invasion/)
```
ai/          — Claude/Gemini advise+judge, analysis
analytics/   — pnl attribution, metrics
backtest/    — historical replay
boot/        — run/wiring
config/      — preg SSOT (_params_*.py) + themes/computed
dashboard/   — operations + intel + sections + feed
data/        — collectors (27)
exchange/    — okx/capital/alpaca/binance + router + broker_sync
market/      — hours, calendar
ops/         — harness_alerter, evolver, adaptive_tuner
signals/     — 47 providers + engine + composer + ml_signal + bayesian
strategy/    — router (softmax) + tournament (Elo) + evolver (mutations)
ticks/       — WS ingest + tick_history
trade/       — entry + exit + pipeline + sublanes + gate_matrix
utils/       — groups, logging, db
main.py · scheduler.py · bus.py
```

## 5. Signal Providers (47 total)

### Internal (31)
| File | Provider | Weight | Asset |
|---|---|---|---|
| providers.py | SentimentSignal, FundingSignal, LSRatio, Taker, FearGreed, Liquidation, Technical, CrossPair | 5–30 | mix |
| providers_technical.py | Momentum, Volatility, PriceAction, MultiTFTechnical | 8+ | all |
| providers_onchain.py | OnChainValuation, BasisSpread, LiquidationCascade, GoogleTrends, LLMSentiment | 12 | crypto |
| providers_macro.py | MacroRegime (CNN F&G + FRED) | 10 | all |
| providers_institutional.py | InstitutionalPosition (COT + Myfxbook) | 12 | forex/cmdty |
| providers_microstructure.py | OrderFlowImbalance, VWAPMeanReversion | 8 | all |
| providers_wqalpha.py | WQAlpha1, WQAlpha6 | 0 (shadow) | all |
| providers_breakout.py | DualThrust, SessionBreakout | 10 | fx/cmdty/idx |
| providers_cross.py | CrossExchange (Binance↔OKX) | — | crypto |
| ml_signal.py | MLSignalProvider (LightGBM) | 5 | all |
| bayesian.py | BayesianPredictor | shadow | all |

### External (16) — `providers_external.py` (commit f52b8f2b)
- **Stock (4)**: EdgarFilings · FinraShortInterest · FinvizScreener · AlpacaNewsCA
- **Option (2)**: CBOEPutCall · CBOEVixTerm
- **Commodity (3)**: BakerHughes · EIAPetroleum · COTData
- **Forex (3)**: ForexFactoryCalendar · OandaPositionBook · Myfxbook
- **Crypto (4)**: BlockchainInfo · DeFiLlama · ApeWisdom · AlternativeMe
- 초기 weight=0.3 (shadow), `provider_mult_<name>` 로 조정.

## 6. Signal Families (14 active + 2 meta)
`family_seeds.py` / DB `signal_families`:

| Family | Regime | Asset | Exchange | 상태 |
|---|---|---|---|---|
| crypto_momentum | RISK_ON/ATTACK | crypto | okx | Active |
| crypto_momentum_reversal | TRANSITION/NEUTRAL | crypto | okx | Active |
| crypto_contrarian | CRISIS | crypto | okx | Active |
| volatility_spike | CRISIS | crypto | okx | Active (long-only) |
| choppy | NEUTRAL | crypto | okx | Active |
| whale_fade | — | crypto | — | **DISABLED** (re-eval spec 743cef07) |
| stock_specialist | ALL | stock | alpaca/cap | Active (g-variants) |
| etf_specialist | ALL | stock | alpaca/cap | Active |
| forex_specialist | ALL | forex | cap | Active (g-variants) |
| indices_specialist | CRISIS | index | cap | Active (long-only) |
| contrarian_commodity | CRISIS | commodity | cap | Active (long-only) |
| session_breakout | RISK_ON | fx/cmdty | cap | Active |
| regime_neutral | NEUTRAL | mixed | all | Parent bucket |
| regime_neutral_scalper | NEUTRAL | mixed | all | Sub-bucket |
| parked (meta) | — | — | — | backoff sink |
| adopted (meta) | — | — | — | broker-originated pending |

## 7. Strategy Evolution Pool (Elo Tournament)

### 현재 Active 상위 (DB `strategies`)
```
 1. forex_specialist_g16_g22_ai        fit=52.63  gen=22
 2. forex_specialist_g16_g20_ai        fit=52.01  gen=20
 3. forex_specialist_g16_g104_bayes    fit=49.63  gen=104
 4. forex_specialist_g16_g53_gauss     fit=47.46  gen=53
 5. etf_specialist_g193                fit=45.23  gen=193
 6. forex_specialist_g16_g107_ai       fit=44.37  gen=107
 7. forex_specialist_g16_g53_bayes     fit=43.52  gen=53
 8. neutral_specialist_g193            fit=24.00
 9. stock_specialist_g193              fit=15.00
10. crypto_specialist_g193             fit=10.50
11. indices_specialist_g193            fit= 6.00
12. commodity_specialist_g193          fit= 1.98
```

### Mutation Types (4)
- **Gaussian** — PARAM_BOUNDS 내 Gaussian noise
- **Bayesian** — threshold_adjustments 적응학습
- **AI-targeted** — WIRE-12 AI 제안 buffer
- **Structural** — 구조적 변형 (드묾)

### 규칙
- MIN_FITNESS_PROMOTE = 50.0
- DISABLE_FITNESS = 50.0 / STOP_WR_DISABLE = 0.20
- ELITE_COUNT = 4 / MAX_STRATEGIES_PER_GROUP = 12
- Router = softmax(fitness) over active

## 8. Entry Gates (순서 · `trade/entry.py` + `signals/engine_gates.py`)
1. Blacklist (preg `ticker_blacklist` + regime-conditional)
2. Cooldown (default 60s · CRISIS=15s)
3. Capacity (portfolio + per-group limit)
4. Market Hours (OKX 제외)
5. Repeat Entry Rate Limiter
6. Funding Gate (crypto long-only, min/max)
7. ~~Signal Strength Floor~~ REMOVED (북극성 block 누적 금지)
8. ~~is_hour_blocked / ticker_blocked~~ REMOVED (commit 97d61292)

## 9. Signal Composition Pipeline
```
Provider.compute() (47×)
  → Bayesian edge_calibration (provider+regime+bucket → posterior)
  → Weighted sum (weight × score × confidence × decay)
  → min_score gate (preg default 20)
  → min_factors gate (default 2)
  → min_agreement gate (regime별 · CRISIS=0.3)
  → SignalVerdict(direction, score, confidence, quality)
```

## 10. Exit Engine (3 categories · `trade/exit.py`)

### STOP (hard_stop_pct — 항상 활성)
| Regime | stop_pct |
|---|---|
| RISK_ON | -2.0% |
| RISK_OFF | -2.5% |
| NEUTRAL | -1.0% |
| CRISIS | -3.5% |

### TRAIL (BEP + 3-tier)
- bep_activate: 0.15–0.8% (NEUTRAL 0.25 · CRISIS 0.4)
- trail_tiers: `[[1.5, 0.8]]` 3단계 interpolation
- holdtime 경과 → trail distance tightening
- profit cap 적용

### TIME (5 sub-rules)
- flat_kill_sec: 300–7200 (NEUTRAL 1200 · CRISIS 7200 · TRANSITION 1800)
- max_hold_sec: 1800–5400 (NEUTRAL 1800)
- profit_decay: 시간 경과 → 목표 수익 감소
- stagnant: 가격 무변동 청산
- pre_market_close: 장마감 전 청산

### Override
- ai_controller (TIGHTEN/KILL)
- market_closed: MarketClosedError → portfolio.remove() + long cooldown

### FSM Canary
- 20% OKX crypto (fsm_okx_crypto_canary_pct)

## 11. Regime System

### Split (regime.py)
- **CryptoRegimeDetector** — alt F&G, funding, OI, taker, BTC ADX → `{crypto}`
- **MacroRegimeDetector** — CNN F&G, HY spread, MOVE, VIX, DXY → `{forex,index,commodity,stock,etf}`

### States (5)
| Regime | Trigger | margin | bep | flat_kill | cooldown |
|---|---|---|---|---|---|
| RISK_ON | BTC ADX+funding+sent ↑ | 24% | 0.5% | 5400s | 60s |
| RISK_OFF | VIX>30, HY>400, DXY↑ | 12% | 0.15% | 5400s | 60s |
| TRANSITION | detecting | 16% | — | 1800s | 180s |
| NEUTRAL | F&G 40-60 | 10% | 0.25% | 1200s | 60s |
| **CRISIS** | VIX>40 OR HY>500 | **35%** | 0.4% | 7200s | **15s** |

**Crisis escalation**: 매크로 CRISIS 감지 시 crypto 도 동반 CRISIS override (aggressive contrarian 극대화).

## 12. Parameter Governance
- SSOT: ParamRegistry (preg) — `invasion/config/_params_*.py`
- 4-Tier: FROZEN / CONFIG / DYNAMIC / COMPUTED
- DYNAMIC: AI Governor 시간당 조정
- AdaptiveTuner: provider pnl_attribution_7d 피드백 (commit 95a85fe5)
- History: `data/param_history.jsonl`

## 13. Scheduler (main.py)
- 24 tick jobs (sched.register)
- Interval: 1s (exit_monitor) ~ 86400s (enricher)
- Inventory: `grep "sched.register" invasion/main.py`

## 14. EventBus
- `trade.entered` → ai, tournament
- `trade.closed` → tournament, evolver
- `regime.changed` → param_orch, pipeline

## 15. Ops / Observability
- HarnessAlerter: silent / wr_1h / dd_1h / loss_streak / regime_thrash / exit_other
- Alert routing: `.claude/harness_alerts/` → `/alert-triage` → `data/alert_route.jsonl`
- AdaptiveTuner (commit 95a85fe5) — signal_providers pnl_attribution_7d write
- Dashboard sections: positions, trades, strategy, regime, market, logs, AI, config, provider chain

## 16. Key Data Files
| File | Purpose |
|---|---|
| `data/live_config.json` | Hot-reload config |
| `data/portfolio_state.json` | Portfolio SSOT |
| `data/invasion.sqlite` | Trade history (WAL) |
| `data/invasion.log` | Rotating application log |
| `data/strategies/*.json` | Per-strategy Elo state |
| `data/param_history.jsonl` | Param audit |
| `data/regime_presets.json` | Regime overrides |
| `data/alert_route.jsonl` | Alert triage log |

## 17. 통계 요약
- **Providers**: 47 (internal 31 + external 16)
- **Families**: 14 active + 2 meta (whale_fade disabled)
- **Active Strategies**: 12 (fitness 기준 상위)
- **Regime States**: 5
- **Entry Gates**: 6 active (block filter 2개 제거됨)
- **Exit Categories**: 3 (STOP·TRAIL·TIME) + AI override
- **Tick Jobs**: 24 / **Collectors**: 27

---

### 참조
- 북극성: `.claude/docs/north_star.md`
- Canonical: `.claude/docs/canonical_files.md`
- Architecture 원문: `docs/ARCHITECTURE.md`
- Handoff: `.claude/agent-memory/harness/handoff_unified_2026_04_20_T7_p2_ready.md`
