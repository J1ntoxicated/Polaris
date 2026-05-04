---
entity_type: hypothesis
entity_id: HYPO-016
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-023]]", "[[ADR-004]]"]
mode: alpha
reviewed_by: codex
maturity: active
tags: [type/hypothesis, status/active, scope/alpha, polaris]
---

# HYPO-016 — Order Flow Imbalance (OFI) Momentum

> OFI signed volume cumulation + VWAP price confirmation 이 HYPO-012 TradeFlow count ratio 대비 lagging을 회피하고 positive expectancy를 달성한다.

## Hypothesis (가설)

- H_0: OFI signed volume + VWAP confirm는 HYPO-012 대비 우위가 없다
- H_1: OFI_norm > 0.40 AND last > vwap * 1.0005 필터가 fee 0.0014 통과 후 expectancy > 0

## Rationale (근거)

- HYPO-012 실패 원인: taker_buy_count / total_count (naive) — volume 가중치 없음, 지연 신호
- OFI: signed volume 합산 (size-weighted) → buy-aggressor 실제 물량 반영
- VWAP confirmation: 가격이 volume-weighted 중심 위로 상승 시만 진입 → lagging 신호 차단
- 근거 논문: Chordia et al. 2021 — order flow momentum alpha (SSRN)
- Codex Round 11 합의: 72% (VPIN 대신 OFI 채택)

## Method (검증 방법)

### Paper (실시간)
- WebSocket OKX SPOT trades channel (최근 50 trades)
- Tickers: BTC/ETH/SOL/DOGE/PEPE/ORDI
- Fee: 0.0014 (round-trip)
- Code: src/strategies/ofi_momentum.py
- TP/SL: +0.6% / -0.35% (runner default)
- Entry: ofi_norm > 0.40 AND last > vwap * 1.0005
- Exit: ofi_norm < -0.20 OR signal_exit (min hold 90s)

### Pass 기준
- n >= 50 trades (충분한 샘플)
- EV > 0 (post-fee expectancy)
- Hit rate >= 45%

## Fast-fail Gate

- fee 0.0014 round-trip × 2 = 0.0028 — TP 0.6% 대비 충분한 margin
- OFI_norm 0.40 threshold: 4:1 buy:sell volume ratio 이상만 진입

## Promotion Gate (PAPER → ADR)

- [ ] 50+ trades paper 결과
- [ ] EV > 0 확인
- [ ] Ticker별 성과 분석
- [ ] vs HYPO-012 직접 비교

## Results

- Backtest: (미실시 — realtime-only 전략, 틱 데이터 필요)
- Paper: 2026-05-04 시작, Round 12에서 24h 결과 집계
- Promotion 결정: TBD

## Related

- HYPO-012: [[60_alpha/active/HYPOTHESIS-012-trade-flow]] (모태 실패 전략)
- INSIGHT-023: [[INSIGHT-023]] (HYPO-011/012 deprecate 근거)
- ADR-004: [[ADR-004]] (외부 코드 리뷰 의무)
- Code: src/strategies/ofi_momentum.py
- Tests: tests/strategies/test_ofi_momentum.py
