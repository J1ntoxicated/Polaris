---
entity_type: hypothesis
entity_id: HYPO-020
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-031]]", "[[ADR-014]]"]
mode: alpha
reviewed_by: code-implementer
maturity: paper-active
tags: [type/hypothesis, status/active, scope/alpha, priority/p1, polaris]
---

# HYPO-020 — VolumeBurst AND DonchianBreakout (VB-DONCH Combination)

> Phase 2h combination grid 발굴. DOGE-USDT 1D viable (OOS +2.78%). ORDI-USDT 1D viable but outlier-driven.
> **2026-05-04 Phase 2h**: cron.py ACTIVE_HYPOS 등록 완료 — paper trading 시작.

## Hypothesis

VolumeBurst(20) AND DonchianBreakout(20,10) 동시 만족 시 ENTER_LONG → positive EV.

- VolumeBurst: 거래량 급증 + 양봉 (모멘텀 신호)
- DonchianBreakout: 20일 채널 돌파 (추세 확인)
- 두 신호 orthogonal → AND 조건 = false positive 감소

## 전략 사양 (cron 실제 운영 파라미터)

```python
ConfluenceSignal(
    sub_strategies=[
        VolumeBurst(),                   # vol_period=20, vol_mult=2.0 (default)
        DonchianBreakout(40, 15),        # entry=40 (2-month high), exit=15 (3-week low)
    ],
    require_all=True,   # AND logic
    target_size_usd=200.0,
)
```

- **Timeframe**: 1D (1H/4H는 fee 과다 → 1D만 viable, INSIGHT-031)
- **Active ticker**: DOGE-USDT (moderate confidence)
- **Pending**: ORDI-USDT — outlier risk, 별도 검증 후 추가
- **min_window**: 41 (DonchianBreakout(40,15) 지배)
- **max_position_pct**: 0.04 (DOGE 단일 ticker 집중)
- **cron entry**: `ACTIVE_HYPOS` (src/paper/cron.py) — 2026-05-04 등록

### 설계 노트: backtest vs cron 파라미터 차이

backtest grid는 `DonchianBreakout(20, 10)` 기준이었으나, cron 등록 시 더 conservative한 `DonchianBreakout(40, 15)` 채택.
근거: 더 긴 lookback = 더 강한 추세 확인 = false positive 추가 감소 (fee 0.7%/side OKX SPOT).
실측 결과로 40/15 vs 20/10 비교 데이터 누적 예정.

## Backtest 결과 (Phase 2h — 2026-05-04)

### DOGE-USDT 1D (moderate confidence)

| 구분 | n_trades | expectancy | Sharpe | MDD |
|------|----------|-----------|--------|-----|
| IS (80%) | 14 | +11.81% | 0.42 | 9.1% |
| OOS (20%) | 3 | +2.78% | — | — |

### ORDI-USDT 1D (low confidence — outlier risk)

| 구분 | n_trades | expectancy | Sharpe | MDD |
|------|----------|-----------|--------|-----|
| IS (80%) | 10 | +44.97% | 0.33 | 12.9% |
| OOS (20%) | 2 | +55.51% | — | — |

**주의**: IS 1개 outlier +435.4% (2023 ORDI inscription boom). 제거 시 IS mean +1.58%.

## 운영 계획

- [x] Paper trading: DOGE-USDT 1D cron 등록 완료 (2026-05-04, Phase 2h)
- [ ] ORDI-USDT: outlier risk 별도 검증 후 추가 (IS outlier 제거 시 EV +1.58% — 추가 backtest 필요)
- [ ] 실측 60일 후 재평가 → 승격 or archived (기준: OOS n >= 10 + Sharpe >= 0.3)

## Cron 실행 이력

| 날짜 | signal | 비고 |
|------|--------|------|
| 2026-05-04 | hold | 첫 실행 — VB/Donchian 조건 미충족 (정상 HOLD) |

## 관련

- [[HYPO-022-3WAY-NofM]] — N-of-M 변형 (비교용)
- [[HYPO-003-SMA]] — 기존 active 1D trend strategy (비교 기준)
- [[INSIGHT-031]] — grid 결과 상세
