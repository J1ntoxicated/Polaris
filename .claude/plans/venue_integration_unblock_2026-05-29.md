# Venue-Integration Unblock — Trustworthy Data Collection Wave

> Execution: TDD per task; every new code change → codex external review (builder≠reviewer); 2× codex consensus = Jin sign-off proxy (autonomous mode). DEMO/PAPER only — aggressive bias preserved, no defensive throttle.

**Goal:** Stop the venue-reject → circuit-breaker-halt cascade so all strategies keep trading on real demo, restore OKX USDT liquidity, size to real balance, and guarantee per-strategy symbol coverage — producing the first trustworthy ground-truth dataset to base profitability optimization on.

**Why (diagnostic evidence, data/diag.sqlite 2026-05-28):** OKX demo equity $65k but only $2,211 liquid USDT (rest = orphan altcoin bags). Sizing assumes hardcoded $79k → $1,244 orders reject (51008). OKX US compliance blocks pairs (51155: GAS, TRUMP). 3 venue rejects → circuit breaker SOFT_HALT tsmom → all OKX trading stopped after tick 1. G3 kills=0 (the "73%" was stale). fx_breakout majors never selected into dynamic focus (single global liquidity rank buries them).

**Architecture:** (1) Propagate venue reject reason through the open path via a small `OpenAttempt` result. (2) Venue open no-fills are external events, NOT strategy faults — release+skip without tripping the strategy circuit breaker; compliance rejects (51155) add the symbol to a runtime non-tradeable blocklist consumed by focus + order guard. (3) Reconcile sizing equity to real venue available balance each cycle. (4) Pin each strategy's declared symbols into focus so fixed-symbol Capital strategies always get bars. (5) One-off orphan liquidation restores USDT.

**Tech stack:** Python 3.13, httpx async, sqlite3 stdlib, pytest, mypy --strict, ruff. Files ≤500 LOC.

---

## File map

- `polaris/scripts/_production_pipeline.py` — open path: add `OpenAttempt`, reject classification in `reserve_and_submit`; stop faulting strategy on external venue reject.
- `polaris/scripts/_smoke_roundtrip_shared.py` — `real_okx_open_fill` / `real_capital_open_fill` return reject code/msg (currently `Fill | None`).
- `polaris/core/isolation/blocklist.py` *(new, small)* — runtime non-tradeable symbol set (in-memory + SQLite-persisted) keyed by `(venue, symbol)` with reason.
- `polaris/scripts/_production_layers.py` — `refresh_focus_watchlist`: merge pinned strategy symbols + exclude blocklisted; `get_focus_targets` raise `max_n`.
- `polaris/strategies/base.py` — `StrategyMetadata.pinned_symbols: frozenset[str] = frozenset()`.
- `polaris/strategies/{fx_breakout_basket,xau_indices_trend,session_breakout}.py` — set `pinned_symbols`.
- `polaris/scripts/_production_balance.py` *(new, small)* — fetch OKX availBal(USDT) + Capital available → reconciled equity for sizing.
- `polaris/scripts/production_paper_loop.py` — wire reconciled equity + blocklist into the tick.
- `polaris/scripts/liquidate_okx_orphans.py` *(new, one-off ops)* — sell OKX non-USDT balances → USDT.

---

## Task 1: Propagate venue reject reason (`OpenAttempt`)

**Files:** Modify `_smoke_roundtrip_shared.py` (real_okx_open_fill / real_capital_open_fill), `_production_pipeline.py` (`_real_open_fill`); Test `tests/test_venue_reject_classification.py`.

Design: introduce
```python
@dataclass(frozen=True, slots=True)
class OpenAttempt:
    fill: Fill | None
    deal_id: str | None = None
    reject_code: str | None = None   # venue code e.g. "51155","51008"
    reject_msg: str | None = None
```
`real_okx_open_fill` already sees the OKX response `code`/`msg` on failure — return `OpenAttempt(fill=None, reject_code=code, reject_msg=msg)` instead of `None`; on success `OpenAttempt(fill=fill)`. Capital analogously (HTTP error code / errorCode). `_real_open_fill` returns `OpenAttempt`.

- [ ] Step 1: failing test — `real_okx_open_fill` returns `OpenAttempt(reject_code="51155")` when adapter returns code 51155 (use a fake adapter). FAIL.
- [ ] Step 2: implement reject-code capture in the open helpers + `_real_open_fill` return type → `OpenAttempt`.
- [ ] Step 3: tests pass; `mypy --strict` + `ruff` clean.
- [ ] Step 4: commit.

## Task 2: Stop strategy halt on external venue reject + classify (#8 + #11 seam)

**Files:** Modify `_production_pipeline.py` `reserve_and_submit` (lines ~237-252); Test same file.

Replace the unconditional `record_fault(FAULT_REJECT)` on real-open no-fill with classification:
- `reject_code == "51155"` (compliance) → `blocklist.add(venue, symbol, reason="compliance")`, release reservation, **no strategy fault**, return None.
- `reject_code in {"51008", insufficient-balance}` → release, **no strategy fault** (portfolio/sizing — Task 4 prevents), return None. Increment a `state.balance_skips` counter for observability.
- any other reject / generic no-fill → release, **no strategy fault** (venue external), return None. (Aggressive bias: venue decisions don't halt strategies. Idempotency-conflict fault at line ~200 stays — that is internal.)

- [ ] Step 1: failing test — feeding a 51155 `OpenAttempt` does NOT call `record_fault` and DOES add to blocklist; a 51008 does not fault; circuit breaker stays ACTIVE after 3 venue rejects.
- [ ] Step 2: implement classification branch.
- [ ] Step 3: tests pass; mypy + ruff clean.
- [ ] Step 4: commit.

## Task 3: Runtime non-tradeable blocklist consumed by focus + order guard (#11)

**Files:** Create `polaris/core/isolation/blocklist.py`; Modify `_production_layers.py` (`refresh_focus_watchlist` excludes blocklisted) + `reserve_and_submit` (skip blocklisted before reserving); schema table `venue_blocklist(venue,symbol,reason,ts_ms)`; Test `tests/test_venue_blocklist.py`.

- [ ] Step 1: failing test — `is_blocklisted(conn, "okx","GAS-USDT")` True after `add_blocklist`; focus excludes blocklisted; `reserve_and_submit` skips blocklisted (no reservation).
- [ ] Step 2: implement table + add/is_blocklisted/load; wire exclude into focus + an early skip in reserve_and_submit.
- [ ] Step 3: tests pass; mypy + ruff clean.
- [ ] Step 4: commit.

## Task 4: Reconcile sizing equity to real venue available balance (#9)

**Files:** Create `polaris/scripts/_production_balance.py`; Modify `production_paper_loop.py` (fetch balance at boot + every N ticks, pass into pipeline as the equity used by sizing) + `_production_pipeline.py` (use reconciled equity instead of `EQUITY_USD_DEMO_DEFAULT` constant when available); Test `tests/test_equity_reconcile.py`.

Design: `fetch_okx_available_usd(adapter)` = USDT `availBal` (cash mode binding constraint for SPOT longs). `fetch_capital_available_usd(session)` = account available. Reconciled equity per venue feeds sizing so proposed notional is bounded by real liquid funds. Keep aggressive caps (% of *real* available, not a defensive cut). Fall back to constant if balance fetch fails. Floor of e.g. $200 so dust doesn't zero out sizing.

- [ ] Step 1: failing test — given a fake OKX balance response (USDT availBal=2211), `fetch_okx_available_usd` returns 2211.29; sizing uses reconciled equity when provided.
- [ ] Step 2: implement balance fetch + thread reconciled equity into sizing seam.
- [ ] Step 3: tests pass; mypy + ruff clean.
- [ ] Step 4: commit.

## Task 5: Per-strategy symbol coverage pinning (#2)

**Files:** Modify `polaris/strategies/base.py` (StrategyMetadata.pinned_symbols), the 3 Capital strategies (set pinned_symbols = their symbol frozenset), `_production_layers.py` (`refresh_focus_watchlist` merges pinned present-in-universe symbols at front ranks; `get_focus_targets`/`FOCUS_CYCLE_TARGET` raised to cover pinned + dynamic, ≤ FOCUS_TARGET_MAX 48); Test `tests/test_focus_pinning.py`.

Design: collect `{(venue,symbol)}` from all strategies' `pinned_symbols`; for those present in active universe but absent from dynamic focus, prepend FocusSelection rows (rank before dynamic). Blocklisted symbols excluded. `get_focus_targets` max_n raised so pinned never truncated.

- [ ] Step 1: failing test — with EURUSD in universe but not in dynamic top-N, after pinning EURUSD IS in `get_focus_targets`; blocklisted pinned symbol excluded.
- [ ] Step 2: add field + set on 3 strategies + merge logic + raise max_n.
- [ ] Step 3: tests pass; mypy + ruff clean. (Note: AUDUSD absent from Capital universe — discovery gap, out of scope; 4/5 majors sufficient.)
- [ ] Step 4: commit.

## Task 6: One-off OKX orphan liquidation (#10)

**Files:** Create `polaris/scripts/liquidate_okx_orphans.py`; manual run, not in loop.

Design: fetch_balance → for each non-USDT ccy with eq>$threshold and a tradeable `{ccy}-USDT` SPOT pair (skip compliance-blocklisted + dust), place market SELL of full availBal. Report recovered USDT. Idempotent-ish (re-run sells remaining). DEMO reversible.

- [ ] Step 1: dry-run mode lists intended sells + est USD without submitting.
- [ ] Step 2: run dry-run, eyeball, then live-run to recover USDT.
- [ ] Step 3: confirm USDT availBal jumps; commit script.

---

## Out of scope (after first data)
- Sizing knob bumps (base 2%→3-4%, total_daily 10%→15%, CS-3) — hybrid: apply after first data batch.
- OKX 4-axis universe breadth (6 symbols; spread filter rejects 104) — tuning after data.
- Capital 429 rate-limit hardening; AUDUSD/XAUUSD discovery gaps.
- fx_breakout generalization to dynamic FX universe (strategy redesign).

## codex R1 corrections (BINDING — supersede task bodies above where conflicting)
- **Sequence**: liquidate(#6/Task6) → equity-reconcile(Task4) → no-fault(Task2) → blocklist(Task3) → pinning(Task5). Equity before no-fault else 51008 = invisible churn. (Liquidation first makes liquid USDT≈$65k so 51008 mostly vanishes immediately.)
- **Task2 (D1)**: keep faulting INTERNAL/client rejects (bad size/symbol format/idempotency/adapter-contract); exempt ONLY external (51155/51008/transport no-fill). Add `state.venue_rejects_by_code` counter + DB telemetry.
- **Task3 (D2)**: `venue_blocklist UNIQUE(venue,symbol)` + UPSERT (reason/code/first_ts/last_ts/count); LOAD at boot; guard in-memory set with process lock (concurrent TaskGroup fan-out = worker.py:294); 51155 permanent, transient codes TTL/reprobe.
- **Task4 (D3) CALL-SITE FIX**: equity is read via `production_default_equity_usd()` in `polaris/scripts/_production_run_signal.py:123-150` → `build_sizer_payload` (NOT `_production_pipeline.py`). Thread `equity_usd_by_venue` through `_run_tick → run_pipeline_for_signal → build_sizer_payload`. Subtract pending in-tick reservations from available cash before sizing each concurrent task (fence serializes order-key, not cash). Floor = min-order-viability check only, never inflate `PortfolioState.equity_usd`. Fetch-fail → stale-last-known + age telemetry (loud), not silent constant.
- **Task5 (D4)**: pinned ADDITIVE within FOCUS_TARGET_MAX=48 (don't displace dynamic top-N); de-dupe pinned/dynamic; raise FOCUS_CYCLE_TARGET + get_focus_targets(max_n) together; add Capital 429 + ingest telemetry.
- Each task → codex code review (R2). 2× consensus = sign-off.

## Verification (whole wave)
Re-run `ignite_p1 --paper --real-roundtrip --db data/diag2.sqlite` for ~10 min after fixes: expect strategies stay ACTIVE (no SOFT_HALT from venue rejects), OKX orders fill (USDT sufficient), fx_breakout symbols in focus, fills accumulate across multiple strategies, faults≈0. Then launch the long collection loop.

---

## 2026-05-29 자율 실행 wave (대시보드 복구 + dynamic viz + 잔여 plan tasks)

> Jin: "봇은 둬도 되는데 대시보드가 없어졌고 다이나믹 비주얼라이제이션 해줘. 플랜에 넣고 추천 순서대로 진행." 오토모드.
> 봇 PID 96290 (`data/polaris_live.sqlite`) 건드리지 않음. DEMO/PAPER, aggressive bias 유지.

### Root cause: 대시보드 실행이 Claude 창을 닫던 정체
- `scripts/start_dashboard.sh` 1c "Aggressive tty cleanup" 가 `$$`(현재 셸) tty 를 "Claude 창"으로 간주 → Claude Code 가 Bash 도구로 실행하면 그 tty 가 Jin 실제 창과 달라 **Jin 창을 닫아버림**.
- 수정: 기본 OFF 반전 (opt-in `AGGRESSIVE_TTY_CLEANUP=1` 일 때만) + 기본 DB → `data/polaris_live.sqlite`. → [[feedback_never_kill_claude_session]].

### 실행 순서 (추천)
- [x] **A. 대시보드 복구** — `SKIP_TTY_CLEANUP=1 POLARIS_DASH_DB=data/polaris_live.sqlite ./scripts/start_dashboard.sh` (PID 떴고 라이브 DB) + start_dashboard.sh 재발방지 패치.
- [x] **B. #1 OKX 고아 청산 — dry-run 후 `--live` SKIP 결정.** dry-run 결과: USDT availBal=**$35,815** (플랜 진단 $2,211 아님 → "USDT 고갈" 이미 해소, 청산 동기 소멸). "고아" 5건(SOL $19k/NEAR $4k/SUI $3.5k/LUNA $2.7k/LTC $1.2k)은 대부분 **현재 봇 PID 96290 의 활성 포지션** — `--live` 시 봇 라이브 포지션을 뒤에서 청산해 수집 데이터 오염 + 역방향 고아. LTC 51008 = 봇 ledger(24.49) > availBal(23.86) 소규모 드리프트, no-fault 노이즈. → 봇 정지 후 클린 재시작 시점에만 의미. 스크립트는 검증됨(commit 대상).
- [ ] **C. #3 미커밋 정리 (Bayesian edge-validation Phase 1)** — `posterior.py`(edge_verdict)+`_production_close_effects.py`+`_primitives.py`+dashboard edge 패널+`test_edge_validation.py`+`test_dashboard_v1.py`. 전체 pytest green → fresh-Claude 외부 리뷰(builder≠reviewer) → commit (start_dashboard.sh fix 동봉).
- [ ] **D. Dynamic visualization (dashboard_v2)** — 신규: (1) 자산/누적PnL **스파크라인**(in-process ring buffer, 매 refresh 갱신), (2) DD·활성노출·AI예산·승률 **바 게이지**(unicode block+색상), (3) cell matrix **히트맵**. 순수함수 TDD → 리뷰 → commit → 대시보드 재시작. 표시 전용, throttle 無.
- [ ] **E. #2 equity reconcile (plan Task 4)** — `_production_balance.py` 신규: OKX availBal+Capital available → 실 가용 사이징. codex R1 §Task4 call-site 따름. TDD → 리뷰 → commit.

### Out (이번 wave): plan Task 5 pinning / fx_breakout 0-signal / xau US100 mismatch / ts_ms drift verify.
