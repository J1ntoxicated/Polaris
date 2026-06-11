"""alerting.notify: per-key throttle, atomic state, log-always-appends truth."""

from __future__ import annotations

import pytest

from tools.ops import alerting
from tools.ops.ops_config import OpsConfig

T0 = 1_781_200_000.0


def _log_lines(cfg: OpsConfig) -> list[str]:
    return cfg.alerts_log.read_text(encoding="utf-8").splitlines()


def test_same_key_suppressed_within_30min_resent_after(cfg: OpsConfig) -> None:
    assert alerting.notify(cfg, "stall_burst", "x", now=T0) is True
    assert alerting.notify(cfg, "stall_burst", "x", now=T0 + 1700.0) is False
    assert alerting.notify(cfg, "stall_burst", "x", now=T0 + 1700.0 + 1860.0) is True
    # different key is independent
    assert alerting.notify(cfg, "ws_gave_up", "y", now=T0 + 1700.0) is True


def test_log_always_appends_even_when_throttled(cfg: OpsConfig) -> None:
    alerting.notify(cfg, "k", "first", now=T0)
    alerting.notify(cfg, "k", "second", now=T0 + 10.0)
    lines = _log_lines(cfg)
    assert len(lines) == 2
    assert "[k]" in lines[0] and "first" in lines[0]
    assert lines[0].split(" ", 1)[0].endswith("Z")  # ISO UTC ts prefix


def test_state_file_atomic_no_tmp_residue(cfg: OpsConfig) -> None:
    alerting.notify(cfg, "k", "m", now=T0)
    residue = [p.name for p in cfg.ops_dir.iterdir() if p.name.endswith(".tmp")]
    assert residue == []
    assert cfg.state_path.exists()


def test_osascript_failure_swallowed_log_still_written(
    cfg: OpsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(text: str) -> None:
        raise OSError("no GUI session")

    monkeypatch.setattr(alerting, "_send_banner", boom)
    assert alerting.notify(cfg, "k", "m", now=T0) is True  # log is the truth source
    assert len(_log_lines(cfg)) == 1
