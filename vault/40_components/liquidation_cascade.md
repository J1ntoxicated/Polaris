---
title: liquidation_cascade
pure: true
code_path: src/strategies/liquidation_cascade.py
test_path: tests/strategies/test_liquidation_cascade.py
hypo: HYPOTHESIS-023
phase: Phase 2k (2026-05-04)
status: active
tags: [pure, strategy, mean-reversion, liquidation, event-driven]
entity_type: component
entity_id: liquidation_cascade
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[INSIGHT-033]]", "[[HYPO-023]]"]
---

# liquidation_cascade

Liquidation Cascade Mean Reversion strategy — pure (P6).

## HYPOTHESIS-023

Binance perp 대규모 short 청산 직후 mean-revert 포착.

### Logic (`evaluate_cascade`)

1. `liq_pressure.total_usd >= min_total_usd` ($1M) — 충분한 cascade 규모
2. `liq_pressure.imbalance < imbalance_threshold` (-0.6) — short 청산 dominant
   - imbalance = (long_liq - short_liq) / total ∈ [-1, +1]
   - 음수 = short 청산 우세 → 강제 매수 → over-extension → mean-revert
3. `price_drop >= price_drop_threshold` (0.4%) — OKX panic 신호
   - price_60s_ago=None → drop guard 스킵
4. → `ENTER_LONG`

### Exit

- `total_usd < exit_total_threshold` ($300k) → pressure 완화 → `EXIT`
- TP/SL/max_hold: runner 담당

### SPOT-only 제약 (ADR-001)

long 청산 dominant (imbalance > -0.6) → downtrend 신호 → skip (HOLD).
ENTER_SHORT 미사용.

## Expected Edge

| Item | Value |
|---|---|
| Fee round-trip | 0.28% (OKX 0.14% × 2) |
| Expected revert | 0.5–2% |
| Net EV | +0.22% – +1.72% per trade |
| Frequency | 시간당 1–3 large liquidation |

## Backtest 한계

Binance historical `forceOrder` 미제공 → paper forward test only.

## Default Thresholds

| Param | Default | Rationale |
|---|---|---|
| `min_total_usd` | $1,000,000 | 충분한 cascade 크기 |
| `imbalance_threshold` | -0.6 | short 청산 60%+ dominant |
| `price_drop_threshold` | 0.4% | OKX panic signal minimum |
| `exit_total_threshold` | $300,000 | pressure 완화 기준 |
| `target_size_usd` | $200 | dynamic sizing 상한 |

## Links

- [[HYPOTHESIS-023]]
- [[binance_liquidation_ws]] (data source)
- [[btc_cascade]] (cascade 패턴 참조)
