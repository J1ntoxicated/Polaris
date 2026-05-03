# Dashboard Redesign v2 — Appendix (확장 맥락 / Phase 분해)

`dashboard_redesign_mockup.md` 본문 보조. Cross-exchange 영향 / Harness 판단 지점 / Phase 분해 상세.

## Cross-exchange impact (feedback_okx_only_test)

- Intel `CROSS-EXCHANGE` 섹션 = Jin 03:38 정정 방향 정합 (OKX 중심 + Alpaca/CAP 사전 대비)
- Operations 는 OKX crypto 중심 (live monitoring/leaderboard 대다수 crypto)
- 월요일 Alpaca/CAP 재개 시 UI 변화 없음 — 데이터 흐름만 확대

## Harness 판단 필요 지점 (확장)

1. **pipeline_flow 이관 확정?** (14 rows, 현 ops 의 debug map). Intel 로 옮길 시 ops 36-49 strategy leaderboard 확장 공간 확보.
2. **winners/losers 분리 확정?** 신규 `sections/winners.py` vs 기존 `trade_quality` 에서 발췌 — 후자 코드 중복 위험, 전자 LOC 추가.
3. **cross-exchange 1 행으로 충분?** 또는 3 행 (OKX/CAP/Alpaca 각 1행) 확장?
   - 1행: `OKX 24/7 | CAP closed 14h | Alpaca closed 18h` 압축
   - 3행: 각 exchange 별 상세 (active pairs / ping ms / rate limit remaining)
4. **LOC budget**: ~200 신규/이동. MSG-ENTRY-ZERO-URGENT 선행 후 이 작업 일정?

## 구현 작업 분해 (원문 상세)

1. **north_star_bar**: 변경 없음 (commit e8f89f17 활용)
2. **operations.py** layout 재정의 — sections 호출 재배치 (~80 LOC)
3. **intel.py** layout 재정의 — sections 호출 재배치 (~80 LOC)
4. **sections/pipeline_flow.py**: 이관 (코드 변경 없음, import 만 이동)
5. **sections/provider_chain.py**: 이미 존재, intel 로 재 wiring
6. **sections/cross_exchange.py** (신규, ~60 LOC): OKX 24/7 + Alpaca/CAP 장마감 상태 1행
7. **sections/winners.py** (신규, ~80 LOC): trade_quality 분리 (winners/losers → ops)

예상 LOC: +140 신규 / ~160 이동 / ~40 삭제

## 후속 Phase (본 MSG 완료 후)

- **Phase A**: ops 재배치 (winners/strategy 확장)
- **Phase B**: intel 재배치 (pipeline_flow + provider_chain 이관)
- **Phase C**: cross_exchange.py 신설

각 phase 별 commit + smoke + restart 권장.

## 참조 back → [dashboard_redesign_mockup.md](dashboard_redesign_mockup.md)
