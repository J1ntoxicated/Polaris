# Space Visualization — "Polaris Neural Cloud" 이식 (2026-05-29)

> Jin 요청: auto-invasion 의 스페이스 비주얼라이제이션을 Polaris 에 적용·구성·띄워줘. 더 좋은 방향 환영. 오토모드. DEMO/PAPER, display-only(사이징/주문 무영향), aggressive bias 보존.

## 발견
`~/Projects/auto_invasion_mk1-main/tools/visualizer/` — "Neural Cloud":
- **Canvas 2D**(WebGL 불필요, CPU 사영) 동심원 tier 우주. `static/sphere-render.js`(169KB) = 엔진.
- **Python stdlib http.server** + `/static/graph.json`(주기 fetch) + `/stream/events`(SSE). SQLite feed.
- tier: 0='pos'(open positions) · 1='exit' · 3='reg'(regime) · 5='strat' · 7='watch' · 8='mkt'(universe) · 9='obs' · 10='action' · 11='orbit' · 12='axis' · 13='exit_tally'. 거래 발생 시 중심→바깥 펄스, 진입/청산 시 shockwave.
- Polaris 8-layer/8-gate 와 거의 1:1 매핑.

## graph.json 계약 (target)
top-level: `nodes, clusters, live_trades, recent_closes, galaxy_universe, trade_chains, lifecycle_paths, exchange_pnl, stats`.
node: `{id,label,ticker,direction,exchange,trade_id,strategy_id,asset_group,pnl_usd,pnl_pct,size_usd,intensity,size_mul,cluster,tier,state,i,phase}`.

## 접근 (MVP — Simplicity First)
- **엔진 재사용**: index.html + sphere-render.js + polaris.css 복사, 타이틀만 수정. JS 로직 불변.
- **Polaris 어댑터 신규**(`tools/visualizer/polaris_graph.py`): mk1 의 2376줄 snapshot.py 재사용 X. 대신 기존 `polaris/scripts/dashboard/snapshot.py::collect_snapshot()` (positions/cell_top·bottom/strategy_stats/regime_bars/recent_trades/equity_curve) 를 graph.json 스키마로 매핑 + `universe` 테이블로 galaxy_universe.
- **server.py**: Polaris 버전, 주기적 build_graph → static/graph.json, /stream/events SSE(신규 fills 폴링). port 8770.
- DB = `data/polaris_live.sqlite`.

## 매핑
| Polaris | Neural Cloud |
|---|---|
| open positions (positions/fills) | tier0 'pos' + live_trades |
| 7 strategies (strategy_stats) | tier5 'strat' |
| universe focus (universe) | tier8 'mkt' + galaxy_universe |
| regime_state | tier3 'reg' + stats.regime |
| cell_matrix score | 노드 intensity/색상 |
| recent closes (fills is_close) | recent_closes |

## 실행 단계
- [x] 1. 빌드: 엔진 복사 + `polaris_graph.py`(298) + `server.py`(214) + 검증(9키, 81 노드, curl 200, ruff/mypy clean). commit `fb96129`.
- [x] 2. 빌드 검증 완료 (build agent self-verify: curl 200 + ruff + mypy). display-only라 별도 중리뷰 생략, P2 후속만.
- [x] 3. 서버 detached 실행 (PID 12266, port 8770) → 브라우저 open 완료. 라이브 데이터(포지션 4/universe 71/regime chop) 렌더.
- [x] 4. vault digest([[2026-05-29_dashboard_revival_dynamic_viz_spaceviz]]) + _NOW 갱신 + 본 plan.
- [ ] 5. (후속) richer SSE 이벤트(signal/gate/regime_flip/ai_decision) + tier 1/7/9~13 데이터 wire + edge posterior verdict → 노드 밝기 매핑.

## 더 나은 방향 (후보, 후속)
- 셀 히트맵을 우주 노드 색으로 직접 매핑(edge posterior verdict → validated-alpha 별 밝기).
- 자산 흐름(allocator reservation)을 시냅스 두께로.
- 터미널 dashboard_v2 와 웹 비주얼 공존(둘 다 라이브 DB).

## Out: 풀 fidelity tier(watch/obs/action/axis) — placeholder 로 시작, 데이터 쌓이면 채움.
