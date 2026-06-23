import json, urllib.request, urllib.error, concurrent.futures

# Per-provider wall-clock cap. Shorter than a true hang so a stalled provider
# never sinks the whole debate — the other side is still emitted.
PROVIDER_TIMEOUT = 180

def read_env(k):
    for line in open('.env'):
        if line.startswith(k+'='):
            return line.split('=',1)[1].strip().strip('"').strip("'")
    return None

OAI=read_env('OPENAI_API_KEY'); GEM=read_env('GEMINI_API_KEY')

PROMPT = """당신은 자율 트레이딩 봇 아키텍처를 비판적으로 검토하는 독립 전문가입니다. 아래 DEMO/PAPER(가상자금) 공격적 봇의 설계 안건을 적대적으로 검토하고, 쟁점 D1/D2/D3 각각에 대해 [당신의 입장 → 근거 → '내 제안'에 대한 반론/맹점 → 최종 권고]를 한국어로 답하세요.

원칙(반드시 준수): 이 봇은 aggressive·flow_not_block — 방어적 throttle/사이즈 축소/거래 차단을 권하지 마세요. 손실방어는 '정밀 엑싯'으로만. 'regulatory cap/professional risk/90d gate/monthly review/표본부족 보수론' 프레이밍 금지. 봇 런타임 LLM=GPT만(Anthropic 금지).

[현 코드 사실] 외부 evidence는 alt-data fuser seam으로만 흡수: cache.py가 자산군 prefix로 소스 라우팅 → fuser.py가 4개 canonical regime label에 가산 스코어(source-weight clamp 0.75~1.25) → compose_regime_candidate가 '가격보다 확신 높을 때만' regime tilt(제안일 뿐, 사이징/차단/엑싯 직접 안 함). COT는 종목별 3년 percentile로 정규화. shadow→acceptance→manual promote 레일 존재(entry_admission_shadow, gate_shadow_events, posterior PnL). P6 conductor(루프 밖 비동기 batch 합성)는 선행 결론상 '나중'.

[검토 대상 제안 — 4 Phase]
P0: ResearchSignal 정규화 계약 {label|score, confidence in [0,1], freshness_ts, evidence_refs[]} + source clamp 상속 + shadow Behavior-0(logged only) + acceptance metric.
P1: 첫 런타임 에이전트 = 뉴스/이벤트 sentiment를 fuser collector로(regime-evidence seam, shadow-only→edge 입증 후 promote).
P2: 포지션 적정성 리뷰어 = G7 exit-evidence(확장만 자유, 축소/조기청산은 결정론 FSM 유지).
P3: P6 conductor + dev-time 전략 마이너(GitHub/유튜브/블로그, 오프라인·거래루프 분리).

[쟁점]
D1: 모든 런타임 리서치 신호를 regime-evidence(fuser) '단일 seam'으로 통일 vs entry(admission)/exit(G7)별 '분리 seam' — 어느 쪽이 옳은가? 단순성 vs 표현력.
D2: MVP를 '뉴스 sentiment'(저위험·edge 측정 명확) vs '포지션 적정성 리뷰어'(운영자 1순위 관심·surgical-strike 직결) 중 무엇으로 시작?
D3: {bounded label+score+confidence+clamp+shadow-acceptance}가 LLM 편향/환각이 사이징을 오염시키는 걸 막기에 충분한가, 아니면 추가 가드(소스간 교차검증/캘리브레이션 곡선/인간 sign-off 등)가 필요한가?

구체적이고 코드 seam 수준으로. 800단어 이내."""

def call_openai():
    body=json.dumps({"model":"gpt-5.2","messages":[{"role":"user","content":PROMPT}],"max_completion_tokens":9000}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
        headers={"Authorization":f"Bearer {OAI}","Content-Type":"application/json"})
    try:
        r=json.load(urllib.request.urlopen(req,timeout=PROVIDER_TIMEOUT))
        return r["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"[OpenAI ERROR {e.code}] {e.read().decode()[:600]}"
    except Exception as e:
        return f"[OpenAI ERROR] {type(e).__name__}: {e}"

def call_gemini():
    body=json.dumps({"contents":[{"parts":[{"text":PROMPT}]}],
        "generationConfig":{"maxOutputTokens":9000}}).encode()
    url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={GEM}"
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"})
    try:
        r=json.load(urllib.request.urlopen(req,timeout=PROVIDER_TIMEOUT))
        parts=r["candidates"][0]["content"]["parts"]
        return "".join(p.get("text","") for p in parts)
    except urllib.error.HTTPError as e:
        return f"[Gemini ERROR {e.code}] {e.read().decode()[:600]}"
    except Exception as e:
        return f"[Gemini ERROR] {type(e).__name__}: {e}"

def run_debate(openai_fn=call_openai, gemini_fn=call_gemini, timeout=PROVIDER_TIMEOUT):
    """Run GPT and Gemini concurrently. If one hangs/fails past `timeout`, still
    emit the other plus a note. Returns (gpt_text, gemini_text)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fo = ex.submit(openai_fn)
        fg = ex.submit(gemini_fn)
        try:
            o = fo.result(timeout=timeout)
        except Exception as e:
            o = f"[OpenAI UNAVAILABLE — proceeding with Gemini only] {type(e).__name__}: {e}"
        try:
            g = fg.result(timeout=timeout)
        except Exception as e:
            g = f"[Gemini UNAVAILABLE — proceeding with GPT only] {type(e).__name__}: {e}"
    return o, g

if __name__ == "__main__":
    gpt, gem = run_debate()
    print("=====GPT-5.2====="); print(gpt)
    print("\n=====GEMINI-2.5-PRO====="); print(gem)
