# Project Polaris — Aggressive Contrarian Trading System

**북극성**: "어느 시장 상황에서도 수익이 있다." — All regimes ATTACK, no defense. Crisis = opportunity, max bet on fear.

자동 진화하는 multi-exchange 트레이딩 봇. Elo tournament + genetic mutation 으로 strategy 가 자가 진화하며, cell_matrix 6-dim 으로 strategy × regime × session × ticker fitness 학습.

## Architecture

```
SCAN → SIGNAL (16+ providers) → CELL_MATRIX score → AI judge (Gemini/Claude) →
GATE (regime/liquidity/cooldown) → SIZE (Kelly + cell mult) → ENTRY →
EXIT_FSM (TRAIL/STOP/TP/BEP/TIME/SIGNAL) → CLOSE → loss_attribution
```

### Exchanges
| Exchange | Asset | Hours |
|---|---|---|
| OKX | crypto perpetuals | 24/7 |
| Capital.com | forex / indices / commodity | 24h (시장별) |
| Alpaca | US stocks / ETF | NY 09:30~16:00 ET |
| Binance | data only | — |

### Modules
```
invasion/
├── __main__.py / boot/run.py      # scheduler (1s tick) + 30+ jobs
├── trade/
│   ├── _pipeline_scan.py / _pipeline_scan_eval.py  # signal eval (Phase A/B)
│   ├── _pipeline_sizing.py / _pipeline_regime.py
│   ├── exit_fsm.py / exit_cycle.py / close_handler.py
│   └── gate_matrix.py
├── signals/
│   ├── composer.py                # weighted N-provider composition + drop dedup/classification
│   ├── providers*.py              # 34+ signal providers (technical/onchain/macro/cross)
│   └── ticker_learner.py          # per-ticker baseline + normalize()
├── strategy/
│   ├── cell_matrix.py             # 6-dim cell (exchange × group × session × regime × strategy × dir)
│   ├── cell_factor_composer.py    # multi-factor weighting (correlation / loss_attribution)
│   ├── evolver.py                 # Elo tournament + genetic mutation
│   └── family_utils.py            # strategy family + kill list (preg toggle)
├── data/
│   ├── store_core.py              # SQLite WAL singleton (boot integrity_check + auto-restore)
│   ├── _schema_migrations.py      # v3→v6 schema migrations
│   ├── _repo_signals.py           # signals atomic mark_acted (T13 H.1)
│   └── unified_schema.py          # 36 table T13 schema
├── dashboard/
│   ├── operations.py              # LEFT — positions/signals/T13 status
│   ├── intel.py                   # RIGHT — 7 panel + LIVE LOG
│   ├── chart_window.py            # CHART — position chart + technicals + price action
│   ├── _kpi_store.py              # SSOT 5s TTL cache (operations + intel 공유)
│   └── sections/                  # 30+ wrapper renderer
├── ops/
│   ├── backup_snapshot.py         # SQLite Backup API + integrity_check + epoch manifest
│   ├── lag_tracker.py             # signal_compose / signal_to_entry / entry_scan / exit_cycle / exit_fill_latency
│   ├── lag_stage_registry.py      # stage SSOT (label / order / severity_p95_ms)
│   ├── kill_switch.py             # file + DD + emergency
│   └── alert_monitor.py           # harness_alerts emit
├── ticks/
│   ├── hourly_stats.py            # ticker/strategy aggregate + lag flush + data_qa run
│   ├── data_qa.py                 # 7-axis QA (NULL ratio / signal_blocks rate / staleness)
│   └── candle_tech.py             # ticker_dynamics tick + candle cache
├── evolution/
│   └── subsystem_reviewer.py      # 14 reviewer (Provider/Exit/Cost/Sizing/Regime/Strategy/...)
├── config/
│   ├── param_registry.py          # ParamRegistry SSOT (4-Tier: FROZEN/CONFIG/DYNAMIC/COMPUTED)
│   └── _params_*.py               # domain별 preg 등록 (signal/sizing/exit/defense/gates/strategy_ai)
└── exchange/
    ├── okx/ / alpaca/ / capital/  # adapter
    └── errors.py
```

## Quick Start

```bash
# 1. Setup
pip install -r requirements.txt
cp .env.example .env             # OKX/Cap/Alpaca API + Gemini/Claude keys

# 2. Run (headless bot + 3-window dashboard via osascript)
bash start.sh                    # WORK profile (평일 9-17) or OFFHOURS (그 외)

# 3. Stop (graceful 30s SIGTERM polling + barrier wait)
bash stop.sh                     # 또는 bash stop.sh --flatten (강제 청산 후 stop)
```

## North Star (영속 원칙)

- **Aggressive always**: 모든 regime ATTACK variant (방어 모드 없음)
- **Loss/Profit asymmetry**: 비대칭 유리 — STOP avg < TP avg, TIME exit 압축
- **No defensive dampen**: amplify-only (mult ≥ 1.0). bound floor 1.0
- **Flow not block**: drop/reject 대신 cell weight 자연 감쇠
- **Crisis = opportunity**: max bet on extreme fear (Contrarian)
- **Auto-evolution**: Elo + genetic mutation (kill list = preg toggle, default 0)

## 🎯 100% 메트릭스화 Pivot (진행 중)

모든 trade decision 이 `strategy_cell_matrix` 8-dim lookup 기반 (`exchange × group × session × regime × strategy × direction × ticker × liquidity_tier`):
- **Sizing** = `cell_score_mult` 단일 (Phase 1, 11 multiplier 통합)
- **Exit threshold** = cell.optimal_trail/bep/max_hold (Phase 2)
- **Direction** = cell_score_long vs cell_score_short (Phase 3)
- **Provider weight** = cell × provider matrix (Phase 4)
- **Strategy Elo** = per-cell tournament (Phase 5)

ParamRegistry preg = global fallback only. Hardcode = FROZEN 영역만 (clean_data_epoch/kill_switch/safety).

Plan: [`.claude/plans/cell-matrix-100pct-pivot.md`](.claude/plans/cell-matrix-100pct-pivot.md)

## Risk / Capacity

| Parameter | Value | Location |
|---|---|---|
| `max_concurrent` | 150 | `_params_defense.py` (preg) |
| `max_position_pct` | 0.15 (15% per trade) | `_params_sizing.py` |
| `max_correlated` | 200 | `_params_defense.py` |
| Kill switch (file+DD) | configurable | `ops/kill_switch.py` |
| EOD flatten (Alpaca) | 5min pre-close | `ticks/eod_flatten.py` |

## AI Integration

- **Gemini (primary)**: signal judge / strategy critique
- **Claude (critical only)**: deep analysis / debate
- 3-AI cross-vote (GO/CAUTION/SKIP) for entry filter
- Cost tracking + budget cap (preg `ai_*_budget_*`)

## Multi-Window Dashboard

3-window osascript spawn (Terminal.app):
- **OPERATIONS** (LEFT 1920×1039): positions / signals / T13 status (KILL/PHS/DUP/Canary/DATA_QA/BACKUP/LOSS_ATTR/CELL)
- **INTEL** (RIGHT 1920×1039): 7 panel (MARKET / STRATEGY×CELL / PIPELINE&FUNNEL / PROVIDERS×AI / EXIT QUALITY / OBS / ACTION) + LIVE LOG
- **CHART** (734×1039): POSITION CHART (34 row) + TECHNICALS (8) + PRICE ACTION (9)

Dynamic ROWS auto-fit + 10s rotate for overflow rows.

## Data Layer

- **SQLite WAL** (`data/invasion.sqlite`) — 36 table T13 schema
  - core: trades / signals / signal_blocks / trade_events / strategy_cell_matrix / position_health
  - lag: lag_kpi_hourly (5 stages with p50/p95/p99)
  - learner: ticker_baseline / ticker_dynamics / strategy_performance / cell_quantiles
  - ops: kill_switch / backup_snapshot / loss_attribution
- **Backup**: SQLite Backup API + integrity_check + manifest.json (epoch eligibility) every 6h
- **Auto-restore**: boot 시 corruption 감지 → manifest epoch 일치 backup 자동 복원

## Development

- **CLAUDE.md** — 절대 규칙 + 의사결정 권한
- **`.claude/docs/`** — north_star / canonical_files / coding_conventions / audit_framework
- **`.claude/agents/`** — 15 specialist agent (dev-coder / ops-executor / forensic / advisor)
- **`tasks/`** — plan_t13_integrated_final.md + harness_items.md (alert→item queue)
- **Loop**: `/loop` skill — autonomous improvement cycles
- **Debate**: `/debate` skill — Trading 3-AI cross-validation
