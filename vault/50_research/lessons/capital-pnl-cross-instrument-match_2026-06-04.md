---
type: lesson
date_created: 2026-06-04
tags: [pnl, capital, cfd, accounting, fills, contribution_id]
status: diagnosed
related: [[MOC-A1-design-dev]], [[zombie_close_session_gate_wrong_predicate_2026-06-04]]
---

# Capital CFD pnl_usd inflated 10^5x — cross-instrument entry match

## Symptom
Dashboard `daily_pnl=$147,973`, individual close `pnl=$145,424.63`, `mfe_r` garbage.
DEMO start capital ~$10K. Capital close-fills SUM(pnl_usd)=$148,489.98; only **3**
of 79 closes are inflated — they carry the entire garbage.

## Root cause (NOT pip/contract/lot)
`real_pnl_r_from_fills` matches the entry fill by
`WHERE contribution_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1`
**with no `instrument_id` filter**. The `position_id` `pos_tick_micro_rever_<ts>`
is derived from a tick timestamp and is **reused across 2-3 instruments**. So a
J225 close (entry ~68514) cross-matched an OIL_CRUDE entry (price 94.168) and used
94.168 as `entry_price`. `pnl_usd = (pnl_abs/entry_price) * size_usd` then divides
the J225 price delta by a 728x-too-small denominator.

## Hand-calc evidence (cid `pos_tick_micro_rever_1780469009`)
- WRONG: `(68565.9-94.168)/94.168 * 200 = 145,424.63` → exactly the stored value.
- CORRECT (J225 entry 68514.3): `(68565.9-68514.3)/68514.3 * 200 = $0.15`.
- Inflation **~965,000x** (entry_price ratio 728x).
- Filtering `AND instrument_id='capital:J225'` returns entry 68514.3 → $0.15. ✓

The pip_value × leverage `size_usd` math is fine here (size_usd=200 is sane);
the bug is purely the entry-fill JOIN crossing instruments.

## Fix (add `AND instrument_id = ?`, bind `f"{trade.venue}:{trade.symbol}"`)
Same instrument-less matcher in 4 spots — all need the filter:
- `polaris/scripts/_production_close_helpers.py:62` (real_pnl_r_from_fills, primary)
- `polaris/scripts/_production_close_helpers.py:155` (_close_excursion_r → mfe_r)
- `polaris/scripts/_production_close_effects.py:219`
- `polaris/scripts/_production_recalc.py:131`

Deeper fix (root): make `position_id` instrument-unique at creation
(`_production_pipeline.py:416` already appends `_{now_ts}`, but the
`pos_tick_micro_rever_` path reuses one id per tick across instruments).
Adding the `instrument_id` filter is the surgical, sufficient correctness fix.
