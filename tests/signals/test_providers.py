"""F-N7 PR5 — SignalResult contract tests (3).

Covers `invasion/signals/base.py::SignalResult`:
  8. test_signal_result_auto_direction     — direction auto-set from score
  9. test_signal_result_ttl_decay_linear   — decay_factor linear over ttl
 10. test_signal_result_clamped_100        — score clamped to [-100, +100]
"""
from __future__ import annotations

import time

import pytest

from invasion.signals.base import SignalResult


# ── 8. auto-direction ────────────────────────────────────────────────


def test_signal_result_auto_direction():
    """__post_init__ sets direction: score>10 → long, <-10 → short, else neutral."""
    # Long zone
    r_long = SignalResult(name="t", score=42.0, confidence=0.8)
    assert r_long.direction == "long"

    # Short zone
    r_short = SignalResult(name="t", score=-55.0, confidence=0.8)
    assert r_short.direction == "short"

    # Neutral band: |score| <= 10
    r_neu_pos = SignalResult(name="t", score=10.0, confidence=0.5)
    assert r_neu_pos.direction == "neutral"
    r_neu_neg = SignalResult(name="t", score=-10.0, confidence=0.5)
    assert r_neu_neg.direction == "neutral"
    r_neu_zero = SignalResult(name="t", score=0.0, confidence=0.5)
    assert r_neu_zero.direction == "neutral"

    # Boundary just above / just below 10
    assert SignalResult(name="t", score=10.1).direction == "long"
    assert SignalResult(name="t", score=-10.1).direction == "short"


# ── 9. Linear TTL decay ──────────────────────────────────────────────


def test_signal_result_ttl_decay_linear(monkeypatch):
    """decay_factor = 1 - age/ttl, clamped at 0.0 past expiry."""
    t0 = 1_700_000_000.0
    clock = [t0]
    monkeypatch.setattr("invasion.signals.base.time.time", lambda: clock[0])

    r = SignalResult(name="t", score=60.0, confidence=1.0, ttl=100.0)
    # Fresh
    assert r.decay_factor == pytest.approx(1.0)
    assert r.decayed_score == pytest.approx(60.0)
    assert r.is_expired is False

    # 25% through TTL
    clock[0] = t0 + 25
    assert r.decay_factor == pytest.approx(0.75, rel=1e-6)
    assert r.decayed_score == pytest.approx(45.0, rel=1e-6)

    # 50% through TTL
    clock[0] = t0 + 50
    assert r.decay_factor == pytest.approx(0.5, rel=1e-6)
    assert r.decayed_score == pytest.approx(30.0, rel=1e-6)

    # Past expiry: floor at 0
    clock[0] = t0 + 150
    assert r.decay_factor == 0.0
    assert r.decayed_score == 0.0
    assert r.is_expired is True

    # ttl <= 0 → no decay (factor stays 1.0)
    r0 = SignalResult(name="t", score=50.0, ttl=0)
    assert r0.decay_factor == 1.0


# ── 10. Score clamped to [-100, 100] ─────────────────────────────────


def test_signal_result_clamped_100():
    """__post_init__ clamps score to [-100, +100] and confidence to [0, 1]."""
    r_hi = SignalResult(name="t", score=250.0, confidence=5.0)
    assert r_hi.score == 100.0
    assert r_hi.confidence == 1.0
    assert r_hi.direction == "long"

    r_lo = SignalResult(name="t", score=-999.0, confidence=-0.5)
    assert r_lo.score == -100.0
    assert r_lo.confidence == 0.0
    assert r_lo.direction == "short"

    # Boundary values pass through unchanged
    r_b = SignalResult(name="t", score=100.0, confidence=1.0)
    assert r_b.score == 100.0
    assert r_b.confidence == 1.0
