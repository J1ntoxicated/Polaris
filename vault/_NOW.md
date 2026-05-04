---
entity_type: live_dashboard
entity_id: now
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[INDEX]]", "[[log]]"]
mode: meta
reviewed_by: jin
tags: [meta, live, dashboard, polaris, bootstrap]
---

# _NOW — Live Diagnostic Dashboard

> **세션 시작 시 이 파일부터 read** — Polaris 현재 상태 + 진단 진입점.

## 🎯 현재 상태 (2026-05-04)

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

- [[INSIGHT-026]] archived HYPO 재평가 완료 — 4개 모두 archived 유지 (Sharpe 미달 일관)
- [[INSIGHT-025]] fee 0.014 latent bug 4건 fix 완료 — backtest 재실행 완료 (INSIGHT-026)
- [[INSIGHT-024]] HYPO-009 deprecate 근거 (n=16, EV -1.33%, TP<SL 비대칭) — Round 9
- [[INSIGHT-023]] HYPO-011/012 deprecate 근거 (n=336/450, EV 계산, signal_exit 구조) — Round 8
- [[INSIGHT-022]] Phase 2g Binance WS + MTA + Codex Round 4~7 fix 누적
- [[INSIGHT-021]] flip-flop fee bleed fix — Round 4 hysteresis + min hold + ticker-global cooldown
- [[ADR-013]] HARNESS Meta Mode 정착 — 모든 작업 mode dispatch
- [[ADR-004]] 코드 리뷰 codex 외부 의무 (Jin 2026-05-03 mandate)

## 🔥 Round 10 (2026-05-04)

**Fix 1 — HYPO-013 MTA HOLD 로깅**: `_eval_and_act` mta branch에 `[MTA-HOLD] {ticker} {reason}` INFO 로그 추가. 24h 후 too strict vs wrong logic 판단 근거 확보.

**Fix 2 — HYPO-014 Binance feed health check**: cross branch에 `[BLEAD-NOFEED]` WARN (rate-limit 5분/ticker) + `[BLEAD-HOLD]` INFO (rate-limit 1분/ticker) 추가. WS feed 미공급 vs threshold 미충족 구분 가능.

**TDD**: `test_mta_hold_logged` + `test_blead_nofeed_warn_rate_limited` RED→GREEN. **160/160 pass**.

**24h 분석 plan**: runner restart 후 logs/realtime.err에서 `grep -E "\[MTA-HOLD\]|\[BLEAD-NOFEED\]|\[BLEAD-HOLD\]"` → HOLD reason 분포 집계 → 2026-05-05 분석.

## 🟢 운영 중 (HYPO 활성)

| HYPO | Strategy | Status |
|---|---|---|
| HYPO-007-RT | RSI15m intraday | active |
| HYPO-008-RT | VolumeBurst 1H | active |
| HYPO-009-RT | BreakoutMomentum 1H | **DEPRECATED Round 9** — n=16, EV -1.33%, TP<SL |
| HYPO-010-TICK | TickMomentum tick | active |
| HYPO-011-BOOK | OrderBookImbalance book | **DEPRECATED Round 8** — n=336, TP 0, -$77.93 |
| HYPO-012-FLOW | TradeFlow flow | **DEPRECATED Round 8** — n=450, EV -0.22%, -$151.77 |
| HYPO-013-MTA | MTAConfluence mta | active, 60분 측정 중 |
| HYPO-014-BLEAD | BinanceLeadSignal cross | active, 60분 측정 중 |

## ⚠️ Watch List

- 모태 `data/edge_calibration.json` 등 학습값 4개 → Phase 1에서 60_alpha로 추출
- 모태 demo WS URL `wss://wsuspap.okx.com:8443` 위험 → Phase 2 코드 작성 시 live URL 교체
