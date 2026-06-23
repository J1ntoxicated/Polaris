import json, urllib.request, urllib.error, concurrent.futures

def read_env(k):
    for line in open('.env'):
        if line.startswith(k+'='):
            return line.split('=',1)[1].strip().strip('"').strip("'")
    return None

OAI=read_env('OPENAI_API_KEY'); GEM=read_env('GEMINI_API_KEY')

PROMPT = """당신은 자율 트레이딩 봇의 실행/유니버스 계층 전문가입니다. 아래 DEMO/PAPER(가상자금) 공격적 봇의 실행층 개선 방향을 비판적으로 검토하고 쟁점 D1~D4에 [입장→근거→반론/맹점→최종 권고]를 한국어로 답하세요.

원칙(필수): aggressive·flow_not_block — 막지 말고 흐름을 재배분. 손실방어는 '정밀 엑싯'으로만. 'regulatory cap/professional risk/90d gate/monthly review/표본부족 보수론' 프레이밍 금지. 봇 LLM=GPT만(Anthropic 금지).

[진단(실측)] 봇 실제 손실 ~−266R(가시 −56R + 숨은 drift −209.7R/130건). 출처: ① volume_burst 단일 전략이 R손실의 80%(−45R): 볼륨 스파이크 top 매수(진입 결함) + OKX 저유동 알트에서 하드스톱 미체결(MAE −34~−100R인데 pnl_r은 −10R 클램프) ② OKX 크립토 전체 적자, 특히 <1h 스캘프 −48R(>24h tsmom은 본전) ③ 슬리피지 출혈(Alpaca $2k·OKX $958, fee보다 큼). 흑자=Capital 지수/원유(US100 +8.95R·J225 +6R WR62%·OIL +4.17R WR63%)·micro_reversion(유일 흑자 전략). 운영봇 리서치 결론: 우리 edge=상류(regime/AI/다이나믹 유니버스, 리테일보다 앞섬), 출혈=하류(유동성 게이팅·실행·reconciliation — 성숙한 봇이 과투자, 우리가 저투자).

[검토 대상 5개 방향 — flow_not_block로 지는 레짐(얇은 유동성 모멘텀 추격)에서 이기는 레짐(깊은 유동성 평균회귀)으로 라우팅]
1. 유동성 등급 Layer-0: 심볼별 vol/spread/±2% depth 등급 → T4 연속 scalar(0.75~1.5) 변조(깊으면 풀사이즈, 얇으면 축소). 하드 차단 아님.
2. volume_burst 극성 뒤집기: 클라이맥스 스파이크를 blow-off top으로 fade(추격 X) + 구조/follow-through 필터.
3. ATR 정규화 스톱: 고정 하드스톱 → entry − ATR×mult(변동 큰 알트는 와이드 스톱=체결되게), 사이즈는 risk$/(ATR×mult)로 R 일정.
4. 분할 엑싯(TWAP/트랜치) + 동적 슬리피지 톨러런스(고정 cap 대신 라이브 변동성/depth 기반).
5. 검증된 흑자(Capital 지수/원유·micro_reversion)로 자본 재집중.

[쟁점]
D1: 이 5개가 우리 진단(crypto×volume_burst·스톱 미체결·슬리피지)을 제대로 겨냥하나? 우선순위/순서는?
D2: aggressive·flow_not_block 보존하며 도입하는 구체 방법 — 특히 1·3이 '방어적 축소/9-stack'으로 변질되지 않게 하려면? (T4는 단일 연속 scalar 안에서, 추가 multiplier 금지)
D3: volume_burst는 '극성 뒤집기(fade)' vs '격리(끄기)' 중 무엇? 근거.
D4: 빠뜨린 출혈/리스크/방향은? (예: OKX 저유동 알트 자체를 유니버스에서 어떻게)

각 쟁점에 GPT와 Gemini의 입장+근거+반론, 그리고 최종 권고. 구체적·코드 seam 수준. 800~900단어."""

def call_openai():
    body=json.dumps({"model":"gpt-5.2","messages":[{"role":"user","content":PROMPT}],"max_completion_tokens":9000}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
        headers={"Authorization":f"Bearer {OAI}","Content-Type":"application/json"})
    try:
        r=json.load(urllib.request.urlopen(req,timeout=420)); return r["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e: return f"[OpenAI ERROR {e.code}] {e.read().decode()[:600]}"
    except Exception as e: return f"[OpenAI ERROR] {type(e).__name__}: {e}"

def call_gemini():
    body=json.dumps({"contents":[{"parts":[{"text":PROMPT}]}],"generationConfig":{"maxOutputTokens":9000}}).encode()
    url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={GEM}"
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"})
    try:
        r=json.load(urllib.request.urlopen(req,timeout=420)); parts=r["candidates"][0]["content"]["parts"]
        return "".join(p.get("text","") for p in parts)
    except urllib.error.HTTPError as e: return f"[Gemini ERROR {e.code}] {e.read().decode()[:600]}"
    except Exception as e: return f"[Gemini ERROR] {type(e).__name__}: {e}"

with concurrent.futures.ThreadPoolExecutor() as ex:
    fo=ex.submit(call_openai); fg=ex.submit(call_gemini)
    o=fo.result(); g=fg.result()
print("=====GPT-5.2====="); print(o)
print("\n=====GEMINI-2.5-PRO====="); print(g)
