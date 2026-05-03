---
entity_type: insight
entity_id: INSIGHT-003
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-002]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-003 — Edge Calibration Baseline (모태 인수)

> 모태 `data/edge_calibration.json` Bayesian Beta 학습값 분석. Polaris 60_alpha 첫 가설 후보 + MTTR-alpha control band baseline.

## Evidence (직접 측정)

총 132 cells (signal × regime × direction).

### Top 10 by sample size
| cell | n_samples | alpha | beta | WR (Beta mean) |
|---|---|---|---|---|
| macro_regime\|neutral\|0 | 14467 | 7351 | 7118 | 0.508 |
| funding\|neutral\|0 | 12631 | 6743 | 5890 | 0.534 |
| fear_greed\|neutral\|1 | 11882 | 6270 | 5614 | 0.528 |
| precomputed\|neutral\|0 | 9992 | 5348 | 4646 | 0.535 |
| ls_ratio\|neutral\|0 | 5804 | 3098 | 2708 | 0.534 |
| technical\|neutral\|0 | 5294 | 2772 | 2524 | 0.523 |
| taker\|neutral\|0 | 4349 | 2346 | 2005 | 0.539 |
| taker\|neutral\|1 | 3890 | 2052 | 1840 | 0.527 |
| price_action\|neutral\|2 | 3665 | 1926 | 1741 | 0.525 |
| price_action\|neutral\|1 | 3647 | 1846 | 1803 | 0.506 |

## 활용 (Polaris)

### 60_alpha 첫 가설 후보
- **HYPO-001**: `funding|neutral|0` (n=12631, WR=0.534) vs `taker|neutral|0` (n=4349, WR=0.539) — 큰 sample 두 cell 비교
- **HYPO-002**: `precomputed|neutral|0` vs `macro_regime|neutral|0` (모두 WR ~0.5 — fee 가정 후 expectancy 분석 필요)

### MTTR-alpha control band baseline ([[INSIGHT-002]])
- 각 cell의 Beta(α,β) → WR 분포 → control band μ±2σ 추출 가능
- σ는 Beta variance: `α·β / [(α+β)² · (α+β+1)]`

## Risk
- WR ~0.5는 **PnL 아님** — TP/SL ratio 가정 필요
- 모태 학습은 멀티 거래소 + perp 환경 — Polaris SPOT-only fee와 다름
- **[[INSIGHT-007]] (OKX SPOT fee 수학) 적용 후 재평가 의무** — fee × 2 < expected_TP 통과 못 하면 archived

## Recommendation
- [ ] Phase 2a: HYPOTHESIS-001 BACKTEST 시 fee 수학 fast-fail gate 적용
- [ ] Phase 4: 라이브 운영 시 cell별 control band 자동 갱신
- [ ] 132 cells 모두 Polaris vault에 가져오지 X — top 10만 활용 (메타 작업 한도)

## Related
- INSIGHT-002 (MTTR-alpha 정의)
- INSIGHT-007 (OKX SPOT fee 수학)
- 60_alpha/_README (워크플로)
- _INHERIT_QUEUE
