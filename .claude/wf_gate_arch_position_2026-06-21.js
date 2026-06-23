export const meta = {
  name: 'gate-arch-position-analysis',
  description: 'Jin: 3-게이트(G3/G4/G7) 구조+현설정 · 봇 아키텍처 전체 · 재시작 stale 포지션 자동정리(reconcile) 유무 → 안전 재기동 + 워치리스트',
  phases: [
    { title: 'Read' },
    { title: 'Synthesize' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/축소/차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·90d gate·monthly review·posture standard·fractional Kelly too aggressive. 불변: 9-stack 봉쇄·hard-MAX·DEMO. 봇 LLM=GPT(Anthropic 호출 금지). 코드 루트=cwd(/Users/jinyoon/Projects/Polaris) 상대경로 Read. /Volumes 경로 금지(미마운트). 실제 file:line 근거 필수, 추측 금지. read-only(수정 절대 금지). 출력=raw, 산문 최소.]`

const CONTEXT = `[상황] 봇 ~9일 정지(6/11→6/21, 현재 정지·MANUAL_STOP sentinel) 후 재기동 예정. DB(data/polaris_live.sqlite) positions status 집계: open=17(6/11 stale), reconciled=96, closed=476 — 즉 'reconciled' 상태/메커니즘이 이미 존재. 최근 W3 커밋 "AI-free cutover: deterministic G3/G4/G7 primary, in-loop GPT calls=0" + S1-S4 센티넬 사이드카(observe-only live-audit) + 테이블 entry_admission_shadow/cell_matrix_shadow_context/position_live_recalc_state. env TICK_ENGINE_ENABLED=1, P5 틱엔진. 부팅 경로 = tools.ops.botctl start → ignite_p1 → run_production_paper_loop.
[Jin 4대 요청] (1) 3-게이트(G3/G4/G7) 구조·현재 설정 정밀 분석, (2) 봇 아키텍처 전체 파악, (3) 재기동 시 stale 17 오픈 포지션을 봇이 '알아서 정리'(reconcile/close)하나 — 메커니즘 위치+자동성 판정, 없으면 최소 안전 변경, (4) 봐야할 워치리스트.`

const RS = {
  type:'object', additionalProperties:false,
  required:['area','current_behavior','config_settings','gap_or_risk','watch_items','code_refs'],
  properties:{
    area:{type:'string'},
    current_behavior:{type:'string', description:'실제 동작(코드 근거)'},
    config_settings:{type:'string', description:'현재 설정값/임계값/env flag/기본값 있는 그대로'},
    gap_or_risk:{type:'string', description:'갭/리스크/미흡점. 없으면 NONE'},
    watch_items:{type:'array', items:{type:'string'}, description:'재기동 후 봐야할 항목'},
    code_refs:{type:'array', items:{type:'string'}, description:'file:line 근거'},
  },
}

const READERS = [
  {area:'3-gate G3/G4/G7 구조·현설정', q:'G3(signal validator)/G4(pre-entry watcher)/G7(adaptive exit)의 현재 구조를 정독. W3 cutover 후 deterministic primary가 맞나? GPT가 in-loop에서 실제 호출되나(0이어야) 아니면 shadow/observe-only인가? 각 게이트의 임계값·파라미터·기본값·env flag, 그리고 어디서 설정되나(config 파일/env/하드코드). polaris gate 구현체 + gating-pipeline 경로.'},
  {area:'봇 부팅·아키텍처 전체', q:'부팅 시퀀스 tools.ops.botctl start → ignite_p1 → run_production_paper_loop 정독. bar pipeline + live recalc(L6) + P5 tick engine(TICK_ENGINE_ENABLED) 배선. 전체 데이터 흐름(universe→bars→signal→gates→size→order→monitor→exit→reflect)과 주요 모듈/엔트리포인트. 어떤 env/플래그가 거동을 바꾸나.'},
  {area:'포지션 reconcile + stale 자동정리', q:'★핵심★ positions.status="reconciled"(현 96개)는 누가/언제/어떤 조건으로 세팅하나? 재기동(boot) 시 DB의 open 포지션을 venue 실제 포지션과 reconcile하는 경로가 있나? 9일 stale open을 자동 close/reconcile/orphan-adopt하는 로직이 있나 — 있으면 정확히 어디·조건·자동성. reconciling-portfolio + PositionLedger + OrderStateNormalizer + boot 시 position 로드/복원. 자동으로 정리되는지 여부가 결론.'},
  {area:'shadow/sentinel 시스템', q:'entry_admission_shadow, cell_matrix_shadow_context, position_live_recalc_state 등 shadow 테이블을 채우는 로직과 S1-S4 센티넬 사이드카(observe-only live-audit)의 구조. GPT/AI 호출이 in-loop 경로에 남아있나(0 검증) 아니면 완전 사이드카인가. 사이징/차단/halt에 영향 주나(영향 없어야).'},
  {area:'risk/sizing/cell/learner/regime', q:'T4 sizing(base×scalar×tier×cell→hard-MAX min), hard caps + cluster caps, cell_matrix(p0/parent2/parent3/shadow), learner network, regime L1/L2/L3(regime_state, classify_regime, regime_flip). 현재 동작·설정값. 아키텍처 완결성 관점에서 빠진 연결이나 stub.'},
]

phase('Read')
const mapRaw = await parallel(READERS.map(function(r){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 영역="${r.area}" 단일 정밀 매핑(read-only). ${r.q}\nschema 반환. file:line 근거 필수, 추측 금지.`,
      {label:`read:${r.area.slice(0,18)}`, phase:'Read', schema:RS, agentType:'general-purpose'})
  }
}))
const maps = mapRaw.filter(Boolean)

phase('Synthesize')
const SS = {
  type:'object', additionalProperties:false,
  required:['gate_config_analysis','architecture_summary','position_cleanup_verdict','position_cleanup_change','safe_restart_plan','watch_list','needs_debate'],
  properties:{
    gate_config_analysis:{type:'string', description:'3-게이트(G3/G4/G7) 현재 설정 분석 — deterministic primary/shadow/임계값/env, 잘못/위험 설정 포함'},
    architecture_summary:{type:'string', description:'봇 아키텍처 전체 요약(부팅→루프→파이프라인→틱엔진)'},
    position_cleanup_verdict:{type:'string', description:'★재기동 시 stale 17 오픈을 봇이 자동 reconcile/정리 하나? YES/NO + 근거(file:line) + 정확한 메커니즘 위치'},
    position_cleanup_change:{type:'string', description:'자동정리 안 되면 최소 안전 변경점/운영절차(코드 위치 포함). 자동이면 "none — 재기동만으로 정리됨"'},
    safe_restart_plan:{type:'array', items:{type:'string'}, description:'안전 재기동 순서(stale 포지션 안전 처리 포함)'},
    watch_list:{type:'array', items:{type:'string'}, description:'재기동 후 봐야할 부분 리스트(우선순위)'},
    needs_debate:{type:'array', items:{type:'string'}, description:'/debate 항목(트레이딩 파라미터/아키텍처)'},
  },
}
const synthesis = await agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 아래 영역 리드들을 종합해 최종 분석을 내라.\n[리드]\n${JSON.stringify(maps)}\n
요구: (1) 3-게이트 현설정 분석(deterministic primary/shadow/임계값/env, 위험설정). (2) 봇 아키텍처 요약. (3) ★재기동 시 stale 17 오픈 자동 reconcile/정리 여부 판정(YES/NO + file:line + 메커니즘 위치). (4) 자동 아니면 최소 안전 변경점/운영절차. (5) 안전 재기동 순서. (6) 봐야할 워치리스트(우선순위). (7) /debate 항목. schema 반환.`,
  {label:'synthesis', phase:'Synthesize', schema:SS, agentType:'general-purpose'})

return { maps, synthesis }
