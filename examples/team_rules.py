"""Example extension. Each compiler contributes expressions to the current CP model."""

from scheduling_planner.domain import whole_number
from scheduling_planner.rules import RuleEffect, require_keys
from scheduling_planner.rules.modeling import (
    all_of, any_of, at_most, complement, covered_by_windows,
)


def _apart(model, first, second, name):
    """Half-open meeting intervals may touch at their endpoints."""
    if (max(first.starts) + first.duration <= min(second.starts)
            or max(second.starts) + second.duration <= min(first.starts)):
        return 1
    first_before = at_most(model, first.end, second.start, f"{name}_before")
    first_after = at_most(model, second.end, first.start, f"{name}_after")
    return any_of(model, [first_before, first_after], name)


def standby_cover(ctx, params):
    """One named responder off the call must be free for its entire duration."""
    require_keys(params, required=["roster_fact"], optional=["responder_decision"])
    fact = params["roster_fact"]
    if not isinstance(fact, str) or not fact.strip():
        raise ValueError("roster_fact must name a snapshot fact")
    roster = ctx.problem.raw.get("facts", {}).get(fact)
    if roster is not None and (not isinstance(roster, list)
            or not all(isinstance(p, str) and p in ctx.problem.people for p in roster)
            or len(set(roster)) != len(roster)):
        raise ValueError("the roster must list distinct people present in the snapshot")
    question = "Who can cover the entire review without another assignment?"
    choice = None
    if "responder_decision" in params:
        name = params["responder_decision"]
        if not isinstance(name, str) or name not in ctx.decisions:
            raise ValueError("responder_decision must name an existing finite choice")
        candidates = ctx.decision_values[name]
        if any(not isinstance(p, str) or p not in ctx.problem.people for p in candidates):
            raise ValueError("responder_decision choices must be known person IDs")
        choice = ctx.decisions[name]
        question = "Can the selected responder confirm duty coverage for the entire review?"
    elif roster is None:
        return RuleEffect(unknown=1, question=question)
    else:
        candidates = roster

    request = ctx.slots["$request"]
    known, possible = [], []
    apart = {}
    for index, person_id in enumerate(candidates):
        # A participant in the review cannot simultaneously provide coverage.
        if person_id in request.attendees or (roster is not None and person_id not in roster):
            continue
        person = ctx.problem.people[person_id]
        free_from_events = []
        if choice is not None:
            selected = ctx.model.new_bool_var(f"chosen_responder[{person_id}]")
            ctx.model.add(choice == index).only_enforce_if(selected)
            ctx.model.add(choice != index).only_enforce_if(~selected)
            free_from_events.append(selected)
        for event_id, event in ctx.slots.items():
            if person_id in event.attendees:
                if event_id not in apart:
                    apart[event_id] = _apart(ctx.model, event, request, f"apart[{event_id}]")
                free_from_events.append(apart[event_id])
        free = all_of(ctx.model, free_from_events, f"available[{person_id}]")
        verified_window = covered_by_windows(ctx.model, request, person.available, f"duty[{person_id}]")
        possible_window = covered_by_windows(ctx.model, request, person.optimistic, f"possible_duty[{person_id}]")
        observed = 1 if person.busy_coverage is None else covered_by_windows(
            ctx.model, request, person.busy_coverage, f"observed_duty[{person_id}]"
        )
        if roster is not None:
            known.append(all_of(ctx.model, [verified_window, free, observed], f"known_cover[{person_id}]"))
        possible.append(all_of(ctx.model, [possible_window, free], f"possible_cover[{person_id}]"))

    verified_cover = any_of(ctx.model, known, "any_known_cover")
    potential_cover = any_of(ctx.model, possible, "any_possible_cover")
    lacks_verified = complement(ctx.model, verified_cover, "no_known_cover")
    return RuleEffect(
        units=complement(ctx.model, potential_cover, "no_possible_cover"),
        unknown=all_of(ctx.model, [lacks_verified, potential_cover], "unknown_coverage"),
        reason=("The selected responder cannot cover the entire review." if choice is not None
                else "No designated responder is available throughout the review."),
        question=question,
    )


def room_capacity(ctx, params):
    """Penalize missing seats using a single decision-to-cost lookup table."""
    require_keys(params, required=["decision", "capacity_fact"])
    name = params["decision"]
    fact = params["capacity_fact"]
    if not isinstance(name, str) or name not in ctx.decisions:
        raise ValueError("decision must name an existing room choice")
    if not isinstance(fact, str) or not fact.strip():
        raise ValueError("capacity_fact must name a fact")
    rooms = ctx.decision_values[name]
    if not all(isinstance(room, str) for room in rooms):
        raise ValueError("room choices must be strings")
    seats = ctx.problem.raw.get("facts", {}).get(fact, {})
    if not isinstance(seats, dict):
        raise ValueError("capacity fact must map room names to seat counts")
    needed = len(ctx.slots["$request"].attendees)

    rows = []
    for index, room in enumerate(rooms):
        capacity = seats.get(room)
        if capacity is None:
            rows.append((index, 0, 1))
        else:
            capacity = whole_number(capacity, f"capacity for {room}", 0)
            rows.append((index, max(0, needed - capacity), 0))
    shortfall = ctx.model.new_int_var(0, needed, "seat_shortfall")
    unknown = ctx.model.new_bool_var("unknown_room_capacity")
    ctx.model.add_allowed_assignments([ctx.decisions[name], shortfall, unknown], rows)
    return RuleEffect(units=shortfall, unknown=unknown, reason="Too few seats in the selected room.",
                      question="What is the capacity of the selected room?")


RULES = {"standby_cover": standby_cover, "room_capacity": room_capacity}
