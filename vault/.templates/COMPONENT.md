---
entity_type: component
entity_id: <module_name>
auto: false                   # 자동 생성은 vault/generated/components/
last_modified: YYYY-MM-DD
expires: never                # 컴포넌트 노트는 무한 (코드 폐기 시 archived)
editable: true                # code-implementer + Jin
back_links: ["[[<관련 ADR/INSIGHT/원칙>]]"]
mode: dev
reviewed_by: codex            # 모든 코드 변경은 codex 외부 리뷰 의무
pure: true|false              # P6 Pure Core / Imperative Shell 분류
tags: [type/component, status/active, scope/spot, mode/dev, reviewed-by/codex, polaris]
code_path: <path/to/module.py>
test_path: <path/to/test_module.py>
---

# Component — <module_name>

> 한 문장 책임.

## Responsibilities

- <책임 1>
- <책임 2>

## Public Interface

```python
def public_function(arg: T) -> R:
    """..."""
```

## Pure / Shell 분류 (P6)

- **Pure**: <pure function 목록 — no I/O>
- **Shell**: <I/O wrapping 함수 목록>

## Dependencies

- 내부: [[40_components/<dep>]]
- 외부 라이브러리: <list>

## Test Coverage

- Unit tests: <count>
- Property-based tests: <count> (P7 적용 영역)
- Integration tests: <count>

## Codex Review History

- 1차 (YYYY-MM-DD): <요약>
- 2차 (YYYY-MM-DD): <요약>

## Related

- ADR: [[ADR-NNN]]
- INSIGHT: [[INSIGHT-NNN]]
