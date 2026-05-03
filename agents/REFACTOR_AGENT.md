# 리팩토링 에이전트 — 마이그레이션 기록 + 가이드

## 역할
코드 구조 개선, 레거시 마이그레이션, 중복 제거를 수행한다. 동작 변경 없는 구조 변경만.

## 마이그레이션 이력

### Phase 1-4 완료 (2026-04-10)
- Phase 1: `okx_pipeline.py`, `market_scanner.py` 삭제
- Phase 2: `shared/` → `data/collectors/`, `core/` → `ops/`, `utils/` 마이그레이션
- Phase 3: 루트 레거시 모듈 7개 삭제
- Phase 4: `shared/` (25파일) + `core/` (8파일) 전체 삭제 — 총 8,233줄 레거시 코드 제거

### 추가 정리
- `exchange/ig/` 삭제 (Capital.com으로 대체)
- `archive/`, `invasion_v6/`, `scripts/`, `tests/` 삭제
- 루트 레거시 파일 9개 삭제
- `tasks/todo.md`, `data/scrum/` 삭제 (메모리 레이어로 이전)
- `ig_` 접두어 → `cap_`/`cfd_` 리네이밍 완료
- `sections/__init__.py` 데드 임포트 제거
- `config/computed.py` 자동 조정 오버라이드 3개 비활성화 (컨트라리안 위반)

## 현재 아키텍처
| 레이어 | 위치 |
|--------|------|
| 거래소 | `exchange/okx/`, `capital/`, `binance/`, `alpaca/` |
| 방어 | `ops/defense.py`, `ops/emergency.py` |
| 데이터 수집 | `data/collectors/` |
| 유틸리티 | `utils/` |
| 설정 | AppConfig (Pydantic) + `live_config.json` |
| 틱 스케줄러 | `ticks/` (19개 활성) |
| 분석 | `analytics/` |

## 잔여 작업
- 대시보드 레거시 5개 파일 삭제 대기 (Jin 승인 필요): `signal.py`, `trading.py`, `intelligence.py`, `system.py`, `log_analysis.py`
- ParamRegistry 미사용 키 감사
- Yahoo 캔들 유럽 시장 시간 확인 + 캐시
- Reconciliation/history_sync 틱 검증
- AdaptiveTuner 모니터링

## 작업 원칙
- 구조 변경 전: `grep -rn "PATTERN" invasion/ --include="*.py" | grep import`
- 변경 후: `python3 -c "import invasion.main"` 검증
- 동작 보존 구조 변경만 허용
- 실수는 `tasks/lessons.md`에 기록
