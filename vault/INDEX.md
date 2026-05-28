---
type: runtime
status: active
date_created: 2026-05-06
tags: [index, catalog]
---

# Polaris Vault INDEX

## Tier 0 (mandatory first read)
- [[_NOW]] — live state
- [[log]] — chronological 1-line append

## 00_charter (constitution)
- [[north-star]] — 0.75% primary / 1.25% stretch / aggressive bias
- [[aggressive-bias]] — defensive 거부 영속 원칙
- [[active-autonomous-vision]] — per-gate AI / dynamic universe / 자가 진화
- [[coding-conventions]] — Python style, naming, no hardcode in plans
- [[karpathy-workflow]] — Ingest / Query / Lint 3 ops

## 10_decisions (ADRs, creation order)
- [[ADR-001]] Vault Structure
- [[ADR-002]] Vision (active autonomous, 0.75%/1.25% target)
- [[ADR-003]] 8-Layer Architecture
- [[ADR-004]] Per-Gate AI Pipeline
- [[ADR-005]] Sizing Formula + Cell Routing
- [[ADR-006]] Cell Matrix 8-dim (4-dim P0)
- [[ADR-007]] Learner Network 7 (3 P0)
- [[ADR-008]] 7 Strategies (signal generator role)
- [[ADR-009]] Harness Collaboration Protocol (3-layer install)
- [[ADR-010]] Venue Round-Trip Activation (real demo wire + db 격리)

## 20_strategies (P0 Day 4 — 7 signal generators)
- [[volume_burst]] — OKX SPOT 1m, vol z>2.5 + prior-high break + ATR floor (corr_group=spot_intraday_event)
- [[tsmom]] — OKX SPOT 1H, 20-bar return basket momentum (corr_group=spot_cross_sectional_momo)
- [[rsi_bb_pullback]] — OKX SPOT 15m, RSI<30 + BB lower + ma200 trend filter (corr_group=spot_mean_reversion)
- [[spot_donchian]] — OKX SPOT 1H, Donchian 40 + ADX>20 (corr_group=spot_breakout)
- [[fx_breakout_basket]] — Capital CFD 1H, FX 5-pair Donchian 40 + ADX>20 30× lev (corr_group=cfd_fx_trend)
- [[xau_indices_trend]] — Capital CFD 1H, XAU/US500/US100/GER40 Donchian 30 + 20bar momentum 20× lev (corr_group=cfd_index_commodity_trend)
- [[session_breakout]] — Capital CFD 5m, 4-sym open ATR×1.5 break 20× lev (corr_group=cfd_session_event)

## 30_components (per-layer, Phase 0 codex round 1 합의)
- [[layer-0-universe-discovery]] — 4-axis active filter + dynamic focus 12-48 + listing watchdog
- [[layer-1-canonical-baseline]] — separate bars/quote_ticks + 5-metric (atr_pct/size/signal/vol/pnl_std) + ratio normalize
- [[layer-2-per-gate-pipeline]] — custom asyncio + Haiku 1/3/4 + Python 2/5-8 + mixed failure + G4 fast-path
- [[layer-3-sizing-risk]] — T4 + cell mult clip-전 + triple cold start floor 3.0/3.5% + headroom min() + fill-rate 70/60
- [[layer-4-cell-matrix]] — 4-dim primary + shadow + EWMA 7d + warmup shrinkage + dynamic quartile (>=20)
- [[layer-5-learner-network]] — incremental + hourly commit + triple block + SQLite snapshot + AI feedback ≠ cell
- [[layer-6-live-recalc]] — dirty-trigger 5s + venue regime + 1-swap/trade + 3-layer stack + close-only override reset
- [[layer-7-strategy-isolation]] — asyncio task + central allocator (one Lock) + 4-state breaker + idempotent key
- [[harness-collab-protocol]] — multi-agent orchestration glue: agent roster + handoff triggers + builder≠reviewer + brain contribution ([[ADR-009]])

## 40_ops
- daily/, incidents/, digests/, lever_changes/
- [[2026-05-28_5axis_audit]] — 5-axis P0 venue wire-miss + fix + Capital live 증명

## 50_research
- forensic/, debates/, lessons/
- [[t-p0-wire_2026-05-28]] — venue wire-miss (green ≠ safe, builder≠reviewer 실증)

## .templates
- ADR.md / INSIGHT.md / STRATEGY.md / COMPONENT.md / LESSON.md

## Tags
- See [[.tag_taxonomy]]
