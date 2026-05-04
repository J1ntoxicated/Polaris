---
entity_type: insight
entity_id: INSIGHT-023
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[INSIGHT-021]]", "[[INSIGHT-022]]", "[[ADR-004]]"]
mode: dev
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex Round 8 직접 측정 (n=336 / n=450, EV 계산, signal_exit 구조 분석). 95% 합의 — deprecate 결정.
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-023 — HYPO-011/012 Deprecate 근거 (Codex Round 8, 95% 합의)

## 측정 결과 (직접 데이터)

| HYPO | n | TP | signal_exit % | lifetime PnL | EV/trade |
|---|---|---|---|---|---|
| HYPO-011-BOOK (OrderBookImbalance) | 336 | 0회 | 99.7% | -$77.93 | < -0.20% |
| HYPO-012-FLOW (TradeFlow) | 450 | 9.8% | 90.2% | -$151.77 | -0.22% |

비교: HYPO-013-MTA = +$0.46/h → HYPO-011/012 대비 **200배 압도**.

## 구조적 실패 원인

### OrderBookImbalance (HYPO-011)
- TP 0회 (n=336) = 알파 zero. signal_exit 99.7% = 신호가 지속적으로 EXIT 방향 생성 → min_hold 90s 안에 exit 불가, TP/SL miss 후 signal_exit 99.7% close.
- book5 snapshot imbalance는 고빈도 노이즈 (단기 스냅샷, market maker 양방향 유동성 = imbalance 덧없음).
- 구조적: 0.14% round-trip fee에 대응할 alpha source 없음.

### TradeFlow (HYPO-012)
- TP 9.8% = fee threshold 29.5% 미달. EV = TP_rate × TP_pct - (1-TP_rate) × SL_pct - fee = 음수.
- taker_buy_ratio 100-trade window = lagging indicator (가격 reverse 후 신호 생성). INSIGHT-021에서 이미 식별된 패턴.
- ratio 비대칭 변경(TP 1.5× / SL 0.7×) 시뮬레이션: EV -0.22% → -0.21% (구제 불가).

## 결정: Deprecate (Rule)

**REALTIME_HYPOS에서 HYPO-011-BOOK + HYPO-012-FLOW 제거.**

- 전략 파일(`src/strategies/orderbook_imbalance.py`, `src/strategies/trade_flow.py`) **삭제 X** — 학습 아카이브 보존. deprecated comment + 코드 자체는 유지.
- Runner 재시작 후 해당 ticker 구독 감소 가능 (HYPO-013/014가 커버하는 ticker에 의존).

## TDD 증거

`tests/paper/test_realtime_runner.py::test_hypo_011_012_not_in_realtime_hypos` — `REALTIME_HYPOS`에서 두 hypo_id 부재 확인. **156/156 pass**.

## 후속 액션

1. Runner restart (HYPO-011/012 비활성화 즉시 적용)
2. 60분 wakeup schedule — HYPO-013 n>=10 측정 시작
3. HYPO-013/014 EV 양수 증명 목표 (post-fee 기준)

## 연결

- [[INSIGHT-021]] flip-flop fee bleed fix — min_hold/hysteresis 배경
- [[INSIGHT-022]] Phase 2g MTA + Binance WS spec (HYPO-013/014 활성화)
- [[ADR-004]] 코드 리뷰 codex 외부 의무 (Round 8 95% 합의 근거)
