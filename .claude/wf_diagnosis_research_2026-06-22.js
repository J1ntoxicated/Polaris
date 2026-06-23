export const meta = {
  name: 'polaris-diagnosis-research',
  description: 'Jin "지금 뭘 해야하나": 라이브 trade 퍼포먼스 + 적정성 기준 + 백테스트 토대 + 코드 전수조사 + 외부 TA/봇 차용 → 근거기반 우선순위 로드맵',
  phases: [
    { title: 'Read' },
    { title: 'Synthesize' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/축소/차단 거부; flow_not_block; 손실방어=정밀엑싯). surgical-strike(정밀 진입/엑싯, 오직 수익, 근거 있는 거래). 거부 키워드 채택 금지: regulatory cap·professional risk·90d gate·monthly review·posture standard·표본부족 보수론. 불변: 9-stack 봉쇄·hard-MAX·DEMO. 봇 런타임 LLM=GPT(Anthropic 금지). 코드 루트=cwd(/Users/jinyoon/Projects/Polaris) 상대경로. DB=data/polaris_live.sqlite. 실제 file:line/수치 근거 필수, 추측 금지. read-only(수정 절대 금지). 출력=raw, 산문 최소.]`

const CONTEXT = `[상황] 봇 라이브 가동중(3-stream: OKX 크립토 SPOT 롱 / Capital CFD FX·지수·원자재 롱숏 / Alpaca 미국주식 롱). 라이브 청산 trade 171건(positions.pnl_r 有). 백테스트=replay_runs 0·benchmark_results 0 (하네스 스캐폴딩됐으나 미실행 — edge 측정수단 부재). in-loop AI-free 결정론(G1-G8), exit=정밀 FSM(run_precise_exit/evaluate_exit), sizing=T4. 알려진 latent: _production_bars.py:412(commodity/index→forex baseline 버킷), _production_layers.py:395(NULL JOIN→crypto fallback), classify_regime P0 stub.
[Jin 요구] (1) 현 구조의 '포지션 적정성 판단기준' 규명, (2) 각 티커별 퍼포먼스 + 백테스팅 할 수 있는 토대, (3) 각종 기술분석·트레이딩 이론 사용 토대, (4) 외부 트레이딩 봇/방법 중 우리가 차용·시도할 것 종합 리서치, (5) 지금까지 데이터 분석해 개선점, (6) 구조적 헛점·개선점(코드 전수조사). → '근본적으로 지금 뭘 해야하나'에 근거기반 답.`

const RS = {
  type:'object', additionalProperties:false,
  required:['area','findings','gaps_weaknesses','recommendations','code_refs'],
  properties:{
    area:{type:'string'},
    findings:{type:'string', description:'실제 발견(수치/코드 근거). DB는 실제 쿼리 결과 수치.'},
    gaps_weaknesses:{type:'array', items:{type:'string'}, description:'갭/약점/헛점'},
    recommendations:{type:'array', items:{type:'string'}, description:'개선/구축 권고(구체적, 범위)'},
    code_refs:{type:'array', items:{type:'string'}, description:'file:line 또는 SQL/테이블 근거'},
  },
}

const READERS = [
  {area:'라이브 trade 퍼포먼스 (per-ticker/strategy/regime)', q:'data/polaris_live.sqlite의 positions(status closed/reconciled, pnl_r 171건) + trades를 실제 SQL로 분석: ticker별·strategy별·regime별·venue별·session별 평균 pnl_r, win-rate, 건수, holding, MFE/MAE(mfe_r/mae_r). 어떤 세그먼트가 edge 있고 어디서 잃나. reconciled(close fill 없는 drift) 비율이 텔레메트리 오염하나. 실제 수치 표로.'},
  {area:'포지션 적정성 판단기준 (현 구조)', q:'현재 봇이 포지션 적정성/exit/sizing을 판단하는 실제 로직: run_precise_exit/evaluate_exit(exit_engine) FSM 분기, profit_target_r/ATR-trail/loser_timeout/protected_bep, T4 sizing 결정요인, G3/G7 결정. "적정성 기준"이 무엇이고 무엇이 빠졌나(예: per-ticker 적응 부재, 백테스트 검증 부재). file:line.'},
  {area:'백테스트/replay 토대 (0 runs)', q:'polaris/core/replay engine + replay_runs/benchmark_results 테이블 + .claude/plans/p1_replay_benchmark_harness 현황. 왜 0 runs인가(미배선? 미실행? 막힘?). per-ticker/per-strategy backtest를 실제로 돌리려면 무엇이 필요한가(데이터·엔트리포인트·결정론 재현). 최소 실행 경로. file:line.'},
  {area:'코드 전수조사 — 구조적 헛점/개선', q:'codebase 구조 감사: dead code, anti-pattern, 알려진 latent(_production_bars:412·_production_layers:395·classify_regime stub) 외 추가 구조결함, 모듈 결합도/God-module, 빠진 연결(asset-class·regime·cell), 테스트 커버리지 약점, 9-stack/sizing 무결성 위험, error-swallowing. 우선순위와 함께. file:line.'},
  {area:'외부 차용 — TA·트레이딩 이론·오픈소스 봇', q:'웹 리서치(WebSearch/WebFetch deferred tool 로드해 사용). 우리 3-stream surgical-strike(롱 크립토 SPOT·CFD 롱숏·주식 롱, 정밀 진입/엑싯, aggressive, in-loop 결정론+GPT evidence)에 적용 가능한: (a) 기술분석 방법/지표(우리가 안 쓰는 것 중 edge 있는), (b) 트레이딩 이론/전략 패러다임, (c) 공개 트레이딩 봇/프레임워크(freqtrade·jesse·backtrader·vectorbt·hummingbot 등)에서 차용·시도할 구체 컴포넌트(백테스트엔진·전략·리스크). 우리 맥락 적용성·차용 난이도와 함께 구체적으로. 거부키워드 0.'},
]

phase('Read')
const mapRaw = await parallel(READERS.map(function(r){
  return function(){
    return agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 영역="${r.area}" 정밀 분석(read-only). ${r.q}\nschema 반환. 근거(수치/file:line) 필수, 추측 금지.`,
      {label:`read:${r.area.slice(0,18)}`, phase:'Read', schema:RS, agentType:'general-purpose'})
  }
}))
const maps = mapRaw.filter(Boolean)

phase('Synthesize')
const SS = {
  type:'object', additionalProperties:false,
  required:['priority_now','top_improvements','foundation_gaps','structural_fixes','external_borrow','sequencing','data_evidence'],
  properties:{
    priority_now:{type:'string', description:'"지금 근본적으로 뭘 해야하나"에 대한 근거기반 1-문단 답'},
    top_improvements:{type:'array', items:{type:'string'}, description:'임팩트 큰 개선 top 3-5 (근거+예상효과)'},
    foundation_gaps:{type:'array', items:{type:'string'}, description:'토대 갭(백테스트/perf측정/TA framework) + 구축 방법'},
    structural_fixes:{type:'array', items:{type:'string'}, description:'구조적 헛점 fix 우선순위(file:line)'},
    external_borrow:{type:'array', items:{type:'string'}, description:'외부 차용 top (무엇을·어디서·우리에 어떻게)'},
    sequencing:{type:'array', items:{type:'string'}, description:'의존 반영 실행 순서'},
    data_evidence:{type:'string', description:'핵심 데이터 근거 요약(per-ticker 퍼포먼스 수치 등)'},
  },
}
const synthesis = await agent(`${HEADER}\n\n${CONTEXT}\n\n[임무] 아래 5개 영역 분석을 종합해 '지금 근본적으로 뭘 해야하나'에 근거기반 우선순위 로드맵을 내라.\n[분석]\n${JSON.stringify(maps)}\n
요구: (1) priority_now 직답. (2) 임팩트순 top 개선. (3) 토대 갭+구축법(측정 못하면 개선 못함 관점). (4) 구조 헛점 fix 우선순위. (5) 외부 차용 top. (6) 실행 순서. (7) 데이터 근거. surgical-strike·aggressive 일관, 거부키워드 0. schema 반환.`,
  {label:'synthesis', phase:'Synthesize', schema:SS, agentType:'general-purpose'})

return { maps, synthesis }
