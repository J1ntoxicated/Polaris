---
entity_type: insight
entity_id: INSIGHT-026
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[INSIGHT-025]]", "[[INSIGHT-007]]", "[[INSIGHT-015]]", "[[HYPO-005]]", "[[HYPO-001]]", "[[HYPO-002]]", "[[HYPO-006]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: fee 0.014 latent bug fix (INSIGHT-025) 후 BTC 1d 1800 candles + 1h 3000 candles 실 데이터 재backtest 실행. promotion_gate.py ADR-011 swing 기준 적용.
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-026 — fee fix 후 archived HYPO 재평가 결과 (2026-05-04)

## 배경

INSIGHT-025 forensic: `DEFAULT_FEE_ROUND_TRIP = 0.014` (10× 오류) → `0.0014` fix.
archived HYPO 판정 당시 fee 0.014 기준 fast-fail된 HYPO들이 fee 0.0014에서 viable한지 재검토.
보정 이론값: expectancy +1.26%p (= (0.014 - 0.0014) × 거래 수 × avg impact). 실측으로 검증.

## 재평가 데이터

- BTC-USDT 1D: 1800 candles (2021-05-31 → 2026-05-04, ~5년)
- BTC-USDT 1H: 3000 candles (2025-12-30 → 2026-05-04, ~4개월)
- Fee: 0.0014 (INSIGHT-007 OKX SPOT LV3+ taker 0.07% × 2)
- Promotion Gate: ADR-011 swing 기준 (Sharpe >= 0.3, MDD <= 50%, n_trades >= 15)

---

## HYPO-005 MACD Trend 1d — 재평가

### 결과

| Config | trades | hit | expectancy | Sharpe | MDD | ann_return | gate |
|---|---|---|---|---|---|---|---|
| MACD(12,26,9) fee=0.0014 | 72 | 34.7% | **+1.17%** | +0.1045 | 53.8% | +9.37% | FAIL |
| MACD(8,21,5) fee=0.0014 | 107 | 34.6% | **+1.13%** | +0.1225 | 47.8% | +17.63% | FAIL |
| MACD(20,50,9) fee=0.0014 | 46 | 41.3% | **+2.94%** | +0.2003 | 52.2% | +20.67% | FAIL |
| MACD(12,26,9) fee=0.014 (구 오류 시뮬) | 72 | 34.7% | **-0.09%** | -0.0081 | 63.9% | -9.11% | FAIL |

### 판정: ARCHIVED 유지

**fast-fail 통과** (expectancy > fee 0.0014): 모두 통과.

**Promotion Gate 실패 사유** (swing 기준, ADR-011):

| Config | Sharpe 기준(>=0.3) | MDD 기준(<=50%) |
|---|---|---|
| MACD(12,26,9) | FAIL (0.10) | FAIL (53.8%) |
| MACD(8,21,5) | FAIL (0.12) | PASS (47.8%) |
| MACD(20,50,9) | FAIL (0.20) | FAIL (52.2%) |

**모든 config Sharpe < 0.3** (swing min). fee fix로 expectancy는 음수→양수로 전환됐으나 Sharpe 미달.

### 근본 원인 (archived 사유 재확인)

MACD 1d는 빈번한 whipsaw (월 5-10회 신호) → 개별 트레이드 수익 분산 크고 Sharpe 낮음.
fee fix는 평균 expectancy를 개선하지만 신호 품질 자체 (hit rate 34-41%, Sharpe 낮은 분산) 는 변하지 않음.
→ **MACD 1d archived 유지 타당**. fee 보정으로 재활 불가.

---

## HYPO-001 RSI Mean Reversion 1h — 재평가

| Config | trades | expectancy | Sharpe | MDD | 판정 |
|---|---|---|---|---|---|
| RSI(14,30,70) 1H | 10 | **-0.99%** | -0.10 | 27.8% | FAIL |
| RSI(14,35,65) 1H | 17 | **-1.42%** | -0.27 | 31.3% | FAIL |
| RSI(7,30,70) 1H | 38 | **-0.24%** | -0.07 | 26.0% | FAIL |

**판정: ARCHIVED 유지**. fee 0.0014에서도 expectancy 음수. 단순 RSI mean reversion은 BTC 1h에서 edge 없음.

---

## HYPO-002 BB Breakout 1h — 재평가

| Config | trades | expectancy | Sharpe | MDD | 판정 |
|---|---|---|---|---|---|
| BB(20,2.0) 1H | 50 | **-0.0057%** | -0.004 | 13.7% | FAIL |
| BB(20,1.5) 1H | 64 | **+0.065%** | +0.039 | 11.5% | FAIL |
| BB(50,2.0) 1H | 26 | **-0.41%** | -0.22 | 18.5% | FAIL |

**판정: ARCHIVED 유지**. BB(20,1.5) 만 expectancy 소폭 양전이나 Sharpe 0.039 (scalp 기준 0.8 대비 극히 미달).

---

## HYPO-006 Ichimoku Tenkan/Kijun 1d — 재평가

| Config | trades | expectancy | Sharpe | MDD | 판정 |
|---|---|---|---|---|---|
| Ichimoku(9,26) | 42 | **+2.68%** | +0.147 | 48.3% | FAIL |
| Ichimoku(12,30) | 34 | **+2.04%** | +0.120 | 60.1% | FAIL |
| Ichimoku(20,60) | 18 | **+4.63%** | +0.181 | 40.9% | FAIL |

**판정: ARCHIVED 유지**. 모두 fast-fail 통과 + MDD 일부 개선이나 **Sharpe < 0.3 (swing min)** 일관 미달.

---

## 전체 패턴 정리

### fee fix 영향

| HYPO | 구 fee=0.014 expectancy | 신 fee=0.0014 expectancy | 변화 | 판정 변화 |
|---|---|---|---|---|
| HYPO-005 MACD(12,26,9) | +0.09% (기재) → 실측 -0.09% | +1.17% | +1.26%p | fast-fail 통과, Sharpe 여전 미달 → archived 유지 |
| HYPO-001 RSI(14,30,70) | 음수 | -0.99% | 소폭 개선 | archived 유지 |
| HYPO-002 BB(20,1.5) | 음수 | +0.065% | 양전 | archived 유지 (Sharpe 극미) |
| HYPO-006 Ichimoku(9,26) | +5.4% (기재, 의심) → 재실측 | +2.68% | 재실측 (구 값 의심) | archived 유지 |

### 핵심 학습

1. **fee fix = expectancy 보정, Sharpe 보정 X**: fee는 평균 수익을 올리지만 신호 noise(분산)는 동일 → Sharpe 개선 없음.
2. **archived HYPO 재활 기준**: fee fix 단독으로는 Sharpe < 0.3 문제 해결 불가. 추가 필터 (regime, volume 조건) 없이는 재활 불가.
3. **1d trend = long-cycle 신호만 viable** (INSIGHT-015 재확인): SMA(50,200), Donchian(40/15) 처럼 신호 빈도 낮은 것만 Sharpe 통과. MACD/Ichimoku 는 신호 너무 잦음.
4. **HYPO-006 기재 expectancy 재실측 이슈**: 원 노트 +5.4%/+3.9% 등은 다른 fee 또는 데이터 기간에서 측정된 것으로 추정. 현 1800 candles 기준 +2.68%로 하향 조정.

---

## 결론

**4개 archived HYPO 모두 archived 유지** — fee fix로 fast-fail 통과 전환이 있어도 Sharpe 기준 미달로 Promotion Gate 불통.

## 권고 Next Step

1. **dispatch 불필요** — HYPO-005/001/002/006 cron 재추가 X.
2. **MACD + regime filter 조합**: MACD가 fast-fail은 통과하므로 bull/bear regime 조건부 진입 필터 추가 시 Sharpe 개선 가능성 있음 (별도 HYPO-013 등으로 신규 등록 권고).
3. **다른 archived 기간 없음**: HYPO-001/002/006 는 기간 재실측에서도 동일 결론. 추가 재평가 대상 없음.

## 연결

- [[INSIGHT-025]] fee 0.014 latent bug fix (재평가 트리거)
- [[INSIGHT-015]] 1d viable 기준 (Sharpe 0.3 swing, SMA/Donchian만)
- [[INSIGHT-007]] OKX fee 함정 원본
- [[HYPO-005]] MACD 1d archived
- [[HYPO-001]] RSI mean reversion archived
- [[HYPO-002]] BB Breakout archived
- [[HYPO-006]] Ichimoku archived
