"""Dashboard E2E — headless Playwright, "what Jin actually sees" (2026-07-02).

DEMO/PAPER display-only surface (no orders, no sizing). Targets the live
dashboard server at http://localhost:8770 (``./scripts/start_dashboard.sh``).
Server-not-running -> pytest.skip (CI-safe, no hard dependency on a bot
process). Headless only — never opens a visible browser window
(feedback_dashboard_no_browser_preview_verify).

Run: python3 -m pytest tests/e2e -m e2e -q
"""

from __future__ import annotations

import urllib.request
from urllib.error import URLError

import pytest

DASHBOARD_URL = "http://localhost:8770"

pytestmark = pytest.mark.e2e


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/snapshot", timeout=2):
            return True
    except URLError:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_dashboard() -> None:
    if not _server_up():
        pytest.skip(
            f"dashboard server not running at {DASHBOARD_URL} "
            "(start with ./scripts/start_dashboard.sh)"
        )


def test_page_loads_with_title(page) -> None:  # type: ignore[no-untyped-def]
    """Page loads and carries the Neural Cloud title."""
    response = page.goto(DASHBOARD_URL)
    assert response is not None
    assert response.status == 200
    assert "POLARIS" in page.title()


def test_snapshot_api_schema(page) -> None:  # type: ignore[no-untyped-def]
    """``/api/snapshot`` returns 200 with the core DashboardSnapshot keys."""
    response = page.request.get(f"{DASHBOARD_URL}/api/snapshot")
    assert response.status == 200
    body = response.json()
    for key in ("ts_now", "equity_now", "positions", "open_positions_n", "daily_pnl_usd"):
        assert key in body, f"missing snapshot key: {key}"


def test_no_console_errors(page) -> None:  # type: ignore[no-untyped-def]
    """No browser console errors during initial load."""
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(DASHBOARD_URL)
    page.wait_for_timeout(1500)
    assert errors == [], f"console errors: {errors}"


def test_neural_cloud_and_board_render(page) -> None:  # type: ignore[no-untyped-def]
    """Left Neural Cloud canvas and right analysis board both mount."""
    page.goto(DASHBOARD_URL)
    canvas = page.locator("canvas#sphere")
    assert canvas.count() == 1
    board = page.locator("#board")
    assert board.count() == 1
    page.wait_for_timeout(1000)
    assert board.locator("*").count() > 0, "board did not populate content"


def test_tab_navigation_click(page) -> None:  # type: ignore[no-untyped-def]
    """A board tab button can be clicked and switches the active pane."""
    page.goto(DASHBOARD_URL)
    tabs = page.locator("#board button.b-tab")
    page.wait_for_timeout(1000)
    count = tabs.count()
    if count < 2:
        pytest.skip("fewer than 2 board tabs rendered — nothing to navigate")
    target = tabs.nth(1)
    tab_id = target.get_attribute("data-tab")
    target.click()
    page.wait_for_timeout(300)
    assert "active" in (target.get_attribute("class") or "")
    if tab_id:
        pane = page.locator(f"#pane-{tab_id}")
        if pane.count() > 0:
            assert "active" in (pane.get_attribute("class") or "")
