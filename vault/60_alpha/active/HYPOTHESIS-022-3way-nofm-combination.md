---
entity_type: hypothesis
entity_id: HYPO-022
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-031]]", "[[HYPO-020]]"]
mode: alpha
reviewed_by: code-implementer
maturity: paper-candidate
tags: [type/hypothesis, status/active, scope/alpha, priority/p2, polaris]
---

# HYPO-022 — 3-Way N-of-M Confluence (VB + SMA(10,30) + Donchain(20,10))

> HYPO-020 변형: 3개 sub-strategy 중 2-of-3 agree → ENTER_LONG (N-of-M).
> DOGE/ORDI 1D viable. HYPO-020과 결과 거의 동일 — 3-way의 실질적 이점 제한적.

## Hypothesis

VolumeBurst(20) + SMACrossover(10,30) + DonchianBreakout(20,10) 중 2-of-3 동시 만족 → positive EV.

```python
ConfluenceSignal(
    sub_strategies=[
        VolumeBurst(vol_period=20),
        SMACrossover(fast=10, slow=30),
        DonchianBreakout(entry_period=20, exit_period=10),
    ],
    require_all=False,
    min_confluence=2,  # 2-of-3
    target_size_usd=200.0,
)
```

## Backtest 결과 vs HYPO-020

| HYPO | Ticker | TF | IS n | IS exp | IS Sharpe | IS MDD | OOS exp |
|------|--------|----|------|--------|-----------|--------|---------|
| HYPO-020 | DOGE | 1D | 14 | +11.81% | 0.42 | 9.1% | +2.78% |
| HYPO-022 | DOGE | 1D | 15 | +10.85% | 0.40 | 9.1% | +2.78% |
| HYPO-020 | ORDI | 1D | 10 | +44.97% | 0.33 | 12.9% | +55.51% |
| HYPO-022 | ORDI | 1D | 10 | +46.37% | 0.34 | **6.2%** | +55.51% |

**ORDI MDD**: HYPO-022 6.2% vs HYPO-020 12.9% — N-of-M의 유일한 실질적 이점.

## 결론

- HYPO-020과 사실상 동일한 alpha signal (OOS EV 같음).
- ORDI에서 MDD 절반 이점 있으나 sample 작음 (n=10).
- DOGE는 거의 차이 없음.
- 3개 sub로 복잡도 증가 → HYPO-020이 단순하고 충분.
- **우선순위**: HYPO-020 먼저, HYPO-022는 MDD 중요 시 대안.

## 관련

- [[HYPO-020-VB-DONCH]] — AND 2-way 기준 전략
- [[INSIGHT-031]] — grid 결과 상세
