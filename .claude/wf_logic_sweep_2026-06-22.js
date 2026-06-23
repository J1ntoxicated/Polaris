export const meta = {
  name: 'okx-else-binary-sweep',
  description: 'Jin "이상한거 다 찾아": 전 코드의 OKX-vs-else 2-거래소 이분법 + 하드코드 asset_class + 3rd-stream(Alpaca) 오처리 스윕 → live-vs-smoke 분류 + 심각도 랭크',
  phases: [ { title: 'Sweep' }, { title: 'Synthesize' } ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금 aggressive surgical-strike. 3-stream: OKX 크립토 SPOT / Capital CFD(FX·지수·원자재) / Alpaca 미국주식. flow_not_block. 거부 키워드 채택 금지: regulatory cap·professional risk·90d gate·monthly review·표본부족 보수론. cwd=/Users/jinyoon/Projects/Polaris 상대경로. read-only(수정 금지). 실제 file:line 근거 필수, 추측 금지. 출력=raw.]`

const CONTEXT = `[문제] 코드가 OKX+Capital 2-거래소 시절에 쓰여 'if venue==okx ... else ...' 이분법이 곳곳에 박힘. 이후 Alpaca(3rd stream, 미국주식 equity)가 추가됐는데 많은 'else' 가지가 Alpaca를 잘못 catch. 확인된 예: _production_bars.py:412 ac="crypto" if okx else "forex"(baseline 버킷 — commodity/index/equity 전부 forex로), _production_layers.py:395/353 & replay/engine.py:154 else "crypto" 폴백, _smoke_pipeline.py:146 leverage 1x if okx else 30x, :309 else "cfd", smoke_paper_loop.py:259 else cap_strats. core/streams/__init__.py:5는 이 'scattered if venue==okx else'를 인정. 거래 자체의 asset_class는 universe에서 정확 태깅되지만, scattered binary들이 baseline/leverage/strategy/group을 오염시킬 수 있음.`

const RS = {
  type:'object', additionalProperties:false,
  required:['area','findings','live_or_smoke','severity_ranked','code_refs'],
  properties:{
    area:{type:'string'},
    findings:{type:'string', description:'발견한 venue-이분법/하드코드/2-거래소 가정 (각각 실제 거동 영향 + Alpaca/multi-asset 오처리 여부)'},
    live_or_smoke:{type:'string', description:'각 발견이 PRODUCTION 경로(production_paper_loop→ignite_p1→_production_*)인지 죽은 smoke harness(_smoke_*, smoke_*)인지'},
    severity_ranked:{type:'array', items:{type:'string'}, description:'심각도 순 (CRITICAL/HIGH/MED/LOW + 한줄 사유). 실제 거동 오염 > 정규화 > 죽은코드'},
    code_refs:{type:'array', items:{type:'string'}, description:'file:line'},
  },
}

const READERS = [
  {area:'bars/baseline/ingest', q:'_production_bars.py 전체 venue-binary(238 venue==okx, 412 ac). ac가 baseline(412-420) 외 안 쓰임 재확인. baseline ATR/vol 정규화가 자산군 혼합으로 R-multiple 오염되는 정확한 범위. core/data/ingest.py·baseline.py의 asset_class 처리. 다른 venue-binary 결함.'},
  {area:'asset_class 태깅/폴백', q:'_production_layers.py:353/395, canonical.py, replay/engine.py:154의 else "crypto"/NULL 폴백. 어떤 실제 심볼이 잘못 태깅되나(live DB 확인 가능하면). universe 태깅이 누락/NULL이면 어디서 crypto로 떨어지나. mislabel의 하류 전파.'},
  {area:'sizing/leverage/cell/cluster', q:'leverage 하드코드(_smoke_pipeline:146 등) — Alpaca equity가 30x lev 받나? product_class/asset_class 가정, T4 사이징·cell_matrix·cluster cap의 venue-binary. live path에서 non-OKX 3rd-stream이 잘못된 lev/size/cluster 받는 곳.'},
  {area:'regime/fuser/altdata', q:'altdata fuser cache 자산군 prefix 라우팅, regime 분류의 자산군/거래소 가정. 2-거래소 잔재(okx/capital만 분기, alpaca 누락). mislabel이 regime evidence/sizing으로 전파되는 경로.'},
  {area:'close/exit/venue 어댑터', q:'_production_close.py:167, _smoke_fills, venue 어댑터의 venue별 close 로직 — okx(base_qty market)/capital(deal_id)/alpaca(shares) 3-way가 정확한가, 아니면 okx-vs-else 2-way로 alpaca를 capital처럼 오처리하나. exit FSM의 자산군 가정.'},
  {area:'live vs smoke 경계', q:'production_paper_loop.py→ignite_p1→_production_* 의 실제 live 경로를 따라가, 발견된 okx-vs-else binary 중 어느 게 LIVE이고 어느 게 죽은 smoke harness(_smoke_*, smoke_paper_loop, smoke_day1, _smoke_pipeline)인가. core/streams StreamConfig/product_class 추상화가 live에서 실제 쓰이나, 아니면 scattered binary가 우회하나. streams/__init__.py 의도 대비 실태.'},
]

phase('Sweep')
const mapRaw = await parallel(READERS.map(function(r){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 영역="${r.area}" 스윕(read-only). ${r.q}\nschema 반환. file:line 필수, 추측 금지. 각 발견은 'live 거동 오염 / 정규화만 / 죽은 smoke / 정당한 venue-specific' 중 무엇인지 분류.`,
      {label:`sweep:${r.area.slice(0,16)}`, phase:'Sweep', schema:RS, agentType:'general-purpose'})
  }
}))
const maps = mapRaw.filter(Boolean)

phase('Synthesize')
const SS = {
  type:'object', additionalProperties:false,
  required:['verdict','critical_live_bugs','normalization_bugs','smoke_only','legit','fix_order','two_to_three_stream_gap'],
  properties:{
    verdict:{type:'string', description:'한 문단 — 얼마나 심각한가, 거래 자체가 오염됐나 아니면 정규화/smoke 위주인가'},
    critical_live_bugs:{type:'array', items:{type:'string'}, description:'LIVE 거동 오염 버그 (file:line + 영향)'},
    normalization_bugs:{type:'array', items:{type:'string'}, description:'정규화/텔레메트리만 오염(거래 직접 X)'},
    smoke_only:{type:'array', items:{type:'string'}, description:'죽은 smoke harness 한정(거래 무영향)'},
    legit:{type:'array', items:{type:'string'}, description:'정당한 venue-specific(고칠 필요 X)'},
    fix_order:{type:'array', items:{type:'string'}, description:'심각도×live 순 fix 우선순위'},
    two_to_three_stream_gap:{type:'string', description:'2-거래소→3-스트림 전환에서 Alpaca/multi-asset이 빠진 구조적 갭 + 근본 처방(StreamConfig 추상화 강제 등)'},
  },
}
const synthesis = await agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 아래 6개 스윕을 종합. ${JSON.stringify(maps)}\n
요구: (1) verdict — 거래 자체 오염 vs 정규화/smoke 위주. (2) LIVE 거동 버그 / (3) 정규화만 / (4) smoke 한정 / (5) 정당 — 4분류. (6) fix 우선순위(심각도×live). (7) 2→3 스트림 갭 근본 처방. 거부키워드 0. schema 반환.`,
  {label:'synthesis', phase:'Synthesize', schema:SS, agentType:'general-purpose'})

return { maps, synthesis }
