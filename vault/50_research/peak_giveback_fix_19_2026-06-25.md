---
type: digest
status: active
date_created: 2026-06-25
tags: [exit, peak-protect, give-back, flow_not_block, asymmetry, demo-paper]
---

# Peak give-back fix (#19) — 2026-06-25

DEMO/PAPER · aggressive · flow_not_block · asymmetry preserved. Branch
`fix/peak-giveback-19`, commit `87fd1db` (off main `c237370`, on the deployed
`f4a499a` reversion-scalp ruler).

## Measured (76 closed trades, post-reset live ledger)

- avg peak `mfe_r` +0.241 (max +1.849); avg recorded `pnl_r` -0.0008.
- Reach rungs: +0.30R 32.9% · +0.45R 18.4% · +1.0R only 7.9%.
- By close reason (dollars): `scalp_target` +$20.3 (avgMFE +0.45) · `atr_trail_stop`
  +$18.7 (avgMFE +0.97) — winners DO bank positive **$**. The bleed is
  `scalp_stop` -$65 + `thesis_cut` -$27 (38 near-zero-MFE small-loss cuts).

## Key insight — dual-ruler artifact

"peak 100% give-back" measured as `(mfe_r - pnl_r)/mfe_r ≈ 1.0` is LARGELY a
measurement artifact: `mfe_r` is per-trade ATR-R (`pnl_usd / risk_usd`), recorded
`pnl_r` is stream-common R (`pnl_usd / (0.5% × equity ≈ $5-8)`). A +0.45R-ATR
harvest of +$1.35 reads as +0.0013 stream-R. The winners are not actually
round-tripping in **dollars** — but the protection still armed too high.

## Fix (profit-side only; loss rails untouched)

1. **Peak-fraction arm 1.0 → 0.45R** (`EXIT_PEAK_LOCK_ARM_R` +
   `_TICK_PEAK_LOCK_ARM_R`). 0.45R = fee-safe common-case rung (frac 0.50 → locks
   ≥ +0.225R), 2.3× the +1.0R coverage. Floor ratchets UP with the peak → winner
   still runs (not a cap).
2. **Reversion `scalp_giveback`** (micro_reversion = 63% of closes, had NO
   peak protection entry→0.35R target). Engine tracks running scalp peak
   (`scalp_peak_r_by_position`); once peaked past `_SCALP_PEAK_ARM_R` (0.25R) and
   surrenders below `peak × 0.60` while above the fee-safe floor
   `_SCALP_PEAK_MIN_BANK_R` (0.10R), bank instead of round-tripping.

## Before / after peak-protect

| peak `mfe_r` | before (arm 1.0) | after (arm 0.45) |
|---|---|---|
| +0.39R (avg) | nothing locked → 100% give-back on wide trail | floor at entry + 0.39×0.5 = **+0.195R locked** |
| +0.50R | nothing locked | **+0.25R locked** |
| +0.30R reversion | no floor → scalp_stop/flow_reversal ~0 | give-back banks ~+0.10-0.18R |

## Verification

- TDD `tests/test_peak_giveback_fix_19.py` (13: small-peak lock, winner-still-runs,
  reversion give-back, fee-safe floor, loss-rail-untouched, legacy byte-id) +
  `test_letrun_peak_fraction_wiring.py` literals 1.0→0.45. mypy --strict + ruff clean.
- Fresh adversarial sub-agent review: **APPROVE** (asymmetry / flow_not_block /
  9-stack / composition / fee-safety all pass; fee-safe floor added per its finding).
- All env-tunable; `peak_r` omitted disarms the give-back (byte-identical).
