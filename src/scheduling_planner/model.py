"""Compile per-occurrence start domains and shared calendar resources into CP-SAT.

All times are integral UTC minutes. A person's turnaround interval has length
meeting duration + required rest. Working windows bound attendance only.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from ortools.sat.python import cp_model as cp

from .domain import MAX_MINUTE, MIN_MINUTE, Meeting, Problem, covers, gap_count, gaps, iso


@dataclass(frozen=True)
class Slot:
    """The shared scheduling variables for one occurrence; starts includes its original."""
    id: str
    start: cp.IntVar
    end: cp.LinearExpr
    duration: int
    attendees: frozenset[str]
    starts: tuple[int, ...]
    domain_size: int
    original: int | None
    moved: cp.IntVar


@dataclass
class BuildContext:
    """Native handles used by rule encoders and the optimization service."""
    model: cp.CpModel
    problem: Problem
    slots: dict[str, Slot]
    decisions: dict[str, cp.IntVar]
    decision_values: dict
    metrics: dict
    unknowns: list[tuple[cp.IntVar, dict]]


def _slot(ctx: BuildContext, occurrence: Meeting) -> Slot:
    starts = [
        start for start in occurrence.starts
        if all(covers(ctx.problem.people[pid].optimistic, start, start + occurrence.duration)
               for pid in occurrence.people)
    ]
    # An existing event may always stay, even outside a person's current hours.
    if occurrence.original is not None:
        starts.append(occurrence.original)
    starts = tuple(sorted(set(starts)))
    domain_size = len(starts)
    if not starts:
        # An impossible request remains a valid, provably infeasible CP model.
        # A placeholder lets all rule configuration still be checked before solve.
        ctx.model.add_bool_or([])
        starts = (0,)
    start = ctx.model.new_int_var_from_domain(cp.Domain.from_values(starts), f"start:{occurrence.id}")
    if occurrence.original is not None and len(starts) > 1:
        moved = ctx.model.new_bool_var(f"moved:{occurrence.id}")
        ctx.model.add(start != occurrence.original).only_enforce_if(moved)
        ctx.model.add(start == occurrence.original).only_enforce_if(~moved)
    else:
        moved = ctx.model.new_constant(0)
    return Slot(occurrence.id, start, start + occurrence.duration, occurrence.duration,
                frozenset(occurrence.people), starts, domain_size, occurrence.original, moved)


def _evidence_window(person, slot, start, evidence):
    """Return observed windows and the attendance or rest interval to check."""
    end = start + slot.duration
    if evidence == "busy_coverage":
        return person.busy_coverage, max(MIN_MINUTE, start - person.rest), min(MAX_MINUTE, end + person.rest)
    return person.available, start, end


def _link_calendar_evidence(ctx, slot, person_id):
    """Channel each candidate to counts of missing working and busy-feed intervals."""
    person = ctx.problem.people[person_id]
    evidence_types = []
    if person.available != person.optimistic:
        evidence_types.append("working_window")
    if person.busy_coverage is not None:
        evidence_types.append("busy_coverage")
    for evidence in evidence_types:
        counts = [0 if start == slot.original else gap_count(*_evidence_window(person, slot, start, evidence))
                  for start in slot.starts]
        low, high = min(counts), max(counts)
        if high == 0:
            continue
        if low == high:
            count = ctx.model.new_constant(low)
        else:
            count = ctx.model.new_int_var(low, high, f"missing:{evidence}:{person_id}:{slot.id}")
            ctx.model.add_allowed_assignments([slot.start, count], zip(slot.starts, counts, strict=True))
        metadata = {"kind": "calendar", "person": person_id, "event_id": slot.id}
        if evidence == "busy_coverage":
            metadata["evidence"] = evidence
        ctx.unknowns.append((count, metadata))


def calendar_questions(ctx: BuildContext, value: Callable) -> list[dict]:
    """Render the exact missing intervals counted by this placement's evidence terms."""
    questions = []
    for count, metadata in ctx.unknowns:
        if value(count) == 0:
            continue
        person_id, event_id = metadata["person"], metadata["event_id"]
        slot = ctx.slots[event_id]
        evidence = metadata.get("evidence", "working_window")
        for left, right in gaps(*_evidence_window(ctx.problem.people[person_id], slot, value(slot.start), evidence)):
            gap = {"person": person_id, "event_id": event_id, "start": iso(left), "end": iso(right)}
            if evidence == "busy_coverage":
                gap["evidence"] = evidence
            questions.append(gap)
    return questions


def _baseline_conflict(slots, rest):
    prior_end = None
    for slot in sorted((s for s in slots if s.original is not None), key=lambda s: s.original):
        if prior_end is not None and slot.original < prior_end:
            return True
        prior_end = slot.original + slot.duration + rest
    return False


def _protect_unchanged(ctx, active, other, rest):
    """An active meeting must clear an unchanged incumbent, including rest on either side."""
    # Integer starts in this closed interval would conflict. Touching the padded
    # endpoints exactly is allowed, matching two half-open resource intervals.
    low = other.original - active.duration - rest + 1
    high = other.original + other.duration + rest - 1
    if bisect_left(active.starts, low) == bisect_right(active.starts, high):
        return
    outside = cp.Domain(low, high).complement()
    constraint = ctx.model.add_linear_expression_in_domain(active.start, outside)
    if active.original is not None:
        constraint.only_enforce_if(active.moved)
    if len(other.starts) > 1:
        constraint.only_enforce_if(~other.moved)


def _calendar_resource(ctx, person_id, slots):
    if len(slots) < 2:
        return
    # The largest possible gap between any two activities is strictly less
    # than this span. Larger user-supplied buffers are equivalent within the
    # model and need not enter OR-Tools' fixed-width integer arithmetic.
    span = max(s.starts[-1] + s.duration for s in slots) - min(s.starts[0] for s in slots)
    rest = min(ctx.problem.people[person_id].rest, span)
    if not _baseline_conflict(slots, rest):
        intervals = [
            ctx.model.new_fixed_size_interval_var(s.start, s.duration + rest, f"busy:{person_id}:{s.id}")
            for s in slots
        ]
        ctx.model.add_no_overlap(intervals)
        return

    # Existing overlaps may persist if both appointments stay at their original
    # time. New and moved appointments still obey every rest and occupancy rule.
    active = [s for s in slots if s.original is None or len(s.starts) > 1]
    intervals = []
    for slot in active:
        name = f"changed-busy:{person_id}:{slot.id}"
        if slot.original is None:
            interval = ctx.model.new_fixed_size_interval_var(slot.start, slot.duration + rest, name)
        else:
            interval = ctx.model.new_optional_fixed_size_interval_var(
                slot.start, slot.duration + rest, slot.moved, name
            )
        intervals.append(interval)
        for other in slots:
            if other is not slot and other.original is not None:
                _protect_unchanged(ctx, slot, other, rest)
    if len(intervals) > 1:
        ctx.model.add_no_overlap(intervals)


def build_model(problem: Problem) -> BuildContext:
    """Build the joint finite-domain calendar model without enumerating combinations."""
    model = cp.CpModel()
    ctx = BuildContext(model, problem, {}, {}, problem.decisions, {}, [])
    calendars = defaultdict(list)
    people_moves = defaultdict(list)
    moves = []
    protected_moves = []
    for occurrence in (problem.request, *problem.events):
        slot = _slot(ctx, occurrence)
        ctx.slots[slot.id] = slot
        can_move = slot.original is not None and len(slot.starts) > 1
        if can_move:
            moves.append(slot.moved)
            if occurrence.protected:
                protected_moves.append(slot.moved)
        for person_id in sorted(slot.attendees):
            calendars[person_id].append(slot)
            _link_calendar_evidence(ctx, slot, person_id)
            if can_move:
                people_moves[person_id].append(slot.moved)

    for person_id, slots in calendars.items():
        _calendar_resource(ctx, person_id, slots)
    disrupted = []
    for person_id, indicators in people_moves.items():
        if len(indicators) == 1:
            disrupted.append(indicators[0])
        else:
            affected = model.new_bool_var(f"affected:{person_id}")
            model.add_max_equality(affected, indicators)
            disrupted.append(affected)
    model.add(sum(moves) <= min(problem.max_moves, len(moves)))
    ctx.metrics.update(protected_moves=sum(protected_moves), moved_meetings=sum(moves),
                       people_disrupted=sum(disrupted), start=ctx.slots[problem.request.id].start)
    for name, values in ctx.decision_values.items():
        ctx.decisions[name] = model.new_int_var(0, len(values) - 1, f"choice:{name}")
    return ctx
