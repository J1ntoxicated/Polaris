# MODULE_REVIEW — `invasion/dashboard/data.py` 985L Split Plan (F-N17)

> dashboard_advisor + data_advisor F-N17: `invasion/dashboard/data.py` = **985 LOC in one file**
> after F-N15 SSOT re-leakage fix (raw `sqlite3.connect` → `DataStore.ro_query`).
> Code discipline: 601-800 분할 검토, > 1000 = P0 split (`.claude/docs/code_size_limits.md`).
> 본 문서는 block map + 저위험 extraction 순서. 모든 F-N15 SSOT 경로(`DataStore.ro_query`) 유지, UI 파라미터 무변경.

---

## 1. File Block Map (data.py 985L, as of 2026-04-18)

| # | Block | Lines | LOC | 역할 | Extract 난이도 | Risk | 우선순위 |
|---|-------|-------|-----|------|----------------|------|---------|
| B0 | Module header + paths + `_cache` + `_cached` | 1-38 | 38 | file paths, TTL cache primitives | — (stay, shared) | — | — |
| B1 | DB helpers (`_sql_query`, `db_table_counts`) | 40-92 | 53 | SSOT wrapper + sqlite_master row counts | — (stay, shared by all) | — | — |
| B2 | Core loaders (`load_state`, `load_trades`, `load_strategy_perf`) | 95-156 | 62 | state JSON + trades + per-strategy perf | — (core, stay) | — | — |
| B3 | Polaris Compass (MSG-158) | 159-183 | 25 | NSI + gate_events + provider_delta (ops/north_star wrappers) | 저 (thin pass-through) | Low | P2 |
| B4 | Cohort / tuner drift (MSG-158 T6) | 186-294 | 109 | `_parse_restart_log`, `load_cohort_comparison`, `load_tuner_drift` | 중 (shared `_parse_restart_log`) | Low | P1 cand. |
| **B5** | **MSG-159 ARCH FLOW loaders** | **297-483** | **187** | `load_shadow_modules`, `load_broker_sync_counts`, `load_strategy_evolver_stats`, `load_restart_impact` | **저 (cohesive MSG-159 block)** | **Low** | **P1 (이 PR)** |
| B6 | `load_exit_stats` + `load_hourly_pnl` | 486-547 | 62 | exit type breakdown + hourly PnL | 저 (pure SQL) | Low | P3 |
| B7 | Log / config / detector / costs loaders | 550-571 | 22 | file-read TTL wrappers | — (stay, trivial) | — | — |
| B8 | Utility + metrics (`file_age`, `calc_metrics`, `file_info`, `data_file_list`) | 574-651 | 78 | freshness + aggregate metrics + monitor list | 저 (pure, no DB) | Low | P2 |
| B9 | AI quality loaders | 656-685 | 30 | `load_ai_quality` (ai_calls + ai_decisions) | 저 | Low | P3 |
| B10 | Signal loaders (heatmap / recent / rejects / provider stats / chain) | 688-866 | 179 | `load_recent_signals`, `load_24h_pnl_by_exchange`, `load_drawdown_1h`, `load_ai_call_rates`, `load_top_winners_losers`, `load_cap_instrument_counts`, `load_signal_heatmap`, `load_reject_analysis`, `load_signal_provider_stats`, `load_chain_stats` | 중 (많은 함수, 시그널 + 거래) | Low | P3 |
| B11 | Provider code mapping | 869-899 | 31 | `PROVIDER_CODES`, `provider_to_code`, `providers_to_codes` | 저 (pure util) | Low | P2 |
| B12 | Strategy map / groups / cap tickers / provider perf | 902-985 | 84 | `load_cap_tickers`, `load_strategy_map`, `load_strategy_groups`, `load_provider_perf` | 저 | Low | P3 |

**합계**: 13 블록 / 985 LOC.

---

## 2. Extraction 순서 (저위험 우선)

### P1 — 본 PR: B5 MSG-159 ARCH FLOW → `data_arch_flow.py`
- Functions: `load_shadow_modules`, `load_broker_sync_counts`, `load_strategy_evolver_stats`, `load_restart_impact`
- Shared helper `_parse_restart_log` (B4) used by `load_restart_impact` — moved to new module as private.
- `_cached` / `_sql_query` imported from `.data` (shared TTL cache + SSOT wrapper).
- `data.py` re-exports via `from .data_arch_flow import ...` for backward compat (현재 live 코드는 이 4개를 import 하지 않지만, 아카이브된 harness 설계서가 `from ..data import` 경로 명시).
- **Saving**: ~187 LOC (+helper) → data.py 가 ~800 LOC 로 하락.

### P2 — 후속 PR: B3 Polaris + B8 Utils + B11 Provider Codes → `data_misc.py`
- Polaris wrappers (25L) + file_age/calc_metrics/file_info/data_file_list (78L) + provider_to_code mapping (31L) = ~134L
- Pure helpers, no DB coupling in B8/B11, B3 is thin wrapper.

### P3 — 후속 PR: B4 Cohort + B6 Exit/Hourly + B9 AI quality + B10 Signals + B12 Strategy map
- Cohesive 세트이지만 크기 큼 — 2개로 쪼개서 진행.
- B10 (signals 179L) + B12 (strategy 84L) → `data_signals.py` / `data_strategy.py`
- B4 + B6 + B9 → `data_analytics.py`

---

## 3. 제약 · 검증

- **F-N15 SSOT 유지**: 모든 DB read 는 `DataStore.ro_query`. 새 파일에서 `sqlite3.connect` 신규 사용 금지.
- **UI 파라미터 무변경**: `operations.py` / `intel.py` / `ai.py` / `signal.py` / `chart_window.py` 기존 `from .data import ...` 그대로 동작.
- **Cache key 충돌 방지**: `_cache` 는 module-level dict 로 `.data` 에 집중. 새 파일은 `from .data import _cached, _sql_query` 로 공유.
- **검증**:
  ```bash
  wc -l invasion/dashboard/data.py invasion/dashboard/data_*.py
  python3 -m py_compile invasion/dashboard/data.py invasion/dashboard/data_*.py
  python3 -c "import invasion.main"
  grep -n "sqlite3.connect" invasion/dashboard/data.py  # 0 유지
  ```

---

## 4. 본 PR 결과 요약

- 신규: `invasion/dashboard/data_arch_flow.py` (~200L, MSG-159 block + helper)
- 변경: `invasion/dashboard/data.py` (985L → ~800L, B5 + `_parse_restart_log` 제거 후 re-export)
- Commits:
  1. `docs(msg-fn17-dashboard-data-plan jin p1)` — 본 문서
  2. `refactor(msg-fn17-dashboard-data-arch-flow jin p1)` — B5 extraction
