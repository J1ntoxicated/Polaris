---
entity_type: hypothesis
entity_id: HYPO-001
auto: false
last_modified: 2026-05-03
expires: 2026-09-03
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-007]]", "[[INSIGHT-012]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: codex
maturity: draft
tags: [type/hypothesis, status/active, scope/alpha, polaris]
---

# HYPO-001 — BTC-USDT 1h RSI(14) Mean Reversion fee-aware

> Polaris 첫 알파 가설. ADR-010 워크플로 적용 (백테스트 fast-fail + 페이퍼 30일 병행).

## Hypothesis

**H₁**: BTC-USDT 1h candle RSI(14) mean reversion 전략 (oversold 30 long entry, overbought 70 exit)이 OKX SPOT paper Lv1 fee 1.4% round-trip 후 **expectancy > 0** (long-run net profit).

**H₀**: expectancy ≤ fee_round_trip (수학적 net 음수, INSIGHT-007 함정).

## Rationale

- 모태 INSIGHT-003 (edge_calibration 132 cells) — neutral regime cells WR ~0.5
- 모태 INSIGHT-004 (volatility_spike ELO 4391) — 단순 named strategy 검증 가능
- BTC-USDT = 가장 liquid pair (slippage 노이즈 최소)
- 1h timeframe = scalp 회피 (INSIGHT-007 fee 함정), longer hold
- RSI mean reversion = 문헌화된 검증 가능 메커니즘
- INSIGHT-012 한계: 90일/50 trades 통계 신뢰도 낮음 → 백테스트 = fast-fail만, 페이퍼가 진짜 검증

## Method (ADR-010 워크플로)

### Stage 1: BACKTEST (24h, fast-fail + sanity)

- 데이터: BTC-USDT 1h, OKX `/api/v5/market/history-candles` 최대 ~2160 candles (90일)
- Strategy: `RSIMeanReversion(period=14, oversold=30, overbought=70)`
- Fee: 0.014 round-trip (INSIGHT-007 OKX paper Lv1)
- Engine: `src/backtest/engine.py` (Pure P6, 68 tests pass)

**Pass 기준 (Promotion Gate, ADR-010 강화)**:
- expectancy > 0.014 (fast-fail)
- n_trades >= 30
- Sharpe >= 0.5
- win_rate >= 0.52
- max_drawdown <= 0.10

**Fail 처리**:
- fast-fail (expectancy <= fee) → archived + INSIGHT (ADR-009 PERP counter +1)
- 다른 기준 fail → archived + INSIGHT (HYPO-002 후보 작성)

### Stage 2: PAPER (백테스트 pass 시, 30일 병행)

- 인프라: Phase 2c (페이퍼 WS feed + simulated order book + position tracker — 작성 예정)
- 운영: 실시간 OKX SPOT WS, simulated fill, paper balance $5000
- 메트릭: live Sharpe / win_rate / MDD / expectancy
- 리스크 관리 (ADR-010): 단일 포지션 ≤ 2%, 일일 손실 ≤ 5%

**Pass 기준 (paper actual ≥ backtest 50%)**:
- paper Sharpe ≥ 0.3 (백테스트 0.5의 60%)
- paper expectancy > 0
- 30일 + n_trades >= 30

### Stage 3: Promotion Gate → ADR

paper pass 시:
- paper/live behavior diff audit (LESSON-002)
- sizing cap 명시
- kill criteria (MTTR-alpha control band 정의)
- rollback plan

## Fast-fail Gate (BACKTEST 24h 내)

INSIGHT-007 적용:
- Pre-condition: `expected_TP > fee × 2` (0.014)
- BACKTEST 결과로 실제 expectancy 측정
- 실패 시 즉시 archived (시간 절약)

## Results

(BACKTEST 실행 후 update)

### BACKTEST
- 실행 일시: TBD
- n_candles: TBD
- n_trades: TBD
- expectancy: TBD
- Sharpe: TBD
- win_rate: TBD
- max_drawdown: TBD
- Promotion Gate: pass / fail (이유)

### PAPER (백테스트 pass 시)
- TBD

## Promotion 결정

(BACKTEST + PAPER 결과 후)

## Related

- ADR-010 (Backtest + Paper parallel)
- INSIGHT-007 (OKX SPOT fee 수학)
- INSIGHT-012 (백테스트 신뢰도 한계)
- 60_alpha/_README (워크플로)
- src/strategies/rsi_mean_reversion.py
- src/backtest/engine.py

---

## ARCHIVED 2026-05-03

전체 RSI 파라미터 매트릭스 (RSI14/7/21 × 30/35/40 × 70/65/60) + 4 타임프레임 모두 fast-fail. 자세히는 [[INSIGHT-013]].
ADR-009 PERP counter: 1/5.
