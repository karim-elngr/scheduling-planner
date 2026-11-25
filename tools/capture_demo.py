"""Capture the documentation figures from the offline demo with Playwright and Chrome."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def capture(output):
    output.mkdir(exist_ok=True, parents=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1160, "height": 1150}, device_scale_factor=1.5,
                                reduced_motion="reduce", locale="en-US", timezone_id="UTC", offline=True)
        page.goto((ROOT / "docs/demo.html").as_uri())
        page.locator('[data-time="09:30"]').click()
        page.locator("#preview").click()
        expect(page.locator("#metric")).to_contain_text("5h 30m earlier")
        expect(page.locator("#decision-state")).to_have_text("Verified calendar fit")
        # Include the date, time choices and full decision panel; exclude unrelated controls below.
        page.screenshot(path=output / "one-move.png", clip={
            "x": 0, "y": 0, "width": 1160,
            "height": page.locator("#workbench").evaluate("el => Math.ceil(el.getBoundingClientRect().bottom + scrollY) + 12"),
        })

        page.locator("#compare-time").click()
        page.locator("#preview").click()
        expect(page.locator("#metric")).to_contain_text("2 meetings moved")
        expect(page.locator("#insight-title")).to_contain_text("09:00")
        page.locator("#workbench").screenshot(path=output / "two-moves.png")

        page.locator('[data-time="09:30"]').click()
        page.locator("#eli-window").click()
        page.locator("#coverage").uncheck()
        page.locator("#preview").click()
        page.locator("#choose").click()
        expect(page.locator("#handoff-status")).to_contain_text("Conditional")
        expect(page.locator("#handoff-evidence")).to_contain_text("15:00 remains verified")
        page.set_viewport_size({"width": 760, "height": 850})
        page.locator("#handoff").screenshot(path=output / "conditional-handoff.png")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory for the three PNGs")
    capture(parser.parse_args().output)
