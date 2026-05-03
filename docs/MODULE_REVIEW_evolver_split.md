# MODULE_REVIEW: evolver Split Plan (F-N17)

**Target**: `invasion/strategy/evolver.py`
**Current size**: 968 lines
**Threshold**: 801-1000L = P1 split candidate (code_size_limits)
**Discoverer**: strategy_advisor F-N17 (batch of files >= 800L)
**Cross-review**: ml_advisor — FitnessFunction path untouched (ml_advisor 관할, lives in `backtester.py`). Elo / genetic mutation extractions are pure-math moves; lifecycle arbitration (promote/disable/sunset) must stay inline for review isolation.

## Why split
- Single file fuses four concerns: (1) mutation operators (gaussian / bayesian / structural / AI-targeted), (2) lifecycle arbitration (disable / prune / overfit), (3) group auto-adjust + neutral specialist spawner, (4) AI-Architect net-new strategy consume pipeline.
- Parameter-bounds data (`PARAM_BOUNDS`) is literal-only and duplicates the same pattern `adaptive_tuner.py` already extracted into `adaptive_params.py`.
- Mutation operators are pure functions (dict-in, dict-out; no `self` state beyond `_ALL_SIGNALS` constant) — trivial to move without breaking behaviour.
- Lifecycle logic (`evolve_cycle`, `_auto_adjust_groups`, `_spawn_neutral_strategies`, `_consume_new_strategies`) is HIGH risk — stays inline so a single reviewer owns the full promote/disable arbitration path.

## Logical block map

| Block | Lines | Responsibility | Extractable? | Risk |
|-------|-------|----------------|--------------|------|
| **A** | 1-21 | imports + `FitnessFunction` re-import (ml_advisor owns actual def) | stays | N/A |
| **B** | 23-58 | `PARAM_BOUNDS` dict + evolution constants (`ELITE_COUNT`, `MIN_FITNESS_PROMOTE`, `DISABLE_FITNESS`, `FITNESS_VERSION`, STOP-WR thresholds) | YES — pure data | LOW |
| **C** | 61-103 | `StrategyEvolver.__init__` + `_load_generation` + `_save_generation` | stays (lifecycle) | LOW |
| **D** | 104-437 | `evolve_cycle` — evaluation, disable gate, STOP-WR gate, idle-mark, mutate, family/asset-class/group prune, overfit check, save, auto-adjust, AI-consume flush | stays (HIGH risk orchestration) | HIGH — DO NOT TOUCH |
| **E** | 439-529 | `_auto_adjust_groups` — group prune (WR < 20%) + uncovered-group specialist spawn | stays (lifecycle) | HIGH — store.save callsites |
| **F** | 531-580 | `_spawn_neutral_strategies` — neutral-regime specialist clone | stays (lifecycle) | MED |
| **G** | 582-606 | `_ALL_SIGNALS` list + `_select_mutation_type` (gen-weighted random.choices) | YES — pure logic | LOW |
| **H** | 608-666 | **Genetic mutation utils**: `_structural_mutate`, `_gaussian_mutate`, `_mutate_dict` | **YES — pure functions** | LOW — dict-in/dict-out, only reads `PARAM_BOUNDS` + `_ALL_SIGNALS` |
| **I** | 668-707 | **Bayesian interpolation utils**: `_bayesian_mutate`, `_interpolate_dict` | **YES — pure functions** | LOW — reads `PARAM_BOUNDS` only |
| **J** | 709-782 | `_ai_targeted_mutate` — AI orchestrator call + mutation-apply loop + net-new buffer | stays (AI integration) | HIGH — orchestrator budget gate + WIRE-12 buffer semantics |
| **K** | 784-807 | `_overlay_live_config` — param_registry overlay onto mutation child | stays (wire-side helper) | LOW |
| **L** | 809-835 | `_infer_preferred_regimes` — DB query per-regime WR | stays (lifecycle) | MED |
| **M** | 837-965 | `_consume_new_strategies` — WIRE-12 AI-Architect validate + gated save (fitness≥50, n≥20, stress.survival) + WIRE-13 family auto-register | stays (HIGH risk promote gate) | HIGH — jin_review_flag semantics |
| **N** | 967-968 | `get_evolution_log` | stays | LOW |

## Extraction order (low-to-high risk)

1. **This batch** — Block H + I into `evolver_mutations.py` (pure genetic mutation operators). Import-back-compat: class methods delegate to module-level functions (one-line shim each).
2. Block B (`PARAM_BOUNDS` + constants) → `evolver_params.py` (mirrors `adaptive_params.py` pattern). Requires import update inside `evolver_mutations.py` and class body.
3. Block G (`_ALL_SIGNALS` + `_select_mutation_type`) → could fold into `evolver_mutations.py` (shared with structural mutate). Evaluate after step 2.
4. Blocks D/E/F/J/M **never extracted** — they are lifecycle/orchestration arbiters. Any cross-file move would force reviewer context-switching between promote/disable logic and pure mutation math, defeating the review-isolation goal.

## Risk controls
- Each extraction is a **mechanical refactor**: identical semantics, identical callsites.
- `FitnessFunction` path untouched — `from .backtester import FitnessFunction` stays at top of `evolver.py`. ml_advisor retains sole authority over fitness formula.
- `data/prompts/evolver_state.json` is a **separate file** used by prompt-evolution layer (exit_advise/proactive_exit Beta posteriors) — NOT the strategy evolver's `data/evolution_state.json` generation counter. Format untouched by this refactor.
- Strategy lifecycle preserved: promote/disable/sunset all remain in `evolve_cycle` + `_auto_adjust_groups` + `_consume_new_strategies`. Zero behaviour change.
- Post-extraction: `wc -l`, `python3 -m py_compile`, `python3 -c "import invasion.main"`.
- Commit scope discipline: `git add <path>` + `git commit -- <path>` (no `git add -A`).

## This batch (#1) — Block H + I extraction

**Scope**: `_gaussian_mutate`, `_mutate_dict`, `_bayesian_mutate`, `_interpolate_dict`, `_structural_mutate` move verbatim into `invasion/strategy/evolver_mutations.py` as module-level functions. `_ALL_SIGNALS` list co-moves (only consumer is `structural_mutate`). Class methods become one-line shims delegating to the module functions, preserving `self` call pattern so no callsite in `evolve_cycle` changes.

**Why lowest risk**:
- Pure functions — no `self` state touched except `self._ALL_SIGNALS` (co-moved as module constant).
- Zero external imports of these methods — all callers are inside `evolve_cycle` on the same class.
- `PARAM_BOUNDS` still lives in `evolver.py`; `evolver_mutations.py` imports it from there (no circular risk — mutations file imports only the literal dict).
- Shim pattern (`def _gaussian_mutate(self, s, sigma): return gaussian_mutate(s, sigma)`) keeps `self.` call convention intact, so `_ai_targeted_mutate` fallback and `_select_mutation_type` branches need zero changes.

**Post-extraction baseline**:
- `evolver.py` expected size: ~870L (968 − ~100 extracted lines + import + shims).
- `evolver_mutations.py` expected size: ~100L (pure functions + `_ALL_SIGNALS` + docstrings).

**ml_advisor cross-review checkpoint**: not required for this batch (no fitness formula touched, pure data-transform moves). Mandatory before any batch touching Block D/J/M.

## Progress (1/~3 phases)
- [x] Batch #1: Blocks H + I → `evolver_mutations.py` (this commit)
- [ ] Batch #2: Block B (`PARAM_BOUNDS` + constants) → `evolver_params.py`
- [ ] Batch #3: Block G (`_select_mutation_type`) → fold into `evolver_mutations.py` (evaluate)
