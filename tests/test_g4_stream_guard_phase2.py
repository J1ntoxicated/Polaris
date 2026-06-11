"""Gate architecture Phase 2 — per-stream G4 pre-entry guard.

DEMO/PAPER ONLY. Aggressive bias preserved: fast-path is an EFFICIENCY /
ELIGIBILITY decision (skip the slow GPT watcher when the entry is clearly
clean), NOT an entry throttle. A not-fast-path-eligible signal always goes to
the SLOW GPT path (which PROCEEDs or KILLs) — eligibility NEVER blocks an entry
outright.

Phase 2 makes fast-path eligibility CORRECT per stream via the
``StreamProfile.guard_hooks`` seam (resolved once, read in G4):
- A (crypto/spot): spread-vs-baseline + listing_age — BYTE-IDENTICAL parity.
- B (cfd): session state (market open / rollover) drives eligibility.
- C (equity): RTH boundary + PDT state + opening gap drive eligibility.

No 9-stack touched, no rejection-keyword, no new entry block.
"""

from __future__ import annotations

import time

import pytest

from polaris.core.pipeline.agents.pre_entry_watcher import (
    FastPathContext,
    is_fast_path_eligible,
    pre_entry_watcher_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.pipeline.payload_builder import build_watcher_payload
from polaris.core.streams import resolve_stream_profile

# Reference UTC timestamps (computed against the deterministic clocks).
RTH_FRIDAY = 1_780_077_600  # 2026-05-29 Fri 14:00 ET -> RTH
EQUITY_CLOSED = 1_780_034_400  # 2026-05-29 Fri 02:00 ET -> closed (pre 04:00)
WEEKEND = 1_780_142_400  # 2026-05-30 Sat 12:00 UTC -> FX + equity closed
FX_OPEN_CALM = 1_780_048_800  # 2026-05-29 Fri 10:00 UTC -> FX open, not shock


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

A_PROF = resolve_stream_profile("okx")
B_PROF = resolve_stream_profile("capital")
C_PROF = resolve_stream_profile("alpaca")


def _clean_spread_fp(**over: object) -> FastPathContext:
    """A microstructure-clean FastPathContext (shared cleanliness satisfied)."""
    base = dict(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    base.update(over)
    return FastPathContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# guard_hooks seam populated (Phase 2) — read in G4
# ---------------------------------------------------------------------------


def test_guard_hooks_populated_per_product_class() -> None:
    assert A_PROF.guard_hooks  # non-empty now
    assert B_PROF.guard_hooks
    assert C_PROF.guard_hooks
    # distinct guard per product_class
    assert A_PROF.guard_hooks != B_PROF.guard_hooks
    assert B_PROF.guard_hooks != C_PROF.guard_hooks


# ---------------------------------------------------------------------------
# A (crypto/spot) — BYTE-IDENTICAL parity
# ---------------------------------------------------------------------------


def test_A_no_profile_is_legacy_crypto_logic() -> None:
    """No profile -> exact legacy crypto eligibility (parity baseline)."""
    fp = _clean_spread_fp()
    assert is_fast_path_eligible(fp) is True
    # listing too young -> not eligible (crypto-specific)
    assert is_fast_path_eligible(_clean_spread_fp(listing_age_hours=12.0)) is False
    # shock window -> not eligible
    assert is_fast_path_eligible(
        _clean_spread_fp(session_open_shock_window=True)
    ) is False
    # wide spread -> not eligible
    assert is_fast_path_eligible(_clean_spread_fp(spread_bps=4.5)) is False


def test_A_spot_profile_matches_no_profile_byte_identical() -> None:
    """Threading the spot (okx) profile must NOT change any A decision."""
    cases = [
        _clean_spread_fp(),
        _clean_spread_fp(listing_age_hours=12.0),
        _clean_spread_fp(session_open_shock_window=True),
        _clean_spread_fp(spread_bps=4.5),
        _clean_spread_fp(signal_strength=1.10),
        _clean_spread_fp(cell_quartile="mid"),
        _clean_spread_fp(recent_reject_in_6h=True),
        _clean_spread_fp(baseline_p50_spread_bps=0.0),
    ]
    for fp in cases:
        assert is_fast_path_eligible(fp, A_PROF) == is_fast_path_eligible(fp)


# ---------------------------------------------------------------------------
# B (cfd) — session state drives eligibility
# ---------------------------------------------------------------------------


def test_B_open_calm_session_fast_path_eligible() -> None:
    fp = _clean_spread_fp(
        cfd_market_open=True,
        cfd_rollover_window=False,
        listing_age_hours=0.0,  # listing age is meaningless for CFD; must not matter
    )
    assert is_fast_path_eligible(fp, B_PROF) is True


def test_B_market_closed_not_fast_path() -> None:
    fp = _clean_spread_fp(cfd_market_open=False, cfd_rollover_window=False)
    assert is_fast_path_eligible(fp, B_PROF) is False


def test_B_rollover_window_not_fast_path() -> None:
    fp = _clean_spread_fp(cfd_market_open=True, cfd_rollover_window=True)
    assert is_fast_path_eligible(fp, B_PROF) is False


def test_B_session_open_shock_not_fast_path() -> None:
    fp = _clean_spread_fp(
        cfd_market_open=True,
        cfd_rollover_window=False,
        session_open_shock_window=True,
    )
    assert is_fast_path_eligible(fp, B_PROF) is False


# ---------------------------------------------------------------------------
# C (equity) — RTH + PDT + opening gap drive eligibility
# ---------------------------------------------------------------------------


def test_C_rth_clean_fast_path_eligible() -> None:
    fp = _clean_spread_fp(
        equity_session_state="rth",
        opening_gap_window=False,
        daytrade_count=0,
        listing_age_hours=0.0,  # meaningless for equity; must not matter
    )
    assert is_fast_path_eligible(fp, C_PROF) is True


def test_C_outside_rth_not_fast_path() -> None:
    for state in ("closed", "pre_market", "after_hours"):
        fp = _clean_spread_fp(
            equity_session_state=state, opening_gap_window=False, daytrade_count=0
        )
        assert is_fast_path_eligible(fp, C_PROF) is False


def test_C_pdt_flagged_not_fast_path() -> None:
    fp = _clean_spread_fp(
        equity_session_state="rth", opening_gap_window=False, daytrade_count=3
    )
    assert is_fast_path_eligible(fp, C_PROF) is False


def test_C_opening_gap_window_not_fast_path() -> None:
    fp = _clean_spread_fp(
        equity_session_state="rth", opening_gap_window=True, daytrade_count=0
    )
    assert is_fast_path_eligible(fp, C_PROF) is False


# ---------------------------------------------------------------------------
# build_watcher_payload supplies per-stream guard inputs (item 3)
# ---------------------------------------------------------------------------


def test_watcher_payload_A_no_extra_keys_when_no_now_ts() -> None:
    """Without the new guard-input params, A payload is unchanged (no session
    keys) — parity with the legacy callers."""
    p = build_watcher_payload(
        spread_bps=1.5,
        baseline_p50_spread_bps=2.0,
        listing_age_hours=8760.0,
        stream_profile=A_PROF,
    )
    assert "cfd_market_open" not in p
    assert "equity_session_state" not in p


def test_watcher_payload_B_supplies_session_state() -> None:
    p = build_watcher_payload(
        spread_bps=1.5,
        baseline_p50_spread_bps=2.0,
        listing_age_hours=0.0,
        stream_profile=B_PROF,
        now_ts=WEEKEND,
    )
    assert p["cfd_market_open"] is False  # weekend -> closed


def test_watcher_payload_C_supplies_rth_pdt_gap() -> None:
    p = build_watcher_payload(
        spread_bps=1.5,
        baseline_p50_spread_bps=2.0,
        listing_age_hours=0.0,
        stream_profile=C_PROF,
        now_ts=EQUITY_CLOSED,
        daytrade_count=4,
    )
    assert p["equity_session_state"] == "closed"
    assert p["daytrade_count"] == 4

    p_rth = build_watcher_payload(
        spread_bps=1.5,
        baseline_p50_spread_bps=2.0,
        listing_age_hours=0.0,
        stream_profile=C_PROF,
        now_ts=RTH_FRIDAY,
        daytrade_count=0,
    )
    assert p_rth["equity_session_state"] == "rth"


# ---------------------------------------------------------------------------
# Gate integration — not-eligible -> SLOW GPT path, NEVER blocked outright
# ---------------------------------------------------------------------------


def _gate_ctx(payload: dict, *, venue: str, symbol: str, profile) -> GateContext:
    return GateContext(
        run_id="r",
        signal_id="s",
        position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue=venue,
        symbol=symbol,
        strategy_id="strat",
        payload=payload,
        started_ts=int(time.time()),
        state=SignalLifecycle.VALIDATED,
        stream_profile=profile,
    )


class _StubGPT:
    """Minimal OpenAI-shaped stub returning a fixed decision JSON."""

    def __init__(self, decision: str = "PROCEED") -> None:
        self._decision = decision
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                class _Block:
                    text = (
                        '{"decision": "' + outer._decision + '", "reason": "stub"}'
                    )

                class _Resp:
                    content = [_Block()]
                    usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

                return _Resp()

        self.messages = _Messages()


@pytest.mark.asyncio
async def test_B_closed_session_goes_to_slow_gpt_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B during closed session: NOT fast-path -> slow GPT PROCEED (not blocked)."""
    # W3 cutover adaptation (NOT a behavior change): pins the LEGACY GPT
    # path under POLARIS_AI_FREE=0; flag=1 is covered by test_ai_free_cutover.py.
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    payload: dict = {
        "validated_signal": {"strategy_id": "fx", "symbol": "XAUUSD",
                             "side": "long", "strength_scalar": 1.4},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=0.0,
            stream_profile=B_PROF,
            now_ts=WEEKEND,
        ),
        "cell_quartile": "top",
    }
    ctx = _gate_ctx(payload, venue="capital", symbol="XAUUSD", profile=B_PROF)
    res = await pre_entry_watcher_gate(ctx, client=_StubGPT("PROCEED"))
    # NOT fast-path (closed) -> slow GPT was consulted, decision = PROCEED
    assert res.model_used == "gpt"
    assert res.decision == GateDecision.PROCEED
    assert res.next_gate == GATE_ENTRY_SIZER


@pytest.mark.asyncio
async def test_C_outside_rth_goes_to_slow_gpt_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # W3 cutover adaptation (NOT a behavior change): pins the LEGACY GPT
    # path under POLARIS_AI_FREE=0; flag=1 is covered by test_ai_free_cutover.py.
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    payload: dict = {
        "validated_signal": {"strategy_id": "eq", "symbol": "AAPL",
                             "side": "long", "strength_scalar": 1.4},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=0.0,
            stream_profile=C_PROF,
            now_ts=EQUITY_CLOSED,
            daytrade_count=0,
        ),
        "cell_quartile": "top",
    }
    ctx = _gate_ctx(payload, venue="alpaca", symbol="AAPL", profile=C_PROF)
    res = await pre_entry_watcher_gate(ctx, client=_StubGPT("PROCEED"))
    assert res.model_used == "gpt"  # NOT fast-path -> slow GPT
    assert res.decision == GateDecision.PROCEED


@pytest.mark.asyncio
async def test_C_pdt_flagged_goes_to_slow_gpt_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # W3 cutover adaptation (NOT a behavior change): pins the LEGACY GPT
    # path under POLARIS_AI_FREE=0; flag=1 is covered by test_ai_free_cutover.py.
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    payload: dict = {
        "validated_signal": {"strategy_id": "eq", "symbol": "AAPL",
                             "side": "long", "strength_scalar": 1.4},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=0.0,
            stream_profile=C_PROF,
            now_ts=RTH_FRIDAY,
            daytrade_count=5,  # PDT-flagged
        ),
        "cell_quartile": "top",
    }
    ctx = _gate_ctx(payload, venue="alpaca", symbol="AAPL", profile=C_PROF)
    res = await pre_entry_watcher_gate(ctx, client=_StubGPT("PROCEED"))
    assert res.model_used == "gpt"  # PDT-flagged is NOT a block, just not-fast
    assert res.decision == GateDecision.PROCEED


@pytest.mark.asyncio
async def test_C_rth_clean_fast_path_skips_gpt() -> None:
    payload: dict = {
        "validated_signal": {"strategy_id": "eq", "symbol": "AAPL",
                             "side": "long", "strength_scalar": 1.4},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=0.0,
            stream_profile=C_PROF,
            now_ts=RTH_FRIDAY,
            daytrade_count=0,
        ),
        "cell_quartile": "top",
    }
    ctx = _gate_ctx(payload, venue="alpaca", symbol="AAPL", profile=C_PROF)
    # client=None: if NOT fast-path it would KILL (no_gpt_client). Clean RTH must
    # fast-path PROCEED without any GPT.
    res = await pre_entry_watcher_gate(ctx)
    assert res.model_used == "python_fast_path"
    assert res.decision == GateDecision.PROCEED
    assert res.skipped is True


@pytest.mark.asyncio
async def test_B_open_calm_fast_path_skips_gpt() -> None:
    payload: dict = {
        "validated_signal": {"strategy_id": "fx", "symbol": "XAUUSD",
                             "side": "long", "strength_scalar": 1.4},
        **build_watcher_payload(
            spread_bps=1.0,
            baseline_p50_spread_bps=2.0,
            listing_age_hours=0.0,
            stream_profile=B_PROF,
            now_ts=FX_OPEN_CALM,
        ),
        "cell_quartile": "top",
    }
    ctx = _gate_ctx(payload, venue="capital", symbol="XAUUSD", profile=B_PROF)
    res = await pre_entry_watcher_gate(ctx)
    assert res.model_used == "python_fast_path"
    assert res.decision == GateDecision.PROCEED
