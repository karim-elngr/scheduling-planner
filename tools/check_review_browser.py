"""Exercise standalone exported scheduling decisions offline in desktop and mobile Chrome."""

import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright

from scheduling_planner import advise, brief
from scheduling_planner.cli import demo_snapshot
from scheduling_planner.ingestion import build_snapshot
from scheduling_planner.portfolio import solve_portfolio
from scheduling_planner.visualize import export_pack, render_html


ROOT = Path(__file__).resolve().parents[1]


def exports(directory):
    snapshot = demo_snapshot()
    saved = directory / "brief.json"
    saved.write_text(json.dumps(brief(advise(snapshot, compare_moves=True))), encoding="utf-8")
    single = render_html(json.loads(saved.read_bytes()), snapshot)
    squad = build_snapshot(json.loads((ROOT / "examples/squad-week-batch.json").read_bytes()))
    export_pack(solve_portfolio(squad, compare_moves=True), squad, directory / "squad")
    maker = json.loads((ROOT / ".agents/skills/plan-meetings/evals/recover-maker-block.json").read_bytes())
    return (single, (directory / "squad/plan.html").read_text(encoding="utf-8"),
            render_html(solve_portfolio(maker, compare_moves=True), maker))


def check_layout(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page overflows viewport"
    expect(page.locator("#calendar-grid")).to_be_visible()


def handoff(page, names):
    page.locator("#choose").click()
    panel = page.locator("#handoff")
    expect(panel).to_be_visible()
    for name in names:
        expect(panel).to_contain_text(name)
    with page.expect_download() as event:
        page.locator("#proposal-download").click()
    text = event.value.path().read_text(encoding="utf-8")
    for name in names:
        assert name in text, f"downloaded proposal omitted {name}"
    page.locator("#close").click()
    expect(panel).not_to_be_visible()


def check_brief(page, output, width):
    cards = page.locator("#picks .candidate")
    expect(cards).to_have_count(3)
    selected = cards.filter(has_text="09:30")
    selected.click()
    expect(selected).to_have_attribute("aria-pressed", "true")
    page.locator("#view-mode [data-mode=proposed]").click()
    grid = page.locator("#calendar-grid")
    expect(grid).to_contain_text("Asha")
    expect(grid).to_contain_text("Eli")
    expect(page.locator("#decision-state")).to_contain_text("Verified")
    check_layout(page)
    moved = grid.locator(".rg-column[data-person=Asha] .rg-moved")
    original = grid.locator(".rg-column[data-person=Asha] .rg-original")
    expect(moved).to_have_count(1)
    assert moved.bounding_box()["y"] > original.bounding_box()["y"], "the review should move to noon"
    if width == 375:
        assert grid.locator(".rg-scroll").evaluate("el => el.scrollWidth > el.clientWidth")
    moved.click()
    detail = grid.locator(".rg-detail")
    expect(detail).to_contain_text("Eli")
    detail.get_by_role("button", name="Close").click()
    expect(moved).to_be_focused()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=output / f"brief-{width}.png", full_page=width == 375)
    handoff(page, ["Asha", "Eli", "09:30", "12:00"])
    cards.filter(has_text="15:00").click()
    expect(page.locator("#insight-title")).to_contain_text("15:00")
    check_layout(page)


def check_portfolio(page, output, width):
    grid = page.locator("#calendar-grid")
    page.locator("#zone").select_option("Asia/Dubai")
    expect(grid).to_contain_text("Amal")
    expect(grid).to_contain_text("Sam")
    check_layout(page)
    page.locator("#view-mode [data-mode=proposed]").click()
    amal = grid.locator(".rg-column[data-person=Amal] .rg-added")
    expect(amal).to_have_count(1)
    expect(amal).to_have_accessible_name(re.compile("13:00"))
    expect(page.locator("#insight-title")).to_contain_text("13:00")
    page.locator("#zone").select_option("UTC")
    expect(amal).to_have_accessible_name(re.compile("09:00"))
    expect(page.locator("#insight-title")).to_contain_text("09:00")
    page.locator("#zone").select_option("Asia/Dubai")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=output / f"squad-week-{width}.png", full_page=width == 375)
    for view in ("current", "compare", "proposed"):
        button = page.locator(f"#view-mode [data-mode='{view}']")
        button.click()
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(grid).to_contain_text("Triage")
        if view == "current":
            expect(grid.locator(".rg-column .rg-added")).to_have_count(0)
        elif view == "compare":
            expect(grid.locator(".rg-panel")).to_have_count(2)
        else:
            expect(amal).to_have_count(1)
        check_layout(page)
    handoff(page, ["Amal", "Sam"])


def check_maker_stages(page):
    cards = page.locator("#picks .candidate")
    expect(cards).to_have_count(3)
    moved_counts = set()
    for card in cards.all():
        card.click()
        expect(card).to_have_attribute("aria-pressed", "true")
        moved_counts.add(page.locator("#calendar-grid .rg-column[data-person=Asha] .rg-moved").count())
    assert moved_counts == {0, 1, 2}, f"expected separate 0/1/2-move schedules, got {moved_counts}"
    check_layout(page)


def check(output, chrome=None):
    output.mkdir(exist_ok=True, parents=True)
    with TemporaryDirectory() as temp:
        single, squad, maker = exports(Path(temp))
    (output / "squad-week.html").write_text(squad, encoding="utf-8")
    with sync_playwright() as playwright:
        launch = {"executable_path": chrome} if chrome else {"channel": "chrome"}
        browser = playwright.chromium.launch(**launch)
        for width in (375, 1280):
            context = browser.new_context(viewport={"width": width, "height": 900}, locale="en-GB", timezone_id="UTC",
                                          offline=True, reduced_motion="reduce", accept_downloads=True)
            for name, document, journey in (("brief", single, check_brief), ("portfolio", squad, check_portfolio)):
                page = context.new_page()
                page.set_default_timeout(5000)
                errors, requests = [], []
                page.on("pageerror", lambda error, log=errors: log.append(str(error)))
                page.on("request", lambda request, log=requests: log.append(request.url))
                page.set_content(document)
                journey(page, output, width)
                assert not errors, f"{name} at {width}px: {errors}"
                external = [url for url in requests if url.startswith(("http:", "https:", "file:"))]
                assert not external, f"{name} requested external resources: {external}"
                page.close()
                print(f"Offline {name} passed at {width}px")
            stage_page = context.new_page()
            stage_page.set_content(maker)
            check_maker_stages(stage_page)
            print(f"Offline portfolio budget selection passed at {width}px")
            context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory for the review screenshots")
    parser.add_argument("--chrome", help="Chrome or Chromium executable path")
    args = parser.parse_args()
    check(args.output, args.chrome)
