---
type: digest
status: archived
date_created: 2026-06-11
tags: [now-archive, handover]
---

# _NOW 아카이브 2026-06-11 p5/6 — P0 Day 5-7 완료 기록 + P0 sprint coherence verdict

(2026-06-11 _NOW 다이어트로 원문 무손실 이동 · 원본 [[_NOW]])

**P0 Day 5 완료 (2026-05-07)**: Venue adapters full + fill normalizer + paper loop smoke + dashboard v0.
- OKX `signing.py` (HMAC-SHA256) + `OKXAdapter` (IOC px clamp 5bps, clOrdId sanitize, balance/positions/orders endpoints) + `constraint_translator.py` (lotSz/minSz/tickSz).
- Capital `session.py` (CST + X-SECURITY-TOKEN, 9-min anti-idle, 401 auto-refresh, 1/s rate cap) + `CapitalAdapter` (open/workingorder/close/list/confirm) + `constraint_translator.py` (min_deal_size/step/leverage).
- `polaris/core/data/fill_normalizer.py` — unified `Fill` + OKX/Capital normalizers (with leverage param).
- Scripts: `smoke_paper_loop.py` (519 LOC) + `_smoke_fills.py` + `_smoke_real_probes.py` + `_smoke_haiku_stub.py` + `dashboard_v0.py` (369 LOC).
- 5 new test files (53 tests) + 2 codex regression tests.
- **Codex Day 5 review = REJECT_WITH_FIXES → 2 P1 fixed** (Capital ts naive→UTC + Capital fill leverage in notional). 1 P2 deferred (`update_baseline_from_bars` iterator — Day 1 code).
- 339 pytest pass · mypy strict 86 files clean · ruff clean.
- **Real demo trades confirmed**: OKX BTC-USDT IOC filled 0.00012262 BTC at $81,514 (ordId 3542763044948807680). Capital EURUSD 100-lot open+close at level 1.1752.

**P0 Day 6 완료 (2026-05-07)**: Full pipeline G3-G7 plumbing + fills ledger + P1.0 ignition wire + Day 1 P2 fix.
- `polaris/core/pipeline/payload_builder.py` (5 builders for G3/G4/G5/G6/G7) + `polaris/core/data/fills_persist.py` (idempotent persist + recent-fills query) + `polaris/scripts/_smoke_real_roundtrip.py` (OKX + Capital demo round-trip with dry-run mode) + `polaris/scripts/ignite_p1.py` (P1.0 entry point) + `polaris/scripts/smoke_day6_full_pipeline.py`. `fills` DDL + 3 indexes added to `polaris/storage/schema.py`. Day 1 P2 fix: `update_baseline_from_bars` materializes iterator + recompute anchored at batch max_ts per (instrument, group).
- **Codex R1 R2 R3 review**: REJECT (3 P0 + 2 P1 + 1 P2: paper-mode db_path / OKX close base_ccy semantics / round-trip ok=True without close persisted / baseline batch max_ts / orders fallback nonexistent column / persist_bars contract) → REJECT_WITH_FIXES (2 P1: outer try/except swallowed close-leg exceptions without step + open_fill_id) → **APPROVE**.
- 385 pytest pass (+46 new), mypy strict 91 files clean, ruff clean.
- Smoke 6s 2-tick: 9 full_pipeline_runs · 9 sized (100%) · gate_pass_counts={6: 9, 7: 9} · 12 fills_persisted from sized + 4 round-trip = 16 total · 3 closed_trades · custom db_path honoured · vault appended.
- Dashboard panel reads from `fills` table directly (size_usd / fee / slippage / pnl_usd / is_close).

**P0 Day 7 완료 (2026-05-07)**: 30-min ignition smoke + 24h watchdog readiness.
- `tests/test_day7_ignition_health.py` (+680 LOC, 18 probes) — auth/isolation/fills/dashboard/kill-switch/drawdown-gap/composite + `polaris/scripts/_smoke_real_roundtrip.py` `resolve_okx_base_url` extraction (urlparse-based, sub-domain bypass-safe).
- **Codex R1 R2 R3 review**: REJECT_WITH_FIXES (3 P0 + 4 P1 + 2 P2: kill-switch lie / composite-learner lie / vault leak / env override static / drawdown weak / cell_total vacuous / race-prone / sqlite leaks) → REJECT_WITH_FIXES (2 P0 + 4 P1: rc=1 too permissive / NOW.md leak blind spot / env override still static / drawdown gap weak / cell_total vacuous / cancel race) → **APPROVE_WITH_NITS** (3 NITS all addressed: drawdown docstring narrowed / urlparse netloc-equality / integration-coverage indirect accepted).
- 406 pytest pass (+21 = 18 day7 + 3 incidental), mypy strict 92 files clean, ruff clean.
- Real-demo 30-min smoke: 736 fills (184 closed) · $133,695 notional · +$167 PnL · 0 pipeline kills · 3 active cells (n_eff=63-65 each) · 4 learner types × 15+ keys · 3 hourly snapshots.

**현재 단계**: P0 Day 7 완료 → P1.0 ignition fired (PID 57257, `--paper --duration 86400 --tick 5 --full-pipeline --real-roundtrip`).

**P0 sprint coherence verdict (2026-05-07 08:37, codex gpt-5.4 R1 = CONDITIONAL PASS)**: primitives complete (415/415, ruff+mypy clean, real demo trades) but **4 P0 cross-layer integration gaps + 7 P1** detected by cumulative review. Day-by-day reviews missed these because each Day reviewed self-consistency, not whole-stack assembly. ignite_p1 keeps running — fixes restart-only.
- **P0 blockers (Day 8 PR — bundle together)**: A2 AllocatorFence not wired in submit path / A3 supervisor + circuit_breaker not wired (raw `asyncio.create_task`) / A5 Layer 0 disconnected from dispatch (`FOCUS` hardcoded) / A6 Layer 1 ingest not driven (`bars/positions/orders/signals/quote_ticks` all 0 rows after 11min, hard caps non-binding because position_risk_state empty).
- **P1 (this week)**: A1 session×regime not multiplied into T4 / A4 Layer 6 dirty sweep not running / A7 regime+session hardcoded vacuous / A8 `emitted[:3]` cap throttles 7→3 strategies / X1 max_hold unwired / X2 idempotent order keys unwired / X3 G6 swap predicate not Layer 6 SSOT.
- **Refuted**: C1 Kelly-not-multiplied = doc clarification only (ADR-005 §Priority already says Kelly governs cap, not chain factor).
- **Aggressive bias**: 0 defensive throttles introduced, all 4 P0 fixes preserve hard MAX `headroom_min()` 1-call contract + cell mult clip-전 placement + top ×1.5 amplify + G8 P0/P1 split.
- Digest: [[2026-05-07_p0_sprint_cumulative_review]] (40_ops/digests).
