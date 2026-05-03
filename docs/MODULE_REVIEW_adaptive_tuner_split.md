# MODULE_REVIEW: adaptive_tuner Split Plan (F-N17)

**Target**: `invasion/ops/adaptive_tuner.py`
**Current size**: 1082 lines (architecture_advisor snapshot said 1015L; live file grew with MSG-P1-ADAPTIVE-EXPAND additions)
**Threshold**: > 1000L = P0 split (code_size_limits)
**Discoverer**: architecture_advisor F-N17 (14 files >= 800L batch)
**Cross-review**: ml_advisor — Thompson Sampling / Beta posterior / walk-forward shadow hook semantics preservation check required before HIGH-risk extractions (phases E/F).

## Why split
- Single file fuses pure data (103 adaptive keys + 104 bound ranges) with Bayesian learner, singleton bandit, rollback arbiter, and analyzer-bias blender.
- Any edit to a param entry forces a reload of the entire Thompson-sample hot path, inflating review context.
- Behaviour-neutral separation unlocks independent ml_advisor review of the learner core without scrolling through 400L of param dict literals.

## Logical block map

| Block | Lines | Responsibility | Extractable? | Risk |
|-------|-------|----------------|--------------|------|
| **A** | 1-21 | imports + `FIXED_PARAMS` lazy import | stays (shared) | N/A |
| **B** | 23-221 | `ADAPTIVE_PARAMS` set (103 flat keys with MSG provenance comments) | YES — pure data | LOW — no behaviour, literal only |
| **C** | 223-373 | `PARAM_BOUNDS` dict (104 `(lo, hi)` tuples with registry alignment comments) | YES — pure data | LOW — no behaviour, literal only |
| **D** | 375-377 | `MIN_TRADES_PER_REGIME` / `MAX_DRIFT_PCT` / `TUNE_INTERVAL` constants | stays (or co-move) | LOW |
| **E** | 380-482 | `RegimeProviderBandit` class + `_GLOBAL_BANDIT` + `get_regime_provider_bandit()` singleton (Thompson Beta-Bernoulli per `(regime, provider)` arm) | YES — independent class | MED — wired via singleton into `signals/composer.py` + `trade/close_handler.py`; rename risk |
| **F** | 485-502 | `flatten_config` / `unflatten_changes` helpers (back-compat shims) | YES — stateless | LOW — but imported by `strategy/param_orchestrator.py` |
| **G** | 505-556 | `AdaptiveTuner.__init__` + `set_execution_service` + `get_exchange_fill_hints` + `should_tune` | stays | LOW |
| **H** | 558-705 | `AdaptiveTuner.tune_cycle` — regime-filtered trades read, walk-forward shadow hook, exit-intelligence hint load, provider-weight hint load, per-param Thompson loop, analyzer-bias merge | stays (HIGH risk orchestration) | HIGH — critical path, DO NOT TOUCH |
| **I** | 707-798 | `AdaptiveTuner._thompson_sample` — bucket stats + bootstrap exploration + posterior sampling | candidate for later `thompson.py` extraction | HIGH — ml_advisor must cross-review any move |
| **J** | 800-850 | `AdaptiveTuner.apply_changes` — pre-Sharpe snapshot, rollback tracking ring-buffer, config_history insert | stays (HIGH risk arbiter) | HIGH — rollback correctness |
| **K** | 852-901 | `AdaptiveTuner.check_rollback` + `_estimate_sharpe` — Sharpe-degradation rollback arbiter (>5% threshold after 1h) | stays (HIGH risk arbiter) | HIGH — rollback correctness |
| **L** | 903-1065 | `AdaptiveTuner._get_analyzer_hints` + `_apply_analyzer_bias` — TradeAnalyzer integration, signal-weight map, crisis-guarded `min_score` hint, exit-hint directional nudge | stays | MED |
| **M** | 1067-1082 | `AdaptiveTuner.get_state` — dashboard snapshot | stays | LOW |

## Extraction order (low-to-high risk)

1. **This batch** — Blocks B + C into `adaptive_params.py` (pure data, zero behaviour). Re-export from `adaptive_tuner.py` for back-compat with any downstream importers + lessons references.
2. Block E → `regime_provider_bandit.py` (independent class + singleton; update two call sites `signals/composer.py`, `trade/close_handler.py`).
3. Block F → tiny `adaptive_config_io.py` (or fold into `adaptive_params.py` since both are data-adjacent) — requires a one-line change in `strategy/param_orchestrator.py`.
4. Block I (`_thompson_sample`) → `thompson.py` as a module-level pure function taking `(param, bounds, trades)`. **ml_advisor cross-review mandatory** before this step — any change to bucket width, posterior σ, or bootstrap exploration could silently alter the learner's equilibrium drift direction.
5. Blocks J + K (rollback arbiter) stay inline; only split if `adaptive_tuner.py` still >= 800L after steps 1-4.
6. Block H (`tune_cycle`) **never extracted** — it is the orchestration root; splitting would break mechanical-refactor discipline.

## Risk controls
- Each extraction is a **mechanical refactor**: identical values, identical lookup semantics, import-back-compat via `from .adaptive_params import *` (or explicit names).
- After each extraction: `wc -l`, `python3 -m py_compile`, `python3 -c "import invasion.main"`, and the two-assertion snapshot:
  ```bash
  python3 -c "from invasion.ops.adaptive_tuner import ADAPTIVE_PARAMS, PARAM_BOUNDS; assert len(ADAPTIVE_PARAMS)==103 and len(PARAM_BOUNDS)==104"
  ```
- Commit per extraction so rollback = single `git revert`.
- Commit scope discipline: `git stash -u` + `git commit -- <path>` (no `git add -A`).

## This batch (#1) — Blocks B + C extraction

**Scope**: `ADAPTIVE_PARAMS` (set, 103 entries) and `PARAM_BOUNDS` (dict, 104 entries) move verbatim into a new `invasion/ops/adaptive_params.py`. `adaptive_tuner.py` re-exports both names via `from .adaptive_params import ADAPTIVE_PARAMS, PARAM_BOUNDS`.

**Why lowest risk**:
- Pure data — no runtime behaviour attached.
- No external module currently imports `ADAPTIVE_PARAMS` or `PARAM_BOUNDS` as module attributes of `adaptive_tuner` (verified via grep — only intra-module reads at lines 491/500/664/669/962/1003/1038).
- `evolver.py` defines its own local `PARAM_BOUNDS`; no name collision across modules.
- Re-export preserves any future dynamic attribute lookup or lessons.md drive-by reference.

**Post-extraction baseline**:
- `adaptive_tuner.py` expected size: ~730L (1082 − 352 data lines + import shim).
- `adaptive_params.py` expected size: ~355L (pure literals + MSG provenance comments preserved).
- Count assertion: 103 / 104 must hold exactly.

**ml_advisor cross-review checkpoint**: not required for this batch (data-only). Mandatory before batches touching Block I (`_thompson_sample`).

## Progress (1/~4 phases)
- [x] Batch #1: Blocks B + C -> `adaptive_params.py` (this commit)
- [ ] Batch #2: Block E (`RegimeProviderBandit`) -> `regime_provider_bandit.py`
- [x] Batch #3: Block F (config shims) -> `adaptive_config.py` (commit d41df667)
- [ ] Batch #4: Block I (`_thompson_sample`) -> `thompson.py` [BLOCKED ON ml_advisor]
