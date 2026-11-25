"""Build an offline calendar review and an export pack from saved solver output."""

from datetime import datetime
from importlib.resources import files
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .brief import brief
from .domain import effective_snapshot, iso, timestamp_minute


def _event(event_id, source, start, end, attendees):
    metadata = source.get("metadata", {})
    title = metadata.get("title") or source.get("title")
    organizer = metadata.get("organizer") or source.get("organizer")
    if not isinstance(title, str) or not title.strip():
        # Humanize common lowercase slugs without rewriting names supplied by calendars.
        if re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", event_id):
            title = re.sub(r"[-_]", " ", event_id).capitalize()
        else:
            title = event_id
    return {"id": event_id, "title": title,
            "organizer": organizer if isinstance(organizer, str) else None,
            "attendees": attendees, "start": iso(timestamp_minute(start)), "end": iso(timestamp_minute(end))}


def _move(event_id, source, before, after, end, attendees):
    duration = timestamp_minute(end) - timestamp_minute(after)
    original = _event(event_id, source, before, iso(timestamp_minute(before) + duration), attendees)
    return {"before": original, "after": _event(event_id, source, after, end, attendees)}


def _single_options(report, source, events):
    options = []
    request = source.get("request", {})
    for option in report["options"]:
        attendees = request.get("attendees", [])
        slot = _event("Group meeting" if len(attendees) > 1 else "Meeting", request,
                      option["start"], option["end"], attendees)
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
        facts = []
        earlier = option.get("minutes_earlier_than_primary", 0)
        if earlier > 0:
            facts.append(f"{_duration(earlier)} earlier than the primary option")
        options.append({"id": option["id"], "availability": option["availability"],
                        "proofStatus": proof["status"], "facts": facts, "impact": facts[0] if facts else None,
                        "label": option["role"].replace("_", " ").capitalize(),
                        "placements": [slot], "moves": moves, "evidence": evidence, "notes": notes})
    return options


def _duration(minutes):
    hours, remainder = divmod(abs(minutes), 60)
    if not hours:
        return f"{remainder}m"
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def _count(number, word):
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def _portfolio_option(plan, label, key, requests, events):
    placements = [_event(p["id"], requests.get(p["id"], {}), p["start"], p["end"], p["attendees"])
                  for p in plan["placements"]]
    moves = [_move(m["id"], events.get(m["id"], {}), m["from"], m["to"], m["end"], m["attendees"])
             for m in plan["moves"]]
    notes = [f"{p['representative']} represents the group at {event['title']}."
             for p, event in zip(plan["placements"], placements, strict=True) if p.get("representative")]
    metrics = plan.get("metrics") or {}
    facts = []
    if metrics.get("cluster_days"):
        facts.append(f"Cluster spans {_count(metrics['cluster_days'], 'day')}")
    focus = None
    if "focus_minutes" in metrics:
        focus = f"Longest daily focus blocks total {_duration(metrics['focus_minutes'])}"
        facts.append(focus)
    status = plan["status"].lower()
    budget = plan.get("budget", 0)
    scope = f"with up to {_count(budget, 'move')}"
    evidence = f"Optimal portfolio {scope}" if status == "optimal" else f"Feasible portfolio {scope}"
    return {"id": key, "label": label, "placements": placements, "moves": moves, "facts": facts,
            "impact": None if metrics.get("cluster_days") else focus,
            "proofStatus": status, "availability": "feasible", "evidence": evidence, "notes": notes}


def _portfolio_choices(report, source, events):
    requests = {p["id"]: p for p in source.get("requests", [])}
    options = []
    schedule_ids = {}

    def add(plan, label, key):
        if plan.get("status") not in ("FEASIBLE", "OPTIMAL"):
            return None
        # A cap or the scatter run can return the very same witness. Keep all
        # proof scopes in the comparison and show each schedule only once.
        placements = sorted((p["id"], p["start"], p["end"], tuple(sorted(p["attendees"])), p.get("representative"))
                            for p in plan["placements"])
        moves = sorted((m["id"], m["from"], m["to"], m["end"], tuple(sorted(m["attendees"])))
                       for m in plan["moves"])
        identity = (tuple(placements), tuple(moves))
        if identity not in schedule_ids:
            options.append(_portfolio_option(plan, label, key, requests, events))
            schedule_ids[identity] = key
        return schedule_ids[identity]

    primary = add(report, "Proposed schedule", "portfolio")
    scatter = report.get("scatter_comparison") or {}
    if primary and scatter.get("days_saved") is not None:
        days = scatter["days_saved"]
        basis = "proved" if scatter.get("proved") else "observed"
        if days > 0:
            impact = f"{_count(days, 'day')} saved against scatter ({basis})"
            options[0]["facts"].append(impact)
            options[0]["impact"] = impact
        elif days == 0:
            options[0]["notes"].append(f"The packed and spread-out schedules use the same number of days ({basis}).")
        else:
            options[0]["notes"].append(f"The spread-out alternative uses {_count(abs(days), 'day')} fewer ({basis}).")
    headers = ["Move budget", "Solver status", "Focus time", "Focus gain", "Free blocks", "Cluster days"]
    comparison = []
    for stage in report.get("stages", []):
        budget = stage["budget"]
        metrics = stage.get("metrics") or {}
        label = "Keep existing times" if budget == 0 else f"Up to {_count(budget, 'move')}"
        key = add(stage, label, f"budget-{budget}")
        gain = stage.get("focus_minutes_gained")
        if gain is not None:
            proof = "proved" if stage["gain_proved"] else "observed"
            sign = "−" if gain < 0 else "+"
            gain = f"{sign}{_duration(gain)} vs {_count(stage['previous_budget'], 'move')} ({proof})"
        focus = metrics.get("focus_minutes")
        comparison.append({"option": key, "cells": [budget, stage["status"].lower(),
                           _duration(focus) if focus is not None else None, gain, metrics.get("free_blocks"),
                           metrics.get("cluster_days")]})
    add(scatter, "Spread across days", "scatter")
    return options, primary, headers, comparison


def _explainers(source):
    notes = source.get("metadata", {}).get("calendar_explainers")
    return [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []


def _introduction(portfolio, options, selected, baseline, participants, requests, report):
    if not selected:
        return "No schedule to review", f"Solver result: {report['status'].lower().replace('_', ' ')}."
    if portfolio:
        slots = selected["placements"]
        if not slots:
            return "Make room for focus time", "Review how moving existing meetings changes the week."
        roles = {r.get("role") for r in requests}
        if roles and roles <= {"one-on-one", "one_on_one", "1:1"}:
            headline = "Where do the 1:1s fit?"
        elif report.get("scatter_comparison"):
            headline = "Can we bring these meetings together?"
        else:
            headline = "How should we plan the week?"
        headcount = len({p for event in slots for p in event["attendees"]})
        group = "1 person" if headcount == 1 else f"{headcount} people"
        summary = f"{_count(len(slots), 'meeting')} for {group}."
        action = "Compare schedules to see what would move." if len(options) > 1 else "Review the proposed times."
        return headline, summary + " " + action
    slot = selected["placements"][0]
    duration = timestamp_minute(slot["end"]) - timestamp_minute(slot["start"])
    if participants:
        summary = f"{_count(len(participants), 'attendee')} · {_duration(duration)}"
    else:
        summary = f"{_duration(duration)} meeting"
    if baseline and any(o["placements"][0]["start"] < baseline["placements"][0]["start"] for o in options):
        headline = "Can we meet earlier?"
    elif selected["availability"] == "conditional":
        headline = "Which time can we confirm?"
    else:
        headline = "When should we meet?"
    return headline, summary + (". Pick a start to see what would move." if len(options) > 1 else ".")


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
        options, primary, headers, comparison = _portfolio_choices(report, source, events)
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
    default_zone = source.get("portfolio", {}).get("timezone", "UTC")
    requests = source.get("requests", []) if portfolio else [source.get("request", {})]
    participants = list(dict.fromkeys(pid for request in requests for pid in request.get("attendees", [])))
    if portfolio and not participants:
        participants = list(dict.fromkeys(pid for option in options for event in option["placements"]
                                          for pid in event["attendees"]))
    baseline = next((o for o in options if not o["moves"] and o["availability"] != "conditional"), None)
    selected = next((o for o in options if o["id"] == primary), options[0] if options else None)
    headline, subtitle = _introduction(portfolio, options, selected, baseline, participants, requests, report)
    for option in options:
        option["proposal"] = _choice_proposal(option)
    zones = {p["metadata"]["timezone"] for p in source.get("people", {}).values()
             if isinstance(p.get("metadata", {}).get("timezone"), str)}
    if default_zone != "UTC":
        zones.add(default_zone)
    view = {"kind": "portfolio" if portfolio else "single", "headline": headline, "subtitle": subtitle,
            "participants": participants, "defaultTimezone": default_zone, "baseline": baseline["id"] if baseline else None,
            "status": report["status"].replace("_", " ").capitalize(), "current": current,
            "options": options, "primary": primary, "comparison": comparison, "headers": headers,
            "explainers": _explainers(source),
            "timezones": sorted(zones),
            "fullSnapshot": snapshot is not None}
    return view, snapshot


def _render(view):
    # A closing script tag in a meeting title must remain JSON text in the HTML parser.
    payload = json.dumps(view, ensure_ascii=True, allow_nan=False, separators=(",", ":")).replace("<", r"\u003c")
    data = files("scheduling_planner").joinpath("data")
    template = data.joinpath("plan.html").read_text(encoding="utf-8")
    for marker, filename in [("{{PLANNER_BRAND}}", "planner-brand.html"), ("{{PLANNER_UI_JS}}", "planner-ui.js"),
                             ("{{PLANNER_CSS}}", "planner.css"), ("{{REVIEW_GRID_CSS}}", "review-grid.css"),
                             ("{{REVIEW_GRID_JS}}", "review-grid.js"), ("{{REVIEW_JS}}", "review.js")]:
        if marker in template:
            template = template.replace(marker, data.joinpath(filename).read_text(encoding="utf-8"))
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


def _choice_proposal(choice):
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
    lines.extend(choice["facts"])
    lines.extend(choice["notes"])
    lines.extend(["", "Please confirm these times and proposed changes before updating calendars."])
    return "\n".join(lines) + "\n"


def _proposal(view):
    options = view["options"]
    if not options:
        return f"Calendar planning result: {view['status']}. No schedule was returned.\n"
    choice = next((p for p in options if p["id"] == view["primary"]), options[0])
    return choice["proposal"]


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
        if directory.exists():
            if not directory.is_dir() or directory.is_symlink() or any(directory.iterdir()):
                raise ValueError("export pack requires a new or empty directory")
            # Windows cannot rename a directory over an existing empty one.
            directory.rmdir()
        pack.rename(directory)
