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

## 2026-06-25 RESOLUTION — HIGH-1/HIGH-2 already-built in HEAD (no edit needed)
재배선 의뢰(signal_density+cell_scores 데드로직 / per-venue z-norm) 착수 시 **둘 다 이미 구현 완료** 확인 — 감사(06-23)는 commit `2a9c6c2`(G1 ranking-brain) 이전 스냅샷 기준이라 갭으로 표기됨. fresh-Claude 적대 검증 IMPLEMENTED-CORRECT 판정(file:line 인용).
- **HIGH-1 (#1+#2)**: `_production_layers.py:457` `compute_signal_density_7d`(real SQL `signals` 7d count) merge `:458-462`, `:463` `read_cell_scores_by_instrument`(real `cell_matrix_p0` n_eff-weighted mean), `:480-486` 둘 다 `compute_dynamic_focus(cell_scores=…)`로 전달. `watchlist.py:120,126,134-141` sig_z(0.25)+cell_z(0.10) 합산 = 설계 35% 활성. 빈 dict→graceful 0(`watchlist.py:65-66` sigma guard), 무crash/무drop. 잔존=데이터 성숙(현 cell.score≈0·signals 1심볼)이지 배선 갭 아님.
- **HIGH-2 (#3)**: `watchlist.py:70-90` `_grouped_z_score` per-(venue/asset_class) z-norm, single-group=global `_z_score` byte-identical no-op. `:118-132` 6축(vol/sig/atr/depth/cell/opp) 전부 grouped. RANKING-only(flow_not_block) — `core/sizing/` 에 focus_score/opportunity_score 참조 0건(9-stack 무관).
- 회귀: G1 ranking+z-norm 테스트 전부 green(`test_g1_scored_ranker.py` 25/25). 유일 실패 `test_rank_top_n_cap_default_and_env`=pre-existing(stale 120 vs `schema.py:158`=1500, WATCH-cap 영역, 본 태스크 무관·별도). NOT live-deployed(메인 supervised). 거부키워드 0.

## 2026-06-24 catalog-completeness 측정 (live API, 실측)
- OKX: us.okx.com tickers=512(public master 518), USDT-quote=246→price>0=186=현재 active 전부. 페이지네이션/region/limit/rank 컷 0(watch_max 1500≫186). 유일 폭 제한 = **USDT-only**(schema.py:47), 512중 266(52%) 제외(BTC59/ETH45/DAI34/USD22/USDC13…). 189=과거 upsert 잔여 3, under-fetch 아님. → watch-all=전체 US-OKX **USDT**-SPOT(의도적 협소, 버그 아님). 첫 확장=USDC(+13).
- Capital: 전체 nav 무캡 크롤 137req 전부 200, 429/non-200 **0**. TRUE 카탈로그 **2565 epic**(SHARES2113·crypto277·FX115·commod33·indices20). 우리 235=의도 scope(FX/idx/commod) 168/168 **100% 커버**+oil노드 SHARES67(equity 재태깅·inactive). depth-cap4 무손실(max depth3), 429 silent-skip(_capital.py:170) 실측 손실 0. 235→과거492 축소=crypto290 라우팅(OKX)+commod 정정. 큰 미수집=**SHARES2086 의도적 제외**(token 화이트리스트 _capital.py:37). → 의도 scope 내 watch-all=진짜 전체.
