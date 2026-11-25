"""Prepare an organizer's exact write set and apply it to their primary Google Calendar."""
from datetime import datetime
from http.client import HTTPSConnection, HTTPException
import json
import re
from urllib.parse import quote, urlencode

from .brief import brief
from .domain import effective_snapshot, iso, nonempty_text, require, snapshot_digest, timestamp_minute, whole_number


def _email(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+", value)),
            "bindings require an explicit calendar email for each identity")
    return value.casefold()


def _witness(snapshot, report, option_id):
    if report.get("kind") == "portfolio":
        require(option_id is None, "portfolio apply uses the displayed portfolio witness")
        require(report.get("status") in ("OPTIMAL", "FEASIBLE"), "portfolio has no feasible witness")
        return (report["snapshot_sha256"], report["placements"],
                [(m["id"], m["from"], m["to"], m["end"]) for m in report["moves"]])
    view = brief(report) if "advice" in report else report
    require(view.get("view") == "brief", "expected a brief, advice or portfolio result")
    option_id = view.get("primary_id") if option_id is None else option_id
    option = next((o for o in view["options"] if o["id"] == option_id), None)
    require(option is not None and option["availability"] == "verified",
            "select a verified option before preparing calendar writes")
    return (view["scope"]["snapshot_sha256"],
            [{"id": "$request", "start": option["start"], "end": option["end"],
              "attendees": snapshot["request"]["attendees"]}],
            [(m["event_id"], m["from_start"], m["to_start"], m["to_end"]) for m in option["moves"]])


def prepare_apply(snapshot, report, bindings, option_id=None):
    """Build a reviewable manifest. Bindings resolve local IDs to authorized Google accounts/occurrences."""
    digest, placements, moves = _witness(snapshot, report, option_id)
    effective_snapshot(report, snapshot)
    organizer = _email(bindings["organizer"])
    events = {e["id"]: e for e in snapshot.get("events", [])}
    requests = {r["id"]: r for r in snapshot.get("requests", [])}
    if "request" in snapshot:
        requests["$request"] = snapshot["request"]
    identities = {k: _email(v) for k, v in bindings["people"].items()}
    require(len(set(identities.values())) == len(identities), "two roster IDs resolve to one calendar")
    operations = []

    def operation(kind, key, source, people, start, end, spec):
        left, right = timestamp_minute(start), timestamp_minute(end)
        require(right - left == source["duration_minutes"], f"unexpected duration for {key}")
        require(isinstance(spec.get("optional_attendees", []), list), "optional_attendees must be a list")
        return {"action": kind, "source_id": key, "start": iso(left), "end": iso(right),
                "rest_minutes": {identities[p]: whole_number(snapshot["people"][p].get("rest_minutes", 0),
                                                             "rest_minutes", 0) for p in people},
                "optional_attendees": sorted({identities[p] for p in spec.get("optional_attendees", [])})}

    for key, original, start, end in moves:
        event = events[key]
        require(timestamp_minute(original) == timestamp_minute(event["start"]),
                f"original start differs for {key}")
        spec = bindings["events"][key]
        op = operation("move", key, event, event["attendees"], start, end, spec)
        operations.append(dict(op, from_start=iso(timestamp_minute(original)),
                               event_id=nonempty_text(spec["event_id"], "Google occurrence ID"),
                               etag=nonempty_text(spec["etag"], "Google occurrence etag")))
    for meeting in placements:
        key = meeting["id"]
        spec = bindings["requests"][key]
        op = operation("create", key, requests[key], meeting["attendees"], meeting["start"], meeting["end"], spec)
        operations.append(dict(op, title=nonempty_text(spec["title"], "new meeting title")))
    require(bool(operations), "empty plan")
    require(len({op["event_id"] for op in operations if op["action"] == "move"}) == len(moves),
            "multiple moves target the same Google occurrence")
    organizer_rest = next((snapshot["people"][p].get("rest_minutes", 0) for p in snapshot["people"]
                           if identities.get(p) == organizer), 0)
    _check_destinations(operations, organizer, organizer_rest)
    manifest = {"organizer_rest_minutes": organizer_rest, "apply_version": 1, "organizer": organizer, "snapshot_sha256": digest,
                "send_updates": "all", "operations": operations}
    return dict(manifest, approval_sha256=snapshot_digest(manifest))


class _CalendarError(ValueError):
    def __init__(self, status, method):
        self.status = status
        super().__init__(f"Google Calendar {method} returned HTTP {status}")


class _GoogleCalendar:
    def __init__(self, token):
        self.token = nonempty_text(token, "GOOGLE_CALENDAR_ACCESS_TOKEN")

    def request(self, method, path, *, query=None, body=None, etag=None):
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if etag:
            headers["If-Match"] = etag
        connection = HTTPSConnection("www.googleapis.com", timeout=30)
        target = "/calendar/v3" + path + ("?" + urlencode(query) if query else "")
        try:
            connection.request(method, target, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            data = response.read()
            if not 200 <= response.status < 300:
                raise _CalendarError(response.status, method)
            return json.loads(data)
        finally:
            connection.close()


def _resource(provider, event_id):
    try:
        return provider.request("GET", "/calendars/primary/events/" + quote(event_id, safe=""))
    except _CalendarError as exc:
        if exc.status != 404:
            raise
        return None


def _occurrence_key(event):
    # Attendee copies can have different resource IDs. A series shares one iCalUID.
    occurrence = event.get("originalStartTime", {})
    original = occurrence.get("date") or occurrence.get("dateTime")
    if "dateTime" in occurrence:
        original = datetime.fromisoformat(original)
    uid = event.get("iCalUID")
    if uid:
        return (uid, original, event.get("organizer", {}).get("email", "").casefold())
    return None


def _verify_existing(event, op, marker, organizer):
    require(event.get("status") != "cancelled" and not event.get("recurrence")
            and not event.get("attendeesOmitted"), "expected a live single occurrence with a complete roster")
    owner = event.get("organizer", {})
    require(owner.get("self") is True and owner.get("email", "").casefold() == organizer,
            "only the event organizer can approve this change")
    start = timestamp_minute(event.get("start", {}).get("dateTime"))
    end = timestamp_minute(event.get("end", {}).get("dateTime"))
    target, target_end = timestamp_minute(op["start"]), timestamp_minute(op["end"])
    attendees = {a.get("email", "").casefold(): a for a in event.get("attendees", [])}
    participants = set(op["rest_minutes"])
    optional = set(op["optional_attendees"])
    if event.get("extendedProperties", {}).get("private", {}).get("schedulePlan") == marker:
        require((start, end) == (target, target_end)
                and attendees.keys() - {organizer} == (participants | optional) - {organizer}
                and all(attendees.get(a, {}).get("optional") for a in optional - {organizer})
                and (op["action"] != "create" or event.get("summary") == op["title"]),
                "an already-applied occurrence changed; review the calendar")
        return True
    require(op["action"] == "move" and event.get("etag") == op["etag"],
            "occurrence revision changed or create ID is occupied")
    require(start == timestamp_minute(op["from_start"]) and end - start == target_end - target,
            "occurrence time changed")
    require(attendees.keys() - {organizer} == participants - {organizer}, "occurrence roster changed")
    return False


def _google_time(minute):
    return datetime.fromisoformat(iso(minute)).isoformat(timespec="seconds")


def _affected(op, organizer, organizer_rest):
    return {organizer: organizer_rest, **op["rest_minutes"]}


def _check_destinations(operations, organizer, organizer_rest):
    # The booking calendar also gets every event, including forums with a delegate.
    intervals = {}
    for op in operations:
        start, end = timestamp_minute(op["start"]), timestamp_minute(op["end"])
        for person, rest in _affected(op, organizer, organizer_rest).items():
            intervals.setdefault(person, []).append((start, end, rest))
    for person, visits in intervals.items():
        previous = None
        for start, end, rest in sorted(visits):
            require(previous is None or previous + rest <= start,
                    f"planned destinations overlap or break rest on {person}'s calendar")
            previous = end


def _clear_intervals(provider, operations, ignored, organizer, organizer_rest):
    for op in operations:
        for person, rest in _affected(op, organizer, organizer_rest).items():
            query = {"singleEvents": "true", "timeMin": _google_time(timestamp_minute(op["start"]) - rest),
                     "timeMax": _google_time(timestamp_minute(op["end"]) + rest)}
            while True:
                page = provider.request("GET", f"/calendars/{quote(person, safe='')}/events", query=query)
                require(page.get("accessRole") in ("reader", "writer", "owner"),
                        f"insufficient calendar visibility for {person}")
                for event in page.get("items", []):
                    if (event.get("status") == "cancelled" or event.get("transparency") == "transparent"
                            or _occurrence_key(event) in ignored):
                        continue
                    if any(a.get("self") and a.get("responseStatus") == "declined"
                           for a in event.get("attendees", [])):
                        continue
                    raise ValueError(f"calendar conflict for {person} near {op['source_id']}")
                if not page.get("nextPageToken"):
                    break
                query["pageToken"] = page["nextPageToken"]


def _write(provider, op, event, marker, event_id):
    creating = op["action"] == "create"
    attendees = {} if creating else {a["email"].casefold(): dict(a) for a in event.get("attendees", [])}
    if creating:
        attendees.update({p: {"email": p} for p in op["rest_minutes"]})
    for email in op["optional_attendees"]:
        attendees.setdefault(email, {"email": email})["optional"] = True
    private = dict((event or {}).get("extendedProperties", {}).get("private", {}), schedulePlan=marker)
    body = {"start": {"dateTime": _google_time(timestamp_minute(op["start"]))}, "end": {"dateTime": _google_time(timestamp_minute(op["end"]))},
            "extendedProperties": {"private": private}}
    if creating or op["optional_attendees"]:
        body["attendees"] = list(attendees.values())
    query = {"sendUpdates": "all"}
    path = "/calendars/primary/events"
    if creating:
        provider.request("POST", path, body=dict(body, id=event_id, summary=op["title"]), query=query)
    else:
        provider.request("PATCH", path + "/" + quote(event_id, safe=""), body=body, etag=op["etag"], query=query)


def apply_google(manifest, approval_digest, token):
    """Apply an explicitly approved batch using the authenticated organizer's token."""
    return _apply(manifest, approval_digest, _GoogleCalendar(token))


def _apply(manifest, approval_digest, provider):
    content = {k: v for k, v in manifest.items() if k != "approval_sha256"}
    digest = snapshot_digest(content)
    require(approval_digest == manifest.get("approval_sha256") == digest, "exact manifest approval required")
    require(manifest.get("apply_version") == 1 and manifest.get("send_updates") == "all",
            "unsupported apply manifest")
    organizer = _email(manifest["organizer"])
    organizer_rest = whole_number(manifest["organizer_rest_minutes"], "organizer_rest_minutes", 0)
    _check_destinations(manifest["operations"], organizer, organizer_rest)
    completed = []
    try:
        account = provider.request("GET", "/calendars/primary")
        require(_email(account.get("id")) == organizer, "approval must come from the signed-in organizer")
        pending, ignored = [], set()
        for index, op in enumerate(manifest["operations"]):
            marker = snapshot_digest([digest, index])
            require(op["action"] in ("create", "move"), "unknown calendar operation")
            event_id = marker if op["action"] == "create" else op["event_id"]
            event = _resource(provider, event_id)
            if event is not None:
                key = _occurrence_key(event)
                require(key is not None, "cannot identify the shared occurrence")
                already_applied = _verify_existing(event, op, marker, organizer)
                ignored.add(key)
                if already_applied:
                    completed.append({"action": op["action"], "source_id": op["source_id"], "event_id": event_id})
                    continue
            else:
                require(op["action"] == "create", "move target no longer exists")
            pending.append((op, event, marker, event_id))
        _clear_intervals(provider, [entry[0] for entry in pending], ignored, organizer, organizer_rest)
        for op, event, marker, event_id in pending:
            _write(provider, op, event, marker, event_id)
            completed.append({"action": op["action"], "source_id": op["source_id"], "event_id": event_id})
    except (ValueError, OSError, HTTPException) as exc:
        # A transport error can arrive after a write committed. A retry reconciles the marker.
        return {"status": "interrupted", "completed": completed, "error": str(exc)}
    return {"status": "applied", "completed": completed}
