# 07 — AI Decision Layer 분석 & 개선 플랜

> 실제 코드 분석 기반 (live.py, prompts.py, feedback.py, orchestrator.py,
> prompt_evolver.py, context_builder.py, base.py, mocks.py)
> 깃허브 레포 대부분은 연구/데모용이라 직접 통합 불가.
> 실질적으로 가져올 것: json-repair 하나.

---

## 현재 AI 레이어 구조

```
Stage 1: LiveSignalAugmenter  → Gemini Flash Lite  (borderline signals)
Stage 2: (StrategyAdvisor)    → Mock only
Stage 3: LiveEntryJudge       → Gemini Flash Lite  (Gate 8)
Stage 4: LiveExitAdviser      → Gemini 90% / Claude CRITICAL (Gate ai_ctrl)
Stage 5: LiveStrategyEvolution → Claude Sonnet     (hourly evolver)
Stage 6: LivePortfolioIntel   → Gemini Flash       (hourly)
+ LiveProactiveExit           → Gemini Flash Lite  (stagnant positions)
+ LiveRegimeAdviser           → Gemini Flash       (regime change)
+ LiveWSPriceIntel            → Gemini Flash Lite  (WS price action)
```

---

## 잘 된 것들 (건드리지 말 것)

- Mock/Live 완전 분리 — API 없이도 100% 동작
- Orchestrator 예산 관리 — daily/hourly 이중 체크
- Thompson Sampling (prompt_evolver) — 수학적으로 올바른 선택
- CONTRARIAN OVERRIDE — fear regime AI skip 무시 로직 명확
- ContextBuilder — instrument_profiles 압축 컨텍스트 잘 설계됨
- `_fallback_text_parse` — JSON 실패 시 안전망 존재

---

## 버그 & 개선 포인트

### 🔴 Bug 1: feedback.py — `ai_calls` 스코프 오류

```python
# 현재 (버그 — dir()은 스코프 변수 확인 안 됨)
ai_calls = ai_calls if 'ai_calls' in dir() else self._get_ai_calls_for_trade(trade_id)

# 수정
try:
    _ = ai_calls  # 이미 위에서 할당됨
except NameError:
    ai_calls = self._get_ai_calls_for_trade(trade_id)

# 또는 더 깔끔하게
if 'ai_calls' not in locals():
    ai_calls = self._get_ai_calls_for_trade(trade_id)
```

**파일**: `invasion/ai/feedback.py` line ~85
**위험도**: 낮음 (AI Selector 트래킹 누락만 발생)

---

### 🔴 Bug 2: JSON 파싱 불안정 — `_call_gemini` / `_call_claude`

두 함수가 동일한 JSON 추출 로직 복붙. `_fallback_text_parse` 존재 자체가
파싱 실패가 실제로 발생한다는 증거.

```python
# 현재: { } 찾기 → [ ] 찾기 → None 반환 → fallback_text_parse
start = raw.find("{")
end = raw.rfind("}") + 1
try:
    parsed = json.loads(raw[start:end])
except json.JSONDecodeError:
    pass  # 실패 시 None

# 개선: json-repair 적용
pip install json-repair

from json_repair import repair_json

def _extract_json(raw: str) -> dict | list | None:
    """Extract and repair JSON from LLM response."""
    # 먼저 { } 범위 추출
    for start_ch, end_ch in [('{', '}'), ('[', ']')]:
        s = raw.find(start_ch)
        e = raw.rfind(end_ch) + 1
        if s >= 0 and e > s:
            try:
                repaired = repair_json(raw[s:e])
                return json.loads(repaired)
            except Exception:
                continue
    # 전체 raw 시도
    try:
        repaired = repair_json(raw)
        result = json.loads(repaired)
        if isinstance(result, (dict, list)):
            return result
    except Exception:
        pass
    return None
```

**파일**: `invasion/ai/live.py`
**효과**: `_fallback_text_parse` 호출 빈도 대폭 감소, confidence=3 저품질 결정 제거

---

### 🟡 개선 1: `_call_gemini` / `_call_claude` 중복 제거

```python
# 현재: 두 함수에 동일한 JSON 추출 코드 복붙
# 개선: _extract_json() 헬퍼로 분리

def _extract_json(raw: str) -> dict | list | None:
    # 위 코드 참고

def _call_gemini(...):
    ...
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = _extract_json(raw)  # 한 줄로
    return parsed, _usage

def _call_claude(...):
    ...
    raw = data["content"][0]["text"]
    parsed = _extract_json(raw)  # 한 줄로
    return parsed, _usage
```

---

### 🟡 개선 2: `prompt_evolver.mutate()` — 실제 프롬프트 변이 없음

현재 mutate()는 version_id만 새로 등록하고 실제 프롬프트 텍스트는 안 바꿈.
`prompts.py`의 상수를 건드리는 코드 없음 → Thompson Sampling이 의미 없음.

```python
# 개선 방향
# 1. data/prompts/{stage}_v{n}.txt 파일에 실제 프롬프트 저장
# 2. live.py에서 prompts.SIGNAL_AUGMENT 대신 파일 읽기
# 3. mutate()에서 Claude로 새 프롬프트 변이 생성

def mutate(self):
    ...
    # 실제 변이 생성 (Claude 사용)
    base_prompt = self._load_prompt(best_vid)  # 파일에서 읽기
    mutation_prompt = f"""
    이 트레이딩 시그널 평가 프롬프트를 개선하라.
    현재 WR: {best_wr:.0f}%
    현재 프롬프트: {base_prompt[:500]}
    
    개선 방향: 더 정확한 contrarian 신호 판별
    응답: 개선된 프롬프트 전체 텍스트만
    """
    new_prompt_text = _call_claude(...)
    self._save_prompt(new_vid, new_prompt_text)
```

**공수**: 중 (3~4시간)
**효과**: 실제 프롬프트 진화 가능

---

### 🟡 개선 3: ENTRY_JUDGE 프롬프트 — size_modifier 룰 충돌

```python
# prompts.py ENTRY_JUDGE에서:
# CONFIDENCE GUIDELINES:
#   3-4: approve with small size (0.5-0.7)
#   7-10: approve with full size (1.0-2.0)
#
# REGIME CONTEXT:
#   CRISIS: size_modifier 1.0-2.0

# 문제: confidence=3 이고 CRISIS면?
#   → CONFIDENCE RULE: 0.5~0.7
#   → REGIME RULE: 1.0~2.0
#   → LLM이 어느 룰 따를지 불명확

# 수정: REGIME이 CONFIDENCE보다 우선 명시
ENTRY_JUDGE = BURRY_PERSONA + """
...
PRIORITY: REGIME > CONFIDENCE for size_modifier.
In CRISIS/RISK_OFF: size_modifier minimum 1.0 regardless of confidence.
...
"""
```

**파일**: `invasion/ai/prompts.py`
**공수**: 소 (30분)

---

### 🟡 개선 4: provider_summary 포맷 명세화

```python
# 현재
provider_summary=str(ctx.provider_scores)[:200]
# → {'sentiment': 72.3, 'funding': 45.1, ...} 딕셔너리 덤프

# LLM이 어떤 값이 높은 게 좋은지 모름

# 개선: 구조화된 포맷
def _format_provider_scores(scores: dict) -> str:
    parts = []
    for name, score in sorted(scores.items(), key=lambda x: -x[1]):
        direction = "bullish" if score > 60 else "bearish" if score < 40 else "neutral"
        parts.append(f"{name}={score:.0f}({direction})")
    return " | ".join(parts)

# 결과: "taker=83(bullish) | sentiment=72(bullish) | funding=45(neutral)"
```

**파일**: `invasion/ai/live.py` LiveSignalAugmenter.augment()
**공수**: 소 (1시간)

---

### 🟢 개선 5: context_builder.py — sector 필드 이미 준비됨

`instrument_profiles`에서 sector 읽는 코드 이미 있음:
```python
ctx.sector = p.get("sector", "")
```

근데 `_format()` 메서드에서 sector를 AI 컨텍스트에 포함 안 함.
**Instrument Enricher (06번)** 붙이면 sector 데이터 채워지고,
아래 한 줄만 추가하면 바로 활용:

```python
# _format() 메서드에 추가
if ctx.sector:
    lines[0] += f" | {ctx.sector}"
# 결과: "INSTRUMENT: BTC-USDT-SWAP | SWAP/CRY | OKX | Layer 1"
```

---

### 🟢 개선 6: PORTFOLIO_REVIEW — BURRY_PERSONA 누락

```python
# 현재
PORTFOLIO_REVIEW = """AGGRESSIVE CONTRARIAN portfolio analyst..."""

# EXIT_REVIEW, SIGNAL_AUGMENT, ENTRY_JUDGE는 BURRY_PERSONA 붙음
# PORTFOLIO_REVIEW, WS_PRICE_INTEL은 안 붙음 → 철학 불일치

# 수정
PORTFOLIO_REVIEW = BURRY_PERSONA + """AGGRESSIVE CONTRARIAN portfolio analyst..."""
WS_PRICE_INTEL = BURRY_PERSONA + """You analyze real-time price action..."""
```

---

## 구현 순서

### Phase A — 버그 픽스 (즉시, 30분)
```
[ ] feedback.py: locals() 버그 수정
[ ] prompts.py: ENTRY_JUDGE size_modifier 룰 충돌 수정
[ ] prompts.py: PORTFOLIO_REVIEW + WS_PRICE_INTEL BURRY_PERSONA 추가
```

### Phase B — JSON 파싱 안정화 (1~2시간)
```
[ ] pip install json-repair
[ ] live.py: _extract_json() 헬퍼 추출
[ ] live.py: _call_gemini / _call_claude 중복 제거
[ ] 검증: _fallback_text_parse 호출 빈도 로그로 확인
```

### Phase C — 컨텍스트 품질 향상 (1~2시간)
```
[ ] live.py: _format_provider_scores() 추가 → SIGNAL_AUGMENT 포맷 개선
[ ] context_builder.py: sector 필드 _format()에 포함
[ ] 06_INSTRUMENT_ENRICHER 완료 후 sector 데이터 자동 활용
```

### Phase D — Prompt Evolution 실제 구현 (장기)
```
[ ] data/prompts/ 폴더에 프롬프트 텍스트 파일로 분리
[ ] prompt_evolver.mutate()에서 Claude로 실제 변이 생성
[ ] live.py에서 파일 기반 프롬프트 로드
```

---

## 외부 레포 참고 결론

| 레포 | 판단 | 이유 |
|------|------|------|
| TradingAgents | ❌ 직접 통합 불가 | LangGraph 기반, 주식 분석용, 실시간 아님 |
| FinMem | ❌ | 연구용, 레이어드 메모리 우리 구조와 충돌 |
| AgenticTrading | ❌ | DAG 기반, 너무 복잡 |
| **json-repair** | ✅ **바로 적용** | pip 한 줄, _fallback_text_parse 대체 |

> 우리 AI 레이어가 저 레포들보다 실전 설계면에서 더 정교함.
> 추가할 것보다 현재 코드 개선이 ROI 더 높음.

---

## 검증

```bash
# json-repair 적용 후 파싱 실패율 확인
grep "fallback_text_parse\|no parseable JSON" data/invasion.log | wc -l

# feedback 버그 수정 후 AI Selector 트래킹 확인
grep "AISelector" data/invasion.log | tail -20

# provider_summary 포맷 개선 확인
grep "SigAug:" data/invasion.log | head -5
```
