"""Run the decision journey from the self-contained demo in offline Chromium."""

import argparse
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import tempfile

DEMO = Path(__file__).resolve().parents[1] / "docs" / "demo.html"
OFFLINE_POLICY = """<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<script>window.blockedResources = [];
document.addEventListener('securitypolicyviolation', e => blockedResources.push(e.blockedURI));
</script>"""
CHECKS = r"""
<script>
window.addEventListener("load", async () => {
  const one = selector => document.querySelector(selector);
  const text = selector => one(selector).textContent.trim();
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const slot = time => one('[data-time="' + time + '"]');
  const state = time => slot(time).querySelector(".candidate-state").textContent;
  const available = selector => {
    const control = one(selector);
    return control && !control.closest("[hidden]") && control.getClientRects().length > 0 &&
      !control.disabled && control.getAttribute("aria-disabled") !== "true";
  };
  const has = (actual, expected, context) => {
    for (const match of expected) assert(actual.includes(match), context + ": missing " + match);
  };
  const click = selector => {
    assert(available(selector), selector + " is unavailable");
    one(selector).click();
  };
  function pick(time) {
    const button = slot(time);
    button.focus();
    assert(document.activeElement === button, "candidate is not keyboard focusable: " + time);
    assert(button.matches("button"), "candidate should support native Enter and Space activation");
    button.click();
    assert(button.getAttribute("aria-pressed") === "true", "missing selected state: " + time);
  }
  function expectHandoff(time, moveCount) {
    assert(available("#handoff"), "chosen proposal must open the handoff");
    const input = JSON.parse(text("#snapshot-json"));
    assert(input.request.starts.length === 1 && input.request.starts[0] === "2026-12-01T" + time + "Z",
      "handoff has the wrong candidate domain");
    assert(input.max_moves === moveCount, "handoff has the wrong move cap");
    return input;
  }
  function noHandoff() {
    assert(!available("#handoff") && !available("#choose"), "blocked candidate offers a handoff");
    assert(!available("#preview"), "blocked candidate can be previewed");
  }
  function fits() {
    assert(document.documentElement.scrollWidth <= window.innerWidth, "page overflows the viewport");
  }
  function expectMove(person, hour) {
    const column = '[data-person="' + person + '"] ';
    const tick = time => [...document.querySelectorAll(".tick")].find(node => node.textContent === time).style.top;
    assert(available(column + ".ghost") && one(column + ".ghost").style.top === tick("09:00"),
      person + " lost the original 09:00 position");
    assert(available(column + ".event") && one(column + ".event").style.top === tick(hour),
      person + " meeting is not at " + hour);
  }
  try {
    assert(slot("15:00").getAttribute("aria-pressed") === "true", "start on the zero-move schedule");
    assert(/clear/i.test(state("15:00")), "the baseline should be clear");
    assert(/2.*conflict/i.test(state("09:00")) && /1.*conflict/i.test(state("09:30")),
      "candidate labels must identify the original conflicts");
    fits();
    click("#choose");
    expectHandoff("15:00", 0);

    pick("09:30");
    assert(!available("#handoff") && !available("#choose"), "inspect the move before choosing it");
    for (const [person, activity] of [["Asha", "conflict"], ["Ben", "clear"], ["Eli", "linked"]]) {
      assert(one('[data-person="' + person + '"]').dataset.activity === activity, "wrong 09:30 impact for " + person);
    }
    click("#preview");
    for (const person of ["Asha", "Eli"]) expectMove(person, "12:00");
    assert(!available('[data-person="Ben"] .ghost'), "the first preview moved Ben’s sync");
    assert(/clear/i.test(state("09:30")), "resolved review is still shown as a conflict");
    has(text("#metric"), ["1 meeting moved", "2 people affected"], "impact of moving the review");
    click("#choose");
    expectHandoff("09:30", 1);
    has(text("#handoff-moves"), ["Asha", "Eli", "12:00"], "one-move handoff");
    assert(!text("#handoff-moves").includes("Dana"), "09:30 incorrectly moved the sync");

    click("#close");
    assert(document.activeElement === one("#choose"), "closing the handoff lost keyboard focus");
    click("#compare-time");
    assert(slot("09:00").getAttribute("aria-pressed") === "true", "Compare did not select 09:00");
    expectMove("Asha", "12:00");
    assert(!available('[data-person="Ben"] .ghost') && !available("#choose"), "second move was not inspected");
    click("#preview");
    for (const person of ["Ben", "Dana"]) expectMove(person, "14:00");
    has(text("#metric"), ["30m earlier than 09:30", "2 meetings moved", "4 people affected"], "09:00 tradeoff");
    click("#choose");
    expectHandoff("09:00", 2);
    has(text("#handoff-moves"), ["Asha", "Eli", "Ben", "Dana", "12:00", "14:00"], "two-move handoff");
    click("#close");
    click("#compare-time");
    assert(slot("09:30").getAttribute("aria-pressed") === "true", "Back did not select 09:30");
    assert(available("#choose") && !available('[data-person="Ben"] .ghost'), "Back retained the extra move");

    click("#lock-review");
    noHandoff();
    assert(/blocked/i.test(state("09:30")) && /blocked/i.test(state("09:00")),
      "locking the review must block both morning starts");
    assert(/clear/i.test(state("15:00")), "locking the review invalidated the baseline");
    has(text("#insight-note"), ["Asha", "Eli", "09:00", "10:00"], "fixed review explanation");
    click("#compare-time");
    assert(slot("15:00").getAttribute("aria-pressed") === "true", "blocked morning should offer clear 15:00");
    click("#choose");
    const fallback = expectHandoff("15:00", 0);
    assert(!fallback.events.find(event => event.id === "design-review").alternatives, "export lost the review lock");
    click("#lock-review");
    click("#lock-sync");
    assert(/blocked/i.test(state("09:00")) && !/blocked/i.test(state("09:30")),
      "a fixed half-hour sync must clear at 09:30");
    click("#lock-sync");

    pick("09:30");
    if (!one("#evidence").open) click("#eli-window");
    click("#coverage");
    assert(/confirm/i.test(state("09:30")) && !/blocked/i.test(state("09:30")),
      "missing coverage must leave a conditional path");
    assert(/clear/i.test(state("15:00")), "missing coverage invalidated the verified fallback");
    click("#eli-window");
    has(text("#question"), ["Eli", "12:00", "13:00"], "calendar evidence question");
    click("#preview");
    assert(/confirm/i.test(state("09:30")), "preview changed a conditional option into a verified option");
    click("#choose");
    const displayed = expectHandoff("09:30", 1);
    assert(/conditional|confirm/i.test(text("#handoff-status")), "conditional handoff lost its evidence qualifier");
    has(text("#handoff-evidence"), ["Eli", "12:00", "13:00"], "conditional handoff question");
    const eli = displayed.people.Eli.busy_coverage;
    assert(eli.every(([a, b]) => Date.parse(b) <= Date.parse("2026-12-01T12:00Z")
      || Date.parse(a) >= Date.parse("2026-12-01T13:00Z")), "export filled in Eli's missing noon evidence");
    let exported;
    const createURL = URL.createObjectURL.bind(URL);
    URL.createObjectURL = blob => { exported = blob; return createURL(blob); };
    document.addEventListener("click", event => {
      if (event.target.closest("a[download]")) event.preventDefault();
    });
    click("#download");
    assert(JSON.stringify(JSON.parse(await exported.text())) === JSON.stringify(displayed),
      "downloaded snapshot differs from the handoff");
    click("#proposal-download");
    const proposal = await exported.text();
    has(proposal, ["09:30", "UTC", "Conditional", "Asha", "Eli", "12:00", "13:00"], "exported proposal");
    assert(!proposal.includes("14:00"), "exported proposal includes an unselected move");
    URL.createObjectURL = createURL;
    fits();
    click("#coverage");
    assert(!available("#handoff") && !/confirm/i.test(state("09:30")), "restoring evidence left stale handoff state");
    setTimeout(() => {
      parent.document.documentElement.dataset.demoCheck = blockedResources.length
        ? "demo requested blocked resources: " + blockedResources.join(", ") : "passed";
    }, 50);
  } catch (error) {
    parent.document.documentElement.dataset.demoCheck = String(error);
  }
});
</script>
"""


class Result(HTMLParser):
    outcome = None

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.outcome = dict(attrs).get("data-demo-check")


def check_viewport(chrome, page, root, width):
    # A fixed iframe gives mobile CSS a genuine 375px viewport even when the
    # installed desktop browser clamps the operating system window's width.
    fixture = root / f"demo-{width}.html"
    fixture.write_text(f'<!doctype html><html><body><iframe width="{width}" height="1000" '
                       f'srcdoc="{escape(page, quote=True)}"></iframe></body></html>', encoding="utf-8")
    process = subprocess.run([
        chrome, "--headless", "--window-size=1400,1200", "--disable-background-networking", "--disable-extensions",
        "--no-first-run", f"--user-data-dir={root / 'profile'}", "--virtual-time-budget=4000",
        "--dump-dom", fixture.as_uri(),
    ], capture_output=True, text=True, timeout=45)
    if process.returncode:
        raise SystemExit(f"Chrome exited {process.returncode} at {width}px: {process.stderr[-1200:]}")
    result = Result()
    result.feed(process.stdout)
    if result.outcome != "passed":
        raise SystemExit(f"{width}px: {result.outcome or 'Browser did not complete the offline demo checks'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", default="google-chrome", help="path or command for Chrome or Chromium")
    args = parser.parse_args()
    page = DEMO.read_text(encoding="utf-8")
    instrumented = page.replace("<head>", "<head>" + OFFLINE_POLICY, 1).replace("</body>", CHECKS + "</body>", 1)
    with tempfile.TemporaryDirectory() as directory:
        for width in (375, 1280):
            check_viewport(args.chrome, instrumented, Path(directory), width)
    print("Offline demo passed at 375px and 1280px: inspect, preview, choose, lock, confirm and export")


if __name__ == "__main__":
    main()
