---
entity_type: live_dashboard
entity_id: now
auto: false
last_modified: 2026-05-04  # Codex Round 17 — drawdown_pct docstring + _KELLY_COLD_START 명확화 + ADR-015 provisional
expires: never
editable: true
back_links: ["[[INDEX]]", "[[log]]"]
mode: meta
reviewed_by: jin
tags: [meta, live, dashboard, polaris, bootstrap]
---

# _NOW — Live Diagnostic Dashboard

> **세션 시작 시 이 파일부터 read** — Polaris 현재 상태 + 진단 진입점.

## 현재 상태 (2026-05-04 — Phase 2N+ Emergency Fix: HYPO-025 cut + cold start cap)

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

### Realtime (tick-driven) — Phase 2M (10개)

| HYPO | Strategy | Status |
|---|---|---|
| HYPO-007-RT | RSI15m intraday | active — cron-style rare trigger |
| HYPO-008-RT | VolumeBurst 1H | active — n=29 win 55% +$3.50 유일 양수 EV |
| HYPO-023 | LiquidationCascade | active — Binance perp forceOrder → OKX SPOT |
| HYPO-024 | CrossExchangeGap | active — Binance bookTicker lead |
| HYPO-027 | FundingRateFilter | active — Binance funding squeeze |
| HYPO-028 | TickBurst | active — 5s price spike |
| HYPO-032 | TSMOM | **Phase 2M NEW** — Moskowitz 2012 JFE, 1d/7d/30d return |
| HYPO-033 | VPINToxicity | **Phase 2M NEW** — Easley 2012 RFS, VPIN > 0.7 |
| HYPO-034 | BTCDominanceLag | **Phase 2M NEW** — Stalder 2025 + Liu 2022 |

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
