# MODULE REVIEW — `invasion/ai/live.py` 1088L split plan (F-N17)

_Owner: Dev (Sonnet). Cross-review: security_advisor._
_Referenced by: CLAUDE.md code size limits (>1000 = P0 split)._

## 1. File Snapshot
- **Path**: `invasion/ai/live.py`
- **Lines**: 1088 (>1000 P0 threshold)
- **Role**: `ai_advisor` live implementations — multi-provider (GPT/Claude/Gemini) dispatch + prompt construction + cache_blocks handling + AI decision post-processing (contrarian override, confidence gate, text fallback)
- **External importers**: `boot/wiring.py` (8 classes), `boot/run.py` (module), `data/candle_cache.py` (`_call_gemini` helper), tests.
- **Locks unaffected**: `AIController` lock (commit 22873285) lives in `ai/orchestrator.py` — this split does not touch it.
- **Asymmetry (commit 87f0127)**: `composite_score * 0.8x` dampens removed — PRESERVED (no dampen logic reintroduced).

## 2. Block Map

| Block | Lines | Category | Risk | Notes |
|-------|-------|----------|------|-------|
| imports + `_format_provider_scores` + `_trade_id_from_pos` | 1–52 | util | low | pure helpers, no side effects |
| URL constants + `_extract_json` | 54–82 | provider/parse | low | `json_repair` wrapper |
| `_call_gpt` | 85–136 | provider API | low | `requests.post` wrapper, logs on error |
| `_call_gemini` | 139–167 | provider API | low | **re-imported by `candle_cache.py` — must remain importable from `ai.live`** |
| `_call_claude` + cache_blocks handling | 170–243 | provider API | low | 3-way precedence `cache_blocks > system_cached > system` |
| `_gpt_cost` + `_claude_or_gemini` dispatcher | 253–324 | provider routing | medium | reads preg `ai_provider_mode`, deadline-aware fallback |
| `LiveSignalAugmenter` | 329–418 | stage S1 | medium | contrarian override logic (fear regime) — asymmetry-critical |
| `LiveEntryJudge` | 423–530 | stage S3 | medium | confidence-based gating + same-group hard cap |
| `LiveProactiveExit` | 535–606 | exit (periodic) | medium | rate-limited TIGHTEN |
| `LiveRegimeAdviser` | 611–663 | regime | low | pure advice list |
| `LiveWSPriceIntel` | 668–744 | exit (WS) | medium | BEP-zone trail tighten |
| `LiveExitAdviser` + `_fallback_text_parse` | 749–909 | stage S4 | high | critical trigger path + text fallback parser |
| `LiveStrategyEvolution` | 914–1012 | stage S5 | medium | Architect flag + new_strategies payload |
| `LivePortfolioIntelligence` | 1017–1090 | stage S6 | low | hourly review |

## 3. Planned Target Layout (multi-step, this MSG does step 1 only)

| New module | Moved blocks | Rationale |
|------------|--------------|-----------|
| `ai/live_providers.py` | `_extract_json` + `_call_gpt` + `_call_gemini` + `_call_claude` + URL constants | Pure HTTP/JSON glue; no stage semantics. Safe lift. |
| `ai/live_dispatch.py` (future) | `_gpt_cost` + `_claude_or_gemini` | Provider routing (depends on providers). Separate for testability. |
| `ai/live_exit.py` (future) | `LiveProactiveExit` + `LiveWSPriceIntel` + `LiveExitAdviser` + `_fallback_text_parse` | Exit-stage cluster; 300+ lines together. |
| `ai/live_entry.py` (future) | `LiveSignalAugmenter` + `LiveEntryJudge` | Pre-entry cluster. |
| `ai/live.py` (remaining) | Stage S5/S6 + utils + re-export shim | Thin facade for back-compat (`boot/wiring.py`, `candle_cache.py`). |

## 4. Step-1 Extraction (this sprint): `live_providers.py`

**Scope**: lines 54–243 (URLs + `_extract_json` + `_call_gpt` + `_call_gemini` + `_call_claude`).

**Back-compat shim in `live.py`**: re-export with
```python
from .live_providers import (
    GEMINI_URL, CLAUDE_URL, OPENAI_URL,
    _extract_json, _call_gpt, _call_gemini, _call_claude,
)
```
so `candle_cache.py` `from ..ai.live import _call_gemini` continues to resolve.

**Behavior preservation checks**:
- `_call_claude` cache_blocks precedence unchanged (`cache_blocks > system_cached > system`).
- `_extract_json` `json_repair` semantics unchanged.
- `log_event` import still required in providers module.
- `requests` / `json` / `time` re-imported inside `live_providers.py`.

## 5. Out-of-Scope Guarantees
- Commit 87f0127 dampen removal — **untouched**. No `composite_score * 0.x` reintroduction.
- Commit 22873285 `AIController._lock` — **not in this file** (lives in orchestrator). Split cannot affect.
- Asymmetry 북극성 — logic only moved, not edited.

## 6. Security Cross-Review Notes (for security_advisor)
- Keys (`cfg.openai_key`, `cfg.anthropic_key`, `cfg.gemini_key`) consumed via closures; extraction does not change surface — secrets stay in `cfg`, passed by argument.
- No new disk/network side effects introduced; `requests.post` identity preserved.
- Exception swallowing: both `_call_*` paths already `log_event(..., "warn")` — no `try/except pass` added.

## 7. Verification Matrix
```bash
wc -l invasion/ai/live.py invasion/ai/live_*.py
python3 -m py_compile invasion/ai/live.py invasion/ai/live_providers.py
grep -n "composite_score\s*\*\s*0\." invasion/ai/live.py   # expect 0
python3 -c "import invasion.main"
python3 -c "from invasion.ai.live import _call_gemini, _call_claude, _call_gpt, _extract_json"
python3 -c "from invasion.data.candle_cache import _call_gemini"  # indirect re-export path
```

## 8. Commits
1. `docs(msg-fn17-ai-live-plan jin p1): ai/live.py 1088L split plan`
2. `refactor(msg-fn17-ai-live-providers jin p1): _call_gpt/gemini/claude + _extract_json extraction`
