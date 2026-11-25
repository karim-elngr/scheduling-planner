"""Native scheduling rules used by the CLI without an extension module."""

from ..domain import timestamp_minute, whole_number
from .api import RuleEffect, require_keys
from .modeling import positive_part


def _slot(ctx, event_id):
    if not isinstance(event_id, str) or event_id not in ctx.slots:
        raise ValueError(f"unknown occurrence {event_id!r}")
    return ctx.slots[event_id]


def finish_by(ctx, params):
    """Violation units are minutes from the deadline to the end of the slot."""
    require_keys(params, required=["event"], optional=["deadline", "fact"])
    if ("deadline" in params) == ("fact" in params):
        raise ValueError("finish_by requires exactly one of deadline or fact")
    event = params["event"]
    slot = _slot(ctx, event)
    if "fact" in params:
        fact = params["fact"]
        if not isinstance(fact, str) or not fact.strip():
            raise ValueError("fact must name a timestamp in the snapshot")
        deadline = ctx.problem.raw.get("facts", {}).get(fact)
        if deadline is None:
            return RuleEffect(unknown=1, question=f"What is the deadline for {event} (fact {fact})?")
    else:
        deadline = params["deadline"]
    cutoff = timestamp_minute(deadline, "finish_by deadline")
    late = positive_part(ctx.model, slot.end - cutoff,
                         max(slot.starts) + slot.duration - cutoff, f"lateness[{event}]")
    return RuleEffect(units=late, reason=f"{event} finishes after its deadline.")


def precedence(ctx, params):
    """Violation units are the minutes missing from the required separation."""
    require_keys(params, required=["before", "after"], optional=["gap_minutes"])
    before, after = params["before"], params["after"]
    earlier, later = _slot(ctx, before), _slot(ctx, after)
    if before == after:
        raise ValueError("precedence requires distinct occurrences")
    gap = whole_number(params.get("gap_minutes", 0), "gap_minutes", 0)
    max_shortage = max(earlier.starts) + earlier.duration + gap - min(later.starts)
    shortage = positive_part(ctx.model, earlier.end + gap - later.start,
                             max_shortage, f"precedence[{before},{after}]")
    return RuleEffect(units=shortage, reason=f"{after} starts too soon after {before}.")
