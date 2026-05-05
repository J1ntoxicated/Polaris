---
entity_type: live_dashboard
entity_id: now
auto: false
last_modified: 2026-05-04  # AI Advisor LONG bias fix: confidence 0.75 + neutral prompt + decision counter
expires: never
editable: true
back_links: ["[[INDEX]]", "[[log]]"]
mode: meta
reviewed_by: jin
tags: [meta, live, dashboard, polaris, bootstrap]
---

# _NOW — Live Diagnostic Dashboard

> **세션 시작 시 이 파일부터 read** — Polaris 현재 상태 + 진단 진입점.

## Phase 6+7 + Codex round-1 (2026-05-05) — Slippage 4-layer + EV flip

**🎯 Polaris EV 양수 첫 전환** — Live readiness 22→35→41/100 (MARGINAL), Paper PnL -$59→-$30→**+$11.82**, Live est -$77→-$37→**+$2.14** (첫 양수). NFI = hidden alpha (n=22, 86% win, +$45.34).

- Phase 6: slippage_model.py (P6 pure, 21 tests). walk_book + compute_fill_price + spread filter (>5bps SKIP).
- Phase 7: compute_liquidity_cap (size ≤ 10% × top-5 ask depth, 6 tests).
- Codex round-1 (3 critical fixes accepted): liq_cap=0 → SKIP, spread filter inf 처리 unconditional, exit_notional = (size_usd/entry_price)×tick_price.
- Codex round-2: NONE (clean).
- Tests: 812 pass. 자세히: [[INSIGHT-036]]

## 현재 상태 (2026-05-04 — Phase 5 Codex Top5 + NFI X7: 825/825 pass)

**Phase 5 Codex Top5 + NFI X7 (825/825 pass)**:
- **CRITICAL (Codex #1)**: strategy singleton `_strategy_instances` + balance cache `_balance_cache` — `_eval_and_act` 매 tick 1회 생성→재사용. 90 calls/tick×15ticker CPU 낭비 제거.
- **CRITICAL (Codex #5)**: fee default 0.0014→**0.002** (OKX paper Lv1 0.1%/side = 0.2% rt). env `LIVE_FEE_ROUND_TRIP` override 가능.
- **HIGH (Codex #3)**: GridBot breakout_buffer_pct = absolute 2% (range×5% → noise trigger on wide-range days 제거).
- **MEDIUM (Codex #4)**: auto_deprecate min_n **5→20** (Bailey 2014 통계), loss_cap **-$5→-$15** (7.5%/30-trade window).
- **NEW (NFI)**: `HYPO-NFI-001` NFI X7 DipBuy — RSI_3 5m/15m<5 + RSI_14 1h<30 + BB lower + AROON_4h<80. `src/strategies/nfi_dipbuy.py` 32 TDD tests.
- **pair universe 15→30**: `_UNIVERSE_30` (BNB/UNI/AAVE/LDO/ICP/FIL/ARB/OP/SHIB/INJ/SEI/TIA/JTO/BLUR/WLD 추가). HYPO-007/008/040/NFI-001 적용.
- Active HYPOs: **8개** (007+008+023+027+028+032+040+NFI-001). 025/024/033/AI-001 deprecated 제거.

**HYPO-023 final diagnosis (792/792 pass)**:
- **확정 원인**: A (rare event). WS 구독 정상 + 60s/8sym 모두 forceOrder 0건 (low-vol day BTC $78k-$80k)
- **Fix**: lookback 60s→**300s**, min_total **$100k→$30k**, exit_total **$30k→$10k**, `get_store_status()` + `[LIQ-STORE]` 5분 주기 진단 log
- 5 신규 tests. deprecate 후보 아님 — 우선 extended window + lower threshold로 관측 계속

**Phase 4 Forensic+Research — 4 핵심 fix (787/787 pass)**:
- **HYPO-023 진단 v1**: $1M→**$100k** (60s window 실현 불가 임계값), exit $300k→$30k, WS raw log 20 events, noise filter $1000→$500
- **HYPO-AI-001 retry**: max 3x + backoff 1/2/5s + rate_limit +5s + credit error OpenAI fallback. 무한 HOLD 루프 완전 차단.
- **HYPO-040 GridBot NEW**: BingX 287K users 검증, ATR<1%+lower 30% range boundary, pure core `_compute_grid_signal`. 25 TDD tests.
- **pair universe 6→15**: HYPO-007-RT/008-RT/040 동일 15 tickers (NFI 표준). 신호 빈도 ~1.7x.
- Active HYPOs: **10개** (007+008+023+024+027+028+032+033+040+AI-001). **+35 신규 tests**.

**AI Advisor LONG bias fix (88% → target 30-50%)**:
- `DEFAULT_MIN_CONFIDENCE`: 0.72 (tuned: 0.75 → too strict → LONG 0.3%)
- neutral prompt, `_ai_decision_counts`, HYPO-AI-001 params 동기화. 747/747 pass.

**VPIN fix + cold_start default fix (698/698 pass)**:
- HYPO-033 `exit_profile`: scalp→**liquidation** (Easley/LdP 2012 — informed flow 5-30min reversion)
- `compute_size` default `n_trades`: `COLD_START_N(20)` → **0** (safe cold start — omit = cap applies)
- `_sized()` helper 추가 (test_dynamic_sizing.py — warm start 비교 테스트 편의)
- 2 신규 테스트: `test_vpin_uses_liquidation_profile` + `TestColdStartCapDefault` (2 tests)

**Phase 2N+ Emergency Fix (663/663 pass)**:
- HYPO-025 VolumeDeltaDivergence: **DEPRECATED** (n=6 win 33%, avg_size $687, -$3.76)
- `src/risk/dynamic_sizing.py`: `compute_size(n_trades=COLD_START_N)` — cold start cap $300 (n<20)
- `src/paper/realtime_runner.py`: `DEPRECATE_CHECK_INTERVAL_S` 300s→60s + n_closed 전달
- 11 신규 tests (`TestColdStartCap` 8개 + runner 3개) TDD RED→GREEN
- Active HYPOs: 9개 (007+008+023+024+027+028+032+033+034)

**Phase 2M+ 신규 전략 (557/557 pass)**:

**Phase 2M+ 신규 전략 (557/557 pass)**:
- HYPO-035 CrossSectionalMomentum: `src/strategies/cross_sectional_momentum.py` — Jegadeesh & Titman 1993 JoF (30d rank)
- HYPO-036 FundingCarry: `src/strategies/funding_carry.py` — Liu & Yu 2024 (funding <= -0.05% carry long)
- `src/data/binance_funding.py` — Binance Futures funding REST poller (TTL cache 60s)
- `scripts/backtest_research_strategies.py` — HYPO-032/034 5년 backtest 실행
- DAILY_PAPER_HYPOS 3개 (HYPO-035/036/020) 등록 — `src/paper/daily_paper_runner.py`
- 58 신규 tests (CSMomentum 22 + FundingCarry 18 + BinanceFunding 18)

**HYPO-032/034 backtest 결과 (2026-05-04 실행)**:
- TSMOM 1D (6 tickers): Sharpe 0.04~0.12 (all below 0.3 threshold). EV 양수 (+0.45~+1.72%). n_trades 198~219.
  → Viable=0/6. ADR-011 swing Sharpe 기준 미달. Crypto 1D vol 과대로 Sharpe 억압.
  → INSIGHT 필요: TSMOM crypto 적합성 vs equity/futures gap.
- BTC Lead-Lag 5m proxy (5 alts): Sharpe -0.18~-0.81. EV 음수.
  → 5m candle-based proxy가 realtime tick 전략을 표현 못 함. 결과 해석 주의.

**Phase 2M 학술 검증 알파 deploy (499/499 pass)**:
- HYPO-029/030/031 (StochRSI/ADXTrendPullback/OBVDivergence) → deprecated (학술 근거 없음)
- HYPO-032 TSMOM: `src/strategies/tsmom.py` — Moskowitz/Ooi/Pedersen 2012 JFE (1d/7d/30d return ratio)
- HYPO-033 VPINToxicity: `src/strategies/vpin_toxicity.py` — Easley/LdP/O'Hara 2012 RFS (VPIN > 0.7)
- HYPO-034 BTCDominanceLag: `src/strategies/btc_dominance_lag.py` — Stalder 2025 + Liu 2022 (5min lead-lag)
- auto_deprecate.py: min_n 10→5, max_loss_usd -$10→-$5 (Phase 2M strict gate)
- TDD 41 신규 tests + runtime verify. Active HYPOs: 007+008+023+024+025+027+028+032+033+034 = 10개.

**이전: Phase 2j AI Dynamic Sizing 완료 (316/316 pass)**:
- Kelly + confidence² + regime + drawdown pipeline. [[INSIGHT-032]]

**HYPO-005/001/002/006 fee fix 후 재평가 완료 (INSIGHT-026)**:
- fee 0.0014 기준 재backtest (BTC 1D 1800 candles, 1H 3000 candles)
- MACD(12,26,9): expectancy -0.09%→+1.17% (fast-fail 통과 전환), Sharpe 0.10 미달 → archived 유지
- BB/RSI/Ichimoku 모두 Sharpe 미달 → archived 유지
- 패턴: fee fix = expectancy 보정 O / Sharpe 보정 X (신호 분산은 fee와 무관)
- 55/55 backtest+domain tests pass
- 다음 액션: HYPO-013/014 60분 실측 + size 결정 (HYPO-008/010)

## 📍 다음 액션

- [x] Phase A 완료 (vault SSOT 콘텐츠 작성)
- [x] Phase B (hooks + lint v4)
- [x] Phase C (agent 20→4 압축)
- [x] Phase D (writing-plans으로 implementation plan)
- [x] Phase 0 완료 (Codex 외부 리뷰 93% 합의 — [[codex_review_phase_abc]])
- [x] Phase 1 완료 (8 인수 소스 → 18 노트: 9 INSIGHT + 4 ADR + 5 LESSON)
- [x] Phase 2a HYPOTHESIS-001 fast-fail (archived) → INSIGHT-013
- [x] Phase 2 HYPOTHESIS-002 BB Breakout fast-fail (archived) → INSIGHT-014
- [x] **Phase 2 HYPOTHESIS-003 SMA crossover 1d = SPOT viable** → [[INSIGHT-015]] [[ADR-011]] (timeframe-aware Gate)
- [ ] Phase 2 — HYPOTHESIS-002 (Bollinger band breakout 또는 momentum 시도)
- [ ] Phase 2c — 페이퍼 인프라 (WS feed + simulated order book + position tracker, ADR-010)
- [ ] Phase F (visualizer + dashboard, 코어 완성 후)

## 🧭 네비게이션

- 영속 원칙: [[10_constitution/principles]] (P1~P7)
- 4 contract: [[10_constitution/4_contracts]]
- 운영 모델: [[10_constitution/operating_model]]
- 코드 리뷰 워크플로: [[10_constitution/code_review_workflow]]
- 마스터 인덱스: [[INDEX]]
- ADR: [[ADR-001]] [[ADR-002]] [[ADR-003]] [[ADR-004]] [[ADR-005]]
- INSIGHT: [[INSIGHT-001]] [[INSIGHT-002]]
- 인수 큐: [[_INHERIT_QUEUE]]

## 🔥 Active Critical

- [[ADR-015]] Dynamic Sizing MAX_FRACTION=0.20 (ADR-010 단일 포지션 2% 조항 supersede) — **provisional, Jin ack 대기**. paper OK, live 도입 시 재평가 필수.
- [[INSIGHT-029]] Round 14 forensic (Codex 88%) — HYPO-010 silent cap bug + 14:36 regime cluster 13 SL + TRUMP 구조적 부적합. 4 fix: size $200 복원 / TRUMP 제거 / regime cluster guard / HYPO-016 trigger 재정의
- [[INSIGHT-028]] Round 13 결정 (Codex 85%) — HYPO-010 size $300 (INSIGHT-029에서 복원) / HYPO-016 deprecate trigger / HYPO-014 vol 5 bps / $15~18/h 추정
- [[INSIGHT-027]] HYPO-010/017 신호 직교성 확인 (orthogonal alpha)
- [[INSIGHT-026]] archived HYPO 재평가 완료 — 4개 모두 archived 유지 (Sharpe 미달 일관)
- [[INSIGHT-025]] fee 0.014 latent bug 4건 fix 완료 — backtest 재실행 완료 (INSIGHT-026)
- [[INSIGHT-024]] HYPO-009 deprecate 근거 (n=16, EV -1.33%, TP<SL 비대칭) — Round 9
- [[INSIGHT-023]] HYPO-011/012 deprecate 근거 (n=336/450, EV 계산, signal_exit 구조) — Round 8
- [[INSIGHT-022]] Phase 2g Binance WS + MTA + Codex Round 4~7 fix 누적
- [[INSIGHT-021]] flip-flop fee bleed fix — Round 4 hysteresis + min hold + ticker-global cooldown
- [[ADR-013]] HARNESS Meta Mode 정착 — 모든 작업 mode dispatch
- [[ADR-004]] 코드 리뷰 codex 외부 의무 (Jin 2026-05-03 mandate)

## 🔥 Round 15 (2026-05-04) — tick-driven scalp 비활성 + 1d trend 강화

**Jin 판단**: tick-driven scalp 5개 전부 deprecated. 1d trend portfolio 강화.

| HYPO | n | win% | PnL | 상태 |
|---|---|---|---|---|
| HYPO-010-TICK | 95 | 43% | -$14.98 | **DEPRECATED** |
| HYPO-013-MTA | 1 | 100% | +$0.46 | **DEPRECATED** (sample 부족) |
| HYPO-014-BLEAD | 1 | 0% | -$0.20 | **DEPRECATED** (vol 미달) |
| HYPO-016-OFI | 37 | 24% | -$3.92 | **DEPRECATED** (사전 trigger) |
| HYPO-017-CASCADE | 0 | - | - | **DEPRECATED** (trigger 빈도 0) |

**코드 변경**:
- `REALTIME_HYPOS` → HYPO-007-RT + HYPO-008-RT (2개)
- `ACTIVE_HYPOS` → SMA 8ticker + Donchian 2 variants (3 entries)
- 238/238 tests pass + INSIGHT-030 + ADR-014 신규

**Codex Round 15 dispatch 의무** (ADR-004 — fundamental portfolio decision review)

## 🔥 Round 11 (2026-05-04) — HYPO-016 + HYPO-017 구현 완료

**HYPO-016 OFI Momentum**: `src/strategies/ofi_momentum.py` (pure P6) + 20 tests. HYPO-016-OFI 등록 (primary_tf="ofi", 6 tickers). Codex 72% 합의.

**HYPO-017 BTC-Led Alt Cascade**: `src/strategies/btc_cascade.py` (pure P6) + `tests/strategies/test_btc_cascade.py` 30 tests. HYPO-017-CASCADE 등록 (primary_tf="cascade", alt 5 tickers: DOGE/SOL/ORDI/PEPE/TRUMP).

- 1min price history: `_update_price_history` / `_get_cascade_state` (module-level deque per ticker, 65s window)
- HYPO-010 orthogonality guard: alt_24h >= +0.5% → HOLD (신호 겹침 차단, INSIGHT-027 forensic)
- ETH confirmation: eth_1min_delta >= +0.10% (false positive 차단)
- Stale guard: btc/eth state >= 30s → HOLD
- Hysteresis deadzone: -0.20% ~ +0.30% (flip-flop 방지)

**TDD**: 30 tests RED→GREEN. 전체 **210/210 pass**. Runtime import + evaluate_cascade() 확인.

**Round 12 dispatch 의무**: ADR-004 — HYPO-016 + HYPO-017 implementation review.

## 🔥 Round 10 (2026-05-04)

**Fix 1 — HYPO-013 MTA HOLD 로깅**: `_eval_and_act` mta branch에 `[MTA-HOLD] {ticker} {reason}` INFO 로그 추가. 24h 후 too strict vs wrong logic 판단 근거 확보.

**Fix 2 — HYPO-014 Binance feed health check**: cross branch에 `[BLEAD-NOFEED]` WARN (rate-limit 5분/ticker) + `[BLEAD-HOLD]` INFO (rate-limit 1분/ticker) 추가. WS feed 미공급 vs threshold 미충족 구분 가능.

**TDD**: `test_mta_hold_logged` + `test_blead_nofeed_warn_rate_limited` RED→GREEN. **160/160 pass**.

**24h 분석 plan**: runner restart 후 logs/realtime.err에서 `grep -E "\[MTA-HOLD\]|\[BLEAD-NOFEED\]|\[BLEAD-HOLD\]"` → HOLD reason 분포 집계 → 2026-05-05 분석.

## 🟢 운영 중 (HYPO 활성)

### Realtime (tick-driven) — Phase 4 (10개)

| HYPO | Strategy | Status |
|---|---|---|
| HYPO-007-RT | RSI15m intraday | active — **15 tickers** (↑ from 6, INSIGHT-035) |
| HYPO-008-RT | VolumeBurst 1H | active — **15 tickers** (↑ from 5) |
| HYPO-023 | LiquidationCascade | active — **$100k threshold** (was $1M, 25h 0 signals fixed) |
| HYPO-024 | CrossExchangeGap | active — Binance bookTicker lead |
| HYPO-027 | FundingRateFilter | active — Binance funding squeeze |
| HYPO-028 | TickBurst | active — 5s price spike |
| HYPO-032 | TSMOM | Phase 2M — Moskowitz 2012 JFE |
| HYPO-033 | VPINToxicity | Phase 2M — Easley 2012 RFS |
| HYPO-040 | GridBot | **Phase 4 NEW** — BingX 287K users, ATR<1%+lower 30% boundary, 15 tickers |
| HYPO-AI-001 | AIAdvisor | Phase 3 — **retry 3x** (was no retry, infinite HOLD loop fixed) |

### 1d Cron (trend) — Promotion Gate PASSED (Round 15)

| HYPO | Strategy | Tickers | Status |
|---|---|---|---|
| HYPO-003-SMA50-200 | SMA 50/200 1D | 8 (BTC/ETH/SOL/DOGE/ADA/XRP/ORDI/SUI) | active |
| HYPO-004-DONCH-40-15 | Donchian 40/15 1D | BTC+ETH | active |
| HYPO-004-DONCH-20-10 | Donchian 20/10 1D | BTC+ETH+SOL | active |

### Paper Stage (ADR-010 — 60일 실측 중, Promotion Gate 미통과)

| HYPO | Strategy | Tickers | paper_since | Walk-Forward |
|---|---|---|---|---|
| HYPO-020-VB-DONCH-DOGE | ConfluenceSignal(VB+Donchian 40/15) 1D | DOGE | 2026-05-04 | ROBUST (3/3 fold PASS) |
| HYPO-035-CS-MOM | CrossSectionalMomentum 1D (30d rank, top 30%) | 8 universe | 2026-05-04 | TBD (paper accumulation) |
| HYPO-036-FUNDING-CARRY | FundingCarry (funding <= -0.05%) | BTC/ETH/SOL | 2026-05-04 | TBD (event-driven) |

### Deprecated

| HYPO | Strategy | 이유 |
|---|---|---|
| HYPO-009-RT | BreakoutMomentum | Round 9 — n=16, EV -1.33%, TP<SL |
| HYPO-010-TICK | TickMomentum | Round 15 — n=95, win 43%, -$14.98 변질 |
| HYPO-011-BOOK | OrderBookImbalance | Round 8 — n=336, TP 0, -$77.93 |
| HYPO-012-FLOW | TradeFlow | Round 8 — n=450, EV -0.22%, -$151.77 |
| HYPO-013-MTA | MTAConfluence | Round 15 — n=1, sample 부족 |
| HYPO-014-BLEAD | BinanceLeadSignal | Round 15 — n=1, 0% win, vol 미달 |
| HYPO-016-OFI | OFIMomentum | Round 15 — n=37, win 24%, -$3.92 사전 trigger |
| HYPO-017-CASCADE | BTCCascade | Round 15 — n=0, 60분 trigger 0 |
| HYPO-025 | VolumeDeltaDivergence | **Phase 2N+** — n=6 win 33%, avg_size $687, -$3.76 (fast_fail trigger + dynamic sizing over-bet) |
| HYPO-029 | StochRSI | **Phase 2M** — 학술 근거 없음 (basic indicator) |
| HYPO-030 | ADXTrendPullback | **Phase 2M** — 학술 근거 없음 (basic combo) |
| HYPO-031 | OBVDivergence | **Phase 2M** — 학술 근거 없음 (basic indicator) |

## ⚠️ Watch List

- 모태 `data/edge_calibration.json` 등 학습값 4개 → Phase 1에서 60_alpha로 추출
- 모태 demo WS URL `wss://wsuspap.okx.com:8443` 위험 → Phase 2 코드 작성 시 live URL 교체
