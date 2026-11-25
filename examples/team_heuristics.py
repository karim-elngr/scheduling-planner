"""A team can add a coordination reminder to existing solver options."""

def coordination_note(context, params):
    """Flag moves that involve people outside the requested call."""
    if params:
        raise ValueError("coordination_note accepts no parameters")
    invitees = set(context["snapshot"]["request"]["attendees"])
    notes = []
    for option in context["options"]:
        for move in option["moves_to_coordinate"]:
            others = sorted(set(move["attendees"]) - invitees)
            if others:
                notes.append({
                    "plan_id": option["id"], "kind": "note",
                    "text": f"Coordinate the proposed move of {move['event_id']} with "
                            f"{', '.join(others)} before changing their calendars.",
                })
    return notes


HEURISTICS = {"coordination_note": coordination_note}
