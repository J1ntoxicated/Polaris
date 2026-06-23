"""Regression guard: /debate must run GPT + Gemini concurrently and degrade
gracefully when one provider hangs/fails (the audit found run_debate.py had
regressed to Gemini-only with no fallback)."""
import importlib.util
import time
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / ".claude" / "run_debate.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_debate_mod", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rd():
    return _load()


def test_both_providers_emitted(rd):
    gpt, gem = rd.run_debate(
        openai_fn=lambda: "GPT-OK",
        gemini_fn=lambda: "GEMINI-OK",
    )
    assert gpt == "GPT-OK"
    assert gem == "GEMINI-OK"


def test_gemini_hang_still_emits_gpt(rd):
    def hang():
        time.sleep(5)
        return "should-not-reach"

    gpt, gem = rd.run_debate(
        openai_fn=lambda: "GPT-OK",
        gemini_fn=hang,
        timeout=0.2,
    )
    assert gpt == "GPT-OK"
    assert "Gemini UNAVAILABLE" in gem


def test_gpt_hang_still_emits_gemini(rd):
    def hang():
        time.sleep(5)
        return "should-not-reach"

    gpt, gem = rd.run_debate(
        openai_fn=hang,
        gemini_fn=lambda: "GEMINI-OK",
        timeout=0.2,
    )
    assert "OpenAI UNAVAILABLE" in gpt
    assert gem == "GEMINI-OK"


def test_concurrent_not_serial(rd):
    """Both calls run concurrently: two ~0.3s calls finish well under 0.6s."""
    def slow(tag):
        time.sleep(0.3)
        return tag

    start = time.monotonic()
    gpt, gem = rd.run_debate(
        openai_fn=lambda: slow("GPT"),
        gemini_fn=lambda: slow("GEM"),
        timeout=2.0,
    )
    elapsed = time.monotonic() - start
    assert gpt == "GPT" and gem == "GEM"
    assert elapsed < 0.55, f"ran serially: {elapsed:.2f}s"
