# Active-Trading Maximization Plan — 5 Parallel Build Groups (2026-07-09)

DEMO/PAPER only · goal = **virtual meaningful fills → hundreds/day across many strategies+venues**.
Invariants (all groups): ① REAL byte-identical (only `_virtual_loosen`/direct-env pattern) ② non-degrading
③ 9-stack / -1.0R rail / sizing / **caps untouched** ④ flow_not_block. Base = main `7b00a23` (virtual-active-unleash).
No file appears in two groups → parallel worktree-safe. Source audits: rsi_bb_inert / alpaca_silence / universe_width /
timeframe_cadence / dedup_cooldown (2026-07-08 overnight, PID 7988).

---

## Group 1 — `virtual/rsi-bb-warmup-revival`  (silent-INERT fix, part A)

**Goal**: un-starve `rsi_bb_pullback` (0/2244) — feed `ma_200` enough real bars + loosen the compound trigger in virtual.

**`polaris/scripts/_production_bars.py`** — `_BAR_FETCH_LIMIT_BY_INTERVAL`: `{"1D": 260}` → `{"1D": 260, "15m": 400}`;
update `bar_fetch_limit_for` docstring to note 15m→400.
- *Why 400*: measured ~50-56% of OKX 15m bars are forward-fill synthetic (flat OHLC or vol≤0); `ma_200` needs **200 real**
  bars, so a 240 window (~120 real) left only 14% of the 227-symbol universe computable. A 400 window at the ~50% real
  fraction yields ~200 real → clears `ma_200` for the majority. DB already holds 1300+ 15m bars/symbol → deeper read just
  slices a longer existing tail (additive, no new fetch; OKX 15m is NOT the Alpaca-429-bounded path). Mirrors the existing
  1D→260 warmup-depth precedent. Infra/all-modes: lowers no threshold, changes no edge; REAL rsi_bb stays dispatch-off.

**`polaris/strategies/rsi_bb_pullback.py`**
- `RSI_THRESHOLD = virtual_loosen(55.0, 39.0)` (was `50.0, 39.0`). *Why 55*: live alt RSI(14) cluster sat 50.9–65 during
  the unleash window so the 50 midline caught almost none; 55 admits the low-50s dips (CFX/SOL 50.9) while BB-proximity +
  `close>ma_200` still enforce the pullback-in-uptrend identity. REAL 39 byte-identical.
- New named const `BB_TOUCH_MULT = virtual_loosen(1.004, 1.0)`; in `generate_raw_signal` change
  `if bb_lo is None or last.low > bb_lo:` → `... last.low > bb_lo * BB_TOUCH_MULT`. REAL 1.0 = exact band pierce
  (byte-identical); virtual 1.004 = low within 0.4% above the lower band. *Why 0.4%*: typical OKX 15m true-range on liquid
  alts >0.4%, so a bar that dipped to within 0.4% of the band is a real band-proximity pullback the exact-pierce rule was
  discarding; 0.4% sits inside one bar's range so no non-pullback bar is admitted.
- `close > ma_200` kept exactly (identity anchor); `warmup_bars`/`TREND_FILTER_MA` unchanged (Group-1 fetch raise feeds it).

**Tests**: (a) REAL (env unset) byte-identical — low just above `bb_lower`, rsi 45 → REAL emits None, virtual emits.
(b) synthetic 400-bar 15m series with 50% flat bars → `ma_200` computes (not None). (c) dispatch-SSOT guard test green.

**Activity effect**: rsi_bb 0 → tens/day (14%→majority symbol coverage × looser 3-way AND) on the OKX 15m spot track.

---

## Group 2 — `virtual/alpaca-fill-route-equity-readmit`  (silent-INERT fix, part B + CONFIRMED misrouting)

**Goal**: stop Alpaca fills being mis-ledgered as Capital, and re-admit the 2 fee-bleed equity 1D strategies in virtual.

**`polaris/scripts/_smoke_fills.py`** — fix the CONFIRMED misrouting (alpaca fell into `normalize_capital_confirm` →
`venue="capital"`, `instrument_id="capital:{epic}"`, `pip_value_usd=10` → every Alpaca fill stored as a fake $10 CFD).
- Add `_alpaca_fill_payload(...)` (mirror `_capital_fill_payload`) → `{"status":"filled","symbol":sym,
  "filled_qty":qty,"filled_avg_price":price,"id":uuid,...}` matching `normalize_alpaca_fill`'s reads; `qty = notional_usd/price`
  (unlevered cash 1:1 — no magic $10).
- `simulate_open_fill` (okx/else at :97/:110) → 3-way: `if okx / elif venue=="alpaca": normalize_alpaca_fill(...) / else capital`.
- `simulate_close` (okx/else at :136/:148) → same 3-way; close qty = `trade.notional/exit_price`.
- *Why*: `normalize_alpaca_fill` already exists (`fill_normalizer.py:283`) and stamps `venue="alpaca"`, bare-equity
  `instrument_id`, `size_usd = qty×price` — the correct unlevered ledger. Fixes the Capital P&L / cap contamination.

**`polaris/strategies/__init__.py`** — extend the existing `if virtual_mode_enabled():` re-registration tuple (4 ids) to
add `equity_52wk_high_breakout` + `equity_vol_expansion_pocket_pivot` (both `timeframe="1D"`, `venue="alpaca"`), and add
their imports. Update the `(NOT the two equity ids — Alpaca-inert on SIP #42)` comment to reflect the re-admit.
- *Why safe*: 1D Alpaca bars are confirmed flowing — `connors_rsi2` (same alpaca 1D feed) fired 2 fills. SIP #42 gated the
  *intraday* SIP feed, not the daily-bar route. Their KILL was B1-prune **fee-bleed** → VOID in virtual (same rationale as
  the existing 4 re-admitted ids).
- **Do NOT** re-admit `equity_tsmom` / `equity_gap_go` / `equity_rsi_bb_pullback`: their 2026-06-26 KILL was gross-negative
  **before** fees (negative even fee-free) → re-admitting manufactures losing churn, not meaningful activity (violates 비퇴화).

**Tests**: `simulate_open_fill(venue="alpaca")` → `Fill.venue=="alpaca"`, `size_usd==qty*price` (≠10.0); close round-trip
likewise; okx/capital branches byte-identical. With env=1 registry holds the 2 equity ids and `all_strategies()` dispatches
them; env unset → registry byte-identical. **Firing-path (verify_firing mandate)**: 1D series at/near 52wk-high in uptrend →
`equity_52wk` emits; no-op series → no crash (degrade-never-crash).

**Activity effect**: Alpaca track connors-only (2/night) → 3 strategies on the US 1D close, all fills correctly attributed.

---

## Group 3 — `virtual/cci-capital-universe-widen`  (universe widen)

**Goal**: lift `cci_reversion` (~6/night) off its gold+index-only universe onto the full Capital CFD reversion pool.

**`polaris/strategies/cci_reversion.py`** — drop `from ...xau_indices_trend import SUPPORTED_SYMBOLS`; define cci's OWN set,
virtual_loosen-wrapped so REAL stays byte-identical:
```
SUPPORTED_SYMBOLS = virtual_loosen(
    frozenset({"XAUUSD","GOLD","US500","US100","DE40","UK100","EU50","US30",
               "EURUSD","GBPUSD","AUDUSD","USDJPY","USDCAD"}),   # +5 Capital FX majors (virtual)
    frozenset({"XAUUSD","GOLD","US500","US100","DE40","UK100","EU50","US30"}))  # REAL byte-identical
```
- FX majors reuse the proven-live `fx_breakout_basket.BASKET_SYMBOLS`; all are `venue="capital"` (cci's venue) → no
  cross-venue routing. Add `"SILVER"`/other spellings only if the active discovering-universe watchlist actually lists them
  (confirm at build). Capital epics carry suffixes (`AUDUSD_ZERO`, `EURUSD_W`) → reuse `fx_breakout_basket`'s 3-line
  `_normalize_basket_symbol` suffix-strip (`split("_",1)[0]`) — replicate inline (no new cross-module dep).
- *Why*: CCI is a universal oversold oscillator; confining it to gold+index directly caused the 3-signals/7h scarcity.
  FX majors already trade live on Capital (fx_breakout_basket) → within-venue, non-degrading, doubles-plus the candidate
  pool. No crypto (crypto reversion is served venue-correctly by rsi_bb_pullback / Group 1). `CCI_OVERSOLD` untouched.

**Tests**: env=1 → `"EURUSD" ∈ SUPPORTED_SYMBOLS`, valid CCI cross-up on EURUSD emits; env unset → `"EURUSD"` absent
(byte-identical to gold/index-only today); suffix-strip `AUDUSD_ZERO→AUDUSD` matched.

**Activity effect**: cci ~6/night → multiples; adds FX reversion to the Capital track.

---

## Group 4 — `virtual/reentry-rotation-cooldown-loosen`  (cadence / cooldown loosen — caps untouched)

**Goal**: attack the 2244→45 dedup collapse by halving the virtual re-entry cooldowns (time gates only — NO cap change).

**`polaris/core/isolation/reentry.py`** — read env directly (layering-safe, mirrors `core/sizing/probe_notional.py`):
`_VIRTUAL = os.environ.get("POLARIS_VIRTUAL_ACCOUNT","0")=="1"`; `_COOLDOWN_FACTOR = 0.5 if _VIRTUAL else 1.0`.
In `bar_seconds`: `return int(_BAR_SECONDS.get(timeframe, _DEFAULT_COOLDOWN_SEC) * _COOLDOWN_FACTOR)` (factor applied after
the fail-safe map get; REAL factor 1.0 = byte-identical; min known tf 1m 60×0.5=30 > 0 so guard never disables).
- *Why 0.5*: virtual has no fee/slippage/capital cost to re-entering a name a bar sooner; halving per-bar cooldown ~2× the
  per-symbol re-entry rate for 1H makers/reversion (weekend_thin, funding_capitulation, cci, connors) while bar-novelty
  stays the primary exemption. REAL byte-identical.

**`polaris/scripts/_production_rotation.py`** — `from polaris.strategies._virtual_loosen import virtual_loosen`
(scripts→strategies legal); `ROTATION_VACATED_COOLDOWN_SEC = _env_float("POLARIS_ROTATION_VACATED_COOLDOWN_SEC",
virtual_loosen(150.0, 300.0))` (keep explicit env override). *Why 150*: half the 300s anti-churn window; virtual has no
re-spend cost so a rotated-out victim re-enters on the next strong signal ~2× sooner. REAL 300 byte-identical.

**OUT OF SCOPE (constraint ③ 캡 무접촉)**: `tailored_concurrent_cap`'s `TAILORED_CAP_WIN_RATE_FLOOR` / `CS3_N_THRESHOLD`
are NOT touched — they gate a concurrent-position **cap**, and `CS3_N_THRESHOLD` is shared with Cold-Start sizing
(`sizing/schema.py`). Only cooldowns (time gates) loosen here.

**Tests**: `bar_seconds("1H")` == 1800 (env=1) / 3600 (unset); unknown tf still returns fail-safe default × factor, never 0;
rotation default 150 vs 300 by env; novelty/anchor logic tests unchanged.

**Activity effect**: 1H-class per-symbol re-entry ~2×; rotated names re-enterable ~2× sooner → materially more fills on the
existing 2244-signal stream without touching any cap.

---

## Group 5 — `virtual/dbwriter-migrate-equity-dash`  (residual infra: (d) db_writer migration + (e) equity dashboard)

**Goal (d)**: eliminate the ~64 DB-lock/min from the 3 un-migrated RW conns + the static-ground direct-persist path.
**Goal (e)**: restore live-derived unrealized PnL in the virtual-equity dashboard model (small, self-contained sub-scope).

### (d) db_writer migration
- **`polaris/scripts/_static_ground.py`** (~:372, the `persist_bars(conn, out)` inside `_one`): when `dbwriter_enabled()`,
  replace the manual `SAVEPOINT ground_persist`/persist/RELEASE/ROLLBACK dance with
  `db_writer.submit_job(lambda w: persist_bars(w, out))` (the writer already wraps each job in its own SAVEPOINT →
  per-batch isolation preserved, degrade-never-halt preserved: a dropped/failed job is counted, never raised). Keep the
  existing direct-conn path as the `POLARIS_DBWRITER_ENABLED=0` fallback (byte-identical kill-switch).
- **`polaris/scripts/_production_tick.py`**: migrate the residual WRITE statements on the loop `conn` / `focus_conn` /
  `probe_conn` (the 3 the db_writer docstring lists as "not yet migrated") to `db_writer.submit_job(...)`; **reads stay** on
  their own connection (reader/writer split — only writes move).
- **`polaris/storage/db_writer.py`**: once the 3 conns are migrated, update the docstring (~:20-24) removing "remain
  independent RW connections (not yet migrated)".
- *Why*: audit measured ~64 lock/min from these conns self-competing for WAL's single-writer lock — routing their writes
  through the one serialized writer is the #74 generalization's whole point. No new tuning constant (reuses the queue +
  `POLARIS_DBWRITER_ENABLED` kill-switch).
- **Tests**: full suite green; focused test — under `POLARIS_DBWRITER_ENABLED=1` the static-ground + tick write paths issue
  no direct `BEGIN/COMMIT` on their own conn (all writes queued); measure lock-rate on a smoke run (target 64/min → ~0);
  assert `TRUNCATE` checkpoint still succeeds (single-writer invariant). ⚠️ Own worktree, NOT concurrent with any other
  full-suite build (feedback_single_heavy_workflow_cpu_freeze).

### (e) equity dashboard fix — self-contained (own file, separable worktree)
- **`polaris/storage/virtual_account_equity.py`** `_unrealized_pnl_now`: current body reads `positions.upnl_usd`, a column
  absent from schema → it always returns 0.0 (dead; dashboard virtual-equity is realized-only). Re-implement to LIVE-DERIVE:
  for each `status='open'` position of the exchange, `upnl = (mark − entry)·qty·side_sign`, `mark` = latest quote/ticker
  price, via a **function-local import** of the positions+marks reader (avoids the storage→scripts circular import the prior
  fix hit). Best-effort: missing mark → that position contributes 0; any sqlite error → 0.0 (never raises — not a trading path).
- **`polaris/scripts/_production_tick.py`** doc-honesty (same group already owns this file): update `_all_strategies`
  docstring (:371-378) — it still lists `rsi_bb_pullback`/`connors_rsi2`/`cci_reversion` as "EXCLUDED"; under virtual their
  `dispatch_eligible=virtual_loosen(True,False)` flips True → they ARE dispatched. Doc-only (behavior already correct).
- **Tests**: seed open position + ticker mark → correct signed upnl; no open positions → 0.0; missing mark → 0.0 no raise;
  `python -c "import polaris.storage.virtual_account_equity"` clean (no import cycle).

**Activity effect (d+e)**: (d) removes lock-induced tick STALLs → the whole pipeline sustains higher throughput (more of the
signal stream reaches fill); (e) makes the activity-max scoreboard (per-venue equity/P&L) trustworthy — currently reports $0
unrealized.

---

## Parallelism / merge notes
- File-disjoint: G1 {_production_bars, rsi_bb_pullback} · G2 {_smoke_fills, strategies/__init__} · G3 {cci_reversion} ·
  G4 {reentry, _production_rotation} · G5 {_static_ground, _production_tick, db_writer, virtual_account_equity}. No cross-group
  file collision → 5 concurrent worktrees safe. G5 is the heavy one (single-heavy-workflow rule: run it alone).
- Every group builder = fresh Claude sub-agent, then fresh adversarial reviewer (builder≠reviewer). Sub-agent prompt header:
  DEMO/PAPER + Aggressive bias + rejection-keyword sweep (0 hits) + REAL byte-identical proof (env-unset test) + vault append.
- Merge base freshness: check merge-base vs main before each merge (feedback_workflow_worktree_stale_base); clean up worktrees
  immediately after. Do NOT touch the live bot / live DB during build — deploy on next 07:30 restart.
