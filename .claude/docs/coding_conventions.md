# 코딩 규약 + Pre/Post-flight + Bot Operations

## Pre-flight (BEFORE any code change)

1. `python3 -c "import invasion.main"` — confirm clean baseline
2. Check file in [Canonical File Map](canonical_files.md) — am I editing the right copy?
3. Check Absolute Rules (CLAUDE.md) — is this file off-limits?
4. If touching imports: `grep -rn "MODULE_NAME" invasion/ --include="*.py" | grep import`

## Post-flight (AFTER every code change)

1. `python3 -c "import invasion.main"` — import still works?
2. `python3 -m invasion --headless` — bot starts without crash? (5s check, Ctrl+C)
3. If error: fix immediately, do NOT move to next task
4. If mistake: update `tasks/lessons.md` with new pattern

## Bot Operations

- Start: `bash start.sh`
- Stop: `bash stop.sh`
- Restart: `bash stop.sh && sleep 2 && bash start.sh`
- Config: `data/live_config.json` (hot-reload 5s)
- State: `data/portfolio_state.json` (Portfolio SSOT)
- DB: `data/invasion.sqlite` (WAL)
- Logs: `data/invasion.log`

## Naming Conventions

- **DB column = canonical**: `asset_group` (not `group`), `max_profit_pct` (not `max_pnl_pct`), `exit_type` (not `exit_reason`)
- **Exchange adapters**: `side` → `direction` at adapter boundary
- **Functions**: `calc_*` = technical math, `compute_*` = statistical/ML
- **Params**: always via `preg()` — never `getattr(config, ...)`

## Parameter Management

- ALL params through `param_registry.get()/set()` — no direct live_config.json access
- AI Governor reviews hourly, promotes/rollbacks based on trade data
- Audit: `data/param_history.jsonl` | Validation: `data/param_validation.json`
- Tier 4 COMPUTED: `config/computed.py` — real-time, not persisted, recalculated every scan
- Current key count: `python3 -c "import json; print(len(json.load(open('data/live_config.json'))))"`
- Full: `docs/ARCHITECTURE.md` "4-Tier Parameter Classification"

## Data Governance (MANDATORY)

- **SSOT**: `docs/governance/data_dictionary.json`
- **Before ANY code change**: read dictionary → find affected entries
- **Every commit MUST update dictionary** if code changes affect modules/functions/formulas
- **Full**: `docs/GOVERNANCE.md`

## Cell-aware Decision Pattern (MANDATORY)

새 decision logic (sizing / exit / direction / provider / strategy 선택) 은 `cell_matrix` lookup 우선 → preg fallback → hardcode FROZEN only. 상세 + 예시: [cell_aware_pattern.md](cell_aware_pattern.md).

## 참조
- [canonical_files.md](canonical_files.md) · [canonical_cell_matrix.md](canonical_cell_matrix.md)
- CLAUDE.md — Absolute Rules, Decision Authority
- [cell_aware_pattern.md](cell_aware_pattern.md) · [cell-matrix-100pct-pivot.md](../plans/cell-matrix-100pct-pivot.md)
