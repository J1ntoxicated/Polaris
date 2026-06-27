"""④ #12 technical store — judge consumption wiring (evidence block).

DEMO/PAPER only. EVIDENCE-ONLY/advisory (flow_not_block): the full technicals
line is read-only context for the judge prompt — it never blocks/kills/sizes.

Verifies the read side of the store: the judge's ``_evidence_block`` renders the
rich technical set (rsi/adx/donchian) when the payload carries a ``technicals``
dict, in ADDITION to the legacy 3-metric ``baseline`` line. When absent the block
is byte-identical to before (no regression).
"""

from __future__ import annotations

from polaris.core.pipeline.agents.ai_judge import _evidence_block


def test_full_technicals_rendered_when_present() -> None:
    payload = {
        "regime": "bull_trend",
        "baseline": {"atr": {"p50": 0.01}},
        "technicals": {
            "rsi_14": 58.0,
            "adx_14": 27.0,
            "donchian_high_40": 109.0,
            "source_bar_ts": 1900,
        },
    }
    block = _evidence_block(payload)
    # Legacy baseline line still present (additive, not a replacement).
    assert "baseline atr/size/volume" in block
    # New full-technicals line surfaces the rich indicators to the judge.
    assert "full technicals" in block
    assert "rsi_14" in block
    assert "adx_14" in block
    assert "donchian_high_40" in block


def test_no_technicals_line_when_absent() -> None:
    """No ``technicals`` key → the block omits the line (byte-identical legacy)."""
    payload = {"regime": "chop", "baseline": {"atr": {"p50": 0.01}}}
    block = _evidence_block(payload)
    assert "full technicals" not in block
    assert "baseline atr/size/volume" in block


def test_empty_technicals_omitted() -> None:
    """An empty technicals dict renders no line (graceful n/a)."""
    payload = {"regime": "chop", "technicals": {}}
    block = _evidence_block(payload)
    assert "full technicals" not in block
