"""Tier write-authority linter — CI smoke.

MVP: existing violations may be numerous; we only assert the linter runs
cleanly and returns a list. Strict `len == 0` enforcement is a future step.
"""
from invasion.ingest._write_lint import check_violations, scan_writes, report


def test_scan_writes_returns_dict():
    result = scan_writes("invasion")
    assert isinstance(result, dict)


def test_tier_write_hygiene_report_only():
    v = check_violations("invasion")
    assert v is not None
    assert isinstance(v, list)
    print(f"[info] {len(v)} potential tier write violations (report only).")


def test_report_string():
    out = report("invasion")
    assert isinstance(out, str)
    assert out.startswith("[LINT_OK]") or out.startswith("[LINT_WARN]")
