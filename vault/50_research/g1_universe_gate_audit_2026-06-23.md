---
type: research
status: active
date_created: 2026-06-23
date_updated: 2026-06-23
tags: [research, audit, g1, universe, eligibility, layer-0, focus, liquidity-floor]
---

# G1 Universe Gate — 전수조사 (ULTRACODE, 누락 0)

DEMO/PAPER, aggressive bias 유지. flow_not_block(probe=판단/shaping, 차단/사이즈컷 X) · 9-stack 봉쇄 · GPT=0. READ-ONLY 감사(코드편집 0). 게싱 0 — code file:line + `data/polaris_live.sqlite` 실데이터 대조. 백링크 [[structure_hardening_2026-06-23]] · [[gate_bus_probe_wiring_audit_2026-06-23]] · [[ADR-003-8-layer-architecture]].

## VERDICT
Eligibility-floor + asset-class-quota 골격은 **구조적으로 타당 + flow_not_block 정합**. 그러나 **랭킹 두뇌가 비어있음**: 설계 rank weight의 ~35%가 dead-wired, cross-venue z-score가 equity를 오정렬, 유일한 junk-exclusion floor가 Alpaca placeholder vol에 우회됨. 형태는 valid, 실질은 data-starved.

## SOUND (건전)
- **Liquidity floor 설계** (schema.py:349-380): per-venue/per-axis, known-bad-only(missing datum→drop 안함), env-tunable, flow_not_block — eligibility 경계지 size-cut 아님. G1 최건전부.
- **Asset-class quota** (schema.py:198, fx4/idx3/comm2/eq4): 24/7 crypto vol 독점 방지 정합.
- **Pure-function core** (watchlist.py): `_z_score` len<2/zero-sigma guard, `_quantile` 정확, stable sort, deterministic upsert.
- **held-position union** (`get_focus_targets`): open position이 max_n에 잘려 빠지지 않음(가산).
- **blocklist intersection** + **gate_events gate_id=1 = PASS only** (2,315 PASS / 0 KILL — aggressive 정합).

## WEAK / MISSING (검증된 실데이터)
| # | finding | 검증 | sev |
|---|---|---|---|
|1|`signal_density_7d` dead, weight 0.25(2위)|95/95 active rows MIN=MAX=AVG=0.0; producer 부재|high|
|2|`cell_scores` 미전달|`_production_layers.py:314` `compute_dynamic_focus(universe, cycle_ts=ts)` — kwarg 없음 → cell_z=0; cell_matrix_p0 112 rows 존재|high|
|3|cross-venue z-score equity 오정렬|단일 mixed-pool z; ABVE($54k/377% ATR) **focus_rank=2 core**(focus_score 1.806) 최신 cycle, NVDA/SPY($1.2B) 하위로 매몰|high|
|4|Alpaca floor 우회|`_alpaca.py:196` placeholder `vol=50e6` 선스탬프(active 선정 前); floor는 vol>0일 때만 평가 → 50e6 통과. ABVE($54k)+SNBR($47k) is_active=1, 5M floor의 100x 미만|high|
|5|un-unified G1 2개|prod=compute_dynamic_focus; `universe_scanner.py:264` gate는 `ctx.payload.get('universe',[])` 읽으나 payload에 키 없음→빈 select→PASS no-op(vestigial)|med|
|6|Eligibility probe seat 빔|`probes/roles.py` PROBE_ROLE_REGISTRY={Exit×3, Position×1} — G1 0개. G1 100% 결정론, shaping 입력 0|med|
|7|active_reason 오표기|`_filter_failure_reason`(legacy 4-axis) 기록; `atr_pct=1.50<2.0` 13,149건(placeholder), `below_rank` **0건**. forensics가 거짓 축 보고(거동 영향 0)|med|
|8|Alpaca stale-active 미비활성 / `last_price` 미영속(DB path min_price floor inert) / `listing_ts` NULL 95/95(신규상장 watchdog 불발) / focus size 30 고정(15,524 cycle 전부) / crypto 과소(okx 3·capital 10·alpaca 18)|DB 검증 완료|med-low|

## 빠진 데이터 차원 (설계 완성도 갭)
correlation/cluster(선정 시 무, L3 다운스트림만) · regime_state(테이블 존재, focus 미공급) · events/news/catalyst · funding/basis · 진짜 L2 microstructure(depth=top-of-book proxy, Alpaca 0.0) · market-cap/liquidity TIER. atr_24h_pct=1일 high-low 단일샘플(진짜 ATR 아님), depth_10bps_usd 오표기.

## BUILD 권고 (전부 flow_not_block · 결정론 · 9-stack 무관)
1. **per-venue z-normalization** (watchlist.py:82, _ranking.py:147) — $1B equity를 $251B BTC 아닌 equity끼리 정렬. equity 오정렬 root fix. **[Jin-surface: 구성 변경]**
2. **Alpaca floor 우회 차단 + last_price 영속** (_alpaca.py:196; discovery persist/read) — junk-exclusion 복원. **[Jin-surface]**
3. **signal_density 7d producer + cell_scores 배선** (_production_layers.py:314) — dead 35% weight 활성. producer build=자율 / weight flip=**[Jin-surface]**
4. **active_reason를 실제 rank/floor 경로로 재기록** (discovery.py:505) — 순수 관측, 거동 0. **[BUILD 자율]**
5. **dual G1 통합 or universe_scanner_gate 명시적 pass-through 강등** — **[BUILD 자율]**
6. **Eligibility ProbeRole seat 구축** (roles.py) — regime-tilt / correlation de-crowd가 RANK만 nudge(차단/cut X). 최대 완성도 add, ranks 1-4 후 시퀀스. **[Jin-surface]**
7. **Alpaca stale-active deactivate sweep + listing_ts merge** — NOTE: `wf_fix_stale_deactivate.js`(git status) 진행 중일 수 있음, 중복 전 확인. **[Jin-surface]**

mandate_ok=true · 거부키워드 0 · READ-ONLY.
