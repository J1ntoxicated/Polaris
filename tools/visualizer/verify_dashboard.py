"""Playwright item-by-item dashboard verification (E4 globe + 8 tabs + exchange selector).

Run:  python3 tools/visualizer/verify_dashboard.py [URL]
Loads the live dashboard, captures console/page errors + failed requests, screenshots
the globe and every tab + every exchange-scope, and prints a JSON verdict. Read-only.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8770/"
OUT = Path("/tmp/dash_verify")
OUT.mkdir(parents=True, exist_ok=True)

TABS = ["positions", "trades", "regime", "strategy", "exit", "ai", "edge", "risk"]
EXCHANGES = ["all", "okx", "capital", "alpaca"]

report = {"url": URL, "console_errors": [], "page_errors": [], "failed_requests": [],
          "globe": {}, "alpaca_lane": None, "tabs": {}, "exchanges": {}, "shots": []}


def shot(page, name):
    p = OUT / name
    page.screenshot(path=str(p))
    report["shots"].append(str(p))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1680, "height": 1000})

    page.on("console", lambda m: report["console_errors"].append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
    page.on("requestfailed", lambda r: report["failed_requests"].append(
        f"{r.method} {r.url} :: {r.failure}"))

    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(6.0)  # globe RAF + graph load + first board poll (SSE → no networkidle)
    shot(page, "00_full.png")

    # globe: canvas present + sized + galaxy state
    report["globe"] = page.evaluate(
        """() => {
            const c = document.querySelector('canvas#sphere');
            const gs = window.PolarisGlobe_galaxyState || null;
            const counts = gs ? Object.fromEntries(Object.entries(gs).map(
                ([k,v]) => [k, (v && v.count!=null)?v.count:null])) : null;
            return {
                canvas: !!c,
                w: c ? c.width : 0, h: c ? c.height : 0,
                hasGlobeApi: typeof window.PolarisGlobe === 'object',
                hasFocus: !!(window.PolarisGlobe && window.PolarisGlobe.focusExchange),
                galaxyState: gs ? Object.keys(gs) : null,
                galaxyCounts: counts,
            };
        }"""
    )

    # alpaca lane equity text
    report["alpaca_lane"] = page.evaluate(
        """() => {
            const el = document.querySelector('[data-ex="alpaca"]');
            return el ? el.innerText.replace(/\\s+/g,' ').trim().slice(0,200) : null;
        }"""
    )

    # tabs: click each, screenshot, record active pane non-empty
    for i, t in enumerate(TABS):
        btn = page.query_selector(f'button.b-tab[data-tab="{t}"]')
        if not btn:
            report["tabs"][t] = {"button": False}
            continue
        btn.click()
        time.sleep(0.8)
        info = page.evaluate(
            f"""() => {{
                const pane = document.querySelector('#pane-{t}');
                const active = pane && pane.classList.contains('active');
                const txt = pane ? pane.innerText.replace(/\\s+/g,' ').trim() : '';
                return {{ active, len: txt.length, sample: txt.slice(0,90) }};
            }}"""
        )
        info["button"] = True
        report["tabs"][t] = info
        shot(page, f"tab_{i}_{t}.png")

    # back to positions, then exercise exchange selector
    pb = page.query_selector('button.b-tab[data-tab="positions"]')
    if pb:
        pb.click(); time.sleep(0.5)
    for ex in EXCHANGES:
        chip = page.query_selector(f'button.xchip[data-ex="{ex}"]')
        if not chip:
            report["exchanges"][ex] = {"chip": False}
            continue
        chip.click()
        time.sleep(1.0)
        info = page.evaluate(
            f"""() => {{
                const c = document.querySelector('button.xchip[data-ex="{ex}"]');
                return {{ active: c ? c.classList.contains('active') : false }};
            }}"""
        )
        info["chip"] = True
        report["exchanges"][ex] = info
        shot(page, f"ex_{ex}.png")

    browser.close()

print(json.dumps(report, ensure_ascii=False, indent=2))
