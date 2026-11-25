"""Describe observed week patterns and propose a soft template for portfolio planning."""

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    arr, gaps, intersect_windows, local_days, merge, nonempty_text, obj, require, timestamp_minute,
    whole_number, window_pairs,
)


def _members(value, label):
    return {nonempty_text(name, label) for name in arr(value, label)}


def _person_days(person, occupied, zone, window):
    eligible = merge(window_pairs(person.get("available"), "available"))
    if window:
        eligible = list(intersect_windows(eligible, [window]))
    coverage = merge(window_pairs(person.get("busy_coverage", []), "busy_coverage"))
    observed = list(intersect_windows(eligible, coverage))
    busy = merge(occupied)
    rows = defaultdict(lambda: {
        "eligible_minutes": 0, "covered_work_minutes": 0, "free_minutes": 0,
        "busy_minutes": 0, "free_blocks": 0, "longest_free_minutes": 0,
    })
    for day, left, right in local_days(eligible, zone):
        rows[day]["eligible_minutes"] += right - left
    for day, left, right in local_days(observed, zone):
        row = rows[day]
        row["covered_work_minutes"] += right - left
        for start, end in gaps(busy, left, right):
            duration = end - start
            row["free_blocks"] += 1
            row["free_minutes"] += duration
            row["longest_free_minutes"] = max(row["longest_free_minutes"], duration)
        row["busy_minutes"] = row["covered_work_minutes"] - row["free_minutes"]
    return {day.isoformat(): row for day, row in sorted(rows.items())}


def _invite_review(event_id, person, optional, tentative, cross_squad):
    signals = []
    if optional:
        signals.append("optional")
    if tentative:
        signals.append("tentative")
    if cross_squad:
        signals.append("multi_squad")
    suggestion = None
    if cross_squad and (tentative or optional):
        suggestion = "Ask the organizer whether one squad delegate can represent this attendee."
    elif optional and tentative:
        suggestion = "Ask the attendee whether to decline this optional invitation."
    return {"event_id": event_id, "person": person, "score": len(signals),
            "signals": signals, "suggestion": suggestion}


def _load_and_hygiene(events, roster, people, window):
    occupied = defaultdict(list)
    series = defaultdict(list)
    reviews = []
    seen = set()
    for event in events:
        obj(event, "event")
        event_id = nonempty_text(event.get("id"), "event.id")
        require(event_id not in seen, f"duplicate occurrence {event_id}; reconcile calendar views first")
        seen.add(event_id)
        facts = obj(event.get("metadata", {}), "event.metadata")
        start = timestamp_minute(event.get("start"), f"{event_id}.start")
        duration = whole_number(event.get("duration_minutes"), f"{event_id}.duration_minutes", 1)
        end = start + duration
        if window:
            start, end = max(start, window[0]), min(end, window[1])
            if start >= end:
                continue
            duration = end - start
        participants = _members(event.get("attendees"), f"{event_id}.attendees")
        require(participants <= roster.keys(), f"{event_id} has an unknown attendee")
        optional = _members(facts.get("optional_attendees", []), "optional_attendees")
        tentative = _members(facts.get("tentative_attendees", []), "tentative_attendees")
        if "tentative_attendees" not in facts and facts.get("status") == "tentative":
            tentative = participants
        squads = _members(facts.get("squads", []), "squads")
        for pid in participants:
            profile = roster[pid].get("metadata", {})
            squads.update(_members(profile.get("squads", []), "person.squads"))
            if profile.get("squad"):
                squads.add(nonempty_text(profile["squad"], "person.squad"))
        organizer = facts.get("organizer")
        if organizer is not None:
            nonempty_text(organizer, "organizer")
        for pid in sorted(participants):
            occupied[pid].append((start, end))
            if facts.get("ooo"):
                people[pid]["ooo_minutes"] += duration
            elif organizer == pid:
                people[pid]["organized_minutes"] += duration
            elif organizer is None:
                people[pid]["unknown_organizer_minutes"] += duration
            else:
                people[pid]["invited_minutes"] += duration
                review = _invite_review(event_id, pid, pid in optional, pid in tentative, len(squads) >= 2)
                if review["signals"]:
                    reviews.append(review)
        if facts.get("series_id"):
            series[nonempty_text(facts["series_id"], "series_id")].append((event_id, facts))
    return occupied, sorted(reviews, key=lambda r: (-r["score"], r["event_id"], r["person"])), series


def _has_sunset(facts):
    count = facts.get("recurrence_count")
    if type(count) is int and count > 0:
        return True
    for key in ("review_date", "renewal_date", "until"):
        value = facts.get(key)
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                continue
            return True
    return False


def _sunset_reviews(series):
    reviews = []
    for series_id, occurrences in sorted(series.items()):
        if any(_has_sunset(facts) for _, facts in occurrences):
            continue
        organizers = sorted({facts["organizer"] for _, facts in occurrences if facts.get("organizer")})
        reviews.append({"series_id": series_id, "organizer": organizers[0] if len(organizers) == 1 else None,
                        "occurrences": sorted(event_id for event_id, _ in occurrences),
                        "suggestion": "Ask the organizer for a review, renewal or end date."})
    return reviews


def _suggest_template(days, timezone):
    # Pool by weekday across dated observations. Rates avoid preferring weekdays
    # merely because they appear more often in the supplied window.
    weekdays = defaultdict(lambda: defaultdict(int))
    for day in days:
        totals = weekdays[date.fromisoformat(day["date"]).weekday()]
        if day["covered_work_minutes"]:
            totals["observed_days"] += 1
        for key in ("covered_work_minutes", "busy_minutes", "sum_longest_free_minutes"):
            totals[key] += day[key]
    covered = [d for d in sorted(weekdays) if weekdays[d]["covered_work_minutes"]]
    rules = []
    reasons = {}
    if covered:
        maker = max(covered, key=lambda d: (
            weekdays[d]["sum_longest_free_minutes"] / weekdays[d]["observed_days"],
            -weekdays[d]["busy_minutes"] / weekdays[d]["covered_work_minutes"]))
        covered.remove(maker)
        rules.append({"role": "maker", "weekdays": [maker], "mode": "soft"})
        reasons["maker"] = "Largest sum of individual longest free blocks per observed date."
    if covered:
        align = max(covered, key=lambda d: weekdays[d]["busy_minutes"] / weekdays[d]["covered_work_minutes"])
        covered.remove(align)
        rules.append({"role": "align", "weekdays": [align], "mode": "soft"})
        reasons["align"] = "Most occupied remaining weekday by share of covered work time."
    if covered:
        one_on_one = max(covered, key=lambda d: weekdays[d]["busy_minutes"] / weekdays[d]["covered_work_minutes"])
        rules.append({"role": "one_on_one", "weekdays": [one_on_one], "mode": "soft"})
        reasons["one_on_one"] = "Cluster 1:1s on the most occupied remaining weekday."
    return {"timezone": timezone, "week_template": rules, "rationale": reasons}


def diagnose_week(snapshot):
    """Summarize normalized occurrences; a proposal neither moves events nor proves a gain.

    Load counts invitation minutes (overlapping invitations each count). Focus
    measures elapsed unoccupied time within observed working intervals. Missing
    coverage stays unknown even for legacy inputs. Day roles use the portfolio's
    team timezone; they are soft suggestions for requests carrying those roles.
    """
    obj(snapshot, "snapshot")
    timezone = nonempty_text(obj(snapshot.get("portfolio", {}), "portfolio").get("timezone", "UTC"),
                             "portfolio.timezone")
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"unknown portfolio timezone {timezone!r}") from error
    roster = obj(snapshot.get("people"), "people")
    people = {}
    for pid, person in sorted(roster.items()):
        obj(person, "person")
        profile = obj(person.get("metadata", {}), "person.metadata")
        people[pid] = {"timezone": profile.get("timezone"), "organized_minutes": 0,
                       "invited_minutes": 0, "unknown_organizer_minutes": 0, "ooo_minutes": 0}
    metadata = obj(snapshot.get("metadata", {}), "metadata")
    window = window_pairs([metadata["window"]], "metadata.window")[0] if "window" in metadata else None
    occupied, reviews, series = _load_and_hygiene(arr(snapshot.get("events", []), "events"), roster, people, window)
    totals = defaultdict(lambda: defaultdict(int))
    for pid, person in people.items():
        person["days"] = _person_days(roster[pid], occupied[pid], zone, window)
        for day, values in person["days"].items():
            aggregate = totals[day]
            for field, value in values.items():
                key = "sum_longest_free_minutes" if field == "longest_free_minutes" else field
                aggregate[key] += value
    days = [{"date": day, **values} for day, values in sorted(totals.items())]
    return {"kind": "calendar_os", "timezone": timezone, "people": people, "days": days,
            "calendar_explainers": list(arr(metadata.get("calendar_explainers", []), "calendar_explainers")),
            "evidence_warnings": list(arr(metadata.get("ingestion_warnings", []), "ingestion_warnings")),
            "invite_hygiene": reviews, "recurring_sunsets": _sunset_reviews(series),
            "template_proposal": _suggest_template(days, timezone)}
