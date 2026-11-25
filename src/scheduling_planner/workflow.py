"""Read-only reference orchestration for hosts providing normalized calendar evidence."""

from copy import deepcopy
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from .brief import brief
from .domain import arr, iso, nonempty_text, obj, timestamp_minute, whole_number, window_pairs
from .profiles import resolve_profile


def proof_claim(cap, ranking, proof):
    """Translate one ranking proof, preserving the original move scope."""
    stages = proof["stages"]
    optimal = proof["status"] == "optimal"
    first_is_start = proof["order"][0] == "start"
    start_proved = bool(stages and stages[0]["metric"] == "start" and stages[0]["proven"])
    feasible = proof["status"] in ("optimal", "best_found")
    return {"max_moves": cap, "ranking": ranking, "status": proof["status"],
            "earliest_proven": feasible and first_is_start and (optimal or start_proved)}


def _occurrence_fields(event):
    """Compare normalized occurrence instants while retaining policy and metadata fields."""
    fields = dict(event, start=timestamp_minute(event["start"]), attendees=sorted(event["attendees"]))
    if "alternatives" in fields:
        fields["alternatives"] = sorted(timestamp_minute(t) for t in fields["alternatives"])
    if "move_windows" in fields:
        fields["move_windows"] = sorted(window_pairs(fields["move_windows"], "move_windows", nonempty=True))
    return fields


def _read_missing(snapshot, read_person, trace):
    """Read missing invitee records; outside identities need an explicitly supplied record."""
    people = snapshot.setdefault("people", {})
    events = list(snapshot.get("events", []))
    by_id = {event["id"]: event for event in events}
    if len(events) != len(by_id):
        raise ValueError("duplicate occurrence IDs in supplied snapshot")
    for person in snapshot["request"]["attendees"]:
        if person in people or read_person is None:
            continue
        trace.append({"action": "read", "person": person})
        response = read_person(person)
        if response is None:
            continue
        if response["person_id"] != person:
            raise ValueError("calendar reader returned a different person")
        people[person] = deepcopy(response["person"])
        for event in response.get("events", []):
            if person not in event["attendees"]:
                raise ValueError("returned event is unrelated to the requested person")
            if event["id"] in by_id:
                previous = by_id[event["id"]]
                if _occurrence_fields(previous) != _occurrence_fields(event):
                    trace.append({"action": "clarify", "reason": "conflicting_occurrence",
                                  "subject": event["id"]})
                    return False
            else:
                events.append(deepcopy(event))
                by_id[event["id"]] = events[-1]
    snapshot["events"] = events
    referenced = set(snapshot["request"]["attendees"])
    for event in events:
        referenced.update(event["attendees"])
    missing = sorted(p for p in referenced if p not in people)
    if missing:
        trace.append({"action": "clarify", "reason": "missing_calendar", "people": missing,
                      "needs": ["dated_availability", "busy_coverage", "rest_minutes"]})
    return not missing


def run_workflow(snapshot, *, goal="any", identity_matches=None, deadlines=(), read_person=None,
                 profile=None, heuristics=None, registry=None, time_limit_seconds=None, display_zones=()):
    """Gather normalized evidence, then solve according to the supplied scheduling goal.

    The host selects an authorized read callback and trusted extensions. Input
    is copied. Outputs contain an observable action trace and an optional brief.
    This proposal workflow has no calendar-write capability.
    """
    if goal not in ("any", "earliest", "compare"):
        raise ValueError("goal must be any, earliest or compare")
    zones = {name: ZoneInfo(name) for name in display_zones}
    data = deepcopy(obj(snapshot, "snapshot"))
    request = obj(data.get("request"), "request")
    attendees = arr(request.get("attendees"), "request.attendees", nonempty=True)
    for person in attendees:
        nonempty_text(person, "request.attendees")
    if len(set(attendees)) != len(attendees):
        raise ValueError("request has duplicate attendees")
    data.setdefault("evidence_policy", "explicit")
    trace = []
    response = {"trace": trace, "recommendation": None}
    for alias in attendees:
        if identity_matches is None or alias not in identity_matches:
            continue
        matches = list(dict.fromkeys(identity_matches[alias]))
        if matches != [alias]:
            # A host that resolves an alias should normalize every attendee reference
            # to the selected ID before calling again.
            trace.append({"action": "clarify", "reason": "resolve_attendee", "subject": alias,
                          "matches": matches, "needs": ["identity", "dated_availability", "busy_coverage",
                                                        "rest_minutes"]})
            return response
    cutoffs = sorted({timestamp_minute(t, "deadline") for t in deadlines})
    if len(cutoffs) > 1:
        trace.append({"action": "clarify", "reason": "conflicting_deadline",
                      "deadlines": [iso(t) for t in cutoffs]})
        return response
    if cutoffs:
        constraints = data.setdefault("constraints", [])
        constraint_id = "workflow-deadline"
        taken = {c["id"] for c in constraints}
        while constraint_id in taken:
            constraint_id += "-"
        constraints.append({"id": constraint_id, "kind": "finish_by", "mode": "hard",
                            "params": {"event": "$request", "deadline": iso(cutoffs[0])}})
    if not _read_missing(data, read_person, trace):
        return response
    # Evidence collection can finish with a clarification before a solver is needed.
    from .advisor import advise

    settings = resolve_profile(data, profile, time_limit_seconds=time_limit_seconds)
    cap = whole_number(data.get("max_moves", 2), "max_moves", 0)
    budgets = [0, cap] if goal == "any" and cap else [cap]
    stop = monotonic() + settings.time_limit_seconds
    for i, permitted in enumerate(budgets):
        remaining = stop - monotonic()
        if remaining <= 0:
            trace.append({"action": "stop", "reason": "budget_exhausted"})
            return response
        rankings = {"any": ["preferred"], "earliest": ["earliest"], "compare": None}[goal]
        result = advise(dict(data, max_moves=permitted), profile=profile, heuristics=heuristics, registry=registry,
                        rankings=rankings, time_limit_seconds=remaining / (len(budgets) - i),
                        compare_moves=goal == "compare")
        raw, advice = result["result"], result["advice"]
        trace.append({"action": "solve", "max_moves": permitted, "objective": raw["scope"]["objective"],
                      "rankings": raw["scope"]["rankings"]})
        response["recommendation"] = brief(result)
        if advice["primary_id"] is not None or i == len(budgets) - 1:
            claims = [proof_claim(permitted, rank, p) for rank, p in raw["verified"]["proofs"].items()]
            if goal == "compare":
                for row in advice["move_comparison"]["caps"]:
                    if row["cap"] == permitted and "earliest" in raw["verified"]["proofs"]:
                        continue
                    if row["proof"] is not None:
                        claims.append(proof_claim(row["cap"], "earliest", row["proof"]))
            local_starts = {}
            for option in advice["options"]:
                if option["id"] == advice["primary_id"]:
                    start = datetime.fromisoformat(option["plan"]["start"])
                    local_starts = {name: start.astimezone(zone).isoformat(timespec="minutes")
                                    for name, zone in zones.items()}
            trace.append({"action": "present", "claims": claims, "guidance": advice["guidance"],
                          "local_starts": local_starts})
            return response
    return response
