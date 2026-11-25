"""Generate the offline decision explorer from the packaged synthetic example."""

import argparse
from datetime import datetime
from importlib.resources import files
import json
from pathlib import Path
import re

from scheduling_planner import plan
from scheduling_planner.cli import demo_snapshot

PAGE = Path(__file__).resolve().parents[1] / "docs" / "demo.html"


def replace_content(page, tag, element_id, content):
    pattern = rf'(<{tag} [^>]*id="{element_id}"[^>]*>).*?(</{tag}>)'
    updated, count = re.subn(pattern, lambda m: m[1] + content + m[2], page, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"Expected one element with ID {element_id!r} in the demo")
    return updated


def gain(previous, current):
    minutes = int((datetime.fromisoformat(previous) - datetime.fromisoformat(current)).total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "") if part) or "0 min"


def render(page):
    assets = files("scheduling_planner").joinpath("data")
    for tag, element_id, filename in (("style", "planner-style", "planner.css"), ("script", "planner-ui", "planner-ui.js")):
        page = replace_content(page, tag, element_id, "\n" + assets.joinpath(filename).read_text(encoding="utf-8"))
    brand = assets.joinpath("planner-brand.html").read_text(encoding="utf-8").strip()
    page, count = re.subn(r"<!-- planner-brand -->.*?<!-- /planner-brand -->",
                          lambda _: f"<!-- planner-brand -->{brand}<!-- /planner-brand -->", page, flags=re.DOTALL)
    if count != 1:
        raise ValueError("Expected one brand fragment in the demo")
    snapshot = demo_snapshot()
    rows = []
    events = {e["id"]: e for e in snapshot["events"]}
    for cap in range(3):
        report = plan(dict(snapshot, max_moves=cap), rankings=["earliest"])
        group = report["verified"]
        if group["proofs"]["earliest"]["status"] != "optimal":
            raise ValueError(f"Missing earliest-time proof for cap {cap}")
        winner = group["earliest"]
        rows.append({"start": winner["start"], "end": winner["end"], "moves": [
            {**move, "attendees": events[move["event_id"]]["attendees"]} for move in winner["moves"]
        ]})
    data = json.dumps({"snapshot": snapshot, "rows": rows, "solver": report["backend"]["version"]},
                      separators=(",", ":")).replace("<", r"\u003c")
    page = replace_content(page, "script", "demo-data", data)
    # The table is the offline fallback for readers with JavaScript disabled.
    table = []
    for cap, row in enumerate(rows):
        delta = gain(rows[cap - 1]["start"], row["start"]) if cap else "Baseline"
        table.append(f'<tr><th scope="row">{cap}</th><td>{row["start"][11:16]}</td><td>{delta}</td></tr>')
    return replace_content(page, "tbody", "rows", "\n".join(table))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify shared interface assets and committed data against new native solves")
    args = parser.parse_args()
    original = PAGE.read_text(encoding="utf-8")
    updated = render(original)
    if args.check and updated != original:
        parser.exit(1, "Demo interface or data is stale. Run python tools/refresh_demo.py\n")
    if not args.check:
        PAGE.write_text(updated, encoding="utf-8")
