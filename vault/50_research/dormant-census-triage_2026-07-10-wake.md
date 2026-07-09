---
type: research
status: active
date_created: 2026-07-10
tags: [audit, dormant, triage, wake, buildgroup]
related: ["[[dormant-census-triage_2026-07-10]]"]
---

# WAKE 빌드그룹 (3)

```json
[
  {
    "branch": "wake/conviction-stacking-pyramiding",
    "goal": "승자 피라미딩 활성 — position_conviction_layers writer + 독립 add-on 사이징으로 이긴 포지션 최대 3층(1.0/0.7/0.5) 증축, 비대칭 상방(feedback_loss_profit_asymmetry, 검증 엣지=추세). 모듈(conviction.py can_stack/compute_stack) 이미 구현·테스트, 라이브 writer만 부재.",
    "spec": "1) /debate 선행(사이징 변경 mandate) — add-on notional 산식 확정. 2) writer: position_monitor(G6)에서 open pnl_r>=CONVICTION_MIN_PNL_R(0.5) & count_layers<3 & group_cap(2.2x) → can_stack_conviction 판정 → INSERT position_conviction_layers + compute_stack_size_mult(n)로 독립 add-on 주문. 3) 9-stack 봉쇄: add-on=base와 별개 주문, T4 continuous scalar 체인에 mult 누적 금지; 층 mult는 add-on notional에만. 4) hard caps(per-symbol/cluster/track)+group_cap을 add-on 포함 재검. 5) shadow-first: 첫 슬라이스 판정만 gate_shadow_events 로그, 실발주 전 라이브 분포 관측.",
    "tests": "can_stack gate(기존)+writer integration(pnl_r 임계 통과→layer INSERT→add-on intent)·cap-respect(group_cap 초과→can_stack=False)·9-stack 회귀(add-on이 T4 체인 미진입 assert)·max 3층 clamp.",
    "risk": "사이징 변경=/debate 의무 · per-symbol 노출 증가(캡 제한) · 층 mult<1이 base에 오적용 시 9-stack 위반(add-on 전용 assert 방지) · 승자 피라미딩은 slow-trend 엣지에 정합."
  },
  {
    "branch": "wake/counterfactual-kill-value-reader",
    "goal": "07-02 root-cause #4(피드백 레그 사망) 부활 — 기존 gate_kill_counterfactuals 15.7k행을 (gate,cohort)별 KILL forward-R로 집계, 게이트가 이긴 신호 죽이는지 실측 → loosening 증거 표면화(flow_not_block). 데이터 실존(자기갱신 fwd_r_24h).",
    "spec": "1) reader(SELECT-only, knowledge_loop.py의 read-only·never-auto·debate_gated 패턴): 해결된 row를 (gate_id,regime/cohort)별 mean_killed_fwd_r vs mean_passed_fwd_r 집계. 2) 산출 KillValueHint{gate,cohort,n,separation,auto_apply:false}. 3) 소비: 대시 'gate value' 패널 + /debate 증거; KILL mean_fwd_r>0(이긴 신호 죽임)이면 임계 완화 후보 surface(자동적용 금지). 4) 층화 검증 선행(07-02 미해결): cohort n>=floor, 표본편향 필터.",
    "tests": "집계 정확성(합성 counterfactual→기대 separation)·never-write assert(SELECT-only)·auto_apply=False/debate_gated=True assert·empty-degrade({present:False}).",
    "risk": "자동 적용 절대 금지(shadow/debate만) · 표본 편향(층화+floor 완화) · 완화 방향은 aggressive 정합(막던 신호 흐르게)이라 안전 · 라이브 임계 미변경(증거만)."
  },
  {
    "branch": "wake/replay-readmodel-schedule",
    "goal": "benchmark_results/replay_runs(현 0행) 채워 대시 confidence 패널 가동. run_replay(구현완료) 스케줄링만. display-only 확정(confidence.py replay_block 'NEVER touches a trading decision').",
    "spec": "1) launchd plist 추가(기존 daily.digest/probe.reranker 패턴) — nightly off-peak `python3 -m polaris.scripts.run_replay --db data/polaris_live.sqlite --interval 1H --instruments <universe top-N 조회> --trials 7`. 2) 유니버스 인자=활성 watchlist top-N(하드코딩 금지, universe 테이블 조회). 3) 라이브 봇 무접촉(read-only sandbox seed + display read-model write만). 4) 선택: replay_runs 최근 N run retention.",
    "tests": "run_replay 스모크(1 interval→replay_runs 1행+benchmark_results tier행)·confidence_summary replay_block present:True(기존 test)·plist lint·universe 하드코딩 부재 grep.",
    "risk": "낮음(display-only, 게이트 미접촉) · replay CPU→nightly off-peak(feedback_single_heavy_workflow_cpu_freeze: 동시 1개) · 유니버스 하드코딩 금지."
  }
]
```

주: learner_prune.py(WAKE-low)는 빌드그룹 미포함 — daily 유지보수 plist에 1줄 추가(dead-strategy sweep), 별도 설계 불요.
