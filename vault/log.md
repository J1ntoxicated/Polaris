# Polaris Log (chronological 1-line append, NO interpretation)

(2026-06-01 이전 라인은 [[log-2026-05]] 로 월별 아카이브 — 2026-06-11 로테이션)

- 2026-06-01 P1 replay 하네스 적대 리뷰(builder2≠reviewer): determinism byte-identical·live DB read-only(mtime 불변)·키워드 0·1488 pass 확인. CONCERNS: (1) bot Sharpe=per-trade vs baseline Sharpe=per-bar 비교 frequency 불일치(relative tier 편향), (2) regime crisis 즉시-flip + initial-seed 미러 누락(replay regime 1bar lag), (3) walk-forward IS/OOS 미배선(is_oos_spread 항상 0), (4) parity test 동어반복(같은 함수 2회). 머지 차단은 아님.
2026-06-01 P1: replay/벤치마크/walk-forward 하네스 커밋(5055eb1) — go-live 측정게이트. gate verdict=FAIL(봇 아직 edge 무, NIG LCB<0)=정직 베이스라인.
2026-06-01 P4: WS 실시간 가격 LIVE(43797fb/11ea8ca) — ws_common/quote_writer/venue WS×3/3 consumer(대시보드 실시간px·exit·G4). OKX wspap 라이브 검증. gitignore data/→/data/ repo위생.
2026-06-01 P0: posterior+regime를 cell routing score에 배선(0c745ac) — edge-routing flow_not_block(9-stack 불변). replay FAIL→FAIL(+0.02%): routing은 없는 edge 못 만듦, 진짜 레버=P3.
2026-06-01 obs: 로깅 전 포인트(77faf35) — exit/close/open/fill/universe/signal/regime/gate/posterior/session 구조화, behavior0.
2026-06-01 핸드오버: _NOW.md 전면 갱신, 봇 PID 46005 전 라이브 재시작. 남은=P2(GPT 측정결정)·P3 self-evolve(edge 레버)·P5 lifecycle·#17. 세션 리셋 준비(봇 포함).
2026-06-01 08:15 [debate p3_self_evolve: Claude RECONSIDER + codex CONFIRM_WITH_CHANGES -> REFRAME(증명먼저) accepted; spec revised + debate record written]
2026-06-01 11:05 [streams P0+P1 LIVE: Capital/Alpaca coverage fix dc3abda + alpaca start-fix be978c1; bot restart PID=22518; verified alpaca 1D bars 0->2951(13 instr), Capital fx_majors_kept=5, megacaps is_active=1; 1584->1595 green]
2026-06-01 04:51 [Edit: /Volumes/Development/Projects/Polaris/vault/_NOW.md]
2026-06-01 17:48 [P3 P0a KILL-spike committed d7cf7f3: verdict VALIDATION_STARVED, positive_control N=53<needed69, is/oos 0pass over 18 entry-trials, 1671 green]
2026-06-01 19:36 [P0 live fix ff9c997: OKX close chunked <=1000USDT (was 51201 bleed); graceful restart PID6702; open 52->19, ALGO 36->2, 51201=0; 1689 green]
2026-06-01 21:33 [P0 followups 60094d1+5cd3887: pnl_r_net dim-fix (|net|>1000=0) + orphan reconcile (open 52->3, reconciled 18 phantom); bot PID45047; 1702 green]
2026-06-02 02:24 [night4: fresh clean DB restart PID85373, reconcile gated off; errorloops=0, OKX trading fee10bps, WS 3venues; commits ff9c997/60094d1/5cd3887/2bc7c76/40d9604/eb398b6; FOUNDATION next=realtime-tick/Capital-strategies/capital-sizing/multisource]
2026-06-02 06:32 [Edit: /Volumes/Development/Projects/Polaris/vault/_NOW.md]
2026-06-03 08:37 [vault 3축 second-brain: telemetry 2632 archived→data/lessons_archive (ai_lessons DB=SSOT), ADR alias×10 (~345 `ADR-NNN` resolve), MOC A1/A2/A3+INDEX bus-map, islands 8→0 dangling→0, post_trade_reflector DB-only 46 green deploys-on-next-bot-start, bot PID85373 DEAD]
2026-06-03 12:35 [P5 tick-engine LIVE: bot PID17127 TICK_ENGINE_ENABLED=1, real orders OKX BTC-USDT flow_pressure-long + Capital GOLD micro_reversion-long, Capital deal_id captured via confirm-poll, clock-domain bug fixed; commits 2854899/6627f1f/23722f4/c1c72a1]
2026-06-03 15:33 [P5 deal_id RESOLVED: capture(c1c72a1)+persist positions.deal_id+restart-hydration(cd7f6a0)+no-deal_id orphan reconcile(657e193); bot PID60866 capital-only live, deal_id closes+scalp exits working, hard-errors 0; tick-engine={capital}(8775734)]
2026-06-03 19:43 [P6 deploy PID2115: ②a asset-class regime (f0a8287 — live DE40 bear_trend/equity crisis = working) + alpaca-close wired (was missing entirely) + no-overnight session-flat (dc80107); 1825 green, 0 errors; SPCE flatten at 13:30 UTC RTH open]
2026-06-04 — freeze 6-root 제거(러너백업/바동기루프/position_id충돌phantomPnL/WAL폭증/baselineGIL/quote_ticks647K-대시보드경합) → 봇+대시보드 동시동작, SPCE 청산완료. commits b3ccc5b·3607205·5c26018·7652947·670f458·b00bdf2·b600617·d869874
2026-06-04 — 라이브 6h 감사(6-agent) → #2 phantom PnL 정정(+144583→-3712 진짜) + #1 좀비-청산 drain(세션게이트, 적대리뷰가 mis-fire 잡음+테스트) + #5b WAL PASSIVE-only 복귀(reclaim이 startup 불안정) + VACUUM 215M→114M + mirror orphan 삭제. 미처리: #3진입다양성·#4 DB-lock STALL·#6 SPCE부분청산·#7 churn·#8 fractionable. commits 4f786b7·69264f1
2026-06-04 — 공격성/asset-class 진입: Alpaca whole-share(non-fractionable 정수재시도) 6030fe9 + FX 라우팅(틱엔진 capital독점→forex는 바파이프라인) 4612b17 + fx_range_fade 신규전략(ADX<20 BB페이드, 엑싯 follow-up) e7416e4 + 위성재배색 e54aeb8. OKX 적게거래=us.okx.com US컴플라이언스 51155 영구blocklist(정당). 다음=FX 배포검증+fx_range_fade 엑싯.
2026-06-05 — Jin '차례대로' 4 follow-up: #1 fade 엑싯 target_harvest(29517f3) #2 안정성=WS침묵+Alpaca quotes-only+DB락해소(e48abd8/f7223aa, symbol-limit·db-lock 0, WAL206→59) #3 코모디티 CFTC COT per-contract percentile 신호(69f5e22, blocker→percentile 수정). 각 적대리뷰 통과. 1869 테스트. 다음=#4 OKX venue제약.
2026-06-10 13:48 [wave1 회계무결성: 부분청산 PnL 슬라이스 스탬핑(A)+hydrate 잔량복원(B)+pending-close 재발사 패리티(E) fix + correct_close_pnl_stamping.py; 1907 green; DB 보정은 승인 대기]
2026-06-10 14:31 [harness-collab-protocol Fable 구조 개정: Workflow 오케스트레이션 SSOT + 루프 3계층(Workflow내/세션/cron) + no-dev-GPT 반영, CLAUDE.md 동기화; fresh 리뷰 2라운드 approve]
2026-06-11 [vault 위생: _NOW 228→45줄(now-archive-2026-06-11-p1..p6 분할이동) + log 5월 1021줄→log-2026-05 로테이션(오늘자 bootstrap 노이즈 5줄 제거) + memory-wikilink 22건 plain화 + banned 표현 4줄 중립 재서술 + 고아 lesson 2건 MOC-A1 연결 — lint error 3→0 / warn 33→5 / info 2→0]
2026-06-11 01:07 [bugc-build: capital T4→lot 변환 배선 — constraint cache + translate + fill 실노출 기록, fixtures 6 epic 프로브, tests 1939 green / mypy strict / ruff clean(기존 baseline.py E402 제외)]
2026-06-11 graph-weave [orphan 97→0 dead 22→0 lint warn 5→0, MOC-lessons+MOC-digests 신설, ADR short-link 139파일 pipe화, reflector telemetry stub 73건 status:deprecated 마킹(삭제 없음), frontmatter 보강 2건]
2026-06-11 bugc-blocker-r1 [constraint_translator step_size minStepDistance→minSizeIncrement 우선 파싱, fixture 6종 grid-정렬 테스트 +13, suite 1957 green / ruff·mypy clean]
2026-06-10 15:55 [bugc R1: step_size=minSizeIncrement 우선 파싱 교정(minStepDistance는 가격거리 오용, GOLD 우연일치 은폐) — capital_sizing 18 tests, 1957 green; 리뷰 approve×3 blocker 0]
2026-06-11 tf-exit-ruler [엑싯 자 타임프레임 정합(_production_atr, MIN_TF_BARS=5)+entry ATR 앵커 분모(positions.entry_atr_pct/tf, R 과장 4-8x 제거)+상대 플로어·±100캡, trail=현재 tf-ATR 유지, scalp/tick 1m 폴백 byte-identical, recalc_excursions dry-run 라이브 403행 검증(-463734R→-0.035R), 테스트 vault 격리 POLARIS_VAULT_DIR(autouse)+메타테스트, suite 2007 green(기존 tick 플레이크 2 제외)/ruff·mypy --strict clean]
2026-06-11 capital-honest-label [D-1/D-2/D-3+B1/B2: Capital 비-200→HTTP_<code> 정직 라벨(http_status 필드, 200+PENDING 경로 byte-identical)+capital HTTP_*/CONFIRM_STALL_PENDING external 분류(fault 0, OKX/Alpaca 무접촉)+open 429/connect-phase·close 5xx/timeout 백오프(3회 ≤1.5s, 모호실패 무재시도=중복주문 0, 합성 HTTP_TIMEOUT/TRANSPORT 반환)+open-leg confirm 폴 httpx 가드(HTTP_CONFIRM)+correct_fake_pending_faults.py(dry-run ro 실측 159 fault/55 halt 전건 INVALIDATED·reverse-mixed 0, --apply 미실행), 신규 테스트 28, suite 2036 green(tick 플레이크 2 기존)/ruff·mypy --strict clean]
