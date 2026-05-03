# AI 모듈 참고 자료 (2026-04-12 리서치)

Jin 지시 "참고할만한 것들" 리서치 결과. AI 모듈 설계·개선 시 첫 참조.

## Public GitHub Repos (LLM + Trading)

| Repo | URL | Stars | 핵심 |
|---|---|---|---|
| TradingAgents | https://github.com/TauricResearch/TradingAgents | 49.8k | LangGraph 7-role + Bull/Bear debate |
| ai-hedge-fund | https://github.com/virattt/ai-hedge-fund | 51.9k | 19 페르소나 agents (Buffett/Wood/Burry) |
| FinGPT | https://github.com/AI4Finance-Foundation/FinGPT | 대규모 | 금융 sentiment LoRA fine-tune |
| FinRL | https://github.com/AI4Finance-Foundation/FinRL | 대규모 | RL backtest 환경 |
| FinMem | https://github.com/pipiku915/FinMem-LLM-StockTrading | 중 | Layered memory + reflection |
| Trading-R1 | https://github.com/TauricResearch/Trading-R1 | 신규 | SFT + RL volatility-adjusted |
| ai-hedge-fund-crypto | https://github.com/51bitquant/ai-hedge-fund-crypto | 중소 | ai-hedge-fund + crypto 포크 |

## 학술 논문 (2024-2025)

| 논문 | arXiv | 핵심 |
|---|---|---|
| TradingAgents Multi-Agent Framework | 2412.20138 | Bull/Bear debate → max DD <2% |
| Trading-R1 RL Reasoning | 2509.11420 | volatility-adjusted thesis |
| FinCon Verbal Reinforcement | 2407.06567 (NeurIPS'24) | profit vs loss episode CVRF |
| FinMem Layered Memory | 2311.13743 (ICLR'24) | shallow/intermediate/deep |
| FinCoT Expert Reasoning | 2506.16123 (ACL'25) | 금융 도메인 CoT 템플릿 |
| LLM Agents ≠ Human Traders | 2502.15800 | LLM 단독 불가, judgment layer만 |
| LLM Finance Hallucination | 2311.15548 | 체계적 측정 + 완화 |
| Adversarial News Trading | 2601.13082 | headline manipulation 취약 |

## Anthropic 공식

- Prompt Caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (90% 비용 절감)
- XML Tags: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
- Long Context Tips: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/long-context-tips (quote-then-reason)
- Structured Outputs (2025-11): beta header `structured-outputs-2025-11-13`
- Output Consistency: https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/increase-consistency (temperature=0 for finance)
- Claude Cookbooks: https://github.com/anthropics/claude-cookbooks

## 업계 사례
- Two Sigma: https://www.twosigma.com/articles/how-to-get-the-most-from-llms/ (LLM 보조 + 인간 검증)
- Bridgewater: AWS Guardrails로 hallucination 75% 차단
- Pantera Capital $200M AI-adjacent crypto 투자 (2025~)
- CoinDesk 2025-12: GPT-5/Gemini Pro/DeepSeek 모두 특화 AI 봇 패배

## 핵심 결론
1. **LLM = judgment layer**, main engine 금지 (우리 구조 ✅)
2. **Prompt Caching + Structured Outputs + XML** 3종은 수일 내 적용 (비용·정확도 즉효)
3. **Bull/Bear debate + Layered Memory + CVRF** 3종은 재설계 로드맵 핵심
4. 대규모 fine-tune (FinGPT/Trading-R1)은 비용 대비 효과 낮음 — prompt 기반 유지

## 관련 메모리
- `project_ai_module_audit_20260412.md` — 내부 감사 6/10 상세
- `feedback_ai_controller_design.md` — AI 호출 데이터 드리븐 원칙
- `feedback_ai_collaboration.md` — Claude 단독 + 전략만 /debate
