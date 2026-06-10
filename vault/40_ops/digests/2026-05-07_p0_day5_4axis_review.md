---
type: digest
status: active
phase: P0
day: 5
date_created: 2026-05-07
tags: [digest, p0, day-5, 4-axis-review, codex, venues, fill-normalizer, dashboard]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[layer-0-universe-discovery]], [[2026-05-07_p0_day5]]
reviewed_by: codex (gpt-5.4) R1-R4
---

# P0 Day 5 — 4-axis Policy Review

## Outcome
**Codex R4 APPROVE on all 4 axes** after 3 rounds of REJECT_WITH_FIXES.

| Axis | R4 verdict |
|---|---|
| 1 — Plan/ADR/Phase 0 spec 정합 | **PASS** |
| 2 — Dead code 0건 | **PASS** |
| 3 — Hardcode 0건 | **PASS** |
| 4 — AI 적절 사용 (Day 5 = Python only) | **PASS** |

Final verification: **378 pytest pass** / mypy --strict clean (91 source files) / ruff clean / smoke_paper_loop end-to-end OK / dashboard renders with fills+orders dual-shape header.

## Findings → Fixes (3 reject rounds)

### R1 — 7 findings (2 P0 / 1 P1 / 4 P2)
- **P0** `okx/adapter.py:22-24` (docstring) — claimed `best_bid * 1.0005` clamp anchor, but implementation uses opposite-side anchor (askPx for buy, bidPx for sell — correct market practice for IOC taker, confirmed by real BTC-USDT fill at $81,514). Fix = aligned docstring to opposite-side semantics.
- **P0** `capital/session.py` — `start_ping_loop()` existed but was never auto-started; long-lived sessions could silently expire. Fix = added `auto_ping=True` (default), spawn loop after first successful login. New test `test_login_auto_starts_ping_loop` proves auto-start. Existing tests opt out via `auto_ping=False` + cleanup via `await sess.aclose()`.
- **P1** `dashboard_v0.py:_read_recent_fills` — read from `orders` table while panel claimed "Recent fills". Fix = now prefers ADR-003 `fills` table; `orders` is fallback when `fills` is empty.
- **P2** `capital/market_proxy.py:get_capital_session_env()` — exported but no caller. **Removed**.
- **P2** `capital/session.py` — inline `5.0` retry backoff. Fix = `PING_RETRY_BACKOFF_SEC: Final[float] = 5.0`.
- **P2** `capital/market_proxy.py` — inline `timeout=15.0` (×2). Fix = `REST_TIMEOUT_SEC: Final[float] = 15.0`.
- **P2** `dashboard_v0.py` — `TARGET_HEIGHT` unused, dimensions not `Final[]`. Fix = removed `TARGET_HEIGHT`; `TARGET_WIDTH` + `DEFAULT_REFRESH_SEC` as `Final[]`; header refresh string interpolated at render time.

### R2 — 2 findings (1 P1 / 1 P2)
- **P1** `dashboard_v0.py` — `orders` fallback coerced `qty` (base ccy / contracts) into `size_usd` column, mislabeling base qty as USD. Fix = expose `qty_base` separately; renderer emits source-specific header (`SIZE_USD/FEE_USD/SLIP_BPS` for `fills`, `QTY_BASE/STATUS` for `orders`).
- **P2** `okx/adapter.py:place_market_order` docstring still claimed `notional_usd` always sent in `sz` via `tgtCcy=quote_ccy`. Fix = method docstring now distinguishes `market`/IOC-fallback (notional path, sz=USDT) from `ioc`/limit/post_only (base-qty path, px sent, tgtCcy omitted).

### R3 — 2 findings (both P2 docstrings)
- **P2** `dashboard_v0.py` module header — listed only the `fills` panel shape. Fix = now describes both `fills` and `orders` shapes.
- **P2** `okx/adapter.py` module header — `POST /trade/order` line conflated semantics. Fix = explicit per-`ordType` listing.

### R4 — APPROVE
All previous fixes verified.

## Files touched

```
polaris/venues/okx/adapter.py             # docstring (module + method) → opposite-side anchor + per-ordType semantics
polaris/venues/capital/session.py         # auto_ping default True + start_ping_loop after login + PING_RETRY_BACKOFF_SEC constant
polaris/venues/capital/market_proxy.py    # REST_TIMEOUT_SEC constant + removed unused get_capital_session_env
polaris/scripts/dashboard_v0.py           # TARGET_HEIGHT removed, Final[] constants, dual-shape Recent-fills panel,
                                          #   prefer fills table, qty_base for orders fallback
polaris/scripts/ignite_p1.py              # contextlib.suppress + mypy narrowing fix (collateral cleanup)
tests/test_capital_adapter.py             # +test_login_auto_starts_ping_loop, all sessions opt out via auto_ping=False
                                          #   and call sess.aclose()
```

## Reject keyword sweep
0 hits across `12주 / 90d gate / regulatory / professional risk / monthly review / regrets / posture standard` on Day 5 source surface.

## Aggressive bias preservation
All fixes were correctness gaps or hygiene cleanups — no defensive throttling, no auto-disable, no hard blocks. The new `auto_ping` default keeps a long-lived demo session alive for the same aggressive 24/7 attack posture mandated by the active autonomous vision.

## Iteration count
4 rounds R1-R4 (3 REJECT_WITH_FIXES + 1 APPROVE → 3 fix passes). All fixes applied autonomously by current Claude session; no escalation to Jin needed.

## Sources
- `/tmp/polaris_p0_day5_4axis_review/r{1..4}_{prompt,response}.md`
- ADR-003 §Per-Venue Adapters
- vault/30_components/layer-0-universe-discovery.md (file layout)
- feedback_okx_region_endpoint, feedback_capital_demo_live_split
