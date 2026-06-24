---
type: runtime
status: active
date_created: 2026-05-06
date_updated: 2026-06-11
tags: [now, tier-0]
---

# Polaris _NOW (Tier 0 — read first)

## What matters now (HAND-WRITTEN)

**🔴 AUDIT VERDICT 2026-06-22 ([[system_design_audit_2026-06-22]] · loop_state.md).** 8-서브시스템 멀티에이전트 감사 결론: **아키텍처(8-Layer·8-gate·StreamConfig·AI-free·T4 9-stack 봉쇄)는 sound, 설계대로 wired.** 깨진 건 골격이 아니라 **입력데이터·측정·청산보호·튜닝**. 헤드라인 **−266R/−209.7R은 대부분 측정 아티팩트**(cross-venue R 합산 무의미 + reconciled-mae를 realized로 오기록 −211R = '추적실패' drift 카운터, **손실 아님**). 정직 $ ledger: capital +$431 / okx −$646 / alpaca −$1881. 실행 프로그램 = **M→S→D→R**(측정 재설계→안정화→/debate→리셋, loop_state.md).
- **Jin 4 락인 결정(2026-06-22)**: ① **fills.$ = 진실 + positions.pnl_r 재정의**(스트림 공통 R, reconciled mae는 R 제외 → 별도 drift 카운터). ② **Alpaca = equity 신규중단 + 복구**(halt+restore). ③ **D-params /debate 4건 승인 → 적용은 측정+리셋 후**(crisis 적응형 cap / venue-native session+expectancy / 틱엔진 OKX 이동 / volume_burst fade-first; 전부 aggressive·flow_not_block). ④ **데이터 리셋 = M+S 랜딩 후**(고친 메트릭으로 클린 슬레이트, DB 아카이브 보존).
- ⚠️ 이전 P0.4(클램프)+fix#1(reconciled mae→pnl_r 백필)이 −211R 아티팩트를 키움 → **REVERTED**(realized R ledger에서 제외). builder≠reviewer가 잡아냄.

**🟢 HANDOVER 2026-06-05 (Jin "차례대로 해줘" — 4 follow-up 순차 처리).** 봇 PID SSOT = `data/paper/production.pid`(리터럴 PID 미고정 — 항상 이 파일이 진실), log=`data/paper/p5_live24.log`, DB=`data/polaris_live.sqlite`, 대시보드 :8770. env=`TICK_ENGINE_ENABLED=1`. 각 신규=빌드(TDD)→적대적 리뷰(Workflow 3렌즈, builder≠reviewer)→커밋→배포 사이클. 1869 테스트 통과.
- ✅ **#1 fade 엑싯 (`29517f3`)**: fx_range_fade는 BB 익스트림 진입→middle 회귀가 edge인데 엑싯엔진은 와이드 ATR-트레일(2 ATR)뿐→평균회귀 이득 반납(round-trip). `StrategyMetadata.profit_target_r`(기본 None=기존 추세전략 불변) 추가, fade는 1.0; `evaluate_exit`가 pnl_r≥target 시 즉시 하베스트(target_harvest), 트레일보다 먼저. 라이브/리플레이 배선. 리뷰 approve_with_nits(1R≈2σ≈BB거리 coarse heuristic→/debate flag + 가드테스트).
- ✅ **#2 안정성 (`e48abd8`+`f7223aa`)**: 깨끗한 진단 위해 먼저 **WS DEBUG 침묵**(`-vv`가 websockets 라이브러리 호가 전프레임 로깅=라이브로그 95%/분당2075→23줄). 그 깨끗한 로그가 진짜 근본 노출: ① **Alpaca WS `symbol limit exceeded`** = quotes+**trades 둘다 구독**(16×2=32>IEX무료 30); trades는 parse에서 전부 버려짐(무용)→**quotes-only**로 한도해소+WS메시지 절반. ② **`database is locked`** 다수 = quote 폭주→WAL 206MB→락경합; quote 절반으로 해소. 검증=symbol-limit 0, db-lock 0, WAL 206→59MB, startup 2배, steady 거의 무stall. (잔존: 무거운 startup 바ingest 1회성; WAL creep PASSIVE 한계→재기동 RESTART 회수.)
- ✅ **#3 코모디티 이벤트 신호 (`69f5e22`)**: Jin "이벤트 기반(EIA/USDA/OPEC)" 선택. EIA/USDA=키 필요(.env 없음)→**CFTC COT**(무료 Socrata, 키불요, 우리 22종중 19 커버)로 빌드. `CFTCCotCollector`=대형투기(noncomm) 순포지션을 **각 contract 자기 ~3년 분포 대비 percentile**(net_spec_pctile). **per-contract 정규화 load-bearing**: 적대리뷰가 **blocker 적발**(절대임계값은 구조적 net-long 편향→GOLD 영구bull/WTI 영구dead)→percentile 수정+라이브 재검증(GOLD 중앙값→중립, WTI→bear가능, 대칭 7불/4베어). fuser commodity 브랜치만(타 asset-class byte-identical), regime 힌트=signal-only(size/block/halt 없음). 임계값/momentum reading /debate flag.
- **⏳ #4 OKX 거래 적음** (다음): us.okx.com=US컴플라이언스 `51155`로 변동성알트 44 영구blocklist(정당, throttle아님). 거래가능 페어∩신호 교집합 얇음(전략이 변동성알트 발화=US제한, 메이저 BTC/ETH 잔잔=미발화). **venue 제약 근본**→거래가능 majors 전략튜닝 or 유니버스 재설계 필요(미착수, 오픈 설계).
- **잔존/후속**: fx_range_fade 1R heuristic & COT 임계값 /debate 캘리브; WAL creep(일일 재기동); Alpaca 페니주 유니버스 품질; 위성 재배색(`e54aeb8`) 하드리로드 필요. `project_ai_conductor_direction`·`project_operating_thesis_surgical_strike`·`project_vault_3axis_secondbrain`.
- **bar↔tick thesis-break 비대칭 (의도, 하드닝 #7 2026-06-23)**: bar-recalc(~5s)는 broken-바 1개를 confirmed로 카운트(`_DEFAULT_BROKEN_STREAK`=`EXIT_THESIS_BROKEN_TICKS` floor — 느린 케이던스라 단일 바 신호=확증), tick(sub-second)은 SUSTAINED gate로 N개 연속 broken-틱 요구(단일 노이즈 틱이 신선한 winner를 BROKEN으로 안 뒤집게). **측정 우선**: close record에 `positions.exit_cadence`(bar/tick) 태그 + since-reset rollup에 close_reason×cadence split 추가. bar-path thesis_cut율이 tick 대비 확증 elevated일 때만 bar에 2-바 연속-broken streak 스레딩(CUT을 LESS 발화=winner 흐르게, throttle 아님) — 데이터 확증 전까지 보류.

## 불변 (나침반 — 항상 유효)

**🎯 봇 목적(Jin 2026-05-30, 나침반)**: surgical-strike — 정밀 타이밍 진입/엑싯, **오직 수익, 근거 있는 거래**. alt-data(뉴스·매크로·CoinGlass·FRED·MyFxBook) = 진입 근거 SIGNAL·레짐 evidence(NOT 방어 blackout/throttle). 손실방어 = **정밀 엑싯**(적응형 stop/타이밍)으로 — 사이징 축소·진입 차단 아님. 데이터 충당 + AI콜/기술계산으로 판단 정밀·신속. ⇒ **#27(AI 타당성+alt-data) 1급 격상**, 엑싯·레짐 정밀도 강조. 위임: "말이 되는 봇 + 목적 충족 + 원하는 대시보드" 나올 때까지 끝까지 자율+검증, 컴프레션/핸드오버 내 판단. ([[north-star]] Operating thesis · `project_operating_thesis_surgical_strike` · `feedback_autonomous_until_intent_met`)
**불변**: 9-stack 봉쇄 · hard-MAX(headroom_min+0.09 ceiling) · AGGRESSIVE(방어throttle/축소 X, 거부키워드 0) · DEMO/PAPER only · builder≠reviewer · OKX 봇 무중단(최종 재기동만 graceful) · Claude 창 kill 금지 · workflow-first 기본.
**목표 3 스트림**: A=OKX 크립토 SPOT(롱only,lev1,24/7) · B=Capital CFD(FX/지수/금,롱숏,per-market lev,세션) · C=Alpaca 미국주식 SPOT(롱,US장/PDT/갭). 크립토 파생 불가(OKX 검증실패=spot, Binance 선물 막힘→Binance 철회 `22fffd9`). **plan SSOT**=`.claude/plans/stream_architecture_redesign_2026-05-30.md`(StreamConfig·product_class 1급·additive 무중단·T0-T17).
**AI 제공자(중요)**: **GPT(OpenAI)=봇 LLM, Gemini=교차검증 — 둘 다 사용 가능. Anthropic/Claude는 Jin이 차단 → 호출해도 안 됨(라우팅 금지).** /debate=GPT+Gemini.

## 핸드오버 아카이브 (2026-06-11 다이어트 — 원문 무손실 이동)

- [[now-archive-2026-06-11-p1]] — 2026-06-04·06-03 핸드오버 (freeze 6-fix · P5 틱엔진)
- [[now-archive-2026-06-11-p2]] — 2026-06-01 핸드오버 4건 (인시던트 캐스케이드 · P0a)
- [[now-archive-2026-06-11-p3]] — 2026-06-01·05-30·05-31 핸드오버 + 운영/debate 기록
- [[now-archive-2026-06-11-p4]] — 2026-05-28 venue wire + P0 Day 1-4 완료 기록
- [[now-archive-2026-06-11-p5]] — P0 Day 5-7 완료 + sprint coherence verdict
- [[now-archive-2026-06-11-p6]] — What changed / Pending decisions / Active plan (구)

## Auto-generated (DO NOT EDIT BELOW)
<!-- AUTO-START -->
- Latest daily: (none yet)
- Open incidents: 0
- Recent ADRs (last 7d): [[ADR-001-vault-structure|ADR-001]] [[ADR-002-vision|ADR-002]] [[ADR-003-8-layer-architecture|ADR-003]] [[ADR-004-per-gate-ai-pipeline|ADR-004]] [[ADR-005-sizing-formula-cell-routing|ADR-005]] [[ADR-006-cell-matrix|ADR-006]] [[ADR-007-learner-network|ADR-007]] [[ADR-008-7-strategies-signal-generator-role|ADR-008]]
- Top touched (7d): vault/10_decisions, .claude/agents, .claude/skills, .claude/hooks, tools/vault_lint.py
<!-- AUTO-END -->

## Implementation status
- P1.0 ignition fired at 2026-06-24 00:32 (paper=True, full_pipeline=True)
