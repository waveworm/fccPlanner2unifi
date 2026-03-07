from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "quickstart-assets"
BASE_URL = "http://127.0.0.1:3000"


def capture() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shots = [
        {
            "path": "/dashboard",
            "outfile": "dashboard.png",
            "wait_for": ".page",
            "full_page": False,
        },
        {
            "path": "/schedule-board?days=7&view=sanctuary_lobby",
            "outfile": "schedule-board.png",
            "wait_for": ".page",
            "full_page": False,
        },
        {
            "path": "/settings",
            "outfile": "door-mapping.png",
            "wait_for": ".page",
            "full_page": False,
        },
        {
            "path": "/office-hours",
            "outfile": "office-hours.png",
            "wait_for": "#office-hours-calendar",
            "prepare": "office_hours",
            "full_page": False,
        },
        {
            "path": "/event-overrides",
            "outfile": "event-overrides.png",
            "wait_for": ".page",
            "full_page": False,
        },
        {
            "path": "/general-settings",
            "outfile": "general-settings.png",
            "wait_for": ".page",
            "full_page": False,
        },
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 820}, device_scale_factor=1)
        for shot in shots:
            page.goto(f"{BASE_URL}{shot['path']}", wait_until="networkidle")
            page.wait_for_selector(str(shot["wait_for"]))
            if shot.get("prepare") == "office_hours":
                page.locator("#office-hours-calendar").scroll_into_view_if_needed()
                page.mouse.wheel(0, -160)
                page.wait_for_timeout(250)
            page.screenshot(path=str(OUT_DIR / str(shot["outfile"])), full_page=bool(shot["full_page"]))
        browser.close()


if __name__ == "__main__":
    capture()
