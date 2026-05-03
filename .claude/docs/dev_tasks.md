# Dev 작업 큐 (Unified)

통합 세션 전환 (2026-04-19) 이후 **작업 큐 = `tasks/harness_items.md`** 단일.
이 파일은 북극성 위반 감사 catalog 만 유지. 일반 task 는 harness_items.md 참조.

## 작업 큐 이관

- 신규 작업: `tasks/harness_items.md` append (Harness 직접)
- historical: `tasks/archive/2026-04-20_dev_tasks_historical.md` (04-19 이전 DONE + 당시 PENDING 아카이브)

## 🌟 북극성 위반 감사 catalog (Jin 04-14)

clean epoch (04-11) 이후 누적. 발견 시 `dev-coder` inline dispatch 로 순차 fix.

| # | 파일:라인 | 위반 패턴 | 설명 | 심각도 |
|---|-----------|----------|------|--------|
| NS-1 | `config.py:223-226` | profit_cap 하드캡 | major 3.0 / large 4.0 / mid 5.0 / meme 6.0 — 상승 잘라냄 | HIGH |
| NS-2 | `config.py:140-141` | long/all_blocked_hours_utc | 시간대별 방향 차단 인프라 (현재 빈 리스트지만 구조 방어적) | LOW |
| NS-3 | `config.py:213` | short_ls_max=2.0 | crowd short 일 때 short 차단 — contrarian 위반 | MED |
| NS-4 | `exit.py:331` | restart STALE/TIME 즉시 exit | restart 시 수익 포지션도 강제 종료 | MED |
| NS-5 | `safety_check.py:156` | SAFETY HALT | equity drop 시 전체 거래 중단 — 위기=기회 위반 | MED |
| NS-6 | `paper.py:486-493` | hold-time trail tightening | 보유 시간 길수록 trailing 좁힘 — winners 조기 절단 | MED |
| NS-7 | `dpm.py:181` | HOLD tighten_pending_confirm | DPM 이 profitable 포지션 trail tighten | LOW |
| NS-8 | `gate_matrix.py:252` | regime stale freshness bar | fragile regime 에서 가격 freshness 강화 — entry 억제 | MED |
| NS-9 | `engine.py:608-623` | low_vol_long/short_block | 저변동성 방향 차단 — 기회 축적기 차단 | LOW |
| NS-10 | `param_registry.py:601` | KILL→TIGHTEN protection | profitable kill→tighten (조사 필요) | LOW |
| NS-11 | `ops/ticker_learner.py:104` ~~REDUCE 0.3-0.6x~~ | amplify-only | ✅ **DONE** commit `93877b4f` (2026-04-20) |

### 처리 원칙
- **HIGH**: 즉시 `dev-coder` dispatch (profit_cap → trailing 전환)
- **MED**: 코드 경로 조사 후 제거 or 반전 (contrarian 방향)
- **LOW**: 현재 비활성 or 영향 미미 — 마지막 batch
- 각 건 수정 시 lessons.md #52/#53/#55 참조

## 참조

- [north_star.md](north_star.md) — 북극성 기준
- [coding_conventions.md](coding_conventions.md) — 코드 규약
- [../../tasks/harness_items.md](../../tasks/harness_items.md) — 신규 작업 큐
- [../../tasks/archive/2026-04-20_dev_tasks_historical.md](../../tasks/archive/2026-04-20_dev_tasks_historical.md) — historical
