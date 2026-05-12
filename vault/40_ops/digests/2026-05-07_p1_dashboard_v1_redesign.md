---
type: digest
status: active
phase: P1
day: dashboard
date_created: 2026-05-07
tags: [digest, p1, dashboard, observability, ui, codex-review, trader-grade]
related: [[dashboard]], [[layer-2-per-gate-pipeline]], [[layer-4-cell-matrix]], [[layer-5-learner-network]], [[ADR-003]], [[feedback_dashboard_periodic_review]]
---

# P1 dashboard_v1 — trader-grade 220×55 redesign (10 panels)

## Why

Jin mandate 2026-05-07 19:43:
> "대시보드 저거 맞아? 저게 뭐 어쨌는데 저게 맞냐고 전문가 다 뭐하는데? 저거 맞아?"

`polaris/scripts/dashboard_v0.py` (Day 5 P0 minimum, 369 LOC) = single-row data dump:

- Daily PnL = R-units (USD 아님)
- Equity / cash / drawdown / Sharpe missing
- Per-strategy WR / PF / PnL / open count missing
- Positions: no uPnL / Δ% / last price / held time
- Gate funnel viz missing
- GPT cost missing
- Learner state bug ("no learner state yet" 인데 actually 1019+ rows persisted — column was `key_dims` but real schema is `key`)
- Recent fills 가 fill 단위 (trade 단위 X — net PnL 안 보임)

= 전문 트레이더 의사결정 도구 X.

## What changed

### Files

| File | LOC | Role |
|------|-----|------|
| `polaris/scripts/dashboard_v1.py` | 102 | Entry + 5s render loop |
| `polaris/scripts/dashboard/__init__.py` | 22 | Subpackage exports |
| `polaris/scripts/dashboard/ansi_palette.py` | 273 | Colors, sparkline, pad/vlen, hline, bar |
| `polaris/scripts/dashboard/snapshot.py` | 907 | DB → DashboardSnapshot (10 sections) |
| `polaris/scripts/dashboard/render.py` | 580 | 10 pure render funcs (220×55 fit) |
| `tests/test_dashboard_v1.py` | 488 | 25 tests (panels + grid + 3 pairing modes) |

Total: 5 source files + 1 test file = ~2370 LOC.

### Panels (10) — fixed 220×55 grid

| Row | Panel | Source |
|-----|-------|--------|
| 1 | Header banner + status | `render_header` |
| 2 | Top metrics: EQUITY/EXPOSED/uPnL/Daily/DD/Sharpe/Open/Cells/Focus | `render_top_bar` |
| 3-5 | 24h equity sparkline + min/max/range/Δ | `render_equity_panel` (288 buckets → 216 chars downsample) |
| 6-13 | Live positions (top 6 by uPnL desc, "+N more" overflow) | `render_positions_panel` |
| 14-22 | Per-strategy stats (7 strategies, WR-band + PF color) | `render_strategy_panel` |
| 23-31 | Gate funnel G1-G8 (1h, pass% bar) | `render_gate_panel` |
| 32-38 | Cell matrix TOP 5 by score (n_eff≥20) | `render_cell_top_panel` |
| 39-44 | Cell matrix BOTTOM 5 (warning band) | `render_cell_bottom_panel` |
| 45-49 | Regime heatmap (4 regimes incl crisis) | `render_regime_panel` |
| 50-53 | Recent closed trades (paired open/close, R/PnL/HELD/REASON) | `render_trades_panel` |
| 54 | Learners (3 P0 with 1h delta arrow) + GPT (calls/h, $/h, 24h proj) | `render_learner_gpt_row` |
| 55 | Alert log (last 1) + universe focus tail | `render_alert_row` |

### Codex review — 4 rounds

| Round | Verdict | Critical findings |
|-------|---------|-------------------|
| R1 | REJECT_WITH_FIXES | P0.1 trade pairing not multi-leg safe / P0.2 regime panel can't show 4 regimes / P0.3 CASH mislabel + P1×4 + P2×2 |
| R2 | REJECT_WITH_FIXES | dashboard ignored production `contribution_id` linkage (FIFO bucket cross-pairs same-bucket positions) |
| R3 | REJECT_WITH_FIXES | non-NULL `contribution_id` should not FIFO-fallback when matching open absent (would re-introduce cross-pair bug) |
| R4 | **APPROVE** | exact pairing rules + 3 regression tests cover all failure modes |

### Pairing rule (codex R3 → R4 contract)

```
contribution_id NOT NULL + exact open found  → pair exact
contribution_id NOT NULL + open ABSENT       → reconstruct from PnL (no FIFO fallback)
contribution_id NULL (legacy/smoke)          → FIFO bucket fallback
```

3 regression tests (`tests/test_dashboard_v1.py:426-540`):
- `test_recent_trades_pairing_uses_contribution_id_exact` — reversed FIFO ordering must still pair correctly
- `test_recent_trades_pairing_no_fifo_when_contribution_missing` — close@$130 with PnL=$30 + open A@$200 same-bucket → reconstruct entry=$100 NOT pair to A
- `test_recent_trades_pairing_is_fifo_safe_multi_leg` — legacy NULL contribution_id rows pair FIFO

### Other R1 fixes applied

- regime panel `max_rows` 3→4 + drop truncation (crisis count must always render)
- `cash_now` → `exposed_usd` (truthful "deployed notional" semantics, WARNING color when exposed>equity)
- per-strategy header `NOTIONAL$` → `OPEN_NOTNL$` + hline `(closed=24h, open/notnl=now)`
- R-unit columns labelled `AVGR$10` / `R$10` to surface the $10/R heuristic
- trade header `EXIT` (last col) → `REASON`
- ANSI docstring updated: cursor-home only (no clear-down at runtime)

### v0 → v1 cutover

- `scripts/start_dashboard.sh` updated: target = `polaris.scripts.dashboard_v1`, default refresh 5s
- v0 file kept (`dashboard_v0.py`) — tests still pass (28 v0+v1 combined)
- WORK / OFFHOURS profile + monitor coordinates unchanged (모태 invasion start.sh pattern)

## Verification

- 25/25 dashboard_v1 tests pass · 470/470 full suite (ex. heavy integration) pass
- ruff clean · mypy --strict 5 source files clean
- live render against `data/polaris.sqlite`: 55 rows × 220 chars exact (vlen-checked)
- top bar shows EQUITY $3.6K / EXPOSED $2.3M / uPnL -$8.4K / DD -71% / Sharpe -0.73 / Open 1683 / Cells 8 / Focus 34890

## Inspiration / references

모태 (auto_invasion_mk1) `invasion/dashboard/`:
- `intel_pipeline_funnel.py` — funnel pass-rate viz pattern
- `ansi.py` — sparkline / pad / hline / vlen helpers (220-col discipline)
- `intel_strat_perf.py` — per-strategy WR/PF color bands

Open-source pro dashboards: Hummingbot terminal, Freqtrade `freqtrade trade` UI, Bloomberg POMS — all ground the 10-panel selection.

## Trader-grade gaps remaining (future P1+)

Codex R1 P1 list (deferred to follow-up Phase P2):
- gross / net exposure split + venue concentration
- fees / slippage rolling totals
- stale-data age per feed
- realized vs unrealized split per strategy
- reject / fill counts per venue
- capital utilization / margin headroom

These would require additional rows beyond the 55-row budget; scheduled for `dashboard_v2` after live-trading green-light decision.

## Real-data signal

Dashboard exposed real architectural debt previously hidden by v0:
- 1683 open positions persisted, $2.3M deployed notional vs $3.6K equity ≈ 640× gross leverage
- DD -71%, Sharpe -0.73 over 24h
- crisis regime non-zero (2 groups, 3.3%)

Actionable for Day 8 P0 wiring fixes (A2 AllocatorFence + A6 ingest persistence).

## Related

- [[ADR-003]] §Unified Schema — `fills.contribution_id` is the position link key
- [[layer-4-cell-matrix]] — top×1.5 / mid×1.0 / bottom×0.5 quartile mult logic
- [[layer-5-learner-network]] — 3 P0 learners (session_mult / regime_mult / max_hold)
- [[feedback_dashboard_periodic_review]] (memory) — 코드 변경마다 dashboard 영향 review 의무
- [[active-autonomous-vision]] — 전문 트레이더가 매 5초 대시보드 보면서 의사결정 가능해야 함

## Run

```bash
# default 5s refresh
./scripts/start_dashboard.sh

# inline (current terminal)
./scripts/start_dashboard.sh --inline

# CLI direct
python3 -m polaris.scripts.dashboard_v1 --refresh 5 --width 220 --db data/polaris.sqlite
```
