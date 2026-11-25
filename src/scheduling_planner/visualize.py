"""Build an offline calendar review and an export pack from saved solver output."""

from datetime import datetime
from importlib.resources import files
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .brief import brief
from .domain import effective_snapshot, iso, timestamp_minute


def _event(event_id, source, start, end, attendees):
    metadata = source.get("metadata", {})
    title = metadata.get("title") or source.get("title")
    organizer = metadata.get("organizer") or source.get("organizer")
    return {"id": event_id, "title": title if isinstance(title, str) and title else event_id,
            "organizer": organizer if isinstance(organizer, str) else None,
            "attendees": attendees, "start": iso(timestamp_minute(start)), "end": iso(timestamp_minute(end))}


def _move(event_id, source, before, after, end, attendees):
    duration = timestamp_minute(end) - timestamp_minute(after)
    original = _event(event_id, source, before, iso(timestamp_minute(before) + duration), attendees)
    return {"before": original, "after": _event(event_id, source, after, end, attendees)}


def _single_options(report, source, events):
    options = []
    for option in report["options"]:
        request = source.get("request", {})
        slot = _event("Requested meeting", request, option["start"], option["end"], request.get("attendees", []))
        moves = [_move(m["event_id"], events.get(m["event_id"], {}), m["from_start"], m["to_start"],
                       m["to_end"], m["attendees"]) for m in option["moves"]]
        proof = option["proof"]
        evidence = (f"{option['availability'].capitalize()} availability · {proof['ranking']} ranking · "
                    f"{proof['status'].replace('_', ' ')} with up to {proof['max_moves']} moves")
        notes = [q["text"] for q in report["questions"] if q.get("plan_id") == option["id"]]
        if option.get("why_confirm"):
            notes.insert(0, option["why_confirm"])
        notes.extend(n["text"] for n in report["annotations"] if n["plan_id"] == option["id"])
        notes.extend(f"{name} = {value}" for name, value in option["decisions"].items())
        if option.get("unresolved_count"):
            notes.append(f"Outstanding evidence checks: {option['unresolved_count']}.")
        options.append({"id": option["id"], "label": option["role"].replace("_", " ").capitalize(),
                        "placements": [slot], "moves": moves, "evidence": evidence, "notes": notes})
    return options


def _portfolio_option(plan, label, key, requests, events):
    placements = [_event(p["id"], requests.get(p["id"], {}), p["start"], p["end"], p["attendees"])
                  for p in plan["placements"]]
    moves = [_move(m["id"], events.get(m["id"], {}), m["from"], m["to"], m["end"], m["attendees"])
             for m in plan["moves"]]
    notes = [f"{p['id']} delegate: {p['representative']}." for p in plan["placements"] if p.get("representative")]
    metrics = plan.get("metrics") or {}
    if "focus_minutes" in metrics:
        notes.append(f"Focus time: {metrics['focus_minutes']} minutes.")
    if "cluster_days" in metrics:
        notes.append(f"Cluster days: {metrics['cluster_days']}.")
    return {"id": key, "label": label, "placements": placements, "moves": moves,
            "evidence": f"Portfolio solve · {plan['status'].lower()}", "notes": notes}


def _explainers(source):
    notes = source.get("metadata", {}).get("calendar_explainers")
    return [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []


def _calendar(report, snapshot):
    if not isinstance(report, dict):
        raise ValueError("expected a brief, advice or portfolio JSON object")
    if "advice" in report and "result" in report:
        report = brief(report)
    portfolio = report.get("kind") == "portfolio"
    if not portfolio and report.get("view") != "brief":
        raise ValueError("expected a --brief, --advise or portfolio report")
    snapshot = effective_snapshot(report, snapshot)
    source = snapshot or {}
    events = {e["id"]: e for e in source.get("events", [])}
    current = [_event(e["id"], e, e["start"], iso(timestamp_minute(e["start"]) + int(e["duration_minutes"])),
                      e["attendees"]) for e in events.values()]
    comparison = []
    if portfolio:
        requests = {p["id"]: p for p in source.get("requests", [])}
        primary = "portfolio"
        options = []
        if report.get("status") in ("FEASIBLE", "OPTIMAL"):
            options.append(_portfolio_option(report, "Selected portfolio", primary, requests, events))
        scatter = report.get("scatter_comparison") or {}
        if options and scatter.get("days_saved") is not None:
            basis = "proved" if scatter.get("proved") else "observed"
            options[0]["notes"].append(f"Days saved against scatter: {scatter['days_saved']} ({basis}).")
        if scatter.get("status") in ("FEASIBLE", "OPTIMAL"):
            options.append(_portfolio_option(scatter, "Scatter comparison", "scatter", requests, events))
        headers = ["Move budget", "Solver status", "Focus minutes", "Focus gain", "Free blocks", "Cluster days"]
        for stage in report.get("stages", []):
            budget = stage["budget"]
            metrics = stage.get("metrics") or {}
            key = f"budget-{budget}"
            gain = stage.get("focus_minutes_gained")
            if gain is not None:
                gain = f"{gain} min vs {stage['previous_budget']} moves ({'proved' if stage['gain_proved'] else 'observed'})"
            is_primary = budget == report.get("budget")
            comparison.append({"option": primary if is_primary else key,
                               "cells": [budget, stage["status"].lower(), metrics.get("focus_minutes"), gain,
                                         metrics.get("free_blocks"), metrics.get("cluster_days")]})
            if stage.get("status") in ("FEASIBLE", "OPTIMAL") and not is_primary:
                options.append(_portfolio_option(stage, f"Up to {budget} moves", key, requests, events))
    else:
        options = _single_options(report, source, events)
        primary = report["primary_id"]
        headers = ["Move budget", "Solver status", "Earliest start (UTC)", "Gain vs previous feasible budget"]
        for row in (report.get("move_comparison") or {}).get("caps", []):
            gain = row["minutes_gained"]
            if gain is not None:
                gain = f"{gain} min ({'proved' if row['gain_proven'] else 'observed'})"
            comparison.append({"option": row["plan_id"], "cells": [row["cap"], row["status"].replace("_", " "),
                                                                    row["start"], gain]})
    view = {"status": report["status"].replace("_", " ").capitalize(), "current": current,
            "options": options, "primary": primary, "comparison": comparison, "headers": headers,
            "explainers": _explainers(source),
            "timezones": sorted({p["metadata"]["timezone"] for p in source.get("people", {}).values()
                                 if isinstance(p.get("metadata", {}).get("timezone"), str)}),
            "fullSnapshot": snapshot is not None}
    return view, snapshot


def _render(view):
    # A closing script tag in a meeting title must remain JSON text in the HTML parser.
    payload = json.dumps(view, ensure_ascii=True, allow_nan=False, separators=(",", ":")).replace("<", r"\u003c")
    template = files("scheduling_planner").joinpath("data", "plan.html").read_text(encoding="utf-8")
    return template.replace("{{CALENDAR_DATA}}", payload)


def render_html(report, snapshot=None):
    """Return a standalone HTML calendar. A matching snapshot adds fixed events and roster facts."""
    view, _ = _calendar(report, snapshot)
    return _render(view)


def _who(event):
    people = [*event["attendees"]]
    if event.get("organizer") and event["organizer"] not in people:
        people.append(str(event["organizer"]))
    return ", ".join(people)


def _time_range(event):
    start = datetime.fromisoformat(event["start"])
    end = datetime.fromisoformat(event["end"])
    start_text = start.strftime("%a %d %b %Y, %H:%M")
    end_text = end.strftime("%H:%M" if start.date() == end.date() else "%a %d %b %Y, %H:%M")
    return f"{start_text}–{end_text} UTC"


def _proposal(view):
    options = view["options"]
    if not options:
        return f"Calendar planning result: {view['status']}. No schedule was returned.\n"
    choice = next((p for p in options if p["id"] == view["primary"]), options[0])
    lines = ["Calendar proposal for review", choice["evidence"], ""]
    for slot in choice["placements"]:
        people = _who(slot)
        lines.append(f"Schedule {slot['title']} on {_time_range(slot)}"
                     + (f" with {people}." if people else "."))
    for move in choice["moves"]:
        event = move["after"]
        people = _who(event)
        lines.append(f"Move {event['title']} from {_time_range(move['before'])} to {_time_range(event)}."
                     + (f" Coordinate with {people}." if people else ""))
    if not choice["placements"] and not choice["moves"]:
        lines.append("Keep the current schedule.")
    lines.extend(choice["notes"])
    lines.extend(["", "Please confirm these times and proposed changes before updating calendars."])
    return "\n".join(lines) + "\n"


def export_pack(report, snapshot, directory):
    """Publish three matching artifacts together in a new or empty directory."""
    if not isinstance(snapshot, dict):
        raise ValueError("export requires the source snapshot")
    view, effective = _calendar(report, snapshot)
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory.parent) as staging:
        pack = Path(staging) / "pack"
        pack.mkdir()
        contents = {"plan.html": _render(view), "snapshot.json": json.dumps(effective, indent=2, ensure_ascii=False),
                    "proposal.txt": _proposal(view)}
        for name, content in contents.items():
            (pack / name).write_text(content, encoding="utf-8")
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ValueError("export pack requires a new or empty directory")
        pack.replace(directory)
