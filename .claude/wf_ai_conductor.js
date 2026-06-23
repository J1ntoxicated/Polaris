export const meta = {
  name: 'ai-conductor-feasibility',
  description: 'Jin 질문: technical 데이터 스트림이 결정하고 AI는 총괄 지휘 — 구조상 가능한가 + 설계 + 적대검증',
  phases: [
    { title: 'Gate analysis' },
    { title: 'Conductor design' },
    { title: 'Adversarial review' },
  ],
}

const HEADER = `[Polaris context — 반드시 준수]
- DEMO/PAPER 가상자금 봇. real-money 보수 논거 무효. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block).
- 거부 키워드(등장 시 무효, 채택 금지): 12주·90d gate·monthly review·regulatory cap·professional risk·real-money safety·fractional Kelly too aggressive·posture standard·표본부족 risk.
- 불변: 9-stack collapse 봉쇄(사이징 체인 1 mult 누적 금지)·hard-MAX headroom·DEMO only.
- AI 제공자: 봇 LLM=GPT(OpenAI, gpt-5-mini P0/gpt-5.5 P1), 교차검증=Gemini. Anthropic/Claude 호출 금지.
- 코드 경로: /Users/jinyoon/Projects/Polaris. 반드시 실제 파일을 Read 해서 근거를 들 것(추측 금지).
- 출력은 raw 데이터(return value). 장황한 산문 금지, 핵심만.`

const CONTEXT = `[Jin 핵심 질문] "테크니컬하게 데이터 스트림으로 결정 가능하게 만들고, AI는 총괄 지휘하는 느낌 — 구조상 가능한가?"
[실측, 19.69h, 24256 gate_events]
- G3 validator: gpt(P0) 3729콜 in=820k(토큰최대) decision=PASS3339/KILL300/MODIFY90
- G4 pre_entry_watcher: gpt(P0) 3429콜 in=433k decision=PROCEED3071/KILL358
- G1 universe_scanner(focus): gpt(P0) 2850콜+cached853 decision=PASS100%(항상통과,selector)
- G6 position_monitor: gpt_p1(P1비쌈) 316콜 in=195k decision=HOLD100%
- G7 adaptive_exit: gpt_p1 46콜 + python fast-path; decision=HOLD100%; FSM(#26 MFE/MAE/ATR트레일/exit_state)는 이미 technical
- G8 post_trade_reflector: gpt_p1 17콜(학습)
- G2 strategy_signal_gen, G5 entry_sizer = 이미 python(AI 호출 0)
[목표] AI를 per-signal 도장에서 빼고, 결정은 deterministic technical(데이터 스트림/지표/임계/규칙)로 내리고, AI는 상위 "총괄 지휘"(레짐 확정·전략 선택·주기 감독/캘리브·이상감지·학습)로 재배치. surgical-strike(정밀 진입/엑싯, 근거있는 거래) 유지 필수.`

const GATES = [
  {id:'G1', file:'polaris/core/pipeline/agents/universe_scanner.py', note:'focus selector, 항상 PASS, 2850 GPT — selector면 GPT 불필요 의심'},
  {id:'G3', file:'polaris/core/pipeline/agents/signal_validator.py', note:'토큰 최대 820k. KILL300/MODIFY90 — 이 KILL/MODIFY 조건이 명시적 규칙으로 technical 가능한지 핵심'},
  {id:'G4', file:'polaris/core/pipeline/agents/pre_entry_watcher.py', note:'433k tok. KILL358 — 진입 전 감시 조건이 technical 규칙화 가능한지'},
  {id:'G6', file:'polaris/core/pipeline/agents/position_monitor.py', note:'P1 비쌈, HOLD100%. 모니터는 technical로 충분한지, AI 고유 판단 있는지'},
  {id:'G7', file:'polaris/core/pipeline/agents/adaptive_exit.py', note:'P1+python FSM(#26 이미 technical). AI 엑싯이 FSM 위에 더하는 가치 있나, 모호 케이스만 AI?'},
  {id:'G8', file:'polaris/core/pipeline/agents/post_trade_reflector.py', note:'학습/reflection — 본질적으로 AI judgment(conductor 후보)'},
]

const GATE_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['gate_id','current_decision','ai_delegated_judgment','technical_replaceable','technical_design','ai_unique_value','recommendation','token_saving','precision_risk'],
  properties:{
    gate_id:{type:'string'},
    current_decision:{type:'string', description:'이 gate가 실제 내리는 결정(input->output), 코드 근거(파일:라인)'},
    ai_delegated_judgment:{type:'string', description:'GPT에 위임된 구체 판단 + payload에 뭘 넣는지'},
    technical_replaceable:{type:'string', enum:['fully','partially','no'], description:'결정을 deterministic technical(지표/임계/규칙/데이터스트림)로 대체 가능한 정도'},
    technical_design:{type:'string', description:'technical로 어떻게 구현(구체 규칙/지표/임계). partially면 어디까지'},
    ai_unique_value:{type:'string', description:'technical로 못하는 AI 고유 가치(있으면). 없으면 NONE'},
    recommendation:{type:'string', enum:['eliminate-ai','technical-with-ai-conductor','keep-ai-per-signal','move-to-conductor'], description:'eliminate-ai=AI빼고 technical만; technical-with-ai-conductor=technical결정+AI상위감독; keep=per-signal유지정당; move-to-conductor=이AI를 상위지휘로'},
    token_saving:{type:'string', description:'이 변경의 토큰/비용 절감 추정(실측 기반)'},
    precision_risk:{type:'string', description:'technical 대체 시 surgical-strike 정밀도/근거 손실 리스크 + 완화책'},
  },
}

phase('Gate analysis')
const analysesRaw = await parallel(GATES.map(function(g){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[너의 임무] Gate ${g.id} 단일 분석. 파일 ${g.file} 을 Read 해서:\n1. 이 gate가 실제 무엇을 결정하는가(input->decision), GPT payload에 무엇을 넣고 무엇을 판단시키는가.\n2. 그 결정/판단이 deterministic technical(데이터 스트림·지표·임계·명시 규칙)로 대체 가능한가? 특히 ${g.note}\n3. KILL/MODIFY/HOLD 등 non-trivial decision이 명시적 조건인지(규칙화 가능) 아니면 진짜 모호한 judgment인지 코드로 확인.\n4. AI를 per-signal에서 빼면 무엇을 잃나(precision/근거). AI 고유 가치가 있다면 그것은 "총괄 지휘"(레짐/전략/감독/학습)로 옮길 수 있나.\nschema로 반환. 코드 라인 근거 필수, 추측 금지.`,
      {label:`analyze:${g.id}`, phase:'Gate analysis', schema:GATE_SCHEMA, agentType:'general-purpose'})
  }
}))
const analyses = analysesRaw.filter(Boolean)

phase('Conductor design')
const DESIGN_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['feasible','verdict','technical_decision_layer','ai_conductor_layer','eliminated_calls','token_reduction_pct','precision_preservation','aggressive_mandate_fit','phased_path','needs_debate'],
  properties:{
    feasible:{type:'string', enum:['yes','partial','no']},
    verdict:{type:'string', description:'Jin 질문 직답: technical-decides + AI-conducts 구조 가능한가, 한 문단'},
    technical_decision_layer:{type:'array', items:{type:'string'}, description:'deterministic technical로 내려갈 결정들(gate별 무엇이 어떤 규칙으로)'},
    ai_conductor_layer:{type:'array', items:{type:'string'}, description:'AI가 총괄 지휘로 담당할 것들(레짐 확정·전략 선택·주기 감독/캘리브·이상감지·G8 학습). 각각 호출 주기(per-signal 아님: per-regime-change/per-N-min/per-trade) 명시'},
    eliminated_calls:{type:'array', items:{type:'string'}, description:'제거되는 per-signal AI 호출 + 실측 토큰 절감'},
    token_reduction_pct:{type:'string', description:'전체 토큰/비용 절감 추정 %(실측 1.70M in 기준)'},
    precision_preservation:{type:'string', description:'surgical-strike 정밀 진입/엑싯·근거있는 거래가 어떻게 유지되는가(technical FSM #26 + alt-data evidence + AI 지휘의 조합)'},
    aggressive_mandate_fit:{type:'string', description:'9-stack 봉쇄·hard-MAX·flow_not_block·거부키워드 0 정합 확인'},
    phased_path:{type:'array', items:{type:'string'}, description:'additive 무중단 phased 전환(P0..). 각 단계 거동영향'},
    needs_debate:{type:'array', items:{type:'string'}, description:'/debate(GPT+Gemini) 교차검증 필요 항목(아키텍처 대규모 변경)'},
  },
}
const design = await agent(`${HEADER}\n\n${CONTEXT}\n\n[너의 임무] 아래 6개 gate 분석을 종합해 "technical-decides + AI-conducts" 아키텍처를 설계하라.\n[gate 분석]\n${JSON.stringify(analyses)}\n\n요구: (1) Jin 질문 직답(구조상 가능?). (2) technical decision layer vs AI conductor layer 명확 분리 — AI는 per-signal 도장 제거, 레짐 확정/전략 선택/주기 감독/이상감지/학습만(각 호출 주기 명시). (3) 실측 토큰 절감 %. (4) 핵심: surgical-strike 정밀 진입/엑싯과 근거있는 거래가 어떻게 유지되는지(정밀도 희생 금지 — Jin은 절감보다 정밀도 우선). (5) aggressive/mandate 정합. (6) additive 무중단 phased path. (7) /debate 항목. schema 반환.`,
  {label:'conductor-design', phase:'Conductor design', schema:DESIGN_SCHEMA, agentType:'general-purpose'})

phase('Adversarial review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['lens','verdict','blocking','concerns','additions'],
  properties:{
    lens:{type:'string'},
    verdict:{type:'string', enum:['endorse','endorse-with-changes','reject']},
    blocking:{type:'array', items:{type:'string'}, description:'설계를 막는 치명 결함(mandate 위반/정밀도 손실/근거 상실). 없으면 빈 배열'},
    concerns:{type:'array', items:{type:'string'}},
    additions:{type:'array', items:{type:'string'}, description:'보강/수정 제안'},
  },
}
const LENSES = [
  {k:'surgical-precision', p:'technical-only 결정이 정밀 진입/엑싯 타이밍을 유지하는가? AI를 per-signal에서 빼면 모호한 경계 케이스(레짐 전환기·뉴스 충격·갭)에서 정밀도가 무너지지 않는가? G7 엑싯에서 AI judgment 상실 영향. 정밀도 우선(Jin) 위배 여부.'},
  {k:'evidence-based', p:'"근거있는 거래"(Jin)가 유지되는가? alt-data/레짐 evidence가 technical 결정에 충분히 반영되나? AI conductor가 레짐을 per-N으로만 보면 빠른 레짐 변화를 놓치지 않나? deterministic 규칙이 근거를 단순화/희석하지 않나.'},
  {k:'aggressive-mandate', p:'9-stack collapse 봉쇄·hard-MAX·flow_not_block·DEMO·거부키워드 0 정합. technical 규칙화가 은밀히 방어 throttle/진입차단/사이징축소를 도입하지 않는가(KILL 규칙이 blanket block 되면 위반). aggressive bias 보존되나.'},
  {k:'conductor-sufficiency', p:'AI를 상위 지휘로만 두면 per-signal 통제를 상실하는가? conductor가 충분히 자주/정확히 개입하나? technical 규칙이 잘못됐을 때 AI가 잡아낼 피드백 루프 있나? 호출 주기가 너무 성기면 봇이 지휘 없이 폭주하지 않나. 실현 복잡도/유지보수.'},
]
const reviewsRaw = await parallel(LENSES.map(function(L){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[너의 임무] 아래 conductor 설계를 lens="${L.k}" 관점에서 적대적으로 검증하라. 반박을 기본값으로(설계가 틀렸다고 가정하고 깨려 시도). 검증 초점: ${L.p}\n[설계]\n${JSON.stringify(design)}\n\nblocking(치명결함)은 보수적으로 — 진짜 mandate 위반/정밀도 손실/근거 상실만. schema 반환.`,
      {label:`review:${L.k}`, phase:'Adversarial review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  }
}))
const reviews = reviewsRaw.filter(Boolean)

return { analyses, design, reviews }
