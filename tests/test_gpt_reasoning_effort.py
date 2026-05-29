"""gpt-5.5 dropped reasoning_effort='minimal' (forensic 2026-05-29 P0).

gpt-5.5 (P1 gates G6/G7/G8) rejected every call with 400
"'reasoning_effort' does not support 'minimal'" → 99.97% silent fallback to
Python. gpt-5-mini (P0 gates) still supports 'minimal'. The resolver must map
minimal→none for the gpt-5.5 family only, and preserve explicit overrides.
"""

from __future__ import annotations

from polaris.core.pipeline.agents._gpt_client import _resolve_reasoning_effort


def test_gpt55_default_minimal_maps_to_none() -> None:
    assert _resolve_reasoning_effort("gpt-5.5", None) == "none"


def test_gpt55_explicit_minimal_maps_to_none() -> None:
    assert _resolve_reasoning_effort("gpt-5.5", "minimal") == "none"


def test_gpt5_mini_keeps_minimal() -> None:
    assert _resolve_reasoning_effort("gpt-5-mini", None) == "minimal"
    assert _resolve_reasoning_effort("gpt-5-mini", "minimal") == "minimal"


def test_explicit_override_preserved() -> None:
    # An explicit non-minimal effort is never rewritten.
    assert _resolve_reasoning_effort("gpt-5.5", "low") == "low"
    assert _resolve_reasoning_effort("gpt-5.5", "high") == "high"
    assert _resolve_reasoning_effort("gpt-5-mini", "medium") == "medium"


def test_gpt55_case_insensitive() -> None:
    assert _resolve_reasoning_effort("GPT-5.5", None) == "none"
