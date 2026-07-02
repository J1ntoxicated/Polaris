# tests/e2e
Dashboard browser E2E (Playwright, headless-only), excluded from default `pytest` run
(`-m 'not e2e'` in `pyproject.toml`). Needs `./scripts/start_dashboard.sh` running first,
else tests skip.
Run: `python3 -m pytest tests/e2e -m e2e -q`
