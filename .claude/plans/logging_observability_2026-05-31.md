# Logging Observability Plan — 2026-05-31 (Jin: 전 포인트 로그 = 검증 surface)

감사 출처: logging-observability-audit workflow (wv1g7ob2l, 4-plane read-only). 전체 raw=transcript. behavior-0(로그/관측만 추가, 거동 0). 구현=P1 커밋 후(Python 파이프라인 충돌). 불변: DEMO/PAPER, AGGRESSIVE, 거부키워드 0, 자격증명 미로깅(clordid/ordId/dealRef만).

## Jin 의도
모든 액티비티 포인트에서 로그 작성 + 대시보드 surface → 코드 재확인 대신 **로그로 거동 검증**.

## 커버리지 요약 (현)
- **모범**: sizing T4 1줄 전단계(engine.py:488) · OKX order POST/RESP(okx/adapter.py:443/457) · learner/cell update(base.py:133, update.py:60) · rotation FIRED/no-fire.
- **암흑(로그 0)**: 신호생성/인디케이터 · regime 후보/synthesis/confidence(flip 확정만 INFO) · 성공 청산 사유/pnl(_production_close.py) · exit FSM 전이 · fill 수신 · persist_universe 전이 · quote_ticks(완전 공백) · Capital place_working_order.
- **DB-only(stdout 안 뜸)**: 게이트 G1-G8 reason(gate_events DB) · exit EXIT_NOW.

## 🔴 WAVE 0 — 관측 인프라 (✅ DONE this session)
`server.py _resolve_bot_log` glob 버그: `collect_*`/`polaris_runtime*`만 찾아 죽은 stale 로그(348MB) tail, 라이브 봇은 `production_overhaul_*.log`에 씀 → 대시보드 로그 패인 죽어있었음. **수정 커밋**: glob `*.log`(noise 제외)+mtime 최신. (대시보드 재시작 시 활성.)

## 우선순위 갭 (P0/P1/P2 — file:line + 추가 로그) — 구현 P1 후
**WAVE 1 (P0, Jin 직격 — exit/close 무관측)**:
- `_production_recalc_exit.py:223-226` evaluate_exit의 `close_reason`(exit_engine.py:233/241/258)를 close path에 전파(시그니처 추가, behavior-0) → 성공 청산 INFO(reason/pnl_r/pnl_usd/exit_price/held/venue/ticker) + FSM 전이 DEBUG.
- `_production_close.py:431/464` 성공 청산 INFO + fill 필드 + close fan-out 요약(ok=N/6).
- `_production_recalc.py:355/434/118` G6/G7/session EXIT_NOW reason.
- `_production_pipeline.py:471`+성공경로 오픈/fill INFO.
- `discovery.py:299/377` persist_universe active 0↔1 전이 집계 + off_venue deactivate rowcount.
**WAVE 2 (P1, decision 가시성)**: 신호 emit/no-emit/skip(_production_tick.py:367/401/415/429) · regime 후보+tilt+confidence(_production_layers.py:352/430) + stage 태그 `[L6/regime]` 단일화 · 게이트 reason 필드(gate_orchestrator.py:221) · posterior NIG verdict(_production_close_effects.py:241) · 세션 전이(Capital/Alpaca).
**WAVE 3 (P2)**: per-instrument bar fetch 연속실패 WARNING · meta-label/G8 reflector · cell/get_mult source DEBUG · Capital place RESP.

## 구조화 로깅 표준
포맷: 기존 SSOT(logging_config.py:40 `ts.ms Z [LEVEL] name:lineno`) 유지 + 본문 key=value 표준화(전면 JSON 전환 지양=대시보드 재작성/risk). 컨벤션:
`[stage] venue=<v> ticker=<t> decision=<d> reason=<r> <k=v...>` · trade_id= correlation key(6-effect grep). T4 라인=골드 스탠다드.
레벨 규율: INFO=결정/거동(신호emit·청산·오픈·flip·swap·rotation·posterior verdict) · DEBUG=상세(no-emit값·FSM전이·regime후보·skip·cell read) · WARNING=degraded(fetch실패·session401) — **정상 AI필터 KILL·정상 학습 BLOCK은 WARNING 금지**(triple_block.py:76 WARNING→INFO 교정) · ERROR=내부 fault.
surface: 신규 INFO/WARN가 root logger→stdout+FileHandler→`_tail_botlog` 자동 노출. board.js:409 classifyLog substring(open/close/fill/tick) 정합되게 신규 라인에 키워드 포함.

## 대시보드 surface 추가 정합
봇 launch 로그 네이밍 정합 권장: (a) 봇이 기본 `polaris_runtime.log` 사용 OR (b) glob `*.log`(✅ 채택). 죽은 stale 로그 가드(미래 mtime 함정)는 운영상 라이브 로그가 mtime 최신이라 정상 동작.
