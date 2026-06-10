---
type: component
status: active
phase: P1
date_created: 2026-05-07
tags: [component, dashboard, observability, ui, polaris]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[layer-2-per-gate-pipeline]], [[layer-4-cell-matrix]], [[layer-5-learner-network]], [[2026-05-07_p1_dashboard_v1_redesign]]
---

# Dashboard component spec — `polaris.scripts.dashboard_v1`

## Purpose

Real-time 220×55 ANSI trading terminal so a trader can scan every paper-loop tick (5s refresh) and decide: pause / scale / kill / inspect.

## Architecture

```
polaris/scripts/
├── dashboard_v1.py              # entry + 5s render loop
└── dashboard/
    ├── __init__.py              # re-exports
    ├── ansi_palette.py          # color, sparkline, pad/vlen, hline, bar
    ├── snapshot.py              # DB → DashboardSnapshot dataclass
    └── render.py                # 10 pure render funcs (220×55)
```

**Layering** (one-way):
```
dashboard_v1.py  →  render.py  →  snapshot.py  →  data/polaris.sqlite (read-only)
                       ↓              ↓
                   ansi_palette.py  (shared util)
```

Pure render functions = unit-testable, no DB or time side effects.
`collect_snapshot()` does ALL DB queries in one read-only transaction.

## DashboardSnapshot fields

| Field | Type | Source |
|-------|------|--------|
| `ts_now` | int | `time.time()` |
| `starting_capital` | float | **Day 9 F12 SSOT**: total demo equity = OKX $79K + Capital $51K = $130K (`polaris.core.sizing.constants.demo_starting_equity_total()`). Env: `POLARIS_DEMO_STARTING_EQUITY_TOTAL`. |
| `starting_capital_okx` | float | OKX SPOT demo $79K (env `POLARIS_DEMO_STARTING_EQUITY_OKX`). Renderer top-bar split. |
| `starting_capital_capital` | float | Capital CFD demo USD-equivalent $51K (A$78K @ ~0.654). Env `POLARIS_DEMO_STARTING_EQUITY_CAPITAL`. |
| `equity_now` | float | starting_capital + cum realised PnL + Σ uPnL |
| `exposed_usd` | float | Σ open positions notional (renamed from `cash_now` — codex P0) |
| `upnl_total` | float | Σ (last_price - entry_price) × qty × side_sign |
| `daily_pnl_usd` | float | Σ pnl_usd from fills.is_close=1, last 24h |
| `daily_trades` | int | count of closed fills, last 24h |
| `drawdown_pct` | float | running_max(equity) - now / running_max × 100 |
| `peak_equity` | float | running_max(equity) |
| `sharpe_24h` | float | mean(returns) / std(returns) × √N from 5-min buckets |
| `open_positions_n` | int | rows in positions WHERE status NOT IN ('closed','cancelled') |
| `active_cells_n` | int | rows in cell_matrix_p0 WHERE n_eff ≥ 20 |
| `universe_focus_n` | int | rows in watchlist_focus |
| `equity_curve` | list[float] | 288 5-min buckets + live point |
| `positions` | list[PositionRow] | sorted by uPnL desc |
| `strategy_stats` | list[StrategyStat] | per-strategy WR / PF / PnL / notional |
| `gate_funnel` | list[GateRow] | 8 gates × pass / kill counts (last 1h) |
| `cell_top` / `cell_bottom` | list[CellRow] | top 5 + bottom 5 by score |
| `regime_bars` | list[RegimeBar] | chop / bull_trend / bear_trend / crisis counts |
| `recent_trades` | list[ClosedTrade] | paired open→close trades, last 10 |
| `learners` | list[LearnerSlot] | 3 P0 (session_mult / regime_mult / max_hold) with 1h delta |
| `gpt_stats` | list[GptStat] | calls/h + cost/h per model |
| `alerts` | list[AlertRow] | risk_events + strategy_fault_events tail |

## Layout (220×55, 10 panels)

| Rows | Panel |
|------|-------|
| 1 | Header banner |
| 2 | Top metrics bar (EQUITY / EXPOSED / uPnL / Daily / DD / Sharpe / Open / Cells / Focus) |
| 3-5 | 24h equity sparkline (216-char) + min/max/range/Δ |
| 6-13 | Live positions (top 6 + overflow hint) |
| 14-22 | Per-strategy stats |
| 23-31 | Gate funnel G1-G8 |
| 32-38 | Cell matrix TOP 5 |
| 39-44 | Cell matrix BOTTOM 5 |
| 45-49 | Regime heatmap (4 regimes incl crisis) |
| 50-53 | Recent closed trades |
| 54 | Learners + GPT cost (combined) |
| 55 | Alert log + universe focus |

## Color discipline

| Element | Code | Use |
|---------|------|-----|
| BORDER | `\x1b[38;5;242m` (grey42) | hline, separators (Jin "톤다운 회색" mandate) |
| POSITIVE | `\x1b[38;5;114m` (pastel green) | profit, long, WR≥55, PF≥1.5 |
| NEGATIVE | `\x1b[38;5;174m` (pastel red) | loss, short, WR<40, PF<1.0 |
| WARNING | `\x1b[38;5;186m` (pastel yellow) | DD, exposed>equity, WR mid, REASON=TIME |
| HIGHLIGHT | `\x1b[38;5;117m` (pastel cyan) | header, key numbers |
| INFO | `\x1b[38;5;110m` (pastel blue) | sparkline, GPT cost |
| NEUTRAL | `\x1b[38;5;248m` (light grey) | label text |
| MUTED | `\x1b[38;5;240m` (darker grey) | secondary, dim hints |

## ANSI rendering rules

- Cursor home only (`\x1b[H`) — no clear-down at runtime, no flicker
- Fixed 55-row height = next frame overwrites previous (no row remnants)
- `pad()` is ANSI-aware + CJK-aware (vlen counts W/F as 2 cells)
- `vlen()` strips ANSI escapes for true visible-width measurement
- `hline(label, width)` produces `─── LABEL ───────...` with grey42 border + cyan label

## Trade pairing contract (codex round 4 APPROVE)

```python
# Sweep all fills chronologically, key by contribution_id where present.
contribution_id NOT NULL + exact open found  → pair exact
contribution_id NOT NULL + open ABSENT       → reconstruct entry from PnL
                                               (do NOT FIFO-pair, prevents cross-pair)
contribution_id NULL (legacy / smoke)        → FIFO per (venue, inst, strat)
```

Production write path always sets `fills.contribution_id = position_id` for both
open and close legs (`_production_pipeline.py:198`, `_production_close.py:155`).

## Operating contract

- Read-only against `data/polaris.sqlite` (file:?mode=ro)
- 5s default refresh — query latency < 200ms (single connection, no joins on indexes)
- Crashes only if DB file unreadable — missing tables / empty rows return zero-snapshot
- v0 retained alongside (`dashboard_v0.py`) for rollback
- Launch via `scripts/start_dashboard.sh` (WORK / OFFHOURS profile auto-detect, monitor bounds)

## Tests

`tests/test_dashboard_v1.py` (25 tests):

- panel-level (10): top bar / sparkline / positions sort / per-strat WR-band / gate funnel /
  cell top / cell bottom / regime 4-row / trades pair / learners delta / GPT projection / alert log
- grid invariants: 220 char per row, 55 row exact, zero-snapshot fallback
- DB-backed: empty / missing / paired-fill PnL
- 3 pairing-mode regressions: contribution_id exact / contribution_id missing → reconstruct /
  legacy NULL contribution_id FIFO

## Future P2

- gross/net exposure split, venue concentration
- fees/slippage rolling totals
- stale-data age per feed
- realized vs unrealized split per strategy
- capital utilization / margin headroom (requires venue balance probe)

## Run

```bash
./scripts/start_dashboard.sh                  # auto-launch right monitor
./scripts/start_dashboard.sh --inline         # current terminal
python3 -m polaris.scripts.dashboard_v1 --refresh 5 --once  # single frame
```

## References

- Digest: [[2026-05-07_p1_dashboard_v1_redesign]] — codex 4-round review trace
- Mother project (auto_invasion_mk1) `invasion/dashboard/sections/` — 53 panel patterns inspiring layout & color
- ADR-003 — `fills.contribution_id` schema link
