# Loop State — 자율 로드맵 Grind (2026-06-22 시작)

> 🚀 **ULTRACODE ON** (Jin 2026-06-22): 모든 substantial 작업 = Workflow, exhaustive, 토큰 제약 없음. 적대 검증 기본. **전면 정합성 감사 진행 중**(wcoherence): plan·runtime·harness/agents/skills·dev-env·bot-config·dashboard·vault-intent 7축이 Jin 의도(mandates/north-star/락인결정)에 정합한지 + 제대로 도는지 → 종합 → 필요한 것 자율 실행. 루프 계속.

근거 로드맵: `.claude/plans/structural_roadmap_2026-06-22.md`. 매 iteration: 측정·버그수정·설계/리서치 업데이트 → 아티팩트(`artifacts/system-architecture.html`)·vault 갱신 → 봇/대시보드 실측. 중대 결정만 STOP+Jin surface.

## 🎯 GOAL (이게 만족되면 루프 STOP + 최종보고 — 무한 금지)
"**측정 정직 + 멀티스트림 정합 + 위생 완료, 거동변경(sizing/execution)은 디베이트돼 Jin 결정 대기**" 상태:
- [ ] P0.2 PF/WR/digest가 drift 반영(두 장부 통합)
- [x] P0.3 ticker별 손익 대시보드 노출 — ticker_stats(positions.pnl_r, drift포함) 라이브+TDD. 발견: 전종목 net-neg(BNT −108R·ADA −17.6R·J225 −16R), best도 −4.7R
- [ ] P1.1 사이징→노출 계측(intended vs expressed 가시화)
- [ ] P2.3 cluster_cap equity 정의
- [ ] P2.5 StreamConfig 선언키↔소비키 정합 CI 테스트
- [ ] P5.2 signals.correlation_group populate
- [ ] P4.1 tick_engine fixture 수리 또는 명시 deferral
- [ ] P6 위생: stale docstring/pyc, /Volumes 결정 surface
- [ ] 아티팩트·vault 최신 + 봇이 전 fix 로드한 채 정상거래 + 대시보드 실측 일치
- [ ] P1.2(LIVE-1)·P3(A/B/C) = /debate 완료 + Jin 결정 대기로 surface
- [ ] 🆕 **대시보드 전면 재구성** (Jin 2026-06-22): Neural Cloud 유지+활동반영 진화 + 우측 탭(활동/퍼포/로직/빌드/가야할길/배운것), 한페이지·정보밀도·**실데이터 only**. 설계 Workflow(wf_5fdb7252-10c)→점진빌드→**계속 업그레이드**(영속 트랙)

## 🛑 STOP & SURFACE (Jin 결정 필요할 때만 중단)
- 우리 구조와 안 맞거나 **구조 설계 선택**이 필요한 것
- **sizing/execution 거동변경**(라이브 노출 변경: LIVE-1, P3) → /debate는 돌리되 **적용은 Jin**
- Capital equity-CFD whitelist 포함 여부(P2.4)
- 테스트가 드러낸 예상 밖 구조 결함
- /Volumes vs /Users canonical 등 운영 결정

## ✅ DONE (this session)
fix#1(drift −209.65R 라이브) · P2.1(bars baseline) · P2.2(layers 폴백) · P0.4(pnl_r clamp ±10→±100) · P4.1 진단(틱엔진 정상=fixture 드리프트) · P3 디베이트 · **P0.3(ticker_stats 라이브+TDD)** · **대시보드 wave1(6탭 composite + /api/buildlog·roadmap·lessons, APPROVE_WITH_NITS, :8770 라이브 audit 통과)**

## ▶ NEXT 순서
**🆕 대시보드 재구성**: wave1(6탭)+wave2(reference탭 + Neural Cloud 활동반영 globe-funnel.js=keeper) 완료. **🚨 Jin 2026-06-22 피드백 → 명료성 재설계 Workflow(wesaghr72) 진행중**: [[feedback_dashboard_english_only]] 영문 원칙 + jargon 덩어리(STRUCTURAL LOSS/BLEED/HIDDEN/drift-loss) 제거, 상단=깔끔 머니 헤드라인, BLEED/HIDDEN→Performance 탭. cloud+log 유지. 브라우저 무오픈([[feedback_dashboard_no_browser_preview_verify]]) — preview로 검증. 랜딩 시 preview screenshot로 Jin에 확인.
📌 **wesaghr72=완료(영문 토대, :8770 preview 검증). 종합 블룸버그 리스타일 = wb02l90vv 진행중(Jin 2026-06-22, [[feedback_dashboard_bloomberg_dense_no_cards]])**: 최대 정보밀도·리스트/테이블·**카드 제거**:
  (a) Exchange 3카드 → compact 행/테이블(venue│equity│today│open│trades 한 줄씩).
  (b) **Logic 탭 = pathway 리스트**: 활성 strategy×regime를 한 줄(`strategy · regime · exit · venue …`)로 쭉, 9-패널 그리드 제거.
  (c) **Activity 탭 = Open Positions + Recent Trades 위아래 풀폭 스택**(side-by-side X — 더 많이·깔끔).
  (d) **Lessons = 디테일 리스트**(레슨당 title+full takeaway 트렁케이트X+status/kind; anti-pattern/RCA/debate 리스트 그룹).
  (e) **Roadmap = 디테일 리스트**(P0→P6 phase+하위item text+DONE/BUILD/DEBATE 태그+plans/next).
  (f) [Jin 2026-06-22 추가, wb02l90vv **다음** 패스]: **좌우보다 위아래 레이아웃 우선** / 필요시 **탭 분할**(크램 대신) / **equity curve를 메인 항상보이는 영역에도**.
  (g) [Jin 2026-06-22] **Live gate-activity 피드**: Activity 탭 gate funnel **바로 아래** — 최근 게이트 결정 라이브 피드(`G# · PASS/KILL · strategy ticker · reason · time`, 폴마다 갱신, 밀도 높게). 신규 snapshot 필드 `recent_gate_events`(gate_events 테이블 last N) 필요. 배치=Activity(Performance 아님 — 라이브 ops). globe Gate Funnel Ring과 짝.
  (g2) [Jin 2026-06-22 긴급] **게이트 피드 필터 완화**(fix Workflow w6c1hh14x 랜딩 즉시, board_tabs_ext.js renderGateFeed): HOLD 이벤트(봇 모니터링 활동, 라벨 빈칸이라 현재 필터가 떨굼=14/20 사라짐)를 **표시하게 완화** — 모든 게이트 활동(gate+decision+time, 라벨 있으면 추가) 보이게. 절대 안 비게. 종목 라벨 JOIN은 M 직후(snapshot).
  (h) [Jin 2026-06-22] **Logic 탭 확장**(="한눈에 뭐가 어떻게 퍼포밍 중인지"): ① Active 전략(지금 발화/보유) ② Active 엑싯(엑싯 FSM 진행 포지션 + 왜) ③ 각 전략/포지션이 **어떤 레짐에 어떻게 물렸는지**(strategy×regime 바인딩 명시) ④ **전략 설명**(각 전략 뭐 하는지 — vault/20_strategies/*.md 소스, 신규 endpoint or static). pathway 리스트(b)를 이걸로 확장.
  (i) [Jin 2026-06-22] **모바일/아이폰 심플 버전**(사이드 deliverable): 별도 페이지 `:8770/m`(server route + 신규 mobile.html/mobile.js, /api/snapshot 소비). 아이폰 폭(~390px) **단일컬럼·심플**(핵심: equity·today·PF/WR·open positions·bot status, 데스크탑 detail 생략). 영문, Bloomberg 원칙 간소화. 데스크탑 board_*.js와 독립.
  (j) [Jin 2026-06-22] **전체 at-a-glance 검토+보강** (🌟 north star='한눈에 알아볼 수 있는 대시보드'): wb02l90vv 랜딩 후 fresh-eyes 글랜스 검토 → 빠진 것 보강. 후보: ① 최상단 health 한줄(alive·tick 신선·win/lose·anomaly) ② equity curve 상시(=f) ③ 색=상태 즉독(녹/적) ④ worst 옆 '왜' 한줄 ⑤ anomaly/alert strip(stuck exit·stale·drift) ⑥ per-stream sparkline. best 반영.
  (k) [Jin 2026-06-22] **Polaris ★ 별 폴리시**: 헤더 ★ **크게** + **라이브 모드(봇 alive+최근 tick 신선)일 때 twinkle/sparkle 애니메이션**(부드러운 glow+반짝), stale/정지면 잔잔. board.js 헤더 + CSS keyframes, live-mode 조건 class. 예쁘게.
  (l) [Jin 2026-06-22] **데이터 기반 종목 이름**(S/리셋 단계): 봇이 회사명 미보유 → Alpaca `/v2/assets`·OKX instruments·Capital markets 메타데이터 fetch → `universe.display_name` 컬럼(+migration) → snapshot 포지션/거래에 name 실어 → 대시보드(board_tabs.js)가 하드코드 SYM_NAME 맵 대신 데이터 사용. 봇 재기동(리셋) 때 populate. 페니주 하드코드 금지(틀릴 위험).

**🌟 대시보드 north star (모든 패스 기준)**: Jin이 0.5초에 "뭐가 어떻게 퍼포밍/도는지" 아는 것. 한눈·영문·블룸버그 밀도·카드 지양·실데이터.
⚠ **프리뷰/서버 안정 가드(2026-06-22 디렉토리리스팅 사고)**: 대시보드 Workflow 에이전트는 **서버 stop/restart 금지**(`stop_dashboard.sh` 호출 X, preview stop X) — 떠있는 :8770을 `preview_start("polaris-dashboard")` **재사용만**. 여러 에이전트가 stop/start 반복하면 :8770 죽어 디렉토리리스팅 뜸. JS 변경은 ?v bump로 충분(재시작 불필요), 백엔드(snapshot/server) 변경만 메인이 1회 재시작. `/` 라우트는 정확매칭이라 **쿼리스트링(`/?_=`) 붙이면 디렉토리리스팅** — 순수 `/`만.
  전부 영문, cloud+log 유지, preview 검증.

## 🔬 시스템 감사 완료(wnmnilw3g) + 실행 프로그램 (Jin 2026-06-22 — 최우선) → 전체 [[system_design_audit_2026-06-22]]
**핵심**: 아키텍처 sound. 깨진 건 측정·입력데이터·청산보호·튜닝. '−266R'는 상당부분 측정 아티팩트(cross-venue R 합산 무의미 + reconciled mae를 realized로 오기록 −211R). 정직 $: capital +$431/okx −$646/alpaca −$1881. Capital FX −0.05R=결함은 data+fill+measurement. ⚠ 내 P0.4+fix#1이 −211R 아티팩트 키움→정정.
**Jin 결정(락인)**: ①fills.$ 진실+positions.pnl_r 재정의(스트림공통 R, reconciled mae는 R 제외→drift 카운터) ②Alpaca equity 신규중단+복구 ③안정화 바로/트레이딩 param /debate.
**프로그램(순서)**:
1. **[M] 측정 재설계**(glance-pass wifk9z3rg 랜딩 후 — snapshot 충돌 회피): R=pnl_usd/risk$ 일관 정의(스트림공통)·클램프 ±10→±100 통일(compute_unrealized_pnl_r)·reconciled exclude·entry_atr high-side bound·DEFAULT_R_USD/$50 denom 정리. 파일: _production_close_helpers·exit_engine·snapshot_queries·confidence. TDD+builder≠reviewer. **+ 게이트 피드 데이터 fix(같은 snapshot_queries.py, M 직후)**: recent_gate_events에 signal/position JOIN해 strategy/symbol 채우고 HOLD/ADJUST_EXIT 포함(봇 모니터링 활동 가시화 — Jin '게이트 비어보임'은 라벨 빈칸 탓, 실제 HOLD 163+ADJUST 30/최근200으로 활발) + gate_funnel 윈도우 점검.
2. **[S] 안정화**(바로 승인): OKX venue-resting 스톱(conditional sl_trigger, flow_not_block 보존)·regime bar-close 5m(1233 flip 해소)·Alpaca recency guard+equity 신규중단·OKX zero-vol/flat 합성바 필터.
3. **[D] /debate 완료 + Jin 4개 전부 승인**(2026-06-22; GPT 2-pass, Gemini hung→재시도 가능; vault [[trading_params_audit_2026-06-22]]): D1 crisis **적응형 임계+상한 cap**·equity vol floor 1.0→2.5%+(crypto 보정 유지) / D2 asia 하드코드 제거→**venue-native session**(US RTH/Capital 영업/OKX 24-7)+**expectancy 가중** / D3 틱엔진 **OKX 이동**·Capital=overshoot-fade 전용 / D4 volume_burst **fade-first**(스파이크 실패+저항→SELL). **적용=측정+리셋 후** 단계적, 각 TDD+리뷰. 전부 aggressive·flow_not_block(사이즈컷/차단 없음).
4. **[R] 데이터 리셋**(🧹 아래 절차) — M+S 랜딩 후(고친 메트릭으로 클린 슬레이트).
5. 깨끗한 측정 시작 → 루프 지속 검증.
(거동변경=Jin 결정 STOP 유지. 측정/안정화=apply-now 승인.)

### ▶ PROGRESS (2026-06-22 라이브)
✅ **M 측정** 완료(risk_unit.py SSOT, BNT −108R 아티팩트 제거, 두 장부 화해, 19 테스트). ✅ **정합성 감사**(Ultracode 8축) → 코어 정합, 드리프트(ops/docs/스트림) → ✅ **remediation**(docs: ADR-011 AI-free/ADR-004·008 정정/_NOW/roadmap superseded; code: /debate GPT+Gemini 병렬·**게이트 JOIN(20/20 라벨)**·verify_dashboard·log rotate 824MB·키워드 위생). ✅ **ops 자동화 설치**(watchdog/daily-restart/digest, 고스트 제거). ✅ **.env 보안**. ✅ 대시보드(6탭·영문·블룸버그·글랜스·접기·게이트피드·모바일+globe+sign·AI-free 라벨·★).
✅ **Jin 결정 3**(2026-06-22): OKX bounded auto-resume / Alpaca 좀비 flat-close+halt / ops 설치(완료).
✅ **S 안정화 완료(APPROVE)**: OKX bounded auto-resume+400 정밀도fix+심볼skip · Alpaca recency guard+halt+좀비reconcile · regime bar-close(1233 flip 해소). flow_not_block 보존, 2318 테스트.
✅ **R 데이터 리셋 완료**(2026-06-22 07:58, Jin 승인 bars/quote 유지): 봇 정지(sentinel)→archive 361M+tag pre-reset-2026-06-22→**1.18M행 wipe**(38 테이블, bars/quote/universe/blocklist 유지)→봇 재기동 PID 30223 **M+S 새 코드 로드**+risk_usd 마이그레이션→**클린 슬레이트**(equity $130k·PF 0·positions 0·drift 0·아티팩트 0). 이제 두 장부 일치·정직 측정.
✅ **D param 4개 완료+적용**(APPROVE, flow_not_block True·no_throttle True; 2026-06-22 08:25 graceful 재기동 PID 35563 M+S+D 코드): D1 crisis 적응형+cap(crypto frozen) · D2 venue-native session(asia 하드코드 근본수정)+expectancy(부당승급만 막음, 사이즈컷 X) · D3 틱엔진 OKX(Capital quote size 없음=fade-only) · D4 volume_burst fade-first(재조준).
# 🏁 fix 프로그램 완성 (M 측정 / S 안정화 / R 리셋 / D 4-param). 봇=완전 고친 시스템, 정직 측정, 바 0.
▶ **forward 단계** (리서치 어젠다 SSOT=[[research_agenda_2026-06-22]] + A/B/C/D/E, 64항목+5갭):
- ✅ **P0 적용완료(whbkbbr8t, APPROVE)**: OKX venue-resting conditional stop + 합성바 필터 + frozenset 4-id fix.
- ✅ **첫 edge-readout 사이클 적용(PID 44311, 09:43)**: flow_pressure(틱,OKX) 주출혈 진단 → **edge는 +14.4bps인데 fee 19.2bps 과잉거래로 net −4.8bps**(anti-edge 아님, 방향 맞음). Jin 전부-retune 승인 → theta_ofi 0.20→0.32 + trail 2→4ATR + maker(타이커폴백), 다 flow_not_block·env-tunable. **venue-stop stopfix**(9896 per-tick → 300s 캐시)도 함께. → **flow_pressure 재측정 ✅ 작동**: per-trade net −$0.858→**−$0.235(73%↓)**, gross/trade +0.62→+1.15(theta↑가 굵은edge 선택), 0 근접(gross 1.15 vs fee 1.38). stopfix도 ✅(스팸 ~150x↓ sparse). **edge readout 2건 추가**: micro_reversion=동일 fee>edge 패턴(retune 후보), tsmom=OKX gross −$16.4(방향문제, thin 5거래). 더 누적 후 조치.
- ⏳ **fill_rate_cut = Jin OK 대기**: 유일한 잠재 방어바(예산 hot시 약신호 0 suppress). 라이브 0건이라 긴급도 낮아 stopfix+retune에서 **분리**. 제거할지 Jin 확인 중.
- **Gate 0 = 클린 데이터 누적**(봇 돌며 NIG posterior/cell-matrix 채움) — 모든 per-strategy edge/regime-EV가 이거 후 답. 봇 돌고 있음.
- ✅ **RADAR '차례대로 다 하자'(Jin) 진행**: 🔴 venue-stop **tgtCcy 버그 fix**(51000→0, 16건 라이브 성공 — gap-through 보호 실제 가동) · 🔴 tsmom 2hr보유=**의도된 loser_timeout 7200s**(버그아님, Jin 결정 대기) · 🟢 **replay harness 부활**(present_unwired, default DB fix → OOS 검증 가능, bar전략용; nightly ops등록 Jin) · 🟢 **classify_regime=DESIGN_ONLY**(STUB 플래그는 stale 오인, compute_real_regime_signal이 진짜 4-state 분류기로 라이브 정상 — 손댈것 없음, agenda 플래그 정정요).
- ✅ **flow_pressure retune 178거래 확정**: per-trade −0.858→**−0.282(67%↓ 유지)**, gross 0.861 vs fee 1.143(아직 살짝 fee-음). **PF 0.19→0.366 상승**, real-fee-net −$797.
- ✅ **fill_rate_cut 제거**(Jin '다 고쳐줘', PID 54510) + **대시보드 dual-equity**(demo·real-fee-net) + **모바일 1초 실시간**.
- 🟡 다음: flow_pressure 0-크로싱(theta 0.32→0.40?)·micro_reversion 같은 retune·US장 Alpaca+bar edge. P1+: exit FSM calib·entry-slippage·alt-data→MarketView·OKX majors. P2+: 리서치-메쉬·AI-conductor.
- 종목이름(l) · **클린 측정 관찰**(핵심: 고친 시스템 PF>1? 며칠).
🚨 Jin 재강조(2026-06-22): **바(방어블록/throttle/사이즈컷) 절대 금지, DEMO 흐르게, 측정 우선** — 모든 리뷰 가디언 강제. 루프 계속.

## 🧹 데이터 리셋 (Jin 2026-06-22 "노이즈 다 죽여 제대로 측정") — 감사 직후 실행
**타이밍**: wnmnilw3g 감사 **완료 후**(감사가 옛 데이터로 진단해야 의미 — 진단→fix→리셋). 지금 리셋 금지.
**절차**: 봇 정지(MANUAL_STOP) → `git tag pre-reset-2026-06-22` + DB→`data/archive/polaris_live_pre-reset-2026-06-22.sqlite`(아카이브, 영구삭제 X) → wipe → equity→starting → 봇 재기동 → 대시보드 clean.
**범위 wipe**: positions·fills·orders·order_intents·gate_events·gate_*·signals·regime_state·learner_*·cell_matrix_*·ticker_baseline_*·strategy_regime_prior·strategy_risk/halt/fault·position_*·risk_events·market_events·meta_labels·loop_*·entry_admission_shadow·allocator_reservations·benchmark_results·replay_runs·rollback_candidates·ai_lessons. **Keep**: universe·venue_blocklist·watchlist_focus·**bars·quote_ticks·altdata_snapshot(시장 history=비-노이즈, warmup 갭 방지)** — Jin이 "전부"면 이것도 wipe. 코드/config/vault 무관.
**검증**: 리셋 후 대시보드 PF/WR/equity/drift 전부 0/clean + 봇 fresh 거래 시작 확인(preview).
⚠ **루프 코디네이션**: 대시보드 Workflow 도는 동안 board.js·globe·polaris_graph **회피** — 루프 **백엔드-순수**: P2.5(키정합 CI) → P5.2(correlation_group) → P1.1(사이징 data) → P2.3.
이후: 대시보드 split(LOC nit: board.js/tabs/server cap초과) → P0.2 → P4.1 → P6 → LIVE-1/P3 surface → 최종보고

## 원칙
각 BUILD=TDD(실패→코드→pass)+ruff/mypy+적대검증(builder≠reviewer). DEMO·aggressive·flow_not_block·9-stack 봉쇄 유지. 거부키워드 0. 주기적으로 봇 재기동(fix 로드)+대시보드 실측.
