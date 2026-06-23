"""Nightly replay entry point — thin launchd-facing wrapper.

launchd plists in this repo invoke `python -m tools.ops.<module>` uniformly
(see test_installer.test_common_plist_contract). The replay window logic
(max-bar lookup, trailing window, harness CLI) already lives in the shell
wrapper scripts/run_replay_nightly.sh, so this module just shells out to it.

flow_not_block: offline OOS analysis only — zero live trading behaviour, never
touches the bot process, sizing, or gates.
"""

from __future__ import annotations

import subprocess

from tools.ops.ops_config import OpsConfig


def run(cfg: OpsConfig) -> int:
    script = cfg.project_dir / "scripts" / "run_replay_nightly.sh"
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(cfg.project_dir),
        check=False,
    )
    return proc.returncode


def main() -> int:
    rc = run(OpsConfig.default())
    print(f"[replay-nightly] rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
