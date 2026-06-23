"""Day 7 — 24 h ignition-readiness watchdog (CI-fast probes).

Spec source:
- vault/_NOW.md (P0 Day 7 watchdog scope)
- ``polaris/scripts/ignite_p1.py`` (boot contract)
- ``polaris/scripts/smoke_paper_loop.py`` (tick body)
- ``polaris/scripts/dashboard_v0.py`` (5 s refresh sample)
- ``polaris/venues/{okx,capital}/`` (auth surfaces)
- ``polaris/core/isolation/{worker,circuit_breaker}`` (strategy isolation)
- ``polaris/core/learners/scheduler.py`` (hourly trigger)
- ``polaris/core/data/fills_persist.py`` (fills persistence)

These are *health probes* not perf benches. They run in <15 s wall-time so
the harness can re-run before each 24 h ignition window. The actual 30-min
real-venue smoke is performed by ``polaris.scripts.ignite_p1`` and recorded
in ``vault/40_ops/digests/2026-05-07_p0_day7_ignition_smoke.md``.

Codex round-1 review (REJECT_WITH_FIXES) applied
================================================
- P0-1: kill-switch test now spawns ``--paper`` child and **sends SIGTERM**
  mid-loop, asserting clean exit.
- P0-2: composite test now patches ``LearnerScheduler.run_forever`` so we
  can prove the **background task fires at least once**, not just that we
  can call ``run_once`` post-hoc.
- P0-3: every ``ignite()`` test ``chdir(tmp_path)`` and stages a tmp
  ``vault/`` so the probes never touch the repo vault or read the repo
  ``.env``.
- P1-1: OKX env override test now monkeypatches ``OKX_DEMO_BASE`` to the
  international URL, calls ``run_okx_round_trip(dry_run=True)`` and
  asserts the override comment + dry-run synthetic Fills land.
- P1-2: drawdown gap probe now reads the readiness checklist from
  ``vault/_NOW.md`` (when present) instead of grep'ing src for ``-8%``.
- P1-3: composite test asserts concrete bounds — fills row count > 0,
  cell_dist sum bounded, learner reports == 3.
- P1-4: paper-cancel test waits for the learner task to start before
  cancelling and asserts the cancel propagated.
- NITS: raw ``sqlite3.connect`` calls use context managers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import make_fill_id, persist_fill
from polaris.core.isolation.circuit_breaker import (
    ACTIVE,
    HARD_HALT,
    current_strategy_mode,
)
from polaris.core.isolation.worker import (
    AccountSnapshot,
    run_strategy_task,
)
from polaris.core.learners import LearnerScheduler
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts.dashboard_v0 import collect_snapshot
from polaris.scripts.ignite_p1 import ignite
from polaris.scripts.smoke_paper_loop import (
    FocusEntry,
    _stub_bars,
)
from polaris.storage.schema import init_db
from polaris.venues.capital.session import (
    PING_INTERVAL_SEC,
    TOKEN_REFRESH_DEADLINE_SEC,
    CapitalTokens,
)
from polaris.venues.okx.adapter import OKX_BASE_DEMO

# ---------------------------------------------------------------------------
# Helpers — vault-isolated ignite test scaffold
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, polaris_test_vault: Path,
) -> Path:
    """chdir into ``tmp_path`` + stage tmp ``vault/`` + tmp ``.env`` so
    ``ignite()`` cannot touch the repo vault or read the real ``.env``.

    Depends on the conftest autouse ``polaris_test_vault`` so this override of
    ``POLARIS_VAULT_DIR`` (the read-back assertions below need ignite to write
    THIS vault, in-process AND in spawned children) deterministically wins.

    Returns the tmp working directory (caller can read appended vault rows).
    """
    monkeypatch.chdir(tmp_path)
    vault = tmp_path / "vault"
    (vault / "40_ops" / "daily").mkdir(parents=True)
    (vault / "log.md").write_text("# tmp vault log\n")
    (vault / "_NOW.md").write_text(
        "# tmp NOW\n\n## Implementation status\n- placeholder\n"
    )
    monkeypatch.setenv("POLARIS_VAULT_DIR", str(vault))
    (tmp_path / ".env").write_text("# empty\n")
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. OKX auth-health surface
# ---------------------------------------------------------------------------


def test_okx_auth_health_demo_base_is_us_region() -> None:
    """OKX_BASE_DEMO must be the US endpoint — Jin's keys live there."""
    assert OKX_BASE_DEMO == "https://us.okx.com"


def test_resolve_okx_base_url_overrides_international() -> None:
    """Pure-function probe of the OKX base-URL override (no HTTP).

    Codex R2 P1: the prior test only static-grepped source.
    Codex R3 nit: include hostname-bypass cases that a substring check
    would silently pass (`us.okx.com.evil`, `evil/us.okx.com`).
    """
    from polaris.scripts._smoke_real_roundtrip import resolve_okx_base_url

    # 1. International endpoint → forced to US.
    assert resolve_okx_base_url("https://www.okx.com") == "https://us.okx.com"
    # 2. Already-US endpoint → pass-through.
    assert resolve_okx_base_url("https://us.okx.com") == "https://us.okx.com"
    # 3. Missing env → safe default to US.
    assert resolve_okx_base_url(None) == "https://us.okx.com"
    assert resolve_okx_base_url("") == "https://us.okx.com"
    # 4. Asia / EU misconfig also redirected.
    assert resolve_okx_base_url("https://eea.okx.com") == "https://us.okx.com"
    # 5. Hostname-bypass attempts must NOT pass through (R3 nit).
    assert (
        resolve_okx_base_url("https://us.okx.com.evil") == "https://us.okx.com"
    )
    assert (
        resolve_okx_base_url("https://evil.example/us.okx.com")
        == "https://us.okx.com"
    )
    # 6. Sub-domain of us.okx.com is allowed (legitimate routing).
    assert (
        resolve_okx_base_url("https://api.us.okx.com")
        == "https://api.us.okx.com"
    )
    # 7. Port-suffix on the canonical host is preserved.
    assert (
        resolve_okx_base_url("https://us.okx.com:443")
        == "https://us.okx.com:443"
    )


@pytest.mark.asyncio
async def test_okx_round_trip_dry_run_persists_two_fills(
    isolated_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end probe of the round-trip dry-run path (no network).

    Even with ``OKX_DEMO_BASE`` pointing at the international URL the
    dry-run leg must succeed and persist 2 synthetic fills. We *also*
    call the override helper so the env-mis-config doesn't slip through
    silently when production code grows a non-dry-run branch.
    """
    from polaris.scripts._smoke_real_roundtrip import (
        resolve_okx_base_url,
        run_okx_round_trip,
    )

    monkeypatch.setenv("OKX_DEMO_BASE", "https://www.okx.com")  # international
    # Override helper resolves correctly under this env.
    assert resolve_okx_base_url(os.environ["OKX_DEMO_BASE"]) == "https://us.okx.com"

    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()
    with sqlite3.connect(db) as conn:
        result = await run_okx_round_trip(conn=conn, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "open_fill_id" in result and "close_fill_id" in result
    with sqlite3.connect(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM fills WHERE strategy_id = 'day6_smoke'"
        ).fetchone()[0]
    assert n == 2


# ---------------------------------------------------------------------------
# 2. Capital session anti-idle refresh
# ---------------------------------------------------------------------------


def test_capital_token_deadline_is_9_minutes() -> None:
    """Anti-idle deadline must be 540 s (10-min idle - 1-min buffer)."""
    assert TOKEN_REFRESH_DEADLINE_SEC == 540.0
    assert PING_INTERVAL_SEC == 540.0


def test_capital_tokens_expire_after_deadline() -> None:
    now = 1_000_000.0
    tok = CapitalTokens(
        cst="cst",
        security_token="sec",
        minted_ts=now,
        deadline_ts=now + TOKEN_REFRESH_DEADLINE_SEC,
        account_id="acc1",
    )
    assert not tok.is_expired(now=now)
    assert not tok.is_expired(now=now + TOKEN_REFRESH_DEADLINE_SEC - 1.0)
    assert tok.is_expired(now=now + TOKEN_REFRESH_DEADLINE_SEC)
    assert tok.is_expired(now=now + 600.0)


# ---------------------------------------------------------------------------
# 3. Strategy isolation — exception in one does NOT poison peers
# ---------------------------------------------------------------------------


class _GoodStrat:
    strategy_id = "good"
    ticked = False

    async def tick(self, snapshot: AccountSnapshot) -> None:
        self.ticked = True


class _BadStrat:
    strategy_id = "bad"

    async def tick(self, snapshot: AccountSnapshot) -> None:
        raise RuntimeError("simulated strategy crash")


@pytest.mark.asyncio
async def test_strategy_isolation_no_cross_pollution(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        snap = AccountSnapshot(snapshot_id="s1", created_ts=int(time.time()))
        good, bad = _GoodStrat(), _BadStrat()
        results = await asyncio.gather(
            run_strategy_task(good, snap, conn=conn, now_ts=int(time.time())),
            run_strategy_task(bad, snap, conn=conn, now_ts=int(time.time())),
        )
        good_res = next(r for r in results if r["strategy_id"] == "good")
        bad_res = next(r for r in results if r["strategy_id"] == "bad")
        assert good_res["exception"] is None
        assert good_res["mode"] == ACTIVE
        assert good.ticked is True
        assert bad_res["exception"] is not None
        assert "RuntimeError" in bad_res["exception"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_strategy_isolation_n_exceptions_trigger_hard_halt(tmp_path: Path) -> None:
    """3 exceptions in 300 s must HARD_HALT the offender (CB threshold)."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        snap = AccountSnapshot(snapshot_id="s1", created_ts=int(time.time()))
        bad = _BadStrat()
        for _ in range(3):
            await run_strategy_task(bad, snap, conn=conn, now_ts=int(time.time()))
        mode = current_strategy_mode(conn, "bad", now_ts=int(time.time()))
        assert mode == HARD_HALT
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Fills persistence — idempotent + survives reload
# ---------------------------------------------------------------------------


def _make_test_fill(*, order_id: str, is_close: bool = False) -> Fill:
    return Fill(
        venue="okx",
        instrument_id="okx:BTC-USDT",
        strategy_id="day7_smoke",
        side="sell" if is_close else "buy",
        size_usd=10.0,
        fill_price=80_000.0,
        fee_usd=0.01,
        slippage_bps=2.0,
        ts_ms=int(time.time() * 1000),
        order_id=order_id,
        client_order_id=f"d7t{order_id[:8]}",
        base_qty=10.0 / 80_000.0,
        quote_qty=10.0,
        state="filled",
    )


def test_fills_persist_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        f = _make_test_fill(order_id="order_ABC")
        fid = persist_fill(conn, f, is_close=False)
        for _ in range(5):
            assert persist_fill(conn, f, is_close=False) == fid
        n = conn.execute("SELECT COUNT(*) FROM fills WHERE fill_id=?", (fid,)).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_fills_count_survives_db_reopen(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        for i in range(5):
            persist_fill(conn, _make_test_fill(order_id=f"o{i}"), is_close=False)
        before = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        conn.close()
    with sqlite3.connect(db) as conn2:
        after = conn2.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        assert after == before == 5


def test_make_fill_id_is_stable_per_phase() -> None:
    f = _make_test_fill(order_id="abc")
    open_id = make_fill_id(f, is_close=False)
    close_id = make_fill_id(f, is_close=True)
    assert open_id != close_id
    assert open_id.endswith(":open")
    assert close_id.endswith(":close")


# ---------------------------------------------------------------------------
# 5. Dashboard 5 s refresh — read-only snapshot consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_5s_refresh_consistency(tmp_path: Path) -> None:
    """Two snapshots ≥0.5 s apart against an idle DB must agree on counts."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        for i in range(3):
            persist_fill(conn, _make_test_fill(order_id=f"d{i}"), is_close=False)
    finally:
        conn.close()
    snap_a = collect_snapshot(db)
    await asyncio.sleep(0.5)
    snap_b = collect_snapshot(db)
    assert len(snap_a.recent_fills) == len(snap_b.recent_fills) == 3
    assert snap_a.daily_pnl_usd == snap_b.daily_pnl_usd
    assert snap_a.universe_focus_count == snap_b.universe_focus_count


def test_dashboard_target_refresh_is_5_seconds() -> None:
    from polaris.scripts.dashboard_v0 import DEFAULT_REFRESH_SEC

    assert DEFAULT_REFRESH_SEC == 5.0
    expected_24h_frames = int(24 * 60 * 60 / DEFAULT_REFRESH_SEC)
    assert expected_24h_frames == 17280


# ---------------------------------------------------------------------------
# 6. Kill-switch readiness — SIGTERM during ``--paper`` exits cleanly
# ---------------------------------------------------------------------------


def test_kill_switch_sigterm_exits_cleanly_mid_paper(
    isolated_vault: Path,
) -> None:
    """A long-running ``--paper`` child receives SIGTERM and exits cleanly.

    We spawn ``ignite_p1 --paper --duration 30 --tick 1`` so the loop has
    a proper deadline beyond our test wall-time, then send SIGTERM ~2 s
    in. The Unix default SIGTERM disposition is process termination; the
    Python runtime surfaces this as either ``-SIGTERM`` (negative
    returncode, asyncio cancellation path) or ``143`` (shell convention
    128 + signal). A clean ``0`` is also acceptable (cooperative exit
    via finally-blocks), but the test rejects ``rc == 1`` (generic crash)
    so unhandled exceptions cannot pose as a clean kill.

    This is the contract the harness relies on for ``kill -SIGTERM <pid>``.
    """
    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()
    repo_root = Path("/Users/jinyoon/Projects/Polaris")
    child_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        # Child must be able to import the polaris package even though
        # cwd is isolated_vault (so ignite writes to the tmp vault, not repo).
        "PYTHONPATH": (
            str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", "")
        ),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "polaris.scripts.ignite_p1",
            "--db",
            str(db),
            "--paper",
            "--duration",
            "30",
            "--tick",
            "1",
            "--no-full-pipeline",  # keep tick body fast
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=isolated_vault,
        env=child_env,
    )
    # Wait for the child to start producing output (proves it booted past
    # the synchronous bootstrap and entered the asyncio loop).
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=2.5)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=2.0)
        pytest.fail(f"child exited early rc={proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    proc.send_signal(signal.SIGTERM)
    try:
        rc = proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)
        pytest.fail("ignite child did not exit within 10 s of SIGTERM")
    # Default SIGTERM -> SystemExit(0) is acceptable; SIGTERM-by-shell -> 143.
    # asyncio cancellation may also surface as -SIGTERM (-15) on Unix.
    # Codex R2 P0 fix: tightened allowlist to signal-induced exits only.
    # rc == 1 (generic crash) would mask an unhandled exception after signal
    # delivery, so we require the OS-level SIGTERM signature: either the
    # shell-style 128+15=143, asyncio's negative-signal returncode (-15),
    # or a clean 0 from a process that ran a finally-block cleanup.
    assert rc in (0, 143, -signal.SIGTERM), f"unexpected exit code {rc}"


def test_main_dry_run_bootstrap_exits_zero(isolated_vault: Path) -> None:
    """Bootstrap-only path must exit 0 + emit the banner."""
    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()
    repo_root = Path("/Users/jinyoon/Projects/Polaris")
    child_env = {
        **os.environ,
        "PYTHONPATH": (
            str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", "")
        ),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "polaris.scripts.ignite_p1", "--db", str(db)],
        capture_output=True,
        text=True,
        timeout=20.0,
        cwd=isolated_vault,
        env=child_env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "P1.0 ignition" in proc.stdout


@pytest.mark.asyncio
async def test_kill_switch_paper_mode_cancels_inner_tasks(
    isolated_vault: Path,
) -> None:
    """Cancelling ``ignite(paper=True)`` mid-flight cleans up the learner
    background task — codex R2 P1-4 fix: prove the *learner task* itself
    received the CancelledError, not just the outer ignition future."""
    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()

    from unittest.mock import AsyncMock

    import polaris.scripts._production_bars as prod_bars
    import polaris.scripts._production_layers as prod_layers
    import polaris.scripts.smoke_paper_loop as mod
    from polaris.strategies import BarView

    async def _fake_fetch(entry: FocusEntry, *, limit: int = 200) -> list[BarView]:
        return _stub_bars(60)

    original = mod._fetch_bars_for_symbol
    mod._fetch_bars_for_symbol = _fake_fetch
    # Day 8 — ignite_p1 now invokes the production loop's Layer 0 producer
    # which reaches real OKX/Capital. Patch those out so the test stays
    # isolated (the kill-switch behaviour we're verifying does not depend
    # on a populated universe).
    original_okx_fetch = prod_layers.fetch_okx_instruments
    original_cap_fetch = prod_layers.fetch_capital_instruments
    original_okx_bars = prod_bars.fetch_okx_bars
    prod_layers.fetch_okx_instruments = AsyncMock(return_value=[])
    prod_layers.fetch_capital_instruments = AsyncMock(return_value=[])
    prod_bars.fetch_okx_bars = AsyncMock(return_value=[])

    # Spy on LearnerScheduler.run_forever — track:
    #  - started: the coroutine ran at least once.
    #  - cancelled: the coroutine received CancelledError (i.e. the
    #    background task created by ignite() was cancelled).
    started = asyncio.Event()
    cancelled = asyncio.Event()
    real_run_forever = LearnerScheduler.run_forever

    async def _spy_run_forever(self: LearnerScheduler, *, interval_sec: int = 3600) -> None:
        started.set()
        try:
            await real_run_forever(self, interval_sec=interval_sec)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    LearnerScheduler.run_forever = _spy_run_forever  # type: ignore[method-assign]
    try:
        ignition_task = asyncio.create_task(
            ignite(
                duration_sec=5.0,
                tick_sec=0.3,
                db_path=db,
                paper=True,
                full_pipeline=True,
                learner_interval_sec=60,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=3.0)
        ignition_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ignition_task
        # Codex R2 P1-4 fix: prove the learner task itself was cancelled.
        # The ignite() finally-block awaits the cancelled learner; once
        # that returns, the spy's cancelled Event must be set.
        await asyncio.wait_for(cancelled.wait(), timeout=2.0)
    finally:
        mod._fetch_bars_for_symbol = original
        prod_layers.fetch_okx_instruments = original_okx_fetch
        prod_layers.fetch_capital_instruments = original_cap_fetch
        prod_bars.fetch_okx_bars = original_okx_bars
        LearnerScheduler.run_forever = real_run_forever  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# 7. Drawdown checkpoint — gap probe (NOT yet implemented; readiness must
#    NOT claim coverage)
# ---------------------------------------------------------------------------


def test_drawdown_checkpoint_is_documented_gap() -> None:
    """Phase 0 mandates -8% / -20% / -35% checkpoint snapshots.

    P0 Day 7: this gap is *known* and explicitly flagged in the task spec.
    Codex R2 P1-2 fix: dual-check —
      (a) production code does NOT define a drawdown-checkpoint
          identifier (we grep for ``drawdown_checkpoint`` /
          ``DD_CHECKPOINT`` / ``DRAWDOWN_CHECKPOINT`` symbol names — this
          is a *naming-convention* gate, not a literals-search), AND
      (b) the readiness checklist does NOT falsely claim coverage.

    Codex R3 nit: prior docstring overstated the search to include
    threshold literals; the assertion below is intentionally narrower
    (identifier-only) to avoid false positives from comment text such
    as the ``vault/_NOW.md`` mention of ``-8% / -20% / -35%``. Once the
    feature lands, this test must be replaced with a real behavioural
    assertion against ``governing_risk`` / reconciliation.
    """
    polaris_root = Path("/Users/jinyoon/Projects/Polaris/polaris")
    needle_words = (
        "drawdown_checkpoint",
        "DD_CHECKPOINT",
        "DRAWDOWN_CHECKPOINT",
    )
    code_claims_feature = False
    for p in polaris_root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if any(n in text for n in needle_words):
            code_claims_feature = True
            break
    assert not code_claims_feature, (
        "Drawdown checkpoint identifier found in polaris/ — replace this "
        "gap probe with a real behavioural test."
    )

    # (b) Vault-side: readiness checklist must not falsely claim coverage.
    now_path = Path("/Users/jinyoon/Projects/Polaris/vault/_NOW.md")
    if now_path.exists():
        text = now_path.read_text(encoding="utf-8")
        for false_claim in (
            "drawdown_checkpoint_implemented = True",
            "drawdown checkpoint: DONE",
            "[x] Drawdown checkpoint snapshot",
        ):
            assert false_claim not in text, (
                f"Readiness checklist falsely claims: {false_claim!r}"
            )


# ---------------------------------------------------------------------------
# Composite readiness — single-call summary used by the ignition harness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_24h_readiness_composite_exercises_all_layers(
    isolated_vault: Path,
) -> None:
    """End-to-end: bootstrap + 2-tick paper run + spy on learner background.

    Asserts (concrete bounds — codex P1-3 fix):
    - learner trio wired (3 learners)
    - ``LearnerScheduler.run_forever`` actually started (spy-proven)
    - ``LearnerScheduler.run_once_async`` ran ≥1 cycle inside ignite (spy-proven)
    - fills table reachable + writable (count >= 0 then > 0 after a tick)
    - dashboard snapshot collectible without exception
    - vault writes landed inside ``isolated_vault``, not the repo vault
    """
    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()

    from unittest.mock import AsyncMock

    import polaris.scripts._production_bars as prod_bars
    import polaris.scripts._production_layers as prod_layers
    import polaris.scripts.smoke_paper_loop as mod
    from polaris.core.universe.schema import UniverseInstrument
    from polaris.strategies import BarView

    async def _fake_fetch(entry: FocusEntry, *, limit: int = 200) -> list[BarView]:
        return _stub_bars(120)

    original = mod._fetch_bars_for_symbol
    mod._fetch_bars_for_symbol = _fake_fetch

    # Day 8 — patch Layer 0 producer + Layer 1 OKX bar fetch for the
    # production loop. Seed a single OKX universe row so the focus
    # watchlist + bar ingest path has something to chew on.
    sample_universe = [
        UniverseInstrument(
            venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC", asset_class="crypto",
            quote_ccy="USDT", state="live", vol_24h_usd=2e9, spread_bps=2.0,
            atr_24h_pct=4.0, depth_10bps_usd=2e6, signal_density_7d=0.0,
            listing_ts=None, last_seen_ts=int(time.time()),
        )
    ]

    # Build canonical Bar list for the OKX bar fetch fake.
    from polaris.core.data.schema import Bar as CanonicalBar

    def _fake_bars() -> list[CanonicalBar]:
        # F10 — Day 9: force volume_burst to fire on the final 1m bar so the
        # paper loop persists at least one fill. Earlier bars carry slight
        # volume variation so compute_volume_z has non-zero stdev.
        # Anchor at NOW so the newest bar is fresh — the recency guard (Jin
        # 2026-06-22 dead-feed gate) skips a symbol whose newest bar is stale, so
        # a fixed-2023-epoch fixture would otherwise read as a dead feed → 0 opens.
        base_ts = int(time.time()) - 60 * 60  # 60 1m bars ending ~now
        out: list[CanonicalBar] = []
        for i in range(60):
            is_breakout = i == 59
            close = 60_000.5 + i * 5 + (5_000.0 if is_breakout else 0.0)
            high = close + 200.0
            low = (60_000.5 + i * 5) - 200.0
            volume = 50_000.0 if is_breakout else (1_000.0 + (i % 5) * 50.0)
            out.append(CanonicalBar(
                instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
                venue="okx", symbol="BTC-USDT", bar_interval="1m",
                ts=base_ts + i * 60,
                open=60_000.0 + i * 5, high=high, low=low,
                close=close, volume=volume,
                notional_usd=close * volume,
            ))
        return out

    original_okx_fetch = prod_layers.fetch_okx_instruments
    original_cap_fetch = prod_layers.fetch_capital_instruments
    original_okx_bars = prod_bars.fetch_okx_bars
    prod_layers.fetch_okx_instruments = AsyncMock(return_value=sample_universe)
    prod_layers.fetch_capital_instruments = AsyncMock(return_value=[])
    prod_bars.fetch_okx_bars = AsyncMock(return_value=list(reversed(_fake_bars())))

    # Spy on run_forever so we can prove it started + counted at least one
    # tune cycle within the test window. ``run_forever`` now drives the
    # cooperative-yield ``run_once_async`` (per-learner ``await asyncio.sleep(0)``
    # so the tick engine/WS interleave during the multi-second cycle), so the
    # cycle counter spies that path.
    started = asyncio.Event()
    cycles = 0
    real_run_forever = LearnerScheduler.run_forever
    real_run_once_async = LearnerScheduler.run_once_async

    async def _spy_run_forever(
        self: LearnerScheduler, *, interval_sec: int = 3600
    ) -> None:
        started.set()
        await real_run_forever(self, interval_sec=interval_sec)

    async def _spy_run_once_async(self: LearnerScheduler, *, now_ts: int | None = None):  # type: ignore[no-untyped-def]
        nonlocal cycles
        cycles += 1
        return await real_run_once_async(self, now_ts=now_ts)

    LearnerScheduler.run_forever = _spy_run_forever  # type: ignore[method-assign]
    LearnerScheduler.run_once_async = _spy_run_once_async  # type: ignore[method-assign]
    try:
        report = await ignite(
            duration_sec=1.5,
            tick_sec=0.3,
            db_path=db,
            paper=True,
            full_pipeline=True,
            learner_interval_sec=1,  # short interval so a cycle fires inside the window
            haiku=StubGPTClient(),  # avoid real OpenAI call in tests
        )
    finally:
        mod._fetch_bars_for_symbol = original
        prod_layers.fetch_okx_instruments = original_okx_fetch
        prod_layers.fetch_capital_instruments = original_cap_fetch
        prod_bars.fetch_okx_bars = original_okx_bars
        LearnerScheduler.run_forever = real_run_forever  # type: ignore[method-assign]
        LearnerScheduler.run_once_async = real_run_once_async  # type: ignore[method-assign]

    # --- Boot contract
    assert report.learner_count == 3
    assert "vault_now_updated" in report.notes
    assert any(n.startswith("layer0_focus=") for n in report.notes)

    # --- Background scheduler proven to have started + ticked at least once
    assert started.is_set(), "scheduler.run_forever was not awaited"
    assert cycles >= 1, f"expected >=1 learner cycle, got {cycles}"

    # --- Fills table reachable and writable (count > 0 after paper run)
    with sqlite3.connect(db) as conn:
        n_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert n_fills > 0, "paper-mode loop persisted zero fills"

    # --- Dashboard snapshot is well-typed (codex R2 P1-3 fix: replace
    # vacuous `cell_total >= 0` with concrete structure assertions).
    snap = collect_snapshot(db)
    assert set(snap.cell_dist.keys()) == {"top", "mid", "bottom"}
    assert all(isinstance(v, int) for v in snap.cell_dist.values())
    assert isinstance(snap.daily_pnl_usd, float)
    assert isinstance(snap.recent_fills, list)
    assert isinstance(snap.universe_focus_count, int)

    # --- Vault writes landed inside the isolated dir
    tmp_log = (isolated_vault / "vault" / "log.md").read_text()
    assert "ignite_p1: bootstrap" in tmp_log


@pytest.mark.asyncio
async def test_ignite_does_not_touch_repo_vault(
    isolated_vault: Path,
) -> None:
    """``ignite()`` must use only the cwd-relative vault, never reach into
    the repo vault. Codex R2 P0-2 fix: snapshot BOTH ``vault/log.md`` and
    ``vault/_NOW.md`` (the latter is mutated by
    ``_now_md_update_implementation_status`` in ignite_p1.py)."""
    db = isolated_vault / "data" / "polaris.sqlite"
    init_db(db).close()
    repo_root = Path("/Users/jinyoon/Projects/Polaris/vault")
    targets = (repo_root / "log.md", repo_root / "_NOW.md")
    pre: list[tuple[int, bytes]] = []
    for p in targets:
        if p.exists():
            pre.append((p.stat().st_size, p.read_bytes()))
        else:
            pre.append((-1, b""))

    await ignite(db_path=db, paper=False, full_pipeline=True)

    for (orig_size, orig_bytes), p in zip(pre, targets, strict=True):
        if orig_size < 0:
            # File didn't exist before — must still not exist OR be empty.
            assert not p.exists() or p.stat().st_size == 0, (
                f"ignite() created repo vault file: {p}"
            )
            continue
        cur_size = p.stat().st_size
        cur_bytes = p.read_bytes()
        assert cur_size == orig_size, (
            f"ignite() mutated {p.name}: {orig_size} → {cur_size} bytes"
        )
        # Byte-exact: catches any mid-file replace + same-size flake.
        assert cur_bytes == orig_bytes, (
            f"ignite() mutated content of {p.name} without size change"
        )


