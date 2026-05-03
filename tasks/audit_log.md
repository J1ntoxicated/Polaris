# Harness Audit Log — Event-Triggered

Harness가 **이벤트 누적 기반**으로 실행하는 감사의 상태 추적.

**갱신 주체**: Harness 세션만. 매 주기마다 카운터 체크 + due된 감사 실행 후 즉시 업데이트.

## 감사 실행 상태 (이벤트 카운터 — 변경 볼륨 기반)

**측정 원칙**: 커밋 수 ≠ 신호. 실제 **코드/데이터 변화 볼륨**이 신호.

| 감사명 | 트리거 | 기준점 스냅샷 | 현재 값 | 진행도 |
|-------|--------|---------------|--------|-------|
| 하드코딩 감사 | invasion/* 500 lines 변경 | HEAD `90eaafc` (2026-04-12 18:58 리셋) | 2003 lines 기측정 → 오늘 감사 6회 돌림, 리셋 후 0 | ✅ 오늘분 완료 |
| 파라미터 적정성 | 50 trades delta | trades 739 (2026-04-12 18:58 리셋) | 0/50 | 대기 |
| 로그 커버리지 | invasion/* 5 파일 수정 | HEAD `90eaafc` (2026-04-12 18:58 리셋) | 44 files 기측정 → 오늘 감사 완료, 리셋 후 0 | ✅ 오늘분 완료 |
| 에러 패턴 추세 | 20 errors 누적 post-restart | 봇 재시작 18:42 기준 | 0/20 (post 18:42) | 대기 |
| 전략 성과 분포 | 50 trades OR 단일 전략 >60% | trades 739 + breakout_donchian 지속 편중 | Ops 관할로 이관 (거래 분석 1순위) | Ops 상시 |
| Evolver 작동 검증 | 100 trades OR Elo entropy 감소 | trades 739 (2026-04-12 18:58 리셋) | 0/100 | 대기 |
| 대시보드 규격 | dashboard/*.py commit | HEAD `90eaafc` (2026-04-12 18:58 리셋) | 0 commits | 대기 |
| IPC 규약 정합성 | tasks/*_to_*.md 50 lines OR daily | 2975 lines (2026-04-12 18:58 리셋) | daily 조건 — 내일 2026-04-13 실행 | 내일 due |
| `.claude/` 정합성 | .claude/* commit OR daily | 2026-04-12 18:58 리셋 | Legacy 삭제분 미커밋 24개 — 이번 주기 정리 중 | 진행 중 |
| **모듈 구조 감사** | invasion/* 3000 lines 변경 OR 새 서브모듈 생성 | HEAD `0f9100e` (2026-04-12 19:10) | 0/3000 | 대기 |

## 측정 커맨드 cheat-sheet (Harness가 매 루프에 실행)

```bash
# 하드코딩 감사 + 모듈 구조 감사 공용 트리거 체크
BASE=0f9100e  # 마지막 실행 기준점 (모듈 구조 감사는 3000 임계)
git log --numstat ${BASE}..HEAD -- invasion/ | awk 'NF==3 {sum+=$1+$2} END {print sum}'
# 모듈 구조 감사: 신규 서브모듈 감지
find invasion -maxdepth 1 -type d -newer tasks/audit_log.md -not -name __pycache__

# 파라미터/전략/Evolver 트리거 체크
sqlite3 data/invasion.sqlite "SELECT COUNT(*) FROM trades WHERE entry_ts > (SELECT IFNULL(MAX(entry_ts), 0) FROM trades WHERE id <= <base_trade_id>)"

# 에러 패턴 트리거 체크 (post-restart 이후)
RESTART_TS="2026-04-12 16:30"
awk -v ts="$RESTART_TS" '$0 ~ ts,0' data/invasion.log | grep -cE "ERROR|Traceback"

# 대시보드 규격 트리거 체크
git log --name-only ${BASE}..HEAD | grep -E "invasion/dashboard/" | head -5

# 로그 커버리지 트리거 체크  
git diff --name-only ${BASE}..HEAD -- 'invasion/*.py' | wc -l

# IPC 볼륨 체크
git log -p ${BASE}..HEAD -- tasks/ | grep -cE "^[+-]"
```

## 사용법 (Harness 세션이 매 주기마다)

1. 이 파일 읽음 + 현재 이벤트 값 수집:
   - `git rev-list --count {기준점}..HEAD` → 커밋 수
   - `sqlite3 data/invasion.sqlite "SELECT COUNT(*) FROM trades"` → 트레이드 수
   - `grep -cE "ERROR|Traceback" data/invasion.log` (post-restart 이후만)
2. 진행도 계산 + 트리거 조건 체크
3. 트리거 발동 → 해당 감사 실행 → 결과 IPC 라우팅
4. 기준점 스냅샷 갱신 (현재 값을 새 기준점으로)

## DATA 감사 (2026-04-12) — 진행 현황

- [x] **P0-1 fees 기록** — ✅ `bb75de9` (pipeline.py + dead-letter)
- [x] **P0-2 tick_snapshots retention** — ✅ `bb75de9`
- [x] **P0-3 trade_count sync** — ✅ `4913654`
- [~] **P0-4 FK mismatch** — Dev 조사 결과 코드 JOIN 0건, 영향 없음. Harness 원안 재검토 필요
- [?] **P0-5 market_snapshots** — Jin 승인 대기: DROP vs retention 스페셜 케이스
- [ ] Live exchange fee 연동 (okx/capital/alpaca) — 별도 P 필요

## UI 감사 (2026-04-12)
- [ ] chart_window.py assert 추가
- [ ] Stochastic RSI 중복 → 다른 지표 교체
- [ ] pipeline_flow closed trades 재활성
- [ ] intel.py 주석 29 vs 30 불일치

## Dead code (2026-04-12) — ✅ 완료
- ✅ `92a4426` — 6 files + 4 methods, -1108 lines

## 감사 전체 실행 완료 보고 (2026-04-12)

Jin 요청: "감사 다 하라했는데?" → 4개 전 감사 완료 + 처리 진행 현황:

| 감사 | 실행 | 에이전트 | 발견 | 조치 |
|------|------|---------|------|------|
| 1. 하드코딩 | 16:42 | codebase-guardian | 50건 검토, MUST 11 | ✅ Dev 5/5 완료 (45 keys 이관) |
| 2. 파라미터 적정성 | 16:42 | trade-strategist | 9 problem + 11 suspicious | Ops 증거기반 보류 / 대신 trade analysis에서 blacklist 적용 |
| 3. **전체 거래 분석** | 17:45 | trade-strategist | TOP 5 action (UTC 01/16, 티커 블랙리스트 등) | ✅ Ops 3/5 적용 (UTC/blacklist) + 2 Dev 영역 |
| 4. 데이터 인프라 | 17:45 | codebase-guardian | 10 action (P0/P1) | ✅ Dev P0-1/2/3 완료 + P0-5 승인 |
| 5. UI | 17:45 | ui-ux-director | TOP 5 긴급 + TOP 5 개선 | ✅ Dev P1 4건 완료 (`5b7a946`) |
| 6. Dead code | 17:45 | codebase-guardian | TOP 30 (HIGH 10) | ✅ Dev 삭제 완료 (-1108 lines, `92a4426`) |
| 7. 코드 분할 전수 | 18:13 | codebase-guardian | 진행 중 | — |

**결론**: 오늘 6개 감사 + 1개 진행 중. Dev 10+ 커밋으로 중요 발견 대부분 이미 조치 완료. 남은 것: 코드 분할 계획, Live fee 연동, Liveness Gate.

## 현재 추적 중인 findings (감사로 제기된 open items)

### 하드코딩 감사 → Dev MSG-008 (16:45 발송)
- [x] **#1 defense.py** — ✅ 완료 (commit `bb814de` @ 16:42). 5 keys, behavior 0 change
- [x] **#2 exit.py `_GROUP_PROFILES`** — ✅ 완료 (commit `389c8de` @ 16:52). 18 keys (6 group × 3 mult), 별칭 정규화, 24 data points 재현 테스트 통과
- [x] **#3 ai_controller.py** — ✅ 완료 (commit `fb4e0c5` @ 17:10). 8 sites → 11 keys
- [x] **#4 regime.py VIX/DXY** — ✅ 완료 (commit `01f1b74` @ 17:25). 8 keys → `regime_presets.json/scoring_thresholds`, 코드 fallback 유지
- [x] **#5 entry.py repeat_entry** — ✅ 완료 (commit `695e942` @ 17:25). 3 keys

**시리즈 종결**: 5 commits, 45 keys migrated, 누적 behavior change 0. Governor 튜닝 공간 +45.

### 파라미터 적정성 감사 → Ops MSG-004 (16:45 발송)
- [ ] `max_hold_sec` vs `flat_kill_sec` 모순 정리
- [ ] `long_bias_mult` 0.5 → 0.3 이하 축소
- [ ] `trail_activate` 0.3 → 0.2
- [ ] `stagnant_minutes` 90 → 45-60
- [ ] `dpm_kill_threshold` 35 → 42-45
- [ ] UTC01/03/16 long 시간대 블랙리스트
- [ ] signal threshold 3중 중복 SSOT 통합 (Dev 에스컬레이션 예정)
- [ ] breakout_donchian 70% 독점 해소 (Dev + Ops 협업 필요)
- [ ] UP 종목 조건부 블랙리스트 (Dev 영역)

## Event-Driven 철학 메모

**왜 이벤트 기반인가**:
- 시간은 proxy. 실제 중요한 건 "의미 있는 변화 누적"
- 주말 48h 동안 시간 기반 감사 여러 번 돌아봤자 변화 없으면 낭비
- 활발한 활동 중엔 1시간에도 여러 번 감사할 가치

**fallback 안전망 유지 이유**:
- Monitor/카운터 자체 고장 대비
- 시스템 정체 감지 (7일 아무 이벤트 없으면 봇 정지 신호)
- 감사 catalog 자체 진화 기회 보장

## 감사 카탈로그 진화

새 감사 추가 / 트리거 조정은 loop.md의 "Harness 주기 감사 프레임워크" 섹션에 먼저 등재 후 이 파일에 행 추가.
