# Debate — Auto-Apply 상세

[debate.md](debate.md)의 합의 후 적용 절차.

## Auto-Apply Steps
합의 형성 후 옵션:

### "Apply" → live_config.json 자동 적용
1. Consensus 파라미터 값 parse
2. 현재 config가 예상 "before" 값과 일치하는지 확인 (mismatch 시 경고)
3. `param_registry.set(name, value, "debate_consensus")` 적용
4. `# N-AI debate (date)` 코멘트 태그 추가
5. 검증: `python3 -c "import invasion.main"`
6. Chain impact 체크 (executor.py, defense.py 등)
7. (Dev smoke 직접) 트리거

### "Another round"
반대 논거로 재토론

### "Hold"
저장만, 적용 안함

## Result Storage
```json
data/debates/debate_YYYYMMDD_HHmmss.json
{
  "question": "...",
  "context": {...},
  "performance": {...},
  "responses": {"gpt": "...", "gemini": "...", "sonnet": "...", "opus": "..."},
  "consensus": "3/3",
  "recommendation": {"param": "value"},
  "applied": true
}
```

## Safety
- `kill_switch_pct` / `max_drawdown_pct` 완화는 Jin 명시 승인 필수
- 모든 변경에 Rollback 코멘트 자동 생성
- Post-apply 검증 스크립트 필수

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
