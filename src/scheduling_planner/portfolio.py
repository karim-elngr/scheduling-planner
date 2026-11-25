"""Joint CP-SAT placement of a team's requests and named movable occurrences.

The model chooses one (start, roster) for each occurrence. A sweep over their
endpoints enforces resources and measures contiguous free time exactly, even
when different requests use different grids. Only observed attendance and busy
coverage can support new or changed placements.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ortools.sat.python import cp_model as cp

from .config import time_limit, worker_count
from .domain import (
    EPOCH, MAX_MINUTE, MIN_MINUTE, arr, covers, fields, intersect_windows, iso, local_datetime, local_days, nonempty_text, obj,
    read_calendar, read_meeting, require, snapshot_digest, whole_number, window_pairs,
)
from .move_comparison import _caps
from .portfolio_tradeoffs import explain_tradeoffs


def _ids(values, label, known=None, nonempty=False):
    ids = [nonempty_text(v, label) for v in arr(values, label, nonempty)]
    require(len(ids) == len(set(ids)), f"{label} must contain unique IDs")
    if known is not None:
        require(set(ids) <= set(known), f"{label} contains an unknown ID")
    return ids


def _zone(value):
    nonempty_text(value, "timezone")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown timezone: {value}") from error


def _minute(value):
    require(isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00", value),
            "expected a local time in HH:MM format")
    h, m = map(int, value.split(":"))
    return 60 * h + m


def _clock(dt):
    return dt.hour * 60 + dt.minute


def _range(left, right):
    low, high = _minute(left), _minute(right)
    require(low < high, "local clock range must end after it starts")
    return low, high


@dataclass(frozen=True)
class MakerTarget:
    person: str
    duration: int
    window: tuple[int, int] | None

    def describe(self):
        info = {"person": self.person, "duration_minutes": self.duration, "status": "unknown", "witness": None}
        if self.window:
            info["window"] = [iso(t) for t in self.window]
        return info


@dataclass(frozen=True)
class Policy:
    zone: ZoneInfo
    movable: set
    focus_people: list
    cluster: set
    cluster_hours: tuple
    template: dict
    preferences: dict
    maker_target: MakerTarget | None


def _policy(raw, people, events, requests, event_facts):
    fields(raw, {"timezone", "movable_rituals", "focus_people", "cluster", "week_template", "preferences",
                 "maker_target"}, "portfolio")
    zone = _zone(raw.get("timezone", "UTC"))
    event_ids = [event.id for event in events]
    movable = _ids(raw.get("movable_rituals", event_ids), "movable_rituals", event_ids)
    focus = _ids(raw.get("focus_people", list(people)), "focus_people", people)
    cluster_ids, hours = [], (0, 1440)
    if "cluster" in raw:
        cluster = raw["cluster"]
        fields(cluster, {"requests", "events", "series", "after", "before"}, "cluster")
        cluster_ids = _ids(cluster.get("requests", []), "cluster.requests", requests)
        cluster_ids += _ids(cluster.get("events", []), "cluster.events", event_ids)
        series = defaultdict(list)
        for mid, facts in event_facts.items():
            if isinstance(facts.get("series_id"), str):
                series[facts["series_id"]].append(mid)
        for series_id in _ids(cluster.get("series", []), "cluster.series", series):
            cluster_ids.extend(series[series_id])
        require(cluster_ids, "cluster requires at least one request, event or series")
        hours = _range(cluster.get("after", "00:00"), cluster.get("before", "24:00"))
    template = {}
    for row in arr(raw.get("week_template", []), "week_template"):
        fields(row, {"role", "weekdays", "mode"}, "week_template entry")
        role = nonempty_text(row.get("role"), "week_template.role")
        require(role not in template, "week_template repeats a role")
        mode = row.get("mode", "hard")
        require(mode in ("hard", "soft"), "week_template.mode must be hard or soft")
        days = arr(row.get("weekdays"), "week_template.weekdays", nonempty=True)
        require(all(type(n) is int and 0 <= n <= 6 for n in days), "weekdays must be 0 (Mon) through 6 (Sun)")
        template[role] = (set(days), mode)
    prefs = {}
    for pid, row in obj(raw.get("preferences", {}), "preferences").items():
        require(pid in people, "preferences has an unknown person")
        fields(row, {"not_before", "lunch", "max_meetings_per_day"}, f"preferences.{pid}")
        lunch = None
        if "lunch" in row:
            pair = arr(row["lunch"], "lunch")
            require(len(pair) == 2, "lunch must contain two local times")
            lunch = _range(*pair)
        cap = whole_number(row["max_meetings_per_day"], "max_meetings_per_day", 0) if "max_meetings_per_day" in row else None
        prefs[pid] = (_minute(row.get("not_before", "00:00")), lunch, cap)
    target = None
    if "maker_target" in raw:
        spec = raw["maker_target"]
        fields(spec, {"person", "duration_minutes", "window"}, "maker_target")
        pid = nonempty_text(spec.get("person"), "maker_target.person")
        require(pid in people, "maker_target.person must be in the roster")
        duration = whole_number(spec.get("duration_minutes"), "maker_target.duration_minutes", 1)
        window = window_pairs([spec["window"]], "maker_target.window")[0] if "window" in spec else None
        target = MakerTarget(pid, duration, window)
    return Policy(zone, set(movable), focus, set(cluster_ids), hours, template, prefs, target)


@dataclass(frozen=True)
class Choice:
    id: str
    start: int
    duration: int
    attendees: tuple
    original: int | None
    delegate: str | None = None

    @property
    def end(self):
        return self.start + self.duration

    @property
    def stays(self):
        return self.original == self.start


def _verified(person, start, end):
    return covers(person.available, start, end) and (
        person.busy_coverage is None or covers(person.busy_coverage, max(MIN_MINUTE, start - person.rest),
                                               min(MAX_MINUTE, end + person.rest)))


def _add_choice(choices, soft, choice, role, policy):
    if role is not None and role in policy.template:
        allowed, mode = policy.template[role]
        outside = len(allowed) < 7 and any(
            day.weekday() not in allowed
            for day, _, _ in local_days([(choice.start, choice.end)], policy.zone)
        )
        if outside and mode == "hard":
            return
        if outside:
            soft.add(choice)
    choices.append(choice)


def _choices(roster, events, requests, event_facts, policy):
    choices = {}
    soft = set()
    for row in requests:
        mid = row["id"]
        base = {k: v for k, v in row.items() if k not in ("id", "role", "representatives")}
        role = nonempty_text(row["role"], "role") if "role" in row else None
        mandatory = _ids(base.get("attendees"), f"{mid}.attendees", roster)
        delegates = (_ids(row["representatives"], f"{mid}.representatives", roster, nonempty=True)
                     if "representatives" in row else [])
        require(set(mandatory).isdisjoint(delegates), "representatives must be distinct from required attendees")
        choices[mid] = []
        for delegate in delegates or [None]:
            attendees = tuple(sorted(mandatory + ([delegate] if delegate else [])))
            meeting = read_meeting(dict(base, attendees=list(attendees)), roster, is_request=True)
            for start in meeting.starts:
                if not all(_verified(roster[p], start, start + meeting.duration) for p in attendees):
                    continue
                choice = Choice(mid, start, meeting.duration, attendees, None, delegate)
                _add_choice(choices[mid], soft, choice, role, policy)

    for event in events:
        attendees = tuple(sorted(event.people))
        starts = [event.original]
        if event.id in policy.movable:
            starts += [s for s in event.starts if all(_verified(roster[p], s, s + event.duration) for p in attendees)]
        choices[event.id] = []
        role = event_facts[event.id].get("role")
        if role is not None:
            nonempty_text(role, f"{event.id}.metadata.role")
        for start in starts:
            choice = Choice(event.id, start, event.duration, attendees, event.original)
            _add_choice(choices[event.id], soft, choice, role, policy)
    return choices, soft


def _sweep(entries):
    """Yield half-open segments and their covering choices, including interior gaps."""
    endpoints = defaultdict(list)
    for choice, left, right in entries:
        endpoints[left].append((True, choice))
        endpoints[right].append((False, choice))
    active = set()
    previous = None
    for instant, changes in sorted(endpoints.items()):
        if previous is not None:
            yield previous, instant, active
        for add, choice in changes:
            if add:
                active.add(choice)
            else:
                active.remove(choice)
        previous = instant


def _any(model, literals, name):
    if not literals:
        return model.new_constant(0)
    if len(literals) == 1:
        return literals[0]
    result = model.new_bool_var(name)
    model.add_max_equality(result, literals)
    return result


def _resources(model, choices, flags, rest):
    # Incumbents may already overlap. When any of them stays, require every
    # new or moved appointment to clear it; never force incumbents apart.
    for _, _, active in _sweep((c, c.start, c.end + rest) for c in choices):
        changed = [flags[c] for c in active if not c.stays]
        if not changed:
            continue
        if len(changed) > 1:
            model.add_at_most_one(changed)
        incumbents = [flags[c] for c in active if c.stays]
        if incumbents:
            model.add(sum(changed) == 0).only_enforce_if(_any(model, incumbents, "incumbent"))


def _focus(model, flags, person, choices, zone):
    # Runs stop at midnight, a gap in observed availability, or a busy segment.
    by_day = defaultdict(list)
    bounds = defaultdict(int)
    blocks = []
    confirmed = person.available if person.busy_coverage is None else intersect_windows(person.available, person.busy_coverage)
    for day, lo, hi in local_days(confirmed, zone):
        require(len(by_day) < 20000 or day in by_day, "focus horizon exceeds 20000 days per person")
        bounds[day] = max(bounds[day], hi - lo)
        live = [(c, max(lo, c.start), min(hi, c.end)) for c in choices if c.start < hi and lo < c.end]
        # Include the full observed window to retain free prefixes and suffixes.
        segments = _sweep([*live, (None, lo, hi)])
        run = 0
        previous_busy = model.new_constant(1)
        for a, b, active in segments:
            busy = _any(model, [flags[c] for c in active if c is not None], "busy")
            length = model.new_int_var(0, b - lo, "free_run_minutes")
            model.add(length == 0).only_enforce_if(busy)
            model.add(length == run + (b - a)).only_enforce_if(~busy)
            started = model.new_bool_var("free_block_starts")
            model.add_min_equality(started, [previous_busy, 1 - busy])
            blocks.append(started)
            by_day[day].append(length)
            run, previous_busy = length, busy
    maxima = {}
    for day, terms in by_day.items():
        longest = model.new_int_var(0, bounds[day], "longest_free_minutes")
        model.add_max_equality(longest, terms)
        maxima[day] = longest
    return maxima, sum(blocks)


def _maker_block(model, target, person, choices, flags):
    # The entire interval must be inside observed working time. An overnight
    # shift can contain a maker block that spans a local-day boundary.
    windows = list(intersect_windows(person.available, person.busy_coverage or ()))
    if target.window:
        windows = intersect_windows(windows, [target.window])
    starts = [(left, right - target.duration) for left, right in windows if right - left >= target.duration]
    if not starts:
        model.add(False)
        return None

    start = model.new_int_var_from_domain(cp.Domain.from_intervals(starts), "maker_block_start")
    for choice in choices:
        # [start, start + duration) overlaps [choice.start, choice.end)
        # exactly for the following integer starts. Endpoints may touch.
        overlap = cp.Domain(choice.start - target.duration + 1, choice.end - 1)
        model.add_linear_expression_in_domain(start, overlap.complement()).only_enforce_if(flags[choice])
    return start


def _preference_cost(model, flags, choices, zone, pref):
    early, lunch, cap = pref
    daily = defaultdict(list)
    terms = []
    for c in choices:
        start = local_datetime(c.start, zone)
        daily[start.date()].append(flags[c])
        if _clock(start) < early:
            terms.append(flags[c])
        if lunch:
            # Check every touched day, including the first for an overnight event.
            for day, _, _ in local_days([(c.start, c.end)], zone):
                midnight = datetime.combine(day, datetime.min.time(), zone)
                low = int(((midnight + timedelta(minutes=lunch[0])) - EPOCH).total_seconds() // 60)
                if day == datetime.max.date() and lunch[1] == 1440:
                    high = MAX_MINUTE + 1
                else:
                    high = int(((midnight + timedelta(minutes=lunch[1])) - EPOCH).total_seconds() // 60)
                if c.start < high and low < c.end:
                    terms.append(flags[c])
                    break
    if cap is not None:
        for group in daily.values():
            if len(group) > cap:
                excess = model.new_int_var(0, len(group), "meetings_over_daily_cap")
                model.add_max_equality(excess, [0, sum(group) - cap])
                terms.append(excess)
    return sum(terms)


def _cluster(model, groups, flags, ids, hours, zone):
    if not ids or any(not groups[mid] for mid in ids):
        return 0, 0, 0
    by_day = defaultdict(list)
    starts, ends, outside = [], [], []
    low = min(c.start for mid in ids for c in groups[mid])
    high = max(c.end for mid in ids for c in groups[mid])
    for mid in sorted(ids):
        group = groups[mid]
        start = model.new_int_var(0, high - low, "cluster_start")
        model.add(start == sum((c.start - low) * flags[c] for c in group))
        starts.append(start)
        ends.append(start + group[0].duration)
        for c in group:
            dt = local_datetime(c.start, zone)
            last = local_datetime(c.end - 1, zone)
            for day, _, _ in local_days([(c.start, c.end)], zone):
                require(len(by_day) < 20000 or day in by_day, "cluster horizon exceeds 20000 days")
                by_day[day].append(flags[c])
            if dt.date() != last.date() or _clock(dt) < hours[0] or _clock(last) >= hours[1]:
                outside.append(flags[c])
    first = model.new_int_var(0, high - low, "cluster_first")
    last = model.new_int_var(0, high - low, "cluster_last")
    model.add_min_equality(first, starts)
    model.add_max_equality(last, ends)
    return sum(outside), sum(_any(model, group, "cluster_day") for group in by_day.values()), last - first


@dataclass(frozen=True)
class Portfolio:
    choices: dict
    soft: set
    people: dict
    events: tuple
    zones: dict
    policy: Policy


def _compile(data, budget):
    model = cp.CpModel()
    groups = data.choices
    policy = data.policy
    flags = {}
    offsets = []
    by_person = defaultdict(list)
    protected = {e.id for e in data.events if e.protected}
    moves, protected_moves = [], []
    for mid, group in groups.items():
        earliest = min((c.start for c in group), default=0)
        for c in group:
            flag = model.new_bool_var(f"{mid}@{c.start}")
            flags[c] = flag
            offsets.append((c.start - earliest) * flag)
            for pid in c.attendees:
                by_person[pid].append(c)
            if c.original is not None and not c.stays:
                moves.append(flag)
                if mid in protected:
                    protected_moves.append(flag)
        model.add_exactly_one(flags[c] for c in group)
    model.add(sum(moves) <= min(budget, len(data.events)))
    for pid, group in by_person.items():
        _resources(model, group, flags, data.people[pid].rest)
    daily_maxima = {}
    free_blocks = 0
    for person in policy.focus_people:
        maxima, blocks = _focus(model, flags, data.people[person], by_person[person], data.zones[person])
        daily_maxima.update(((person, day), longest) for day, longest in maxima.items())
        free_blocks += blocks
    penalties = sum(_preference_cost(model, flags, by_person[p], data.zones[p], pref)
                    for p, pref in policy.preferences.items())
    outside, days, span = _cluster(model, groups, flags, policy.cluster, policy.cluster_hours, policy.zone)
    maker_start = None
    if policy.maker_target:
        pid = policy.maker_target.person
        maker_start = _maker_block(model, policy.maker_target, data.people[pid], by_person[pid], flags)
    return model, flags, maker_start, daily_maxima, {
        "protected_moves": sum(protected_moves), "preference_penalty": penalties + sum(flags[c] for c in data.soft),
        "cluster_outside_hours": outside, "cluster_days": days, "cluster_span_minutes": span,
        "focus_minutes": sum(daily_maxima.values()), "free_blocks": free_blocks,
        "moved_meetings": sum(moves),
        "start_offset_minutes": sum(offsets),
    }


def _read(solver, flags, metrics, soft):
    placements, moves, weekday_misses = [], [], []
    for choice, flag in flags.items():
        if not solver.value(flag):
            continue
        if choice in soft:
            weekday_misses.append(choice.id)
        if choice.stays:
            continue
        row = {"id": choice.id, "end": iso(choice.end), "attendees": list(choice.attendees)}
        if choice.original is None:
            row["start"] = iso(choice.start)
            if choice.delegate:
                row["representative"] = choice.delegate
            placements.append(row)
        else:
            moves.append(dict(row, **{"from": iso(choice.original), "to": iso(choice.start)}))
    return {"placements": placements, "moves": moves, "week_template_misses": weekday_misses,
            "metrics": {k: solver.value(v) for k, v in metrics.items()}}


def _solve(data, budget, deadline, workers, lite, scatter=False, daily_focus=None):
    result = {"budget": budget, "status": "UNKNOWN", "placements": [], "moves": [], "metrics": {}}
    target = data.policy.maker_target
    if target:
        result["maker_target"] = target.describe()
    if monotonic() >= deadline:
        return result
    model, flags, maker_start, daily_maxima, metrics = _compile(data, budget)
    solver = cp.CpSolver()
    solver.parameters.num_search_workers = workers
    objectives = [k for k, expr in metrics.items() if not isinstance(expr, int)]
    if scatter and "cluster_span_minutes" in objectives:
        # Use the same evidence, preferences, move cap and cluster hours. The
        # counterfactual maximizes occupied days, then gets its best focus time.
        objectives.remove("cluster_span_minutes")
    if lite or not objectives:
        objectives = [None]
    for key in objectives:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return result
        if key == "focus_minutes" or (scatter and key == "cluster_days"):
            model.maximize(metrics[key])
        elif key is not None:
            model.minimize(metrics[key])
        solver.parameters.max_time_in_seconds = remaining
        status = solver.solve(model)
        if status == cp.MODEL_INVALID:
            raise ValueError(f"invalid portfolio model: {model.validate()}")
        if status == cp.INFEASIBLE:
            result["status"] = "INFEASIBLE"
            if target:
                result["maker_target"]["status"] = "infeasible"
            return result
        if status not in (cp.OPTIMAL, cp.FEASIBLE):
            return result
        result.update(_read(solver, flags, metrics, data.soft), status="FEASIBLE")
        if daily_focus is not None:
            daily_focus.update((key, solver.value(longest)) for key, longest in daily_maxima.items())
        if target:
            start = solver.value(maker_start)
            result["maker_target"].update(status="met", witness={"start": iso(start), "end": iso(start + target.duration)})
        if lite or status != cp.OPTIMAL:
            return result
        if key is not None:
            model.add(metrics[key] == result["metrics"][key])
    result["status"] = "OPTIMAL"
    return result


def solve_portfolio(snapshot, *, time_limit_seconds=30.0, workers=1, proof_lite=False, compare_moves=False):
    """Solve all requests jointly, within one shared time budget for every stage."""
    deadline = monotonic() + time_limit(time_limit_seconds)
    workers = worker_count(workers)
    require(type(proof_lite) is bool and type(compare_moves) is bool, "proof_lite and compare_moves must be booleans")
    snapshot = deepcopy(snapshot)
    fields(snapshot, {"schema_version", "$schema", "snapshot_id", "requests", "events", "people",
                      "max_moves", "evidence_policy", "facts", "portfolio"}, "portfolio snapshot")
    digest = snapshot_digest(snapshot)
    roster, events, cap, evidence = read_calendar({"schema_version": "1.1", **snapshot})
    requests = arr(snapshot.get("requests"), "requests")
    ids = _ids([obj(r, "request").get("id") for r in requests], "request IDs")
    require("$request" not in ids and not set(ids).intersection(e.id for e in events),
            "request IDs must be distinct from event IDs and $request")
    event_facts = {e["id"]: e.get("metadata", {}) for e in snapshot.get("events", [])}
    policy = _policy(snapshot.get("portfolio", {}), roster, events, ids, event_facts)
    zones = {p: _zone(row.get("metadata", {}).get("timezone", policy.zone.key)) for p, row in snapshot["people"].items()}
    groups, soft = _choices(roster, events, requests, event_facts, policy)
    data = Portfolio(groups, soft, roster, events, zones, policy)
    budgets = _caps(cap, sum(any(not c.stays for c in groups[e.id]) for e in events)) if compare_moves else [cap]
    jobs = [(cap, False), *((b, False) for b in budgets if b != cap)]
    if policy.cluster:
        jobs.append((cap, True))
    rows, focus_by_budget = {}, {}
    for i, (budget, scatter) in enumerate(jobs):
        now = monotonic()
        stage_end = now + (deadline - now) / (len(jobs) - i)
        daily_focus = None
        if compare_moves and not scatter and (soft or policy.preferences):
            daily_focus = focus_by_budget[budget] = {}
        rows[budget, scatter] = _solve(data, budget, stage_end, workers, proof_lite, scatter, daily_focus)
    primary = rows[cap, False]
    report = dict(primary, kind="portfolio", timezone=policy.zone.key, evidence_policy=evidence, snapshot_sha256=digest,
                  snapshot_id=snapshot.get("snapshot_id"), proof_lite=proof_lite)
    if compare_moves:
        report["stages"] = [rows[b, False] for b in budgets]
        previous = None
        for row in report["stages"]:
            if row["metrics"]:
                if previous is not None:
                    row["focus_minutes_gained"] = row["metrics"]["focus_minutes"] - previous["metrics"]["focus_minutes"]
                    row["previous_budget"] = previous["budget"]
                    row["gain_proved"] = row["status"] == previous["status"] == "OPTIMAL"
                previous = row
    if policy.cluster:
        reference = rows[cap, True]
        comparison = dict(reference, proved=False, days_saved=None, focus_minutes_gained=None)
        if reference["metrics"] and primary["metrics"]:
            comparison.update(proved=primary["status"] == reference["status"] == "OPTIMAL",
                              days_saved=reference["metrics"]["cluster_days"] - primary["metrics"]["cluster_days"],
                              focus_minutes_gained=primary["metrics"]["focus_minutes"] - reference["metrics"]["focus_minutes"])
        report["scatter_comparison"] = comparison
    explain_tradeoffs(report, snapshot, focus_by_budget)
    return report
