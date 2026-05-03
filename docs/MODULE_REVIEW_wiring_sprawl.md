# `invasion/boot/wiring.py` — Inline Import Sprawl Plan (F-N16)

Baseline: **870 LOC, 66 inline `from ..X import Y` statements** inside 9 `_init_*` functions. Originally extracted from `main.py` (F-N1 2/5) as a pure move; inline lazy imports were kept "to avoid circular imports". architecture_advisor flagged wiring.py as having evolved into a "secret Registry" — module boundaries blurred, real cycle status unknown.

## Cycle Reality Check (grep-verified)

| Source | Imports back to boot/main? | Verdict |
|--------|---------------------------|---------|
| `invasion/signals/**` top-level | **none** (only `..utils.events`, `..config.canonical_names`, `..utils.technicals`) | **safe top-level** |
| `invasion/main.py` | shim re-export from `boot/*` only | no issue |
| `invasion/ops/emergency.py` | lazy `from ..main import _init_config` (runtime only) | no hard cycle |

Conclusion: most inline imports are **inertia**, not real cycle guards. Real cycle risk is concentrated in `ai/` ↔ `ops/` and `trade/` ↔ `strategy/` cross-dependencies, which need per-subpackage grep before each extraction.

## Inline Import Inventory — 66 Statements by Category

| # | Category | Count | Functions | Subpackages Touched | Extraction Candidate |
|---|----------|-------|-----------|--------------------|---------------------|
| 1 | **Config / Bus** | 2 | `_init_config`, `_init_data` | config, bus | leave inline (load-order sensitive) |
| 2 | **Data** | 3 | `_init_data` | data.store, data.candle_cache, data.data_collector | low risk, consider Phase 2 |
| 3 | **Exchange (OKX/Cap/Alpaca/Binance)** | 16 | `_init_exchanges`, `_start_cap_ws_feed` | exchange.*, utils.market_hours | Phase 3 — largest, env-dependent, keep isolation |
| 4 | **Signals** | 10 | `_init_signals` | signals.engine, signals.providers, signals.providers_extended, signals.providers_onchain, signals.providers_technical, signals.ml_signal, signals.bayesian | **✅ This sprint (Phase 1)** |
| 5 | **Strategy** | 2 | `_init_strategy` | strategy.engine, strategy.evolver | Phase 4 |
| 6 | **Trade pipeline** | 8 | `_init_trade` (+ `_on_close` closure) | trade.*, ops.safety, exchange.errors | Phase 5 — closure uses runtime preg; keep preg inline |
| 7 | **AI** | 13 | `_init_ai` | ai.orchestrator, ai.feedback, ai.prompt_evolver, ai.analysis.*, ai.base, ai.live (8 variants) | Phase 6 — `ai.live` try/except ImportError pattern must survive (graceful stage degradation) |
| 8 | **Regime / Safety / Controllers** | 5 | `_init_regime_and_safety` | market.regime, market.regime_service, data.collectors.cnn_feargreed, strategy.param_orchestrator, ops.ai_controller | Phase 7 |
| 9 | **param_registry preg (scattered)** | 4 | `_init_exchanges`, `_init_signals` ×2, `_on_close`, OKX slip stamp | config.param_registry | **runtime-only** — these are fetched on each close/eval, NOT init-time. Keep inline. |
| 10 | **Signal providers nested try/except branches** | 3 sub-blocks inside `_init_signals` | flow, breakout, on-chain | absorb into Phase 1 |

Total counted: 66 inline statements. (Category 9 = 4 runtime preg calls that should **never** move to top-level.)

## Extraction Order (Risk-Graded, Monotonic)

1. **Phase 1 — Signals (this sprint)**: `wiring_signals.py`. Zero backward imports, provider registry is self-contained, try/except ImportError around optional providers stays intact. Delete 10 inline imports from `_init_signals`; keep the 2 runtime `preg` lookups inline.
2. **Phase 2 — Data collectors**: move 3 data imports to top-level inside `wiring.py` itself (no new file). Data modules also don't back-import boot.
3. **Phase 3 — Exchange**: biggest block (16 imports, ~160 LOC). Candidate for its own `wiring_exchange.py`. Must preserve all try/except ImportError for optional WS feeds.
4. **Phase 4 — Strategy**: 2 imports + try/except around evolver. Trivial; can be top-level in wiring.py.
5. **Phase 5 — Trade pipeline**: `_on_close` closure intentionally keeps `preg` + `MarketClosedError` lazy (runtime path). Only move the 8 init-time imports to top-level.
6. **Phase 6 — AI**: tricky because `ai.live` uses per-stage `ImportError` fallback (`_LogOnlyFallback`). Extraction must preserve graceful degradation — each `try: from ..ai.live import X` stays, but non-try imports (orchestrator/feedback/prompt_evolver/analysis/base) move.
7. **Phase 7 — Regime / Safety**: 5 imports, cleanest after everything else stabilizes.

## Phase 1 — Signals Extraction (This Commit)

New file: `invasion/boot/wiring_signals.py`

- Top-level imports (all signals, one config helper used per-provider-weight):
  - `from ..signals.engine import SignalEngine`
  - `from ..signals.bayesian import BayesianPredictor`
  - `from ..signals.providers import (SentimentSignal, FundingSignal, LSRatioSignal, TakerSignal, FearGreedSignal, LiquidationSignal, TechnicalSignal, CrossPairSignal)`
  - `from ..signals.providers_extended import (MomentumSignal, VolatilitySignal, PriceActionSignal, CrossExchangeSignal, MacroRegimeSignal, InstitutionalPositionSignal, DualThrustSignal, SessionBreakoutSignal, WQAlpha1Signal, WQAlpha6Signal, OrderFlowImbalanceSignal, VWAPMeanReversionSignal)`
  - `from ..signals.providers_technical import MultiTFTechnicalProvider`
  - `from ..signals.ml_signal import MLSignalProvider`
  - `from ..signals.providers_onchain import (OnChainValuationSignal, BasisSpreadSignal, LiquidationCascadeSignal, GoogleTrendsSignal, LLMSentimentSignal)`
  - `from ..config.param_registry import get as preg` (used for ML / WQ weight lookup — init-time not runtime)
  - `from ..utils.events import log_event`

- Per-group `try/except ImportError` blocks in the **function body** are retained for runtime resilience (e.g. a provider file deletion on a live bot shouldn't crash init). Converting top-level imports flips ImportError behaviour from per-group to whole-module fail. To preserve parity: **top-level imports for the 8 base providers (mandatory) + providers_technical**, but keep `try/except ImportError` lazy imports for the 5 optional groups (providers_extended variants, providers_onchain, ml_signal). This keeps the graceful-degradation semantics identical to today.

- `_init_signals` is re-implemented in `wiring_signals.py`. `wiring.py` re-exports via `from .wiring_signals import _init_signals` so `main.py`'s existing `from .boot.wiring import _init_signals` continues to work.

Expected delta: `wiring.py` −165 LOC (the body of `_init_signals`), `wiring_signals.py` +165 LOC new file, net neutral but cohesion is now per-concern.

## Verification Steps

```bash
wc -l invasion/boot/wiring.py invasion/boot/wiring_signals.py
grep -c "^from\|^import" invasion/boot/wiring*.py
python3 -m py_compile invasion/boot/wiring.py invasion/boot/wiring_signals.py
python3 -c "import invasion.main"
```

Behavior check: `log_event` call count in `_init_signals` identical, total providers registered identical.

## Cross-Review Ask

- **trading_advisor**: confirm that moving signals imports top-level does not affect hot-path latency (init-time only — should be no-op for tick loop).
- **architecture_advisor**: confirm the ImportError preservation pattern for optional provider groups matches intent (fail-per-group, not fail-whole-init).

## Follow-up Phases (Reference)

Each subsequent phase gets its own commit + own plan update section when executed. No phase touches behavior, only call-site of the `from` statement. Dashboard / runtime paths are unaffected — all 66 statements live exclusively inside `_init_*` functions called once at boot.
