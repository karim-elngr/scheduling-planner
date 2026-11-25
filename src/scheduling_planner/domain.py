"""Snapshot validation, time-domain normalization, and immutable occurrence records."""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from operator import itemgetter
import re

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MIN_MINUTE = (datetime.min.date() - EPOCH.date()).days * 1440
MAX_MINUTE = (datetime.max.date() - EPOCH.date()).days * 1440 + 1439
REQUEST = "$request"
MAX_GENERATED_STARTS = 20000
MAX_DATE_MINUTES = MAX_MINUTE - MIN_MINUTE
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::00(?:\.0+)?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")


def nonempty_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def integer_value(value):
    """JSON Schema treats an integral numeric value such as 30.0 as an integer."""
    return int(value) if type(value) is float and value.is_integer() else value


def whole_number(value, field, minimum):
    value = integer_value(value)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def timestamp_minute(value, label="timestamp"):
    """Convert an offset-aware, minute-aligned timestamp to UTC epoch minutes."""
    nonempty_text(value, label)
    if not TIMESTAMP.fullmatch(value):
        raise ValueError(f"{label} must be an offset-aware ISO timestamp at whole-minute precision")
    try:
        instant = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        offset = instant.utcoffset()
        if offset is None or offset.total_seconds() % 60 or instant.second or instant.microsecond:
            raise ValueError("requires whole-minute precision and an explicit UTC offset")
        delta = instant.astimezone(timezone.utc) - EPOCH
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{label}: invalid timestamp ({error})") from error
    return delta.days * 1440 + delta.seconds // 60


def require(condition, message):
    if not condition:
        raise ValueError(message)


def obj(value, label):
    require(isinstance(value, dict), f"{label} must be an object")
    return value


def fields(value, allowed, label):
    """Reject unsupported constraint fields. Domain-specific facts belong in metadata."""
    obj(value, label)
    extras = set(value).difference(allowed, {"metadata"})
    require(not extras, f"{label} has unsupported fields: {', '.join(sorted(map(str, extras)))}")
    if "metadata" in value:
        obj(value["metadata"], f"{label}.metadata")


def arr(value, label, nonempty=False):
    require(isinstance(value, list), f"{label} must be a list")
    require(not nonempty or bool(value), f"{label} must be nonempty")
    return value


def iso(value):
    try:
        return (EPOCH + timedelta(minutes=value)).isoformat(timespec="minutes").replace("+00:00", "Z")
    except OverflowError as error:
        raise ValueError("A proposed end time exceeds the supported date range") from error


def merge(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def intersect_windows(left, right):
    """Intersect two lists of sorted, disjoint time windows."""
    a = b = 0
    while a < len(left) and b < len(right):
        lo, hi = left[a]
        start, end = right[b]
        if max(lo, start) < min(hi, end):
            yield max(lo, start), min(hi, end)
        if hi <= end:
            a += 1
        if end <= hi:
            b += 1


def local_days(windows, zone):
    """Split UTC minute windows at local midnights, preserving DST day lengths."""
    minute = timedelta(minutes=1)
    for start, end in windows:
        while start < end:
            day = (EPOCH + start * minute).astimezone(zone).date()
            boundary = end
            if day != datetime.max.date():
                midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
                # Round up to the next UTC grid minute for historical offsets.
                boundary = min(end, -((EPOCH - midnight) // minute))
            yield day, start, boundary
            start = boundary


def window_pairs(values, label, nonempty=False):
    result = []
    for i, pair in enumerate(arr(values, label, nonempty)):
        require(isinstance(pair, list) and len(pair) == 2, f"{label}[{i}] must be a pair")
        start, end = (timestamp_minute(value, f"{label}[{i}]") for value in pair)
        require(start < end, f"{label}[{i}] requires end after start")
        result.append((start, end))
    return result


def _window_domain(values, label, step, duration, original, allow_changes):
    """Build each window's own grid, merging overlapping grids of equal phase.

    A whole meeting must fit inside one supplied window. The length cap is on
    unique replacement starts, measured before filtering by attendance windows.
    """
    by_phase = {}
    for left, right in window_pairs(values, label, nonempty=True):
        last = right - duration
        if last < left:
            continue
        phase = left % step
        first_index = (left - phase) // step
        stop_index = (last - phase) // step + 1
        by_phase.setdefault(phase, []).append((first_index, stop_index))
    grid = [(phase, start, stop) for phase, blocks in by_phase.items()
            for start, stop in merge(blocks)]
    count = sum(stop - start for _, start, stop in grid)
    if original is not None and any(
        original % step == phase and start <= original // step < stop
        for phase, start, stop in grid
    ):
        count -= 1
    require(count <= MAX_GENERATED_STARTS,
            f"{label} expands to {count} starts; maximum is {MAX_GENERATED_STARTS}")
    if not allow_changes:
        return [], count
    starts = (phase + index * step for phase, start, stop in grid for index in range(start, stop))
    return sorted(when for when in starts if when != original), count


def generate_starts(values, label, step, duration, original):
    """Expand an occurrence's declared time grids to distinct replacement starts."""
    starts, _ = _window_domain(values, label, step, duration, original, allow_changes=True)
    return starts


def _explicit_starts(values, label, original, duration, allow_changes, timestamps):
    """Check declared alternatives even when the move budget fixes the event.

    Shared grids reuse timestamp parsing within this snapshot. Offset aliases for
    the same instant must still fail uniqueness checks.
    """
    seen = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{label} must contain offset-aware timestamps")
        if value not in timestamps:
            timestamps[value] = timestamp_minute(value, label)
        start = timestamps[value]
        if start in seen:
            raise ValueError(f"{label} contains duplicate instants")
        if start == original:
            raise ValueError(f"{label} repeats the original start")
        seen.add(start)
    if seen:
        iso(max(seen) + duration)
    starts = sorted(seen) if allow_changes else []
    return starts, len(seen)


_window_start = itemgetter(0)
_window_end = itemgetter(1)


def covers(windows_, start, end):
    """Test containment in merged windows, whose endpoints are strictly increasing."""
    index = bisect_right(windows_, start, key=_window_end)
    return index < len(windows_) and windows_[index][0] <= start and end <= windows_[index][1]


def gap_count(windows_, start, end):
    """Count uncovered runs without enumerating all intervening evidence windows."""
    first = bisect_right(windows_, start, key=_window_end)
    stop = bisect_left(windows_, end, lo=first, key=_window_start)
    if first == stop:
        return int(start < end)
    # Each adjacent pair of merged windows has one gap. There can also be
    # uncovered time before the first window and after the last one.
    return stop - first - 1 + (start < windows_[first][0]) + (windows_[stop - 1][1] < end)


def gaps(windows_, start, end):
    """Parts of an optimistic attendance interval that still need confirmation."""
    cursor = start
    first = bisect_right(windows_, start, key=_window_end)
    for index in range(first, len(windows_)):
        left, right = windows_[index]
        if left >= end:
            break
        if cursor < left:
            yield cursor, left
        cursor = max(cursor, right)
        if cursor >= end:
            return
    if cursor < end:
        yield cursor, end


@dataclass(frozen=True)
class Person:
    available: tuple[tuple[int, int], ...]
    optimistic: tuple[tuple[int, int], ...]
    rest: int
    busy_coverage: tuple[tuple[int, int], ...] | None = None


@dataclass(frozen=True)
class Meeting:
    id: str
    people: frozenset[str]
    duration: int
    original: int | None
    starts: tuple[int, ...]
    protected: bool
    domain_count: int
    attendees_complete: bool = True


def read_meeting(item, people, is_request=False, *, timestamps=None, allow_changes=True):
    item = obj(item, "meeting")
    mid = REQUEST if is_request else nonempty_text(item.get("id"), "event id")
    require(is_request or mid != REQUEST, f"{REQUEST} is reserved")
    domain_fields = ({"starts", "windows"} if is_request else
                     {"id", "start", "alternatives", "move_windows", "move_tier", "attendees_complete"})
    fields(item, {"attendees", "duration_minutes", "step_minutes"} | domain_fields, mid)
    raw = arr(item.get("attendees"), f"{mid}.attendees", nonempty=True)
    attendees = frozenset(nonempty_text(p, f"{mid}.attendees") for p in raw)
    require(len(raw) == len(attendees), f"{mid} has duplicate attendees")
    require(attendees <= people.keys(), f"{mid} has an unknown attendee")
    attendees_complete = item.get("attendees_complete", True)
    require(type(attendees_complete) is bool, f"{mid}.attendees_complete must be a boolean")
    allow_changes = allow_changes and attendees_complete
    duration = whole_number(item.get("duration_minutes"), f"{mid}.duration_minutes", 1)
    require(duration <= MAX_DATE_MINUTES, f"{mid}.duration_minutes exceeds the supported date range")
    original = None if is_request else timestamp_minute(item.get("start"), f"{mid}.start")
    key = "starts" if is_request else "alternatives"
    window_key = "windows" if is_request else "move_windows"
    require(not (key in item and window_key in item),
            f"{mid}: supply only one of {key} or {window_key}")
    require(not is_request or key in item or window_key in item,
            f"{mid}: supply one of {key} or {window_key}")
    step = whole_number(item.get("step_minutes", 15), f"{mid}.step_minutes", 1)
    if window_key in item:
        starts, count = _window_domain(item[window_key], f"{mid}.{window_key}", step, duration,
                                       original, allow_changes)
        # No optimistic or verified solution can use these discarded starts.
        starts = [start for start in starts if all(
            covers(people[pid].optimistic, start, start + duration) for pid in attendees
        )]
    else:
        raw_starts = arr(item.get(key, []), f"{mid}.{key}", nonempty=is_request)
        starts, count = _explicit_starts(raw_starts, f"{mid}.{key}", original, duration,
                                         allow_changes, {} if timestamps is None else timestamps)
    if original is not None:
        iso(original + duration)
    tier = item.get("move_tier", "ordinary")
    require(tier in ("ordinary", "protected"), f"{mid}.move_tier is invalid")
    return Meeting(mid, attendees, duration, original, tuple(starts), tier == "protected", count, attendees_complete)


@dataclass(frozen=True)
class Problem:
    raw: dict
    people: dict[str, Person]
    request: Meeting
    events: tuple[Meeting, ...]
    max_moves: int
    decisions: dict
    objective: tuple[str, ...] | None
    migration_notes: tuple[str, ...]
    evidence_policy: str = "legacy"


def snapshot_digest(data):
    """Fingerprint the exact JSON evidence and policy used for a solve."""
    try:
        serialized = json.dumps(data, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as error:
        raise ValueError("snapshot must be JSON data with finite numbers and UTF-8 text") from error
    return sha256(serialized).hexdigest()


def effective_snapshot(report, snapshot):
    """Verify the supplied evidence against the solve, including a profile-provided objective."""
    if snapshot is None:
        return None
    scope = report.get("result", report).get("scope", {})
    digest = report.get("snapshot_sha256") or scope.get("snapshot_sha256")
    if snapshot_digest(snapshot) == digest:
        return snapshot
    if "objective" not in snapshot and "objective" in scope:
        effective = {**snapshot, "objective": scope["objective"]}
        if snapshot_digest(effective) == digest:
            return effective
    raise ValueError("the supplied snapshot differs from the solver report's snapshot_sha256")


def read_calendar(data):
    """Read the calendar shared by the single-request and portfolio models."""
    version = data.get("schema_version", "1.0")
    if version not in ("1.0", "1.1"):
        raise ValueError("unsupported schema_version; this release supports 1.0 and 1.1")
    if "$schema" in data:
        nonempty_text(data["$schema"], "$schema")
    obj(data.get("facts", {}), "facts")
    if "snapshot_id" in data:
        nonempty_text(data["snapshot_id"], "snapshot_id")
    policy = data.get("evidence_policy", "explicit" if version == "1.1" else "legacy")
    require(policy in ("legacy", "explicit"), "evidence_policy must be legacy or explicit")
    cap = whole_number(data.get("max_moves", 2), "max_moves", 0)
    roster = obj(data.get("people"), "people")
    people = {}
    for pid, raw in roster.items():
        nonempty_text(pid, "person id")
        fields(raw, {"available", "possible", "rest_minutes", "busy_coverage"}, pid)
        available = merge(window_pairs(raw.get("available"), f"{pid}.available"))
        possible = merge(window_pairs(raw.get("possible", []), f"{pid}.possible"))
        rest = whole_number(raw.get("rest_minutes", 0), f"{pid}.rest_minutes", 0)
        busy_coverage = None
        if "busy_coverage" in raw or policy == "explicit":
            busy_coverage = merge(window_pairs(raw.get("busy_coverage", []), f"{pid}.busy_coverage"))
        people[pid] = Person(available, merge(available + possible), rest, busy_coverage)
    timestamps = {}
    events = [read_meeting(raw, people, timestamps=timestamps, allow_changes=cap > 0)
              for raw in arr(data.get("events", []), "events")]
    require(len({event.id for event in events}) == len(events), "event IDs must be unique")
    events.sort(key=lambda event: event.id)
    return people, tuple(events), cap, policy


def parse_snapshot(data: dict) -> Problem:
    """Normalize calendar data once. All timestamps are UTC epoch minutes."""
    fields(data, {"schema_version", "$schema", "request", "people", "events", "facts", "constraints", "objective", "decisions",
                  "snapshot_id", "max_moves", "max_states", "evidence_policy"}, "input")
    people, events, cap, policy = read_calendar(data)
    request = read_meeting(data.get("request"), people, is_request=True)
    notes = ()
    if "max_states" in data:
        if integer_value(data["max_states"]) != 200000:
            raise ValueError("max_states counted complete enumerations in version 0.1; "
                             "remove it and set --time-limit or time_limit_seconds for CP-SAT")
        notes = ("Legacy default max_states was for enumeration. CP-SAT uses the configured time limit.",)
    objective = None
    if "objective" in data:
        objective = tuple(nonempty_text(name, "objective metric") for name
                          in arr(data["objective"], "objective", nonempty=True))
        require(len(set(objective)) == len(objective), "objective repeats a metric")
    return Problem(data, people, request, tuple(events), cap, read_decisions(data), objective, notes, policy)


def read_decisions(data):
    domains = obj(data.get("decisions", {}), "decisions")
    normalized = {}
    for name in sorted(domains, key=str):
        nonempty_text(name, "decision name")
        domain = domains[name]
        arr(domain, f"decisions.{name}", nonempty=True)
        domain = [integer_value(item) for item in domain]
        value_type = type(domain[0])
        require(value_type in (str, bool, int) and all(type(item) is value_type for item in domain),
                f"decisions.{name} requires values of one type: strings, integers or booleans")
        require(len(domain) == len(set(domain)), f"decisions.{name} contains duplicate values")
        normalized[name] = domain
    return normalized
