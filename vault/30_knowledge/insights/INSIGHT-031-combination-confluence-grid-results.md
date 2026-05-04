---
entity_type: insight
entity_id: INSIGHT-031
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[INSIGHT-030]]", "[[ADR-014]]", "[[HYPO-020]]", "[[HYPO-022]]"]
mode: alpha
reviewed_by: code-implementer
maturity: fresh
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-031 — Combination Confluence Backtest Grid Results (Phase 2h)

> Phase 2h 결론: 단일 indicator 음수 EV → AND/N-of-M combination으로 일부 viable 알파 발굴.
> **4 viable** (IS Sharpe >= 0.3 + EV > 0 + OOS EV > 0): DOGE/ORDI 1D candidates.

## Context

- HYPO-009/011/012/016 deprecated 후 "scalp 불가" 가설 정정 (Jin mandate 2026-05-04).
- 실제 답: combination/multi-signal confluence (단일 indicator 단독 → 음수 EV 증명됨).
- VolumeBurst (55% win) + Donchian (trend filter) 조합 → viable 알파 가능성.

## Grid 설계

- **Tickers**: BTC-USDT, ETH-USDT, SOL-USDT, DOGE-USDT, ADA-USDT, ORDI-USDT
- **Timeframes**: 1H, 4H, 1D
- **Combinations**: 6 (HYPO-018~023)
- **Total runs**: 108
- **Fee**: 0.0014 round-trip (INSIGHT-007 OKX paper Lv1)
- **Walk-forward**: 80% IS / 20% OOS split
- **Viable filter**: IS Sharpe >= 0.3 AND IS expectancy > 0 AND n_trades >= 10 AND OOS expectancy > 0

## 핵심 결과

### Viable (IS + OOS 양수 EV 4개)

| HYPO | Ticker | TF | IS n | IS exp | IS Sharpe | IS MDD | OOS n | OOS exp |
|------|--------|----|------|--------|-----------|--------|-------|---------|
| HYPO-020-VB-DONCH | ORDI-USDT | 1D | 10 | +44.97% | 0.33 | 12.9% | 2 | +55.51% |
| HYPO-022-3WAY-NofM | ORDI-USDT | 1D | 10 | +46.37% | 0.34 | 6.2% | 2 | +55.51% |
| HYPO-020-VB-DONCH | DOGE-USDT | 1D | 14 | +11.81% | 0.42 | 9.1% | 3 | +2.78% |
| HYPO-022-3WAY-NofM | DOGE-USDT | 1D | 15 | +10.85% | 0.40 | 9.1% | 3 | +2.78% |

### ORDI 주의사항

- IS 10 trades 중 1개 outlier (+435.4%: entry=10.13 → exit=54.26, 2023 inscription boom 시기).
- outlier 제거 시 IS mean = +1.58% (크게 축소) — ORDI는 outlier-driven alpha.
- OOS n=2 (매우 적음) → 통계적 신뢰도 낮음.
- **결론**: ORDI 결과 = low-confidence viable. 추가 확인 필요.

### DOGE 신뢰도 분석

- IS n=14, OOS n=3 — ORDI보다 trade 수 많음.
- IS/OOS 모두 양수 EV (11.81% / 2.78%).
- Sharpe 0.40~0.42 (기준 0.3 상회).
- MDD 9.1% (관리 가능).
- **결론**: DOGE-USDT 1D HYPO-020/022 = moderate confidence viable.

## 패턴 분석

1. **1D timeframe 독점**: 모든 viable이 1D. 1H/4H는 fee 부담 + false positive 과다.
2. **VB-DONCH 조합 우위**: VolumeBurst (volume momentum) + DonchianBreakout (channel 확인)
   = 두 orthogonal 신호 조합 효과. VB 단독 55% win-rate + Donchian trend filter.
3. **SMA-DONCH (HYPO-019) 부진**: death cross + channel breakout 타이밍 불일치.
   양수 EV는 있으나 OOS 확인 불가 (n_trades < 10 or OOS EV 음수).
4. **N-of-M 3-way (HYPO-022) vs AND 2-way (HYPO-020)**: 거의 동일 결과.
   N-of-M = trade 수 소폭 증가, IS EV 소폭 상이, MDD 22번이 유리 (6.2% vs 12.9% ORDI).

## 실행 결정

- **즉시**: HYPO-020-VB-DONCH DOGE-USDT 1D 페이퍼 트레이딩 추가 (HYPO-003/004 portfolio 확대).
- **관찰 후**: ORDI-USDT 1D — outlier risk awareness 후 소규모 paper (6개월 후 재평가).
- **HYPO-018/019/021/023**: 모두 OOS 음수 EV or n_trades 미달 → 추가 개발 보류.

## 다음 HYPO 방향 (아직 미탐구)

- BTC dominance filter (시장 전체 방향 + 개별 confluence)
- Funding rate filter (perpetual market 신호를 spot filter로)
- VolumeBurst vol_mult 최적화 (2.0 → 2.5~3.0 변형)
- Exit 로직 강화 (TP/SL band 대신 trailing stop)

## 코드 산출물

- `src/strategies/confluence_signal.py` — ConfluenceSignal meta-strategy (P6 pure)
- `scripts/backtest_combinations.py` — 108-run grid backtest script
- `tests/strategies/test_confluence_signal.py` — 23 unit tests (all GREEN)

## 링크

- [[ADR-014]] — Polaris alpha portfolio v1 (portfolio 확장 결정)
- [[INSIGHT-020]] — HYPO-004 Donchian walk-forward robust
- [[INSIGHT-025]] — fee 0.0014 fix 적용 backtest
- [[HYPO-020-VB-DONCH]] — viable candidate note
