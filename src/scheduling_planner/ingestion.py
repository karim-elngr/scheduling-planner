"""Compile one roster and a dated batch of host calendar evidence into a snapshot.

The host supplies complete read intervals explicitly. A missing calendar leaves a
person's occupancy unknown, even when their working schedule is well known.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    arr, intersect_windows, iso, local_datetime, local_days, merge, nonempty_text, obj, parse_snapshot,
    read_calendar, require, timestamp_minute,
    whole_number, window_pairs,
)

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ONE_DAY = timedelta(days=1)
MOVE_FIELDS = ("alternatives", "move_windows", "step_minutes")
EVENT_FACTS = ("title", "organizer", "priority", "role", "status", "squads", "review_date",
               "renewal_date", "until", "series_id", "recurrence_count")


def _zone(name):
    try:
        return ZoneInfo(nonempty_text(name, "timezone"))
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Invalid IANA timezone: {name!r}") from exc


def _days(values, label):
    days = arr(values, label)
    require(all(day in DAYS for day in days) and len(set(days)) == len(days),
            f"{label} must contain distinct weekday names from {DAYS}")
    return sorted(days, key=DAYS.index)


def _date(value, label):
    try:
        return date.fromisoformat(nonempty_text(value, label))
    except ValueError as exc:
        raise ValueError(f"{label} requires an ISO date") from exc


def _clock(value):
    try:
        require(isinstance(value, str) and len(value) == 5 and value[2] == ":",
                "working_hours requires HH:MM")
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("working_hours requires HH:MM") from exc


def _wall(day, clock, zone, fold=0):
    instant = datetime.combine(day, clock, tzinfo=zone).replace(fold=fold)
    # ZoneInfo permits nonexistent wall times. Reject them instead of shifting
    # a meeting during a DST gap without the host's knowledge.
    stamp = timestamp_minute(instant.isoformat(timespec="minutes"))
    require(local_datetime(stamp, zone).replace(tzinfo=None) == instant.replace(tzinfo=None),
            f"{instant} falls in a local clock gap; supply the dated exception")
    return stamp


def _clip(intervals, window):
    return merge((max(a, window[0]), min(b, window[1])) for a, b in intervals
                 if max(a, window[0]) < min(b, window[1]))


def _pairs(intervals):
    return [[iso(a), iso(b)] for a, b in intervals]


def _person(pid, profile, calendar, window):
    zone = _zone(profile.get("timezone"))
    default_weekend = ["Sat", "Sun"] if "working_hours" in profile else []
    weekend = _days(profile.get("weekend", default_weekend), "weekend")
    metadata = dict(obj(profile.get("metadata", {}), f"{pid}.metadata"),
                    timezone=zone.key, weekend=weekend)
    for key in ("squad", "squads"):
        if key in profile:
            metadata[key] = profile[key]
    available = _clip(window_pairs(profile.get("available", []), f"{pid}.available"), window)
    possible = _clip(window_pairs(profile.get("possible", []), f"{pid}.possible"), window)
    first = local_datetime(window[0], zone).date()
    last = local_datetime(window[1] - 1, zone).date()
    explain = f"{pid} ({zone.key})"
    if "working_hours" in profile:
        hours = arr(profile["working_hours"], f"{pid}.working_hours")
        require(len(hours) == 2, "working_hours requires [opening, closing]")
        opening, closing = map(_clock, hours)
        require(opening < closing, "working_hours requires closing after opening")
        intervals = []
        for offset in range((last - first).days + 1):
            day = first + offset * ONE_DAY
            if DAYS[day.weekday()] not in weekend:
                intervals.append((_wall(day, opening, zone), _wall(day, closing, zone)))
        eligible = _clip(intervals, window)
        available = intersect_windows(available, eligible) if "available" in profile else eligible
        possible = intersect_windows(possible, eligible)
        metadata["working_hours"] = list(hours)
        explain += f" works {hours[0]}–{hours[1]} local time"
    elif "weekend" in profile:
        # An explicit weekend also restricts ad hoc dated availability.
        eligible = [(start, end) for day, start, end in local_days([window], zone)
                    if DAYS[day.weekday()] not in weekend]
        available = intersect_windows(available, eligible)
        possible = intersect_windows(possible, eligible)
    explain += f". Weekend: {', '.join(weekend) or 'none'}."
    observed = window_pairs(calendar.get("busy_coverage", []), f"{pid}.busy_coverage")
    return {"available": _pairs(available), "possible": _pairs(possible),
            "busy_coverage": _pairs(_clip(observed, window)),
            "rest_minutes": whole_number(profile.get("rest_minutes", 0), "rest_minutes", 0),
            "metadata": metadata}, explain


def _endpoint(value, label, zone):
    if isinstance(value, str) and len(value) == 10:
        return _wall(_date(value, label), time(), zone)
    return timestamp_minute(value, label)


def _event_bounds(event, zone):
    start = _endpoint(event.get("start"), "start", zone)
    if "end" in event:
        end = _endpoint(event["end"], "end", zone)
        require(end > start, "event end must follow start")
        if "duration_minutes" in event:
            require(whole_number(event["duration_minutes"], "duration_minutes", 1) == end - start,
                    "end and duration_minutes disagree")
    else:
        end = start + whole_number(event.get("duration_minutes"), "duration_minutes", 1)
        iso(end)
    return start, end


def _recurrences(series, zone, window):
    """Enumerate original local dates, including exceptions moved into the window."""
    start, end = _event_bounds(series, zone)
    anchor = local_datetime(start, zone)
    rule = obj(series.get("recurrence"), "recurrence")
    require(not set(rule) - {"frequency", "weekdays", "count", "until", "interval"},
            "unsupported recurrence fields")
    frequency = rule.get("frequency")
    require(frequency in ("daily", "weekly"), "recurrence frequency must be daily or weekly")
    interval = whole_number(rule.get("interval", 1), "recurrence.interval", 1)
    count = whole_number(rule["count"], "recurrence.count", 1) if "count" in rule else None
    until = _date(rule["until"], "recurrence.until") if "until" in rule else None
    require("weekdays" not in rule or frequency == "weekly", "weekdays requires weekly recurrence")
    days = [DAYS.index(d) for d in _days(rule.get("weekdays", [DAYS[anchor.weekday()]]), "weekdays")]
    require(anchor.weekday() in days, "series start must be one of its recurrence weekdays")
    exceptions = {_date(k, "exception date"): v for k, v in
                  obj(series.get("exceptions", {}), "exceptions").items()}
    # Look back far enough for occurrences which overlap the left boundary.
    # Explicitly visit moved exceptions even when their original date is outside.
    left = max(anchor.date(), local_datetime(max(start, window[0] - (end - start)), zone).date())
    right = local_datetime(window[1] - 1, zone).date()
    dates = {left + delta * ONE_DAY for delta in range((right - left).days + 1)}
    for day in sorted(dates | exceptions.keys()):
        delta = (day - anchor.date()).days
        if frequency == "daily":
            matches = delta >= 0 and delta % interval == 0
            index = delta // interval
        else:
            week = (delta + anchor.weekday()) // 7
            matches = delta >= 0 and week % interval == 0 and day.weekday() in days
            index = (week // interval * len(days) + sum(d < day.weekday() for d in days)
                     - sum(d < anchor.weekday() for d in days))
        occurs = matches and (count is None or index < count) and (until is None or day <= until)
        if day in exceptions:
            require(occurs, f"exception date {day} is not in the series")
        if not occurs or (day in exceptions and exceptions[day] is None):
            continue
        override = obj(exceptions.get(day, {}), "recurrence exception")
        event = {**series, **override}
        if "start" not in override:
            event["start"] = iso(_wall(day, anchor.time(), zone, anchor.fold))
        if "end" not in override:
            event.pop("end", None)
            # All-day series keep their calendar-day length over DST.
            if len(series["start"]) == 10 and "end" in series and len(series["end"]) == 10 and "duration_minutes" not in override:
                length = _date(series["end"], "end") - anchor.date()
                target_day = local_datetime(_endpoint(event["start"], "start", zone), zone).date()
                require(length <= date.max - target_day,
                        "all-day occurrence end exceeds the supported date range")
                event["end"] = iso(_wall(target_day + length, time(), zone))
            else:
                event["duration_minutes"] = override.get("duration_minutes", end - start)
        elif "duration_minutes" not in override:
            event.pop("duration_minutes", None)
        event["series_id"] = series.get("uid", series["id"])
        # The series master is never a writable occurrence. A host may supply
        # the actual single-instance ID and etag on a dated exception.
        event["_master_id"] = series["id"]
        if "etag" not in override:
            event.pop("etag", None)
        event["original_date"] = day.isoformat()
        if count is not None:
            event["recurrence_count"] = count
        if until:
            event["until"] = until.isoformat()
        yield event


def _ids(values, label):
    return {nonempty_text(v, label) for v in arr(values, label)}


def _occurrence_aliases(record, event, identity):
    """Use original local dates for aliases, including rescheduled series instances."""
    aliases = {identity, record["id"], event["id"]}
    for row in (record, event):
        if "host_id" in row:
            aliases.add(nonempty_text(row["host_id"], "host_id"))
    if "original_date" in event:
        original = _date(event["original_date"], "original_date").isoformat()
        aliases.update(f"{ref}@{original}" for ref in aliases - {identity})
    return aliases


def _view(event, owner, calendar_id, people, zone, window):
    if event.get("status") in ("cancelled", "canceled", "declined") or event.get("transparency") == "transparent":
        return None
    start, end = _event_bounds(event, zone)
    if start >= window[1] or end <= window[0]:
        return None
    provider_id = nonempty_text(event.get("id"), "event id")
    identity = nonempty_text(event.get("uid", provider_id), "uid")
    source = {"calendar_id": nonempty_text(calendar_id, "calendar_id"), "event_id": provider_id}
    if "original_date" in event:
        original_date = _date(event["original_date"], "original_date").isoformat()
        series_id = nonempty_text(event.get("series_id", identity), "series_id")
        identity = f"{series_id}@{original_date}"
        source["original_date"] = original_date
    if "_master_id" in event:
        source["series_id"] = event["_master_id"]
        if provider_id == event["_master_id"]:
            del source["event_id"]
    if "etag" in event:
        source["etag"] = nonempty_text(event["etag"], "etag")
    attendees = _ids(event.get("attendees", []), "attendees") | {owner}
    optional = _ids(event.get("optional_attendees", []), "optional_attendees")
    require(type(event.get("optional", False)) is bool, "optional must be a boolean")
    if event.get("optional") is True:
        optional.add(owner)
    tentative = _ids(event.get("tentative_attendees", []), "tentative_attendees")
    if event.get("status") == "tentative":
        tentative.add(owner)
    attendees.update(optional | tentative)
    complete = event.get("attendees_complete", "attendees" in event)
    require(type(complete) is bool, "attendees_complete must be a boolean")
    ooo = event.get("ooo", False)
    require(type(ooo) is bool, "ooo must be a boolean")
    tier = event.get("move_tier", "protected" if event.get("priority") == "high" else "ordinary")
    require(tier in ("ordinary", "protected"), "invalid move_tier")
    metadata = {**obj(event.get("metadata", {}), "event.metadata"),
                **{k: event[k] for k in EVENT_FACTS if k in event}}
    return {"id": identity, "start": iso(start), "duration_minutes": end - start,
            "attendees": sorted(attendees & people),
            "attendees_complete": complete and attendees <= people, "external": bool(attendees - people),
            "move_tier": "protected" if ooo else tier,
            "metadata": {**metadata, "ooo": ooo, "source": source,
                         "optional_attendees": sorted(optional), "tentative_attendees": sorted(tentative)},
            **{k: event[k] for k in MOVE_FIELDS if k in event}}


def _move_grant(views, warnings):
    """Reconcile authorized replacements after normalizing equivalent start instants."""
    grants = []
    omitted_original = False
    first = views[0]
    start = timestamp_minute(first["start"])
    for view in views:
        if "alternatives" not in view and "move_windows" not in view:
            continue
        grant = {k: view[k] for k in MOVE_FIELDS if k in view}
        if "alternatives" in grant:
            values = arr(grant["alternatives"], f"{first['id']}.alternatives")
            instants = [timestamp_minute(t, f"{first['id']}.alternatives") for t in values]
            omitted_original |= start in instants
            grant["alternatives"] = [iso(t) for t in sorted(instants) if t != start]
        grants.append(grant)
    if omitted_original:
        warnings.append(f"Ignored the original start in alternatives for {first['id']!r}. Staying in place is implicit.")
    same_grant = grants and all(grant == grants[0] for grant in grants)
    return grants[0] if same_grant else {}


def _combine(views, warnings):
    """Prefer richer authorized facts. Use conservative attendance and move grants."""
    first = views[0]
    attendees = sorted({a for view in views for a in view["attendees"]})
    # A private Busy entry can be reconciled with a complete organizer view.
    complete = (not any(v["external"] for v in views)
                and any(v["attendees_complete"] and v["attendees"] == attendees for v in views))
    metadata = {}
    sources = []
    for view in views:
        facts = view["metadata"]
        for key, value in facts.items():
            if key not in ("source", "ooo", "optional_attendees", "tentative_attendees"):
                if key not in metadata or metadata[key] in ("", None, "Busy"):
                    metadata[key] = value
        if facts["source"] not in sources:
            sources.append(facts["source"])
    organizers = {nonempty_text(view["metadata"]["organizer"], "organizer") for view in views
                  if view["metadata"].get("organizer")}
    if len(organizers) > 1:
        metadata.pop("organizer", None)
        complete = False
        warnings.append(f"{first['id']} has conflicting organizers. Confirm ownership before moving it.")
    metadata["sources"] = sources
    metadata["ooo"] = any(v["metadata"]["ooo"] for v in views)
    for key in ("optional_attendees", "tentative_attendees"):
        metadata[key] = sorted({pid for v in views for pid in v["metadata"][key]})
    return {"id": first["id"], "start": first["start"], "duration_minutes": first["duration_minutes"],
            "attendees": attendees, "attendees_complete": complete,
            "move_tier": "protected" if any(v["move_tier"] == "protected" for v in views) else "ordinary",
            "metadata": metadata, **(_move_grant(views, warnings) if complete and not metadata["ooo"] else {})}


def _resolve_refs(values, label, id_map):
    resolved = []
    refs = [nonempty_text(ref, label) for ref in arr(values, label)]
    require(len(set(refs)) == len(refs), f"{label} must contain unique IDs")
    for ref in refs:
        targets = id_map.get(ref, [])
        require(targets, f"{label}: unknown occurrence {ref!r} in the collected window")
        require(len(targets) == 1,
                f"{label}: ambiguous occurrence {ref!r} matches {targets}; use a unique occurrence reference")
        resolved.append(targets[0])
    return list(dict.fromkeys(resolved))


def _resolve_portfolio(portfolio, id_map):
    policy = dict(obj(portfolio, "portfolio"))
    if "movable_rituals" in policy:
        policy["movable_rituals"] = _resolve_refs(policy["movable_rituals"], "portfolio.movable_rituals", id_map)
    if "cluster" in policy:
        cluster = dict(obj(policy["cluster"], "portfolio.cluster"))
        if "events" in cluster:
            cluster["events"] = _resolve_refs(cluster["events"], "portfolio.cluster.events", id_map)
        policy["cluster"] = cluster
    return policy


def build_snapshot(batch):
    """Build a v1.1 single-request or portfolio snapshot from provider-neutral host data."""
    obj(batch, "batch")
    window, = window_pairs([batch.get("window")], "window")
    roster = obj(batch.get("roster"), "roster")
    calendars = obj(batch.get("calendars", {}), "calendars")
    require(roster and calendars.keys() <= roster.keys(), "calendars must be keyed by roster person")
    require(not ("request" in batch and "requests" in batch), "supply either request or requests")
    people, explainers = {}, []
    groups = defaultdict(lambda: defaultdict(list))
    for pid, profile in sorted(roster.items()):
        nonempty_text(pid, "person id")
        calendar = obj(calendars.get(pid, {}), f"{pid}.calendar")
        people[pid], explanation = _person(pid, obj(profile, pid), calendar, window)
        explainers.append(explanation)
        for key in ("events", "series"):
            for record in arr(calendar.get(key, []), key):
                obj(record, key)
                nonempty_text(record.get("id"), "event id")
                zone = _zone(record.get("timezone", profile["timezone"]))
                occurrences = _recurrences(record, zone, window) if key == "series" else [record]
                for event in occurrences:
                    view = _view(event, pid, calendar.get("calendar_id", pid), roster.keys(), zone, window)
                    if view:
                        view["aliases"] = _occurrence_aliases(record, event, view["id"])
                        groups[view["id"]][(view["start"], view["duration_minutes"])].append(view)
    events, warnings, reserved = [], [], set(groups)
    aliases = defaultdict(set)
    for identity, placements in sorted(groups.items()):
        conflict = len(placements) > 1
        if conflict:
            warnings.append(f"{identity} has conflicting dates or durations. All observed intervals are blocked until reconciled.")
        for _, views in sorted(placements.items()):
            event = _combine(views, warnings)
            if conflict:
                occurrence_id = f"{identity}@{event['start']}/{event['duration_minutes']}"
                while occurrence_id in reserved:
                    occurrence_id += "#"
                reserved.add(occurrence_id)
                event.update(id=occurrence_id, attendees_complete=False)
                # Every attendee observed on this disputed identity may be busy
                # at either reported time, even if one calendar hides the invite.
                event["attendees"] = sorted({p for group in placements.values() for v in group for p in v["attendees"]})
                for key in MOVE_FIELDS:
                    event.pop(key, None)
            aliases[event["id"]].add(event["id"])
            for view in views:
                for alias in view["aliases"]:
                    aliases[alias].add(event["id"])
            events.append(event)
    id_map = {ref: sorted(targets) for ref, targets in sorted(aliases.items())}
    snapshot = {k: v for k, v in batch.items() if k not in ("window", "roster", "calendars")}
    snapshot.update(schema_version="1.1", evidence_policy="explicit", people=people, events=events,
                    metadata={**obj(batch.get("metadata", {}), "metadata"),
                              "window": _pairs([window])[0], "event_id_map": id_map,
                              "calendar_explainers": explainers, "ingestion_warnings": warnings})
    if "portfolio" in snapshot:
        snapshot["portfolio"] = _resolve_portfolio(snapshot["portfolio"], id_map)
    if "request" in snapshot:
        parse_snapshot(snapshot)
    else:
        arr(snapshot.setdefault("requests", []), "requests")
        read_calendar(snapshot)
    return snapshot


def collect_snapshot(batch, read_calendar):
    """Call an authorized host reader once per roster member for the dated window.

    read_calendar(person_id, start, end) returns the same provider-neutral feed
    as a calendars entry. The reader owns pagination and reports exactly which
    intervals it observed in busy_coverage. Missing coverage stays unknown.
    """
    obj(batch, "batch")
    require("calendars" not in batch, "collect_snapshot reads calendars from the supplied reader")
    window, = window_pairs([batch.get("window")], "window")
    start, end = (iso(value) for value in window)
    calendars, warnings = {}, []
    for pid in sorted(obj(batch.get("roster"), "roster")):
        try:
            calendars[pid] = read_calendar(pid, start, end)
        except OSError as exc:
            warnings.append(f"{pid}: calendar read failed ({type(exc).__name__}); coverage is unknown.")
    snapshot = build_snapshot({**batch, "calendars": calendars})
    snapshot["metadata"]["ingestion_warnings"].extend(warnings)
    return snapshot
