---
type: research
status: active
date_created: 2026-07-10
tags: [audit, dormant, triage, delete, buildgroup]
related: ["[[dormant-census-triage_2026-07-10]]"]
---

# DELETE 빌드그룹 (2)

```json
[
  {
    "branch": "delete/table-graveyards-market-events-orders",
    "goal": "reader-0 테이블 2종 제거로 WAL 경합·혼란 제거 — market_events(43k행, regime_state가 라이브 SSOT라 중복, ~3,200 INSERT/day 단일-writer 경합) + orders(0행, writer 영구 부재, fills가 역할 흡수).",
    "spec": "1) 아카이브 선행: `sqlite3 data/polaris_live.sqlite '.dump market_events' | gzip > data/archive/market_events_2026-07-10.sql.gz`(orders는 0행이나 DDL 스냅샷). 2) writer 중립화: regime_flip.py `_append_market_event`(:332-342) INSERT+호출부 제거 — regime_state UPDATE(:175)는 무손상(별도 statement, 라이브 신호 유지). 3) DROP TABLE market_events; DROP TABLE orders; schema_ddl_core.py DDL(:200,:432) 제거. 4) orders reader(dashboard_v0.py:187 dead fallback)는 DELETE-2에서 모듈째 제거 → 정합.",
    "tests": "regime_flip 회귀(regime_state UPDATE·consumers strategy_swap:258/entrance_leans 무영향)·`rg market_events|FROM orders`=0·봇 부팅 스모크(schema init OK)·전체 스위트 green.",
    "risk": "regime 라이브 신호는 regime_state로 무손상(소비자 3종 확인) · market_events reader repo 0 확정 · orders writer 0 · git+dump 이중 보존 · DROP은 아카이브 검증 후."
  },
  {
    "branch": "delete/orphan-module-sweep",
    "goal": "production 배선 전무 orphan 모듈 제거로 '존재!=도달' 혼란 제거(감사 근본원인 #1 동형 패턴 정리).",
    "spec": "각 모듈+테스트 커밋 삭제: 1) dashboard_v0.py(449)+test_dashboard_v0.py — 웹 대시(tools/visualizer)로 대체·웹서버도 미참조. 2) shadow_acceptance.py(417)+test — 목적(G3/G4 cutover 결정)이 ADR-011(6/11 완료)로 소멸, 라이브는 _production_close_effects.py:339 인라인 미러(7/07 터치=mass-rename뿐). 3) normalize.py(169)+test — T11 API, 5주+ orphan(6/01), 라이브 L1 미사용 입증. 4) knowledge_loop.py(107)+calibration.py(273)+tests — 각 `probe_decisions`/`v_probe_outcomes·probe_readings`(DB 미존재) reader, write-side 미빌드로 데이터소스 0.",
    "tests": "삭제 후 전체 스위트 green(orphan이라 import 파손 0)·ignite_p1/visualizer import 스모크·`rg <module>`=0.",
    "risk": "전부 AST 도달 0(census 검증)+dynamic import 없음(grep 확인)+git 보존 · **knowledge_loop/calibration은 probe-engine slice 2 재개 시 재작성 지점 → Jin veto 가능**(현 데이터소스 0이므로 삭제가 기본, 슬라이스2 착수 확정 시 보류)."
  }
]
```

주: REFINE_TIMING stamp 제거(ai_judge.py:648-653 inert payload)는 거동무변 surgical DELETE이나 active judge 프롬프트 vocab 변경은 별건(/debate 게이트) → 빌드그룹 미포함, 표에만 DELETE 판정.
