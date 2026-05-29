---
type: runtime
status: active
date_created: 2026-05-06
date_updated: 2026-05-30
tags: [now, tier-0]
---

# Polaris _NOW (Tier 0 — read first)

## What matters now (HAND-WRITTEN)

**🔴 HANDOVER 2026-05-30 (Jin 취침 — 자율 완성 mandate. 다음 세션 여기서 이어가기. 핸드오버 digest=[[2026-05-30_handover_3stream]]).**
**Mandate**: 3-스트림 아키텍처를 끝까지 자율 완성 + 대시보드 + 교차검증, 자는 동안. 각 단계 워크플로우(build TDD→adversarial review→거동게이트)→커밋→보고. 끊김 없이 체인. 돌이키기 어려운 외부/venue 변경은 보류·기록.
**🎯 봇 목적(Jin 2026-05-30, 나침반)**: surgical-strike — 정밀 타이밍 진입/엑싯, **오직 수익, 근거 있는 거래**. alt-data(뉴스·매크로·CoinGlass·FRED·MyFxBook) = 진입 근거 SIGNAL·레짐 evidence(NOT 방어 blackout/throttle). 손실방어 = **정밀 엑싯**(적응형 stop/타이밍)으로 — 사이징 축소·진입 차단 아님. 데이터 충당 + AI콜/기술계산으로 판단 정밀·신속. ⇒ **#27(AI 타당성+alt-data) 1급 격상**, 엑싯·레짐 정밀도 강조. 위임: "말이 되는 봇 + 목적 충족 + 원하는 대시보드" 나올 때까지 끝까지 자율+검증, 컴프레션/핸드오버 내 판단. ([[north-star]] Operating thesis · [[project_operating_thesis_surgical_strike]] · [[feedback_autonomous_until_intent_met]])
**불변**: 9-stack 봉쇄 · hard-MAX(headroom_min+0.09 ceiling) · AGGRESSIVE(방어throttle/축소 X, 거부키워드 0) · DEMO/PAPER only · builder≠reviewer · OKX 봇 무중단(최종 재기동만 graceful) · Claude 창 kill 금지 · workflow-first 기본.
**목표 3 스트림**: A=OKX 크립토 SPOT(롱only,lev1,24/7) · B=Capital CFD(FX/지수/금,롱숏,per-market lev,세션) · C=Alpaca 미국주식 SPOT(롱,US장/PDT/갭). 크립토 파생 불가(OKX 검증실패=spot, Binance 선물 막힘→Binance 철회 `22fffd9`). **plan SSOT**=`.claude/plans/stream_architecture_redesign_2026-05-30.md`(StreamConfig·product_class 1급·additive 무중단·T0-T17).
**AI 제공자(중요)**: **GPT(OpenAI)=봇 LLM, Gemini=교차검증 — 둘 다 사용 가능. Anthropic/Claude는 Jin이 차단 → 호출해도 안 됨(라우팅 금지).** /debate=GPT+Gemini.
**/debate 확정**(`b565392`, vault/50_research/debates/trading_params_leverage_trackC_2026-05-30.md): Capital 레버리지 flat30→per-market(폴백 FX30/지수20/원자재20/cCFD2, 0-폴백 교체, fill_normalizer 전달) · Track C buying_power 기준 gross 3.0/per-sym 0.99/daily 0.99, PDT 별도 카운터.
**완료·커밋**: 파운데이션 P0-1 `e932af6`(StreamConfig SSOT+product_class+venue 이진분기 9곳→resolve_stream+schema additive+대시보드 단계1) — 815 green. ✅ **Phase2 `cf98ed8`**(T6 Capital 3전략[fx/xau/session] 숏 미러+T7 per-market 레버리지[FX30/idx20/cmd20/cCFD2, flat30 버그교정]+T14 net-edge 인프라 **display-only**[SKIP_ON_NEGATIVE=False, AI 타당성 debate 후 게이팅 결정]) — 856 green(+41), ruff/mypy --strict clean(144), OKX 거동 100% 동일(4전략 0 diff), 거부키워드 0, 9-stack ok, 3 fresh adversarial review APPROVE_WITH_NITS(blocker 0/위반 0)+nit 보강(숏 negative-path×3+abs×2 pin; dual asset_class spelling 'index'vs'indices' SAFE 판정-코드무변경). OKX 봇 무중단.
**다음 세션 착수(워크플로우)**: ⏳ **#24 Phase3 진행중**(T8 Track "C" 타입+캡[gross3.0/per-sym0.99/daily0.99/PDT별도]+T9 Alpaca 어댑터[OKX 미러+idempotency]+T10 universe/_alpaca+refresh+T11 StreamConfig C 등록+_is_external_reject Alpaca 코드+T12 equity 전략3종[equity_tsmom·rsi_bb 리스킨+equity_gap_go 신규,MarketView prev_close/gap_pct]+T13 RTH/PDT 게이트=랭킹다운). paper키 검증됨 $79.7k BP. 스코핑 agent 가동중) → #25 Phase4(대시보드 단계2+3-스트림 봇 클린재기동+**교차통합 live-audit**: 3 venue 각자 레짐·전략·엑싯 맞물림·cross-contam 0) → #26 엑싯·레짐 정밀도(손실방어=정밀엑싯, Jin 강조) → **#27 AI 사용 타당성·정당성 판단(Jin 명시 요청, 1급)**: live DB G1-G8 pass-rate/all-PASS 실태·AI콜 효과·모델배치·멀티소스 의사결정경로[auto_invasion mk1 ~/Projects/auto_invasion_mk1-main]+전체설계 재검증(read-only /debate=GPT+Gemini) → alt-data wire[CoinGlass·FRED·MyFxBook→레짐 근거] → #28 비용 모니터링(fee+slip+AI$ stream별→대시보드) → 최종 5-axis+3스트림 live 증명. task #1-8 로 추적.
**.env toolkit**: AI=OpenAI/Gemini(가용)·Anthropic(차단) · venue=OKX/Capital(CAP_*)/Alpaca(ARCHIVE_ALPACA_PAPER)/IG(ARCHIVE_IG=또다른 CFD)/Binance(철회) · data=CoinGlass(펀딩/OI/청산)/FRED(매크로)/Quandl/MyFxBook(센티먼트).
**운영**: OKX 스팟 봇 pid 67774 `data/polaris_live.sqlite` 수집중 · 웹 :8770(3:7 글로브+board) · 아카이브 DB=polaris_churn/session2/okx_s3. **버그노트**: .gitignore `data/` 패턴이 소스 `polaris/core/data/`까지 잡음 → 거기 신규 파일은 `git add -f`.

**2026-05-29 대시보드 부활 + 동적 시각화 + 스페이스 비주얼**: 봇 PID 96290 (`data/polaris_live.sqlite`, 24h 수집) 가동 유지. (1) **"봇 실행이 Claude 창 닫던" root cause 해결** — `start_dashboard.sh` aggressive tty cleanup 가 Bash 도구의 `$$` tty 를 Claude 창으로 오인해 Jin 창을 닫음 → 기본 OFF 반전 + 기본 DB → polaris_live ([[feedback_never_kill_claude_session]]). 대시보드 PID 7638 라이브 가동. (2) **`7e4cf33`** Bayesian edge-validation P1 (`posterior.py` NIG, display-only) + dashboard overflow 힌트 + crisis silent-drop 수정 + orphan liquidation 스크립트 (666 green, fresh-Claude SAFE). (3) **`5940107`** dashboard_v2 동적 시각화 (자산 스파크라인 + DD/노출/AI/승률 게이지 + 셀 히트타일, 40행 불변). (4) **스페이스 비주얼** `tools/visualizer/` 이식 중 (mk1 Neural Cloud → collect_snapshot 어댑터, plan: `.claude/plans/space_viz_neural_cloud_2026-05-29.md`). 판단: OKX 고아 `--live` 청산 SKIP (USDT $35.8k 충분, 고아=봇 활성 포지션). digest [[2026-05-29_dashboard_revival_dynamic_viz_spaceviz]].

**2026-05-28 P0 venue wire fix + 라이브 증명**: 5-axis 검수가 P0 발견 — production paper loop 이 실제 demo 주문을 한 번도 안 보냄 (open/close 둘 다 simulate-only, `--real-roundtrip` 폐기). 기존 `data/polaris.sqlite` 17,259 fills 전부 가짜. builder TDD 로 wire 복구 (618 green) → **codex 외부 review 가 green 코드에서 실주문 안전 P0 5건 포착** (db 혼재 / reservation 누수 / orphan 실포지션 / Capital deal_id 유실 / pnl_r 오산) → 재수정, codex 재review 7/7 safe=yes → 627 green. **라이브 증명**: `ignite_p1 --real-roundtrip --db data/polaris_live.sqlite` 로 Capital demo 실왕복 (13 fills 실 dealId, fault 0 / orphan 0 / fence conflict 0). OKX live = 시그널 미발화로 미확인 (gap). silent-drop 2건 warning 추가. 교훈 = green ≠ safe, builder≠reviewer 실증 → [[ADR-010]] + [[t-p0-wire_2026-05-28]] + [[2026-05-28_5axis_audit]].

**Day 8-10 backlog 상태 (stale 해소)**: Day 8 P0 quad (AllocatorFence/supervise/dynamic focus/ingest) + Day 9 24h production (G6/G7/G8 GPT wire + live_recalc) 완료. Day 10 P1 L5→L3 sizing wire (session×regime×triple_block in T4) done 2026-05-26. Day 10 P0 Capital silent-drop = audit frame error 로 debunk (ts_ms -10h drift historical only). 현재 최우선 = 위 venue round-trip 활성화 완료, 남은 gap = OKX live 증명 + orphan scanner.

Phase -1 (하네스 build) **완료**. Phase 0 (8 layer codex harden-up) **완료** (2026-05-06): L0~L7 codex round 1 합의 + `vault/30_components/layer-0..7-*.md` 8 spec write. 거부 키워드 0건.

**P0 Day 1 완료 (2026-05-06 + codex R1 fix 2026-05-07)**: Layer 0 (Dynamic Universe) + Layer 1 (Canonical + Baseline) implement.
- `polaris/core/universe/{schema, discovery, watchlist}.py` + `polaris/core/data/{schema, canonical, baseline, normalize}.py` + `polaris/storage/schema.py` (DDL bootstrap).
- **Codex R1 review = REJECT → 4 P0 blockers all fixed** (4-axis hard / Capital P0 categories whitelist / listing_ts wiring / asset_class fallback chain). Debate: `vault/50_research/debates/2026-05-06_p0_day1_codex_review.md`.
- 58 tests pass (37 L0 + 24 L1, 4 hypothesis property), ruff clean, mypy --strict clean.
- Smoke: OKX 182 + Capital 387 (P0 categories only) → 4-axis hard filter 24 (OKX only — Capital lacks vol/depth proxy at P0; Day 2 chart-endpoint task) → dynamic focus 24 (all listing_watch first cycle).

**P0 Day 2 완료 (2026-05-07)**: Layer 7 (isolation) + Layer 4 (cell_matrix) + Capital vol/depth proxy + Layer 3 sizing skeleton.
- `polaris/core/{isolation,cell_matrix,sizing}/` 11 new files ~1750 LOC + `polaris/venues/capital/market_proxy.py` + storage/schema.py 확장 (cell_matrix_*, strategy_halts, allocator_reservations, order_intents, positions/orders/risk_events).
- **Codex R1 R2 R3 review**: REJECT 4 P0 (warmup dead-code / linear parent decay / non-monotonic severity / RISK_ONLY skip) → REJECT (no prod caller) → **APPROVE** (Layer 3 sizing seam wires `apply_cell_routing_mult`).
- 143 tests pass (143 = 58 Day 1 + 32 cell_matrix + 28 isolation + 11 capital_proxy + 7 sizing + 7 추가 monotonic/RISK_ONLY regression), mypy strict + ruff clean.
- Smoke: OKX 182 → filter 19-30 (intra-session); Capital raw 454 (forex/indices/commodity/crypto P0) → 16 typical 4-axis pass with proxy (forex 3 + indices 9 + commodity 2 + crypto 2; peak 95 during active session); cell routing dist top 7 / bottom 7 / mid 11; allocator fence asyncio race PASS.

**P0 Day 3 완료 (2026-05-07)**: Layer 2 per-gate pipeline (8-gate orchestrator + 4 Haiku gates) + Layer 3 T4 full + Layer 1 ingest. 225 tests pass.

**P0 Day 4 완료 (2026-05-07)**: Layer 5 (3 P0 learners + adaptive_learner_attack triple block + hourly commit + snapshot rollback) + Layer 6 stubs (tick recalc dirty mark + regime flip 2-consecutive + strategy swap max-1/trade + conviction stacking) + 7 strategies signal-generator port + schema additions.
- `polaris/core/learners/{base,session,regime,max_hold,scheduler}.py` + `polaris/core/live_recalc/{tick_recalc,regime_flip,strategy_swap,conviction}.py` + `polaris/strategies/{base,volume_burst,tsmom,rsi_bb_pullback,spot_donchian,fx_breakout_basket,xau_indices_trend,session_breakout}.py`.
- vault/20_strategies/ 7 신규 spec; layer-5/6 spec 기존 유지.
- **Codex L4 R1 REJECT_WITH_FIXES → all 4 fixed (P0 max_hold baseline 1.0→expected_holding_bars / P1 commit_hourly atomicity reads-in-tx / P1 strategy_swap venue/symbol/side check / P2 regime initial_seed confirmed=False) → R2 APPROVE**.
- 56 new tests = 281 total pass; mypy strict + ruff clean.
- Smoke: 7/7 strategies emit RawSignal · 3 learner Δ live · live_recalc 3 dirty + regime confirmed_2x + 2 swap decisions.

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

## What changed since last session (HAND-WRITTEN)

- 2026-05-06 reset: clean slate (모든 v1 코드 삭제, tag pre-reset-2026-05-06 archive)
- 4 round codex 디베이트 (round 1 ROLLBACK demo context 누락, round 2 demo unlock, round 3 4 critical/high, internal review + Jin sign-off)
- Jin clarification 21:30: per-gate AI + dynamic universe + 자가 진화 (active autonomous vision, [[active-autonomous-vision]])
- Memory 정리: 108 → 27 (81 archive)
- .env 통합 (OKX US demo + Capital CFD demo + AI providers)
- OKX 401 root cause = base URL `www.okx.com` (international) → `us.okx.com` (US region) (`feedback_okx_region_endpoint` — global memory)
- **Phase 0 완료 (2026-05-06)**: 8 layer codex 디베이트 (gpt-5.4, round 1 each) + vault/30_components/ 8 spec write. ADR-005 patch 권고 (top mult ×1.3 → ×1.5). raw fragments `/tmp/polaris_phase0/L*_r1_response.md`.

## Pending decisions (HAND-WRITTEN)

- [x] Phase 0 codex round 8 (L0~L7) — 2026-05-06 완료
- [x] **ADR-005 patch** (file patched 2026-05-06): §Cell Routing Mult `top ×1.3` → `×1.5` 적용 ✓ (audit 2026-05-07 verify, [[2026-05-07_p1_full_audit]])
- [x] **ADR-006 patch** (file patched 2026-05-06): warmup shrinkage 5-19 + EWMA decay 7d + dynamic quartile activation gate (≥20) 적용 ✓ (audit 2026-05-07 verify)
- [ ] Plan v5 `valiant-baking-sutton.md` detail 통합 update (선택)
- [x] P0 Day 1 완료: Layer 0 dynamic universe + Layer 1 canonical/baseline (2026-05-06)
- [x] P0 Day 2 완료: Layer 7 isolation + Layer 4 cell_matrix + Capital proxy + Layer 3 sizing skeleton (2026-05-07)
- [x] P0 Day 3: Layer 2 per-gate skeleton + 4 Haiku gates + Layer 1 ingest wiring + Layer 3 full T4 (2026-05-07)
- [x] P0 Day 4: Layer 5 learner network + Layer 6 live recalc stub + 7 strategies signal-generator port (2026-05-07)
- [x] P0 Day 5 완료: venue adapters full + fill normalizer + paper loop smoke + dashboard v0 + codex review (2026-05-07)
- [x] P0 Day 6 완료: full pipeline G3-G7 plumbing + fills DDL + ignite_p1 + Day 1 P2 fix + codex R1 R2 R3 review APPROVE (2026-05-07)
- [x] P0 Day 7 완료 (24h watchdog 18 probes APPROVE_WITH_NITS) + P1.0 ignition fired PID 57257 (2026-05-07)
- [x] P0 sprint cumulative coherence review (2026-05-07): codex APPROVE w/ 4 P0 + 7 P1 wiring debt
- [ ] **Day 8 P0 PR (one bundle)**: A2 AllocatorFence wire / A3 supervise_strategies + record_fault wire / A5 ignite_p1 dynamic focus inject / A6 per-tick ingest_bars + persist positions/orders/risk_state
- [ ] Day 8+ P1: A1 session×regime in T4 / A4 Layer 6 dirty sweep / A7 regime+session SSOT / A8 emitted[:3] cap removal / X1 max_hold consume / X2 idempotent order keys / X3 G6 swap → Layer 6 SSOT
- [ ] Day 8+ P2 docs: ADR-005 Kelly clarification / sprint-complete digest test name update / `tests/test_integration_p0_pipeline.py`
- [ ] Vault `regrets/` 폐기 confirm (B'+D+C 대체 — [[ADR-002]])
- [ ] Live 진입 결정 = 별도 ADR (본 plan 책임 X, Jin 단독)
- [x] **Vault audit P1 wave 1 (2026-05-07)**: 199→0 lint issues, vault_lint hardened, post_trade_reflector frontmatter inline, log.md 261-dupe collapse, strategy backlink density fix → digest [[2026-05-07_p1_vault_audit]]
- [ ] Day 9+ vault backlog: 8 component spec split (≤60 line summary + impl/decisions sub-pages) / start_dashboard hook 1-min dedup / ADR-007 provenance back-fill / vault-curator agent pattern (per-Day dispatch)
- [x] **Day 9 P0 quad bundle (full-audit 2026-05-07 [[2026-05-07_p1_full_audit]])**: F1+F2 G6/G7 GPT P1 wire + per-tick re-invocation + close_specific_position (FIFO 폐기) + G8 phase=P1 default — done 2026-05-07. F10 timeframe + F11 supervise + F12 equity SSOT — done 2026-05-07.
- [x] **Day 9 24h production loop completed (2026-05-08)**: G6 GPT 27,003 / G7 GPT 20,833 / G8 GPT lessons 1,917 / live_recalc exit_now 95,778 widen 10,645 / OKX PnL +$599.43 / cell pool 201 / fence reservations 5,616. Audit: [[2026-05-08_p1_day9_24h_full_audit]] + [[2026-05-08_p1_day9_24h_audit_detail]].
- [x] **Day 10 P0 "Capital fills 0 silent drop" — diagnosed 2026-05-26** ([[2026-05-26_p0_capital_silent_drop_diagnosis]]): audit query frame 잘못 (ts_ms 필터가 -10h drift된 ts 못 잡음 — 실제 165 capital fills 정상 persist). 진짜 P0 = `fills.ts_ms` -36000s drift (Sydney AEST naive→UTC artefact, historical only). 현재 코드는 0 drift reproduce — 다음 24h paper run 으로 확정 close.
- [ ] **Day 10 P0 follow-up**: 다음 24h paper run 후 신규 capital fills 의 ts_ms drift verify (0 → close ; non-0 → remaining naive-ts path hunt)
- [x] **Day 10 P1 (session×regime in T4) — done 2026-05-26**: [[ADR-005]] T4 chain 에 L5_product wire (`9d3c79d feat(L3) wire L5 learner mults`). plan: `.claude/plans/p0_l5_l3_sizing_wire.md`. 10 new tests, 609 suite pass, mypy strict + ruff clean.
- [ ] Day 10 P1 remaining: fx_breakout_basket 0 signals all-time / xau_indices_trend US100 ticker mismatch / G3 KILL ratio 73% (target 50%, Variant B v2 + cell_score evidence)
- [ ] Day 10+ P2: F6 persist signals/orders/quote_ticks / fault_events table empty vs counter 153 reconcile / F8 ignite_p1 bootstrap dedup hook

## Auto-generated (DO NOT EDIT BELOW)
<!-- AUTO-START -->
- Latest daily: (none yet)
- Open incidents: 0
- Recent ADRs (last 7d): [[ADR-001]] [[ADR-002]] [[ADR-003]] [[ADR-004]] [[ADR-005]] [[ADR-006]] [[ADR-007]] [[ADR-008]]
- Top touched (7d): vault/10_decisions, .claude/agents, .claude/skills, .claude/hooks, tools/vault_lint.py
<!-- AUTO-END -->

## Active plan
- Main plan v5: `/Users/jinyoon/.claude/plans/valiant-baking-sutton.md`
- Detail spec: `/Users/jinyoon/Projects/Polaris/.claude/plans/polaris_v2_plan_final.md` (520줄, 일부 superseded)
- 8-layer architecture: see [[ADR-003]]
- Per-gate AI pipeline: see [[ADR-004]]

## Implementation status
- P1.0 ignition fired at 2026-05-29 16:23 (paper=False, full_pipeline=True)
