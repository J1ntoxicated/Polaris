export const meta = {
  name: 'build-g1-assetaware',
  description: 'STEP 6: G1 asset-class-aware — 자산군별 focus 쿼터(crypto 독식 방지, FX/지수/금 보장) + Capital vol_24h_usd 채우기 (TDD→adversarial review→gate)',
  phases: [
    { title: 'Build' },
    { title: 'Review' },
    { title: 'Gate' },
  ],
}

const HEADER = `[Polaris — DEMO/PAPER 가상자금. AGGRESSIVE bias 보존(방어 throttle/사이징축소/진입차단 거부; flow_not_block). 거부 키워드 채택 금지: regulatory cap·professional risk·real-money safety·90d gate·monthly review·posture standard. 불변: 9-stack 봉쇄·hard-MAX·DEMO only. 봇 LLM=OpenAI GPT, Anthropic 호출 금지. 코드: /Users/jinyoon/Projects/Polaris, Python 3.13 async, mypy --strict, ruff clean, TDD(실패→구현→pass), pytest. vault 권한. 출력=raw, 산문 최소.]`

const PLAN = `[설계 SSOT] .claude/plans/stream_regime_integration_2026-05-30.md STEP 6 정독. Jin (a) 완전배제 확정: A=OKX crypto, B=Capital FX/지수/금, C=Alpaca equity.
[문제] STEP1에서 G1을 deterministic scored vol-ranker로 전환(universe_scanner.py scored_top_n, score=w_vol*log1p(vol)+...). 그런데 Capital 심볼은 _capital.py:178에서 vol_24h_usd=0.0으로 박혀 log1p(0)=0 → G1 ranking 최하위 → focus 못 받음 → Capital(FX/지수/금) 거래 0. 즉 STEP2에서 crypto 배제했지만 Capital이 focus에 못 들어가 여전히 안 거래됨.
[참조] universe_scanner.py(scored_top_n, _select_focus), polaris/core/universe/watchlist.py(compute_dynamic_focus), _capital.py:166-185(vol=0.0 위치), schema.py(UniverseInstrument vol_24h_usd).`

phase('Build')
const BUILD_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['files_changed','tests_added','approach','quota_design','vol_fill','behavior_safety','pytest_result','ruff_mypy'],
  properties:{
    files_changed:{type:'array', items:{type:'string'}},
    tests_added:{type:'string', description:'추가 테스트 수 + 검증'},
    approach:{type:'string', description:'전체 접근(쿼터 vs vol채우기 어떤 조합)'},
    quota_design:{type:'string', description:'자산군별 focus 쿼터 설계(min 슬롯·env·clamp). crypto 독식 방지 + FX/지수/금 보장'},
    vol_fill:{type:'string', description:'Capital vol_24h_usd 채우기(chart endpoint or proxy or 쿼터로 우회). 봇 정지라 실 API 검증 제한 — mock 테스트'},
    behavior_safety:{type:'string', description:'쿼터가 flow 증가(throttle 아님)·OKX(crypto만)/Alpaca(equity만) 무영향·G1 항상 PASS 보존·9-stack 무관 확인'},
    pytest_result:{type:'string', description:'pytest 실제 실행 결과'},
    ruff_mypy:{type:'string', description:'ruff + mypy --strict 결과'},
  },
}
const build = await agent(`${HEADER}\n${PLAN}\n
[임무] STEP 6 G1 asset-class-aware (TDD 엄수: 실패 테스트 먼저→구현→pass).
1. 정독: universe_scanner.py(scored_top_n/_select_focus, STEP1 결과), watchlist.py(compute_dynamic_focus), _capital.py:166-185(vol=0.0).
2. **(b) 자산군별 focus 쿼터(1차 해결)**: focus selection에서 한 자산군(crypto)이 전 슬롯 독식하지 못하게 + Capital(FX/지수/금)·Alpaca(equity)가 최소 슬롯 보장받게. 자산군별 min 쿼터(env-override 보수적 기본, /debate 캘리브 플래그). ⚠vol=0인 Capital도 쿼터로 focus 보장(vol 채우기 없이도 거래 가능하게). ⚠쿼터는 crypto를 줄이는 게 아니라(throttle 금지) FX/지수/금에 슬롯을 보장(flow 증가, aggressive 정합). focus가 per-venue인지 cross-venue인지 코드로 확인 후 적절히.
3. **(a) Capital vol_24h_usd 채우기(2차/best-effort)**: _capital.py:178 vol=0.0의 근원 확인. 가능하면 Capital chart/candle endpoint 또는 이미 fetch한 데이터의 proxy로 실 vol 채우기. ⚠봇 정지 상태라 실 API 검증 제한 → mock/단위 테스트로 로직 검증, 실 API 의존부는 graceful(없으면 0 유지 → 쿼터가 커버). 무리한 실 API 추가보다 쿼터(b)가 거래 가능성의 1차 보장.
4. ⚠불변: G1 항상 PASS(selector), clamp 12-48, OKX(crypto만)·Alpaca(equity만) 무영향(쿼터가 단일 자산군 venue엔 no-op), 9-stack 무관(universe 단계), aggressive(쿼터=flow 증가 not throttle).
TDD: 실패 테스트(crypto 독식 시 FX/지수/금 쿼터 보장·vol=0 Capital focus 포함·OKX/Alpaca 무영향·clamp·PASS 보존) → 구현 → pytest 실제 pass. ruff+mypy --strict clean. **작업 완료 후 반드시 StructuredOutput(schema) 호출.**`,
  {label:'build:g1-assetaware', phase:'Build', schema:BUILD_SCHEMA, agentType:'general-purpose'})

phase('Review')
const REVIEW_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['verdict','blocking','nits','aggressive_ok','no_collateral','capital_tradeable'],
  properties:{
    verdict:{type:'string', enum:['approve','approve-with-nits','reject']},
    blocking:{type:'array', items:{type:'string'}, description:'머지 블로커. 없으면 빈 배열'},
    nits:{type:'array', items:{type:'string'}},
    aggressive_ok:{type:'string', description:'쿼터가 flow 증가(FX/지수/금 보장)이지 crypto throttle 아님 확인. 거부키워드 0.'},
    no_collateral:{type:'string', description:'OKX(crypto만)·Alpaca(equity만) venue 무영향. G1 PASS·clamp 보존. 9-stack 무관.'},
    capital_tradeable:{type:'string', description:'vol=0인 Capital FX/지수/금이 실제로 focus에 들어가 거래 가능해지나(쿼터 효과)'},
  },
}
const review = await agent(`${HEADER}\n${PLAN}\n
[임무] builder≠reviewer adversarial review(반박 기본). STEP 6 빌드 검증.
[빌드]\n${JSON.stringify(build)}\n
파일 직접 Read 검증: (1) ⚠capital_tradeable: vol=0인 Capital FX/지수/금이 쿼터로 실제 focus 진입→거래 가능해지나(STEP 6 핵심 목표). (2) 쿼터가 crypto throttle/축소 아니라 FX/지수/금 flow 보장(aggressive). (3) ⚠OKX(crypto만)/Alpaca(equity만) 단일자산군 venue 무영향(쿼터 no-op). (4) G1 항상 PASS·clamp 12-48 보존. (5) vol 채우기(있으면) graceful·실 API 의존부 안전. (6) 9-stack 무관. (7) 테스트 충분·ruff/mypy clean.
blocking 보수적으로(진짜 collateral/aggressive 위반/목표 미달성만). schema 반환.`,
  {label:'review:g1-assetaware', phase:'Review', schema:REVIEW_SCHEMA, agentType:'general-purpose'})

phase('Gate')
let final = { build, review, fix: null }
if (review.verdict === 'reject') {
  const fix = await agent(`${HEADER}\n${PLAN}\n[임무] review reject. blocking 해결(TDD 유지, 불변 보존).\n[review]\n${JSON.stringify(review)}\n[원빌드]\n${JSON.stringify(build)}\nblocking 수정→pytest green→ruff/mypy clean. 반드시 StructuredOutput 반환.`,
    {label:'fix:g1-assetaware', phase:'Gate', schema:BUILD_SCHEMA, agentType:'general-purpose'})
  const rereview = await agent(`${HEADER}\n[임무] 재검증. fix를 다시 adversarial review.\n[fix]\n${JSON.stringify(fix)}\nschema 반환.`,
    {label:'rereview:g1-assetaware', phase:'Gate', schema:REVIEW_SCHEMA, agentType:'general-purpose'})
  final = { build, review, fix, rereview }
}
return final
