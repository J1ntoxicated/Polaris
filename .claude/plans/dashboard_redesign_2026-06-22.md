# Dashboard 전면 재구성 — 빌드 SSOT (2026-06-22)

Jin mandate: Neural Cloud 유지 + 우측 탭 재구성, 한 페이지·정보밀도·**실데이터 only**, "앱 활동 그래픽화", 계속 업그레이드. 설계=Workflow wf_5fdb7252-10c(5-agent). 미감 유지: 다크 미니멀 #05070b, monospace, 고정 팔레트(신규색 금지), 색=상태인코딩, 장식 금지, 한 페이지 no-scroll 30/70.

## 6 탭 (우측)
1. **활동(activity)** — 기본 랜딩, "지금 뭐하나" 3초 판정. VERDICT strip(equity/dayPnL/PF/WR/Sharpe24h/DD) · BLEED callout(reconciled_loss_r/n, forced_exit, rotation) · Gate funnel G1→G8(gate_funnel[] pass녹/kill적 테이퍼) · 3-stream lane(streams[]) · Open positions top6(positions[]) · Recent trades ledger(recent_trades[]).
2. **퍼포먼스(performance)** — real-fee-net 정직 수익성. Dual equity sparkline(equity_curve[] vs equity_curve_real_fee_net[], GAP=정직) · per-strategy(strategy_stats[]) · **per-ticker R(ticker_stats[]=P0.3)** · confidence cells(confidence.cells[]) · fee drag(fee_drag_real/demo_r) · per-stream cost(streams[].fee/slippage/ai_cost/net_after_cost).
3. **로직(logic)** — 8-layer/8-gate 결정 흐름. AI-free banner+shadow agreement(ai_shadow/gpt_stats) · regime matrix(regime_states[]) · cell top/bottom(cell_top/bottom[]) · exit surface funnel(exit_surface) · edge posteriors(edge_validation[]) · learner network(learners[]).
4. **개발(build)** 🆕endpoint — commit timeline · wave digest(vault/log.md) · activity heat-strip · test-health badge. → `GET /api/buildlog`.
5. **가야할길(path)** 🆕endpoint — phase ladder P0→P6 · plan kanban · next strikes(loop_state NEXT) · blockers. → `GET /api/roadmap`.
6. **배운것(learned)** 🆕endpoint — lessons feed(vault/50_research/lessons/) · anti-pattern wall(CLAUDE.md) · root-cause(forensic/) · debate verdicts. → `GET /api/lessons`.

## Neural Cloud 진화 (Canvas 2D, NO Three.js — globe-core/satellites/flows.js)
기존 primitive를 무시되던 라이브 snapshot row에 바인딩(display-only, 트레이드패스 무접촉):
(1) **Gate Funnel Ring** gate_funnel→conductor 주위 8-seg 호(녹pass/적kill 폭∝vol) (2) **Stream Breath** streams open_n/trades/exposed→galaxy halo glow (3) **Regime Tide** regime_states→galaxy 배경색 wash+shimmer (4) **Learner Tremor** |delta_1h|→sat laser 빈도 (5) **Tick Heartbeat** MAX(gate_events.created_ts) advance→conductor.pulse ring (6) **Kill Sediment** kill→transient grey mote fade(측정정직, NOT throttle).

## 빌드플랜 (무중단 점진)
- **P0 스캐폴드** (board.js/board_tabs.js/index.html): 8탭→6탭 재매핑. **#board grid 6-row 계약 불변**(grid-template-rows: auto×5 + minmax(0,1fr) — 행 추가/제거 금지, 탭 strip 내부에서만). 활동=기본. 신규3탭=빈 placeholder. /api/snapshot 무변경.
- **P1 라이브탭 채움** (board_tabs.js/board_tabs_ext.js/board.js): 활동·퍼포먼스·로직 = 기존 snapshot 필드 바인딩(mock 0). dual sparkline+gate funnel=경량 SVG helper, renderKpis reshape 재사용.
- **P2 reference endpoint** (server.py 확장만): /api/buildlog(git log+log.md, 30s캐시) · /api/roadmap(plans+loop_state, 60s) · /api/lessons(lessons+forensic+CLAUDE.md, 60s). **독립 캐시+serve-stale**(요청핸들러서 git 직접호출 금지=_bg_refresh 경합 방지).
- **P3 reference탭 채움** (board_tabs_ext.js/board.js): 개발·가야할길·배운것 = static calm(탭진입 1회 fetch, no polling).
- **P4 클라우드 진화** (polaris_graph.py/globe-flows.js/+신규 globe-funnel.js): graph.json ~4 array + draw pass. globe-core 546LOC=cap초과→신규코드 분리. 트레이딩 무접촉.
- **P5 적대검증** (fresh Claude, builder≠reviewer): 실데이터 grep(mock=0) · 거부키워드 sweep(0건) · 6-row grid 무파괴 · API 하위호환 · 라이브 :8770 audit · vault append.

## 리스크 (빌드 가드)
- ⚠ #board grid 6-row 계약 깨면 레이아웃 붕괴 — 행 불변.
- ⚠ /api/buildlog git subprocess 매요청=_bg_refresh 경합(과거 60s빌드 폭발) — 독립 캐시 필수.
- ⚠ globe-core 546LOC 이미 cap초과 — 신규코드 globe-flows/신규파일 분리.
- ⚠ thin-sample(n<thr) 라벨이 거부키워드(샘플 신뢰도/입증 관련 금지어)로 새지 않게 — 중립 display rule(n<thr이면 dim 처리, 평가 문구 금지), P5 sweep.
- ⚠ SSE {events:[{type,ticker,direction,exchange,ts}]} — Tick Heartbeat {type:'tick'} 추가 시 기존 파서 무손상.
- ⚠ reference 탭 절대경로 read — 파일이동 시 graceful zero.

상태: 설계 완료. 빌드 P0 착수. 관련 [[structural_roadmap_2026-06-22]] · loop_state.md.
