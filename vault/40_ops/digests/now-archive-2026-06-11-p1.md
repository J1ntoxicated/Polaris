---
type: digest
status: archived
date_created: 2026-06-11
tags: [now-archive, handover]
---

# _NOW 아카이브 2026-06-11 p1/6 — 2026-06-04 · 06-03 핸드오버 (freeze 6-fix · P5 틱엔진 LIVE)

(2026-06-11 _NOW 다이어트로 원문 무손실 이동 · 원본 [[_NOW]])

**🟢 HANDOVER 2026-06-04 (FREEZE 해결 + 라이브 감사·후속 fix).** 봇 PID=`data/paper/production.pid`(현재 20723), DB=`data/polaris_live.sqlite`(VACUUM 215M→114M), log=`data/paper/p5_live18.log`, env=`TICK_ENGINE_ENABLED=1`. 대시보드 :8770 라이브 직독 HTTP 200. 봇 거래중(틱엔진 HK50 숏 등 opens+closes), SPCE 청산완료.

**🔬 라이브 감사(6h 무중단 후, 6-agent read-only fan-out) 판정**: "봇은 살아서 실주문·자가복구하나 아직 surgical-strike 아님". 6h 무중단=freeze 6-fix 검증. 4대 기능결함 발견 → 처리:
- ✅ **#2 phantom PnL 정정**(봇 정지 중): fills.pnl_usd cross-match garbage 2행(J225 +$145,424·+$2,870) + 전파 segments 2행 → 실현 PnL **+$144,583 → -$3,712(진짜)**. position_id fix(`7652947`)로 신규는 정상(검증: 최근 cid 전부 venue+symbol, 충돌 0).
- ✅ **#1 좀비-청산 drain**(`4f786b7`): exit 판정됐으나 venue가 영구 거부(deal 만료/데모 자동청산, 579 reject) 5종(J225/US30/OIL_BRENT/LIVECATTLE/GASOIL)을 **세션 OPEN 중 거부 40회+나이 1h+ 시 terminal 처리**(state-drift recovery, integrity-only). 적대리뷰가 **mis-fire 버그 잡음**(Alpaca-only `stream_session_gate_active`를 Capital fx_indices_cal에 잘못 배선→주말 살아있는 포지션 오인) → `_fx_in_session` 캘린더별 디스패치 + partial/full 청산 시 카운터 리셋 + 주말-윈도우 회귀테스트(`test_zombie_close_drain.py`)로 수정. 좀비는 재기동 후 in-session 거부 누적→drain.
- ✅ **#5b WAL 크리프**: P9 reclaim(매10사이클 TRUNCATE) 시도했으나 **큰 startup WAL서 full checkpoint가 bar-ingest write와 I/O 경합→루프 분단위 지연**(`69264f1`로 PASSIVE-only 복귀=6h 안정 동작). 크리프(~18MB/h)는 일일 재기동으로 해소하는 trade. + VACUUM으로 DB 114M.
- ✅ stale `dashboard_mirror.sqlite` 451MB orphan 삭제.
- **증상(Jin)**: 봇이 반복적으로 얼어 → 대시보드 무갱신 + recalc 굶어 SPCE(알파카) 미청산 + phantom 손실 표시.
- **잡은 freeze 뿌리(커밋체인)**: `b3ccc5b`+`3607205`(러너 218MB 디스크백업=매시간 ~3분 루프블록; `_flush_disk_snapshot` 안에서 게이트 → max_hold 포함 모든 호출자 OFF, `LEARNER_DISK_SNAPSHOT=1`로만 ON. rollback SSOT=in-DB row라 디스크 .db는 아무도 안 읽음) · `5c26018`(바 파이프라인 regime/indicator 45심볼 동기 루프 → 심볼간 `asyncio.sleep(0)` yield) · `7652947`(**position_id 충돌**=틱엔진 generic signal_id(`tick_micro_reversion`)로 동일틱 다종목이 `pos_{sig[:16]}_{ts}` 같은 id → INSERT OR REPLACE로 positions 행 덮어쓰기 + fill을 contribution_id만으로 cross-match → J225 close가 OIL 94.168 entry로 계산 +$145K phantom ~965,000x; venue+symbol 유일화 + close/recalc 4개 매처에 `instrument_id` 필터) · `670f458`(WAL 무한성장 729MB=상시 reader가 autocheckpoint truncate 막음 → DB op이 wal-index 걷느라 UN(uninterruptible) 디스크 I/O freeze; 전용 스레드 재청구 체크포인트 15s, off-loop) · `b00bdf2`(**baseline sort GIL**=`compute_baselines_batch` 순수 Python `sorted()`가 to_thread여도 GIL 보유 → 유니버스 refresh(5min)마다 신규 focus 심볼 7일窗 시딩이 루프 분단위 블록(STALL 225s); 8-window 청크로 await 사이 루프 숨쉼).
  · `d869874`(**대시보드 freeze 근본**=`quote_ticks` 647K행/215MB라 대시보드 1s `collect_snapshot` read가 무거운 random-IO 스캔→봇 write와 경합→WAL 폭증→UN freeze. 봇이 대시보드 붙는 순간만 얼고 단독은 멀쩡했음. mirror-backup 시도는 역효과(215MB/15s backup이 snapshot 잡아 WAL 712MB로 키움). 근본=`quote_ticks` 2h 캡(틱엔진은 in-mem ring 읽으므로 안전), prune+PASSIVE 체크포인트를 스레드서 15s. 작은 DB→빠른 대시보드 read→WAL 바운드→freeze 없음, 대시보드 라이브 직독).
- **교훈**: 내 백그라운드 `sqlite3` CLI 진단이 hung reader로 WAL 핀 → freeze 가중+진단 오염. 라이브 DB 쿼리=`python3 -c`(명시적 close)로. + 두 프로세스 SQLite 경합은 테이블 크기에 비례 → 핫 테이블 캡이 근본. + `wal_checkpoint(TRUNCATE)`는 reader와 데드락(PASSIVE 써야). `feedback_realtime_price_first_principle`.
- **남은 기능결함(감사 발견, 미처리 — 다음 작업)**: ③ **진입 채널 붕괴**(틱엔진 진입 100% micro_reversion/Capital, OKX 0)→왜 다른 family/venue가 게이트/sizer서 탈락하는지 `gate_events` 추적·다변화 ④ **STALL steady-state**(감사 6h간 972회 max 514s, `database is locked` 동반)→**VACUUM(215M→114M)으로 worst 514s→5.6s 급감**(비대 free-page DB가 DB-op·lock 경합 주범이었음). 잔여 5.6s stall은 write 커넥션 분리로 더 줄일 여지(아키텍처, 우선순위 낮아짐) ⑥ SPCE 부분청산 누적손익 재기록+청산총액>진입(슬라이스별 pnl + 잔여수량 클램프) ⑦ US100/US500 단일종목 churn(7.8분 reversion, 승률 29%)·reversion 엣지 LCB 재평가 ⑧ not-fractionable 1024 reject 노이즈(universe 사전필터/정수주문). #3·#4가 surgical-strike의 핵심.
- **운영 메모**: WAL high-water 크리프는 PASSIVE-only의 알려진 trade(일일 재기동 reset). `start_dashboard.sh` pkill 직후 재기동 시 가끔 2 proc(포트체크 레이스).

**🟢🟢 HANDOVER 2026-06-03 (P5 틱-엔진 LIVE — "실시간-틱 의사결정" 완성·기동).** 봇 PID=`data/paper/production.pid`=2115 LIVE, DB=`data/polaris_live.sqlite`, log=`data/paper/p5_live4.log`, 대시보드 :8770. env=`TICK_ENGINE_ENABLED=1`(SHADOW off=live).
- **무엇**: 라이브 WS 틱 → 미시구조 features(velocity/burst_z/ofi/aggr_flow/overshoot/spread) → 양방향 신호(burst_rider/flow_pressure/micro_reversion) → 실시간 진입/엑싯(모멘텀=ATR-trail, reversion=fast-scalp). 바=인디케이터 base, 틱=결정. 틱-엔진 owns **{capital}** only(OKX 데모 틱 희박 ~0.03/s+OKX open close 오라우팅 → OKX/Alpaca=바). `core/ticks/` + `_production_tick_engine.py`. SSOT=`.claude/plans/p5_tick_decision_engine_2026-06-03.md`.
- **라이브 검증(✅ 풀 라운드트립+수익)**: shadow=False, 실주문 다수. **GOLD micro_reversion: open→confirm ACCEPTED dealId→scalp_target→`close_position dealId=`→ +0.51R** (deal_id 캡처+close end-to-end 입증). Capital US30 숏(양방향) 셰도우. tick 에러 0.
- **커밋체인**: `2854899`(볼트 3축 세컨브레인=텔레메트리 2632 아카이브+ADR alias+MOC) · `6627f1f`(P5 빌드, 적대리뷰 PASS) · `23722f4`(**clock 버그**=epoch초ms오인+monotonic혼선, uptime23.5일이라 전부 stale→수정 후 신호발화 + eval 텔레메트리 30s) · `c1c72a1`(**Capital deal_id 캡처**=open PENDING→confirm 폴링 ACCEPTED까지→affectedDeals[0].dealId) · `8775734`(**틱-엔진 = {capital} ONLY**: OKX 데모 틱 희박+OKX open이 close venue 오라우팅→{capital,okx}→{capital}, OKX/Alpaca는 바).
- **✅ deal_id 완전 해결**: 캡처(`c1c72a1` confirm폴링 PENDING→ACCEPTED) → persist(`cd7f6a0` **positions.deal_id 컬럼**+idempotent ALTER 마이그+open persist) → **재기동 생존**(하이드레이션 + legacy=fill order_id fallback, 적대리뷰 PASS) → un-addressable 고아 **reconcile**(`657e193` no-deal_id→CloseOrphan, error-loop 차단). 라이브 검증: 새 Capital 진입·**deal_id 청산** 정상(scalp +0.51R 등), 하드에러 0, Traceback 0. US100 등 legacy 고아는 엑싯 시 자동 reconcile.
- **✅ P6 (대시보드서 Jin 지적)**: **②a 레짐 컨텍스트**(`f0a8287` ATR-정규화 임계 → FX/지수 trend 적정감지, crypto byte-identical + asset_class별 MarketView additive ema/trend_eff) · **알파카 청산 통째 누락 fix + no-overnight**(`dc80107`: `_real_close_fill`에 **알파카 브랜치가 0개였음** → 진입만 되고 청산 안 됨 → `real_alpaca_close_fill` 주식 SELL 신설·배선(alpaca_adapter 전체 close체인 관통); `session_forced_exit` **stale-overnight 트리거**=마감 지나 보유 시 다음 in-session 오픈에 flatten). 둘 다 적대리뷰 PASS, 스위트 **1825**. 재기동(PID2115)로 배포 — SPCE 2개는 **13:30 UTC RTH 오픈에 자동 flatten** 예정.
- **남은(로드맵)**: ① 임계 캘리브(θ_burst1.5/θ_ofi0.2/θ_revert1.5 → 신호별 승률·EV, posterior 쌓이면) ② **②b 멀티소스 백데이터**(Yahoo/FRED fallback — ②a 후속, Capital 미래바 stale도) ③ Alpaca 틱 Phase2 + OKX tick `trade.venue` ④ 신호 다양화 ⑤ 모바일 대시보드 ⑥ 비전: capital waterfall·self-evolve·AI conductor. follow-up: `_production_indicators.py` 614 LOC 분리. 교훈=`feedback_realtime_price_first_principle`·`project_vault_3axis_secondbrain`·`project_exit_decision_vs_close_execution`.

