# MODULE_REVIEW: scan_cycle Split Plan (F-N13)

**Target**: `invasion/trade/_pipeline_scan.py:22` `ScanMixin.scan_cycle()`
**Current size**: 1027 lines (file), ~1005 lines inside `scan_cycle` method
**Discoverer**: architecture_advisor
**Predecessor**: F-N1 split decomposed `pipeline.py` into mixins (ScanMixin/SizingMixin/RegimeMixin/ExitCycleMixin/CloseHandlerMixin) but the `scan_cycle` method body itself stayed monolithic.

## Why split
- Blast radius: every entry path traverses this method. A single syntax/logic regression silently breaks ALL exchanges.
- Review/debug cost: locating a phase requires scrolling ~1000 lines.
- Testability: mid-loop state (`_loss`, `candidates`, `filtered`) is not observable in isolation.

## Phase map

| Phase | Lines | Responsibility | Extractable? | Risk |
|-------|-------|----------------|--------------|------|
| **A1** | 31-44 | computed params refresh (300s throttle) | YES (helper) | LOW — side-effect only, no return |
| **A2** | 46-54 | safety halt check | YES (helper) | LOW — early return, pure boolean |
| **A3** | 56-71 | GateMatrix safety eval | YES (helper) | LOW — early return |
| **A4** | 73-88 | candle_available cache refresh (300s throttle) | YES (helper) | LOW — side-effect only |
| **A5** | 90-96 | reject cooldown expire | YES (helper) | LOW — in-place dict filter |
| **B1** | 98-221 | per-ticker loop: regime/pre_signal/signal eval → candidates[] | YES (new module) | MED — big payload, returns (candidates, _loss) |
| **C**  | 222-242 | scan summary + SCOPE4 funnel log | YES (helper) | LOW — log-only, no mutation of candidates |
| **D**  | 244-281 | ML meta filter | YES (helper) | MED — mutates candidates list, early-return path |
| **E**  | 283-332 | AI S1 signal augmenter | YES (helper) | MED — modifies cand["score"] |
| **F1** | 334-335 | sort by abs score | NO (1 line) | N/A |
| **F2** | 337-432 | StrategyRouter + family/exchange gates | YES (helper) | HIGH — mutates candidates via multiple filter lists |
| **G**  | 434-448 | regime-scaled max_concurrent + portfolio filter | YES (helper) | LOW — returns (filtered, _max_concurrent) |
| **H1** | 450-492 | mid-loop: race check + entry_gate | stays inline (per-cand) | N/A |
| **H2** | 494-520 | LIVENESS_SHADOW log | YES (helper) | LOW — log only |
| **H3** | 522-572 | AI S2 strategy advisor | YES (helper) | HIGH — may swap strategy_id + early continue |
| **H4** | 574-615 | strategy_direction_kill gate | YES (helper) | MED — early continue + log event |
| **H5** | 617-677 | family anti-contrarian block (preg toggled) | YES (helper) | MED — early continue |
| **H6** | 679-734 | AI S3 entry judge | YES (helper) | HIGH — may reject + set _ai_size_modifier |
| **H7** | 736-804 | contract V2 gate (edge_prob / exec_risk) | YES (helper) | MED — early continue |
| **H8** | 806-829 | exit_params calc + ExitIntelligence nudge | YES (helper) | LOW — mutates cand["exit_params"] |
| **H9** | 831 | size calc | stays inline (1 line) | N/A |
| **H10** | 833-863 | execute_fn dispatch | YES (helper) | MED — cross-boundary |
| **H11** | 865-1009 | Position build + portfolio.add + DB insert | YES (new module) | HIGH — most complex, many branches |
| **H12** | 1011-1027 | bus publish trade.entered | YES (helper) | LOW — log/publish only |

## Extraction order (low-to-high risk)

1. **Phase A1 + A4 refresh helpers** (this batch) — pure throttled side-effects.
2. Phase C telemetry summary.
3. Phase H2 LIVENESS_SHADOW + Phase H12 bus publish (log-only).
4. Phase A2/A3/A5 setup gates (early-return semantics).
5. Phase G portfolio filter.
6. Phase H8 exit_params calc.
7. Phase D ML meta filter + Phase E AI S1 (list mutation).
8. Phase H4/H5/H7 mid-loop gates (early continue).
9. Phase F2 StrategyRouter (high risk — multi-list mutation).
10. Phase H3 AI S2 + Phase H6 AI S3 (strategy_id swap + re-routing).
11. Phase H10 execute_fn + Phase H11 Position build (most complex last).
12. Phase B1 per-ticker loop (largest — defer until signatures are stable).

## Risk controls
- Each extraction is a **mechanical refactor**: parameter list + return shape preserved, no logic change.
- After each extraction: `python3 -c "import invasion.main"` smoke + `py_compile`.
- Commit per extraction so rollback = single `git revert`.
- No parameter tuning, no gate behaviour change until decomposition complete.

## This batch (#1) — DONE (b381eef)
**Scope**: Phase A1 (`_scan_refresh_computed`) + Phase A4 (`_scan_refresh_candle_cache`) extracted as helper methods on `ScanMixin`. Both are throttled (300s) no-return side-effects — safest possible seed extraction.

**Verification**:
- `wc -l _pipeline_scan.py` before/after (net +helpers, scan_cycle body shorter)
- `import invasion.main` smoke
- `py_compile` clean

## This batch (#2) — Phase C + H12
**Scope**: Phase C telemetry (`_scan_log_summary`) + Phase H12 bus publish (`_scan_publish_trade_entered`) extracted as helper methods on `ScanMixin`. Both are log/publish-only side-effects — second-safest batch after A1/A4.

**Signatures**:
- `_scan_log_summary(market_data, candidates, _loss, now_ts)` — mutates `self._signal_candidates` + `self._last_exch_funnel_ts`, no return.
- `_scan_publish_trade_entered(pos, cand, signal_meta)` — early-return when `self.bus is None`, try/except publish, no return.

**Verification**:
- `wc -l _pipeline_scan.py` 1063 → 1092 (+29 net: helpers +94L, inline removal −35L from scan_cycle body).
- scan_cycle body: ~1005L → ~972L (−33L).
- `python3 -m py_compile` clean.
- `python3 -c "import invasion.trade._pipeline_scan"` OK.
- `pytest tests/trade/` 60 passed, 1 pre-existing fail (test_fsm_slice_on_enables — unrelated).

## This batch (#3) — Phase A2 + A3 + A5
**Scope**: Phase A2 (`_scan_check_safety_halt`) + A3 (`_scan_check_gate_matrix_safety`) + A5 (`_scan_expire_rejects`) extracted as helper methods on `ScanMixin`. A2/A3 return `bool` (True = halt, caller returns). A5 is in-place dict prune, no return.

**Signatures**:
- `_scan_check_safety_halt() -> bool` — SafetyManager halt check; True = abort.
- `_scan_check_gate_matrix_safety() -> bool` — GateMatrix hard-block check; True = abort.
- `_scan_expire_rejects(now_ts, cooldown_sec) -> None` — mutates `self._recent_rejects`.

**Verification**:
- `wc -l _pipeline_scan.py` 1092 → 1129 (+37 net: helpers +55L, inline removal −18L from scan_cycle body).
- scan_cycle body: ~972L → ~954L (−18L).
- `python3 -m py_compile` clean.
- `python3 -c "import invasion.trade._pipeline_scan"` OK.
- `pytest tests/trade/` 60 passed, 1 pre-existing fail (test_fsm_slice_on_enables — unrelated).

## This batch (#4) — Phase H2 + G + H8
**Scope**: Phase H2 (`_scan_liveness_shadow`) + Phase G (`_scan_portfolio_filter`) + Phase H8 (`_scan_calc_exit_params`) extracted as helper methods on `ScanMixin`. All three are LOW risk — log-only / pure filter / mutation-on-cand with no early-return branching.

**Signatures**:
- `_scan_portfolio_filter(candidates) -> tuple[list, int]` — returns `(filtered, _max_concurrent)`, also mutates `self._portfolio_filtered` counter.
- `_scan_liveness_shadow(ticker) -> None` — log-only, never blocks.
- `_scan_calc_exit_params(cand, ticker) -> dict` — mutates `cand['exit_params']`, returns the dict (preserves inline `exit_params` local var semantics).

**Verification**:
- `wc -l _pipeline_scan.py` 1129 → 1170 (+41 net: helpers ~107L, inline removal ~66L from scan_cycle body).
- scan_cycle body: ~954L → ~888L (−66L).
- `python3 -m py_compile` clean.
- `python3 -c "import invasion.trade._pipeline_scan"` OK.
- `pytest tests/trade/` 60 passed, 1 pre-existing fail (test_fsm_slice_on_enables — unrelated).

## Progress (11/22 phases done)
- [x] Phase A1 (`_scan_refresh_computed`) — b381eef
- [x] Phase A4 (`_scan_refresh_candle_cache`) — b381eef
- [x] Phase C  (`_scan_log_summary`) — 612b79c
- [x] Phase H12 (`_scan_publish_trade_entered`) — 612b79c
- [x] Phase A2 (`_scan_check_safety_halt`)
- [x] Phase A3 (`_scan_check_gate_matrix_safety`)
- [x] Phase A5 (`_scan_expire_rejects`)
- [x] Phase H2 (`_scan_liveness_shadow`)
- [x] Phase G  (`_scan_portfolio_filter`)
- [x] Phase H8 (`_scan_calc_exit_params`)
- [ ] Phase D ML meta / Phase E AI S1 (MED)
- [ ] Phase H4 / H5 / H7 mid-loop gates (MED)
- [ ] Phase F2 StrategyRouter (HIGH)
- [ ] Phase H3 AI S2 / H6 AI S3 (HIGH)
- [ ] Phase H10 execute_fn / H11 Position build (HIGH)
- [ ] Phase B1 per-ticker loop (defer last)
