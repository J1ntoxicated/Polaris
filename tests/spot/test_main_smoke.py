import subprocess
import sys


def test_main_help_exits_clean():
    r = subprocess.run(
        [sys.executable, "-m", "invasion.spot", "--help"],
        capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "headless" in r.stdout.lower()


def test_main_dry_run_boots_and_exits():
    r = subprocess.run(
        [sys.executable, "-m", "invasion.spot", "--dry-run"],
        capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert "spot" in (r.stdout + r.stderr).lower()
