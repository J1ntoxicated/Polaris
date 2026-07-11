"""Source-level wiring lint — the DFII10 gold-conviction tag stamp (frontgate
item #5) reaches the live ``_run_tick`` persist call, and that
``persist_emitted_signal`` is never invoked with a hard-coded non-None tags
default for a non-GOLD symbol (byte-identical everywhere else).

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


def test_dfii10_tag_computed_before_persist_call() -> None:
    tag_idx = _SRC.index("persist_tags = (")
    persist_idx = _SRC.index("persist_emitted_signal(")
    assert tag_idx < persist_idx


def test_persist_call_passes_tags_kwarg() -> None:
    persist_idx = _SRC.index("persist_emitted_signal(")
    close_idx = _SRC.index("\n                )", persist_idx)
    segment = _SRC[persist_idx:close_idx]
    assert "tags=persist_tags" in segment


def test_dfii10_tag_gated_to_gold_symbols_and_non_none_altdata() -> None:
    tag_idx = _SRC.index("persist_tags = (")
    close_idx = _SRC.index("else None", tag_idx)
    segment = _SRC[tag_idx:close_idx]
    assert '{"GOLD", "XAUUSD"}' in segment
    assert "mv.altdata.dfii10 is not None" in segment


def test_dfii10_tag_never_touches_sizing_or_gating_fields() -> None:
    """Behavior-0 structural check: the tag-computation block must not
    reference sizing_hint/strength/T4 scalars."""
    tag_idx = _SRC.index("persist_tags = (")
    close_idx = _SRC.index("else None", tag_idx) + len("else None")
    segment = _SRC[tag_idx:close_idx]
    assert "sizing_hint" not in segment
