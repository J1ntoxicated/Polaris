"""Single start/stop/status implementation for the DEMO/PAPER bot.

Safety contract (absolute, sealed by tests/ops/test_no_kill_patterns.py):
- The ONLY signal ever sent is SIGTERM, exactly once, to a PID whose
  `ps` command line strictly matches the bot. No escalation, ever.
- Strict matcher = python executable in argv[0] + exact
  `-m polaris.scripts.ignite_p1` token pair + `--paper` token. (argv[0]
  is NOT required to be `.venv/bin/python`: macOS venv python execs the
  framework binary, so `ps` shows .../Python.app/.../MacOS/Python.)
- MANUAL_STOP sentinel: created BEFORE the signal on `stop --manual`
  (on every failure path too); cleared only by `start --manual`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import time

from tools.ops import alerting
from tools.ops.ops_config import (
    FLAP_BACKOFF_SEC,
    FLAP_COUNT,
    FLAP_UPTIME_SEC,
    FLAP_WINDOW_SEC,
    LOCK_STALE_SEC,
    START_VERIFY_SEC,
    STOP_WAIT_SEC,
    OpsConfig,
    iso_utc,
    load_state,
    save_state,
)

BOT_MODULE = "polaris.scripts.ignite_p1"

# --- seams (monkeypatched in tests; kept tiny and side-effect-pure) --------


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _send_sigterm(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def ps_command(pid: int) -> str | None:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out or None


def list_processes() -> list[tuple[int, str]]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, str]] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        pid_s, _, cmd = line.partition(" ")
        if pid_s.isdigit():
            rows.append((int(pid_s), cmd.strip()))
    return rows


def ancestor_pids() -> set[int]:
    """Self + ancestor chain — never adopt/match our own process tree."""
    out: set[int] = set()
    pid = os.getpid()
    for _ in range(16):
        out.add(pid)
        try:
            proc = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            break
        ppid_s = proc.stdout.strip()
        if not ppid_s.isdigit():
            break
        ppid = int(ppid_s)
        if ppid <= 1:
            break
        pid = ppid
    return out


# --- strict bot matching ----------------------------------------------------


def is_bot_command(cmd: str) -> bool:
    """Strict triple gate against PID-reuse / innocent-process mis-targeting."""
    tokens = cmd.split()
    if not tokens:
        return False
    exe = tokens[0].rsplit("/", 1)[-1].lower()
    if "python" not in exe:
        return False
    if "--paper" not in tokens:
        return False
    return any(
        tok == "-m" and tokens[i + 1] == BOT_MODULE
        for i, tok in enumerate(tokens[:-1])
    )


def find_bot_processes(exclude: set[int] | None = None) -> list[tuple[int, str]]:
    excl = ancestor_pids() if exclude is None else exclude
    return [(p, c) for p, c in list_processes() if p not in excl and is_bot_command(c)]


# --- pidfile / lock ---------------------------------------------------------


def read_pidfile(cfg: OpsConfig) -> int | None:
    try:
        text = cfg.pidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def write_pidfile(cfg: OpsConfig, pid: int) -> None:
    cfg.pidfile.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg.pidfile.with_name(cfg.pidfile.name + ".part")
    tmp.write_text(f"{pid}\n", encoding="utf-8")
    os.replace(tmp, cfg.pidfile)


def _lock_owner(cfg: OpsConfig) -> int | None:
    try:
        text = (cfg.lock_dir / "owner.pid").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def acquire_lock(cfg: OpsConfig, *, now: float | None = None) -> bool:
    """Atomic mkdir lock. Stale takeover only when age>10min AND owner dead."""
    now_f = time.time() if now is None else now
    cfg.ops_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            cfg.lock_dir.mkdir()
        except FileExistsError:
            if attempt == 1:
                return False
            try:
                age = now_f - cfg.lock_dir.stat().st_mtime
            except FileNotFoundError:
                continue  # vanished between checks — retry mkdir
            owner = _lock_owner(cfg)
            owner_dead = owner is None or not pid_alive(owner)
            if age > LOCK_STALE_SEC and owner_dead:
                shutil.rmtree(cfg.lock_dir, ignore_errors=True)
                alerting.notify(
                    cfg, "stale_lock_cleared",
                    f"stale start.lock cleared (age={age:.0f}s, owner dead)",
                )
                continue
            return False
        else:
            (cfg.lock_dir / "owner.pid").write_text(
                str(os.getpid()), encoding="utf-8"
            )
            return True
    return False


def release_lock(cfg: OpsConfig) -> None:
    shutil.rmtree(cfg.lock_dir, ignore_errors=True)


# --- flap accounting (review fix: late-crash loop must surface + back off) --


def note_unexpected_death(cfg: OpsConfig, *, now: float | None = None) -> None:
    """Record a bot death; short uptime deaths accumulate into flap backoff."""
    now_f = time.time() if now is None else now
    state = load_state(cfg.state_path)
    last = state.get("last_start_ts")
    if not isinstance(last, int | float):
        return
    state["last_start_ts"] = None  # consume — never double-count one death
    if now_f - float(last) < FLAP_UPTIME_SEC:
        raw = state.get("flap_events")
        events = [float(e) for e in raw] if isinstance(raw, list) else []
        events = [e for e in events if now_f - e < FLAP_WINDOW_SEC]
        events.append(now_f)
        state["flap_events"] = events
        if len(events) >= FLAP_COUNT:
            state["backoff_until"] = now_f + FLAP_BACKOFF_SEC
            alerting.notify(
                cfg, "bot_flapping",
                f"{len(events)} short-uptime deaths in 1h — retry interval "
                f"relaxed to {FLAP_BACKOFF_SEC / 60:.0f}min (no halt)",
            )
    save_state(cfg.state_path, state)


def in_backoff(cfg: OpsConfig, *, now: float | None = None) -> bool:
    now_f = time.time() if now is None else now
    until = load_state(cfg.state_path).get("backoff_until")
    return isinstance(until, int | float) and now_f < float(until)


# --- start / stop -----------------------------------------------------------


def _spawn_env() -> dict[str, str]:
    """Env dict for the spawned bot subprocess — pure, unit-testable."""
    env = dict(os.environ)
    # 2026-07-03 명시 INERT (RCA wf_acf9db2c): tick 시그널 로스터(_SIGNAL_FNS)가
    # 06-26 KILL 이후 빈 dict → 엔진이 6일째 decisions=0 순수 공회전(44만 평가/일,
    # CPU만 소모 — Jin 컨퍼런스콜 간섭). 로스터 재건 시 "1" 복귀. 호출 셸의
    # TICK_ENGINE_ENABLED로 오버라이드 가능.
    env["TICK_ENGINE_ENABLED"] = os.environ.get("TICK_ENGINE_ENABLED", "0")
    # VIRTUAL ACCOUNT (Jin 2026-07-07): every start_bot.sh launch runs fully
    # virtual — no real venue orders/reconcile/balance calls. Caller shell can
    # still override to "0" for an explicit real-roundtrip debug run.
    env["POLARIS_VIRTUAL_ACCOUNT"] = os.environ.get("POLARIS_VIRTUAL_ACCOUNT", "1")
    # OKX LIQUIDITY FLOOR STEP-DOWN (Jin 2026-07-09, very-active/focus-expand):
    # step 1 of a sequential, observe-then-proceed widening of the focus
    # universe — OKX min_vol_24h_usd floor $20M schema default -> $15M (25%
    # notch, NOT a big-bang drop). POLARIS_WATCH_MAX (resource-guard ceiling,
    # already 1500) is deliberately NOT touched here — only the quality floor
    # moves, never the cap. Caller shell can still override for the next step.
    env["POLARIS_LIQFLOOR_OKX_MIN_VOL_24H_USD"] = os.environ.get(
        "POLARIS_LIQFLOOR_OKX_MIN_VOL_24H_USD", "15000000"
    )
    # ALPACA FREEZE STOPGAP REVERTED (storage-split 2026-07-14): the per-venue
    # rank top-N default 1500 gave Alpaca a 1500-instrument active set (OKX 180
    # / Capital 114 sit below it). Grinding 1500 instruments' bar/ground
    # persists flooded the single db_writer queue → WAL write-lock contention
    # → the main tick froze mid-signal-emit for ~10h (2026-07-13). TEMP cap to
    # 400 held until the ROOT fix landed — market-data (bars/baseline/focus/
    # ground/quote/altdata) storage now writes through a SEPARATE marketdata
    # DB + its own DBWriter, so the grind no longer contends the trading WAL
    # lock. Restored to 1500 (aggressive breadth back). Caller shell can
    # still override.
    env["POLARIS_UNIVERSE_RANK_TOP_N"] = os.environ.get(
        "POLARIS_UNIVERSE_RANK_TOP_N", "1500"
    )
    # PROBE PROTECT PROMOTION (P3, 2026-07-16, vault/50_research/
    # built-not-wired-audit.md): the probe performance readout (25,624 favorable-
    # MFE positions) showed TIGHTEN/HARVEST correctly call giveback ahead of it
    # (HARVEST realized +0.088R vs HOLD -0.08R) — default the G6 consumer ON for
    # every botctl-spawned run. The consumer itself only ever escalates HOLD ->
    # ADJUST_EXIT (a tighter trail via G7); it never touches the -1.0R rail, size,
    # or entry. Caller shell can still override to "0" to fall back to plain HOLD.
    env["POLARIS_G6_PROBE_TIGHTEN"] = os.environ.get("POLARIS_G6_PROBE_TIGHTEN", "1")
    return env


def _spawn(cfg: OpsConfig) -> int:
    cfg.ops_dir.mkdir(parents=True, exist_ok=True)
    env = _spawn_env()
    with open(cfg.spawn_log, "ab") as out:
        proc = subprocess.Popen(
            cfg.start_cmd,
            cwd=str(cfg.project_dir),  # .env is cwd-relative in ignite_p1
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def _log_growing(cfg: OpsConfig, pre_size: int, spawn_ts: float) -> bool:
    try:
        st = os.stat(cfg.bot_log)
    except FileNotFoundError:
        return False
    return st.st_size > pre_size or st.st_mtime >= spawn_ts - 5.0


def _record_start_outcome(cfg: OpsConfig, *, ok: bool, now: float) -> int:
    state = load_state(cfg.state_path)
    failures = state.get("start_failures")
    count = failures if isinstance(failures, int) else 0
    if ok:
        state["start_failures"] = 0
        state["last_start_ts"] = now
        count = 0
    else:
        count += 1
        state["start_failures"] = count
    save_state(cfg.state_path, state)
    return count


def start(
    cfg: OpsConfig,
    *,
    manual: bool = False,
    have_lock: bool = False,
    now: float | None = None,
) -> bool:
    now_f = time.time() if now is None else now
    if not cfg.env_file.exists():
        alerting.notify(
            cfg, "start_env_missing",
            f".env missing at {cfg.env_file} — start aborted (half-boot guard)",
        )
        return False
    if not have_lock and not acquire_lock(cfg, now=now_f):
        return False
    try:
        # Sentinel re-check under the lock: a manual stop issued after an
        # automation cycle began must never be silently overridden.
        if cfg.sentinel.exists() and not manual:
            return False

        excl = ancestor_pids()
        orphans = find_bot_processes(exclude=excl)
        if orphans:
            pid, cmd = orphans[0]
            write_pidfile(cfg, pid)
            alerting.notify(
                cfg, "bot_adopted",
                f"live bot found without pidfile — adopted pid={pid} argv={cmd}",
            )
            if manual:
                cfg.sentinel.unlink(missing_ok=True)
            return True

        pre_size = cfg.bot_log.stat().st_size if cfg.bot_log.exists() else 0
        pid = _spawn(cfg)
        write_pidfile(cfg, pid)
        _sleep(START_VERIFY_SEC)
        if not (pid_alive(pid) and _log_growing(cfg, pre_size, now_f)):
            cfg.pidfile.unlink(missing_ok=True)
            failures = _record_start_outcome(cfg, ok=False, now=now_f)
            alerting.notify(
                cfg, "start_failed",
                f"bot died within {START_VERIFY_SEC:.0f}s of spawn "
                f"({failures} consecutive) — see {cfg.spawn_log}",
                throttle_sec=0.0 if failures < 3 else 1800.0,
            )
            return False

        _record_start_outcome(cfg, ok=True, now=now_f)
        if len(find_bot_processes(exclude=excl)) > 1:
            alerting.notify(
                cfg, "duplicate_instance",
                "more than one bot process matched after start — NOT killing "
                "anything (human judgement required)",
            )
        if manual:
            cfg.sentinel.unlink(missing_ok=True)
        return True
    finally:
        if not have_lock:
            release_lock(cfg)


def stop(cfg: OpsConfig, *, manual: bool = False, reason: str = "manual stop") -> bool:
    # Step 0 (unconditional, before ANY other step): record intent so the
    # watchdog can never resurrect a manually stopped bot — on every path.
    if manual:
        cfg.sentinel.parent.mkdir(parents=True, exist_ok=True)
        cfg.sentinel.write_text(
            f"{iso_utc(time.time())} {reason}\n", encoding="utf-8"
        )

    target: int | None = None
    pid = read_pidfile(cfg)
    if pid is not None and pid_alive(pid):
        cmd = ps_command(pid)
        if cmd is not None and is_bot_command(cmd):
            target = pid
    if target is None:
        # pidfile dead/stale/mismatch → fall back to a strict-match orphan.
        orphans = find_bot_processes(exclude=ancestor_pids())
        if orphans:
            target = orphans[0][0]
            if len(orphans) > 1:
                alerting.notify(
                    cfg, "stop_multiple_bots",
                    f"{len(orphans)} bot processes matched — signalling first only",
                )
    if target is None:
        cfg.pidfile.unlink(missing_ok=True)  # stale file cleanup; no process touched
        return True

    _send_sigterm(target)  # exactly once, exact PID
    for _ in range(STOP_WAIT_SEC):
        if not pid_alive(target):
            cfg.pidfile.unlink(missing_ok=True)
            return True
        _sleep(1.0)
    alerting.notify(
        cfg, "stop_timeout",
        f"pid {target} still alive {STOP_WAIT_SEC}s after SIGTERM — not "
        "escalating; manual investigation needed",
    )
    return False


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="botctl", description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args(argv)
    cfg = OpsConfig.default()
    if args.action == "start":
        ok = start(cfg, manual=bool(args.manual))
        print(f"[botctl] start ok={ok} pid={read_pidfile(cfg)}")
        return 0 if ok else 1
    if args.action == "stop":
        ok = stop(cfg, manual=bool(args.manual))
        print(f"[botctl] stop ok={ok} sentinel={cfg.sentinel.exists()}")
        return 0 if ok else 1
    pid = read_pidfile(cfg)
    alive = pid is not None and pid_alive(pid)
    cmd = ps_command(pid) if alive and pid is not None else None
    match = cmd is not None and is_bot_command(cmd)
    print(
        f"[botctl] pid={pid} alive={alive} match={match} "
        f"sentinel={cfg.sentinel.exists()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
