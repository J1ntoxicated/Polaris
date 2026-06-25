---
type: research
status: built-reviewed
date_created: 2026-06-25
tags: [incident, dependency, degrade-never-crash, yahoo-bars, flow_not_block]
---

# yfinance import guard — live-incident hardening

## Incident
Yahoo bars (`polaris/scripts/_yahoo_bars.py`) deployed; bot crashed on start.
`yfinance` was missing from `.venv`, so the top-level `import yfinance as yf`
raised `ModuleNotFoundError`. `_production_bars.py` imports `fetch_yahoo_bars`
from that module → the error propagated up the whole `ignite_p1` import chain →
`botctl start ok=False`. Manual recovery: `.venv/bin/pip install yfinance==1.2.0`.

Root class: an optional runtime dep on a hard import path with no env declaration
+ no degrade path. One absent wheel takes the whole bot down.

## Fix (two parts, surgical)
1. **Declared dep** — `pyproject.toml [project].dependencies += "yfinance==1.2.0"`.
   It is the SSOT (no requirements.txt). Fresh env / other machine now auto-installs.
   Exact pin = reproducible deploy of the dep whose absence caused the incident.
2. **Import guard** (`_yahoo_bars.py`) — `import yfinance` wrapped in
   `try/except ImportError` → `_YF_AVAILABLE` flag (`yf = None` on absence) + one
   warning at import. `fetch_yahoo_bars` early-returns `[]` when `_YF_AVAILABLE is
   False`, BEFORE any `yf.` access / resolve / network. The existing
   `fetch_bars_one` path (`if yahoo_bars: ... else exchange fallback`) then reaches
   the exchange bar fetch. **degrade-never-crash**: yfinance absent → bot still
   boots, exchange bars still work. flow_not_block — gates no entry/size/exit.

The only `yf.` use (`_yf_history_blocking`) is reachable solely via
`fetch_yahoo_bars`, so the short-circuit makes the absent path never touch `yf`.

## LOC split
`_yahoo_bars.py` was 499/500. The guard pushed it over, so the pure converter
(`_to_float` + `_yahoo_df_to_bars`, no yfinance dep) moved to a new sibling
`polaris/scripts/_yahoo_frame.py` (re-exported back). Converter logic byte-identical;
no circular import (`_yahoo_frame` imports only `Bar`). `_yahoo_bars.py` now 444 LOC.

## Verification
- TDD: absent→`[]` (and `_yf_history_blocking` never called); module imports without
  raising under a `meta_path` block + `importlib.reload` (state restored in `finally`);
  exchange fallback reached; present-behavior unchanged.
- 45/45 yahoo tests · mypy --strict clean · ruff clean · LOC 444 ≤ 500 · 거부 키워드 0.
- Fresh-Claude adversarial review: **APPROVE**.
- In-process repro: meta_path-blocked yfinance → module imports, `_YF_AVAILABLE=False`,
  `fetch_yahoo_bars→[]`, single warning. Incident reproduced and proven fixed.
