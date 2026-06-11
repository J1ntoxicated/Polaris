---
type: component
status: active
phase: P5
date_created: 2026-06-11
tags: [component, ops, watchdog, restart, digest, observability, polaris]
related: [[dashboard]], [[harness-collab-protocol]]
---

# Ops automation — `tools/ops/` (watchdog · daily restart · daily digest)

DEMO/PAPER 봇 운영 자동화. 트레이딩 거동·사이징·게이트 무접촉 — 쓰기 표면은
pidfile·sentinel·`data/paper/ops/*`·vault daily digest·log.md 1줄뿐.

## 구성 (SSOT = `tools/ops/ops_config.py`)
- `botctl` — start/stop 유일 구현. start: `start.lock`(mkdir 원자, stale=10분+owner 사망 시만 해제)
  → sentinel 재확인 → 고아 adopt(시그널 0) → spawn(cwd=PROJECT, `.env` preflight, TICK_ENGINE_ENABLED=1)
  → 20s 생존+로그 성장 검증 → 중복 인스턴스는 알림만. stop: sentinel 먼저(--manual) →
  ps command strict-match → SIGTERM 정확 1회 → 60s 대기 → 미종료=알림 후 포기(강제 킬 영구 금지,
  테스트 봉인 `tests/ops/test_no_kill_patterns.py`).
- strict matcher: argv[0]에 python(macOS venv는 ps에 framework `.../MacOS/Python`로 표시 — 실측)
  + 정확한 `-m polaris.scripts.ignite_p1` 토큰쌍 + `--paper`. ruff/grep/에디터류 오살 체인 차단.
- `watchdog` (launchd 5분 폴링, KeepAlive 배제): sentinel→무동작(24h 리마인더 1회) /
  생존→헬스(wedge·STALL·db-lock·WS flap/포기·WAL>512MB — 전부 알림만, 봇 차단·축소 없음) /
  사망→adopt 또는 재기동. 단명(<300s) 사망 3회/1h → `bot_flapping` + 재시도 간격 30분 완화(정지 아님).
- `daily_restart` (07:30 로컬 Sydney ≈ 21:30 UTC): lock → SIGTERM graceful → 봇 다운 구간에서만
  로그 로테이션(14개 보존) → start → wal before/after 1줄. stop 타임아웃 = 전체 중단(중복 기동 0).
  DB 비접촉(WAL 크기는 os.stat). WAL creep ~18MB/h 회수 수단 = 재기동 자체.
- `daily_digest` (10:10 로컬 ≈ 00:10 UTC): 전일 UTC 일자, ro-URI+명시 close, SELECT만.
  출력 `vault/40_ops/digests/daily-auto/YYYY-MM-DD.md` ≤60줄 숫자 테이블(해석 0) + log.md 1줄(멱등).
  렌더 전문을 거부 키워드 런타임 sweep — strategy_id 등 DB 유입 문자열까지 redact.
- `alerting`: `ops_alerts.log` append(항상 진실) + osascript 배너(키별 30분 스로틀, 실패 무시).

## MANUAL_STOP sentinel 규약
- `data/paper/MANUAL_STOP` — `scripts/stop_bot.sh`가 SIGTERM **이전** 생성(모든 실패 경로 포함).
- 효과: watchdog 기동 0 · daily restart 0. 해제는 `scripts/start_bot.sh`(성공 후)만.
- sentinel 없이 수동 kill → ≤5분 내 부활 = 의도된 기본값(연속 운영).

## launchd (설치: `scripts/install_ops_automation.sh`, 멱등)
- `com.polaris.watchdog`(300s) · `com.polaris.daily.restart`(07:30) · `com.polaris.daily.digest`(10:10).
- 유령 plist 2개(`com.polaris.paper.realtime`·`com.polaris.paper.daily`, src.paper.* 미존재) 백업 후 제거.
- `com.polaris.dashboard` 불가침(스크립트 비참조, 테스트 봉인). 07:30 전후 재실행 회피.
- Sydney DST(10–4월): 트리거만 1h 표류, 다이제스트 내용은 UTC epoch 계산이라 불변.

## 검증
`tests/ops/` 92 tests (kill-패턴 봉인·stop/start 안전성·decide 전수 2^4·증분 스캔·스로틀·
로테이션·골든 다이제스트·키워드 redact·plist 계약·ruff+mypy --strict 게이트).
