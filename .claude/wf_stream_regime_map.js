export const meta = {
  name: 'stream-regime-connection-map',
  description: 'Jin "다 연결됨": universe→asset_class→stream→regime→cell→G1 의존 체인 매핑 + Capital crypto 오염 근원 + 통합 빌드 순서',
  phases: [
    { title: 'Map readers' },
    { title: 'Integrated plan' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/축소/차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO. 봇 LLM=GPT, Anthropic 호출 금지. 코드: /Volumes/Development/Projects/Polaris. 실제 파일 Read 근거 필수, 추측 금지. read-only(수정 금지). 출력=raw, 산문 최소.]`

const CONTEXT = `[Jin 핵심] "레짐 셀렉션이 다이나믹이어야 — 상황/티커/익스체인지별. 그리고 이거 다 연결되어있다." 3-스트림 의도: A=OKX 크립토 SPOT(롱,lev1,24/7), B=Capital CFD(FX/지수/금,롱숏,세션), C=Alpaca 미국주식(롱,RTH).
[발견된 문제] regime_state DB: capital venue 69그룹이 전부 'crypto:LIT','crypto:XLM','crypto:FET' 등 crypto 알트 — 즉 Capital이 의도(FX/지수/금)와 달리 crypto 알트 CFD를 거래 중. compute_underlying_group_id(canonical.py:25-59)는 asset_class로 crypto/forex/index/commodity 분기하는데 Capital 심볼 asset_class가 'crypto'로 박힘. classify_regime(regime_flip.py)은 P0 stub(confidence 0.5 고정). alt-data fuser는 prefix(crypto/forex/...)로 자산군별 민감 소스 라우팅(crypto→funding/F&G, forex/commodity→macro, equity→gap).
[연결 가설] universe selection → asset_class 태깅 → StreamProfile(#13) → regime L2(자산군 민감) → cell_matrix(exchange,strategy,ticker,regime) → G1 ranker. asset_class 오태깅이 전 체인 오염.`

const READER_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['area','current_behavior','broken_point','connections','fix_needed','code_refs'],
  properties:{
    area:{type:'string'},
    current_behavior:{type:'string', description:'이 영역 실제 동작(코드 근거)'},
    broken_point:{type:'string', description:'깨진/오염 지점(있으면). 없으면 NONE'},
    connections:{type:'string', description:'이 영역이 무엇과 연결되는가(상류 입력/하류 소비)'},
    fix_needed:{type:'string', description:'교정 필요한 것 + 범위'},
    code_refs:{type:'array', items:{type:'string'}, description:'file:line 근거'},
  },
}

const READERS = [
  {area:'Capital universe/stream selection', q:'Capital(venue=capital)이 왜 crypto 알트 CFD(LIT/XLM/FET)를 거래하나? Layer0 dynamic universe가 Capital 심볼을 어디서 가져오나(venue adapter instrument list, universe scanner, StreamConfig). 의도한 FX/지수/금이 아니라 crypto가 선택되는 근원. polaris/venues/capital* + polaris/core/.../universe* + streams/config.py 정독.'},
  {area:'asset_class 태깅 체인', q:'심볼→asset_class가 어디서 결정되나? compute_underlying_group_id(canonical.py)의 asset_class 인자 출처. Capital 심볼이 asset_class=crypto로 박히는 위치. instrument metadata / venue adapter / StreamProfile product_class와의 관계. 올바른 forex/index/commodity 태깅으로 교정하려면 어디를 고쳐야 하나.'},
  {area:'StreamProfile #13 현황', q:'venue→stream(A/B/C) resolve, product_class, StreamConfig/StreamProfile(core/streams/config.py + _stream_guards.py + session_exit_rail.py). 이게 universe selection·asset_class·regime과 어떻게 연결되나(또는 안 되나). Phase0-3 빌드된 것 중 asset_class/universe를 실제로 제어하는 부분이 있나.'},
  {area:'regime→cell→G1→sizing 소비 체인', q:'asset_class/regime이 어디서 소비되나: regime_flip classify_regime, altdata fuser(prefix 분기), cell_matrix(exchange,strategy,ticker,regime), G1 universe_scanner ranking, sizing engine regime context. asset_class 오태깅이 각 소비처에 어떻게 전파/오염되나. G1 ranker가 asset-class aware해야 하나(regime L2가 ranking에 영향?). 계층 regime(L1 macro/L2 asset-class/L3 ticker) 실구현 시 각 소비처가 받는 영향.'},
]

phase('Map readers')
const mapRaw = await parallel(READERS.map(function(r){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[너의 임무] 영역="${r.area}" 단일 매핑(read-only). ${r.q}\nschema 반환. file:line 근거 필수, 추측 금지.`,
      {label:`map:${r.area.slice(0,20)}`, phase:'Map readers', schema:READER_SCHEMA, agentType:'general-purpose'})
  }
}))
const maps = mapRaw.filter(Boolean)

phase('Integrated plan')
const PLAN_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['dependency_chain','root_cause','integrated_build_order','per_step_scope','g1_timing','needs_debate','risks'],
  properties:{
    dependency_chain:{type:'string', description:'universe→asset_class→stream→regime→cell→G1 실제 의존 그래프(무엇이 무엇을 전제)'},
    root_cause:{type:'string', description:'Capital crypto 오염 근원(어느 코드 한 곳/몇 곳을 고치면 자산군 태깅이 바로잡히나)'},
    integrated_build_order:{type:'array', items:{type:'string'}, description:'통합 빌드 순서(의존 반영). G1 즉시절감 + stream 교정 + regime 계층을 어떤 순서로. 각 단계 선행조건 명시'},
    per_step_scope:{type:'array', items:{type:'string'}, description:'각 단계 파일/범위'},
    g1_timing:{type:'string', description:'G1 ranker 빌드를 stream/regime 교정 前에 독립으로 해도 되나, 後여야 하나(asset-class aware 필요 여부). Jin "다 연결" 관점에서 판정'},
    needs_debate:{type:'array', items:{type:'string'}, description:'/debate 항목(트레이딩 파라미터/아키텍처)'},
    risks:{type:'array', items:{type:'string'}, description:'무중단·거동보존·오염전파 리스크'},
  },
}
const plan = await agent(`${HEADER}\n\n${CONTEXT}\n\n[너의 임무] 아래 4개 영역 매핑을 종합해 **통합 빌드 계획**을 내라(Jin "다 연결, 한꺼번에"). \n[매핑]\n${JSON.stringify(maps)}\n
요구: (1) 실제 의존 그래프. (2) Capital crypto 오염 근원(최소 수정점). (3) 통합 빌드 순서 — G1 즉시절감 + stream 교정(Capital→FX/지수/금) + regime 계층(L1/L2/L3) 을 의존 반영해 차례대로, 각 단계 선행조건. (4) ⚠G1을 stream/regime 前 독립 빌드해도 되나 後여야 하나(asset-class aware 필요성 — Jin "다 연결" 핵심). (5) /debate 항목. (6) 무중단/거동보존 리스크. schema 반환.`,
  {label:'integrated-plan', phase:'Integrated plan', schema:PLAN_SCHEMA, agentType:'general-purpose'})

return { maps, plan }
