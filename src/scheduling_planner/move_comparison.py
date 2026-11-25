"""Compare verified earliest starts at explicit move budgets with a shared deadline."""

from time import monotonic

from .domain import timestamp_minute
from .engine import plan


# Comparison describes a decision a human can actually inspect. Large ranges retain
# the baseline, the first move and evenly spaced intermediate caps within this limit.
MAX_SCENARIOS = 9


def _caps(maximum, movable):
    effective = min(maximum, movable)
    # Reserve a row for the exact authorized cap, even if fewer events can move.
    available = MAX_SCENARIOS - int(maximum > effective)
    if effective < available:
        caps = list(range(effective + 1))
    else:
        caps = [0, *(1 + i * (effective - 1) // (available - 2) for i in range(available - 1))]
    if maximum > effective:
        caps.append(maximum)
    return caps


def _row(cap, report):
    proof = report["verified"]["proofs"]["earliest"] if report is not None else None
    witness = report["verified"]["earliest"] if report is not None else None
    status = proof["status"] if proof else "not_run"
    row = {"cap": cap, "status": status, "proof_status": status, "proof": proof,
           "snapshot_sha256": report["scope"].get("snapshot_sha256") if report else None,
           "plan_id": None, "start": None, "moves": None, "previous_cap": None,
           "minutes_gained": None, "gain_proven": False, "gain_basis": None, "frontier": False}
    if witness is not None:
        row.update(start=witness["start"], moves=witness["metrics"]["moved_meetings"])
    return row, witness


def _start_proved(row):
    proof = row["proof"]
    return proof is not None and (proof["status"] == "optimal" or any(
        stage["metric"] == "start" and stage["proven"] for stage in proof["stages"]
    ))


def compare_move_caps(settings, registry, primary_report, deadline):
    """Return a row for each considered cap, with proof scoped to that exact scenario."""
    scope = primary_report["scope"]
    maximum, movable = scope["max_moves"], scope["movable_event_count"]
    # A registered policy can consult ctx.problem.max_moves, even if every
    # calendar event is fixed. Only built-in policies are known to be cap-invariant.
    uses_custom_policy = registry is not None and any(
        rule["kind"] in registry for rule in scope["constraints"]
    )
    relevant_limit = maximum if uses_custom_policy else min(maximum, movable)
    caps = _caps(maximum, relevant_limit)
    reports = {maximum: primary_report} if "earliest" in scope["rankings"] else {}
    pending = [cap for cap in caps if cap not in reports]
    for index, cap in enumerate(pending):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        reports[cap] = plan(dict(settings.snapshot, max_moves=cap), registry=registry,
                            rankings=["earliest"], workers=settings.workers,
                            time_limit_seconds=remaining / (len(pending) - index))

    pairs = [_row(cap, reports.get(cap)) for cap in caps]
    rows = [row for row, _ in pairs]
    feasible = [row for row in rows if row["start"] is not None]
    previous = None
    seen = set()
    for row in feasible:
        if previous:
            row.update(previous_cap=previous["cap"],
                       minutes_gained=timestamp_minute(previous["start"]) - timestamp_minute(row["start"]),
                       gain_proven=_start_proved(previous) and _start_proved(row))
            row["gain_basis"] = "proved_starts" if row["gain_proven"] else "observed_starts"
        point = (row["moves"], timestamp_minute(row["start"]))
        # Equal cost/time witnesses can differ in move destinations. One representative
        # is enough here; the preferred ranking can still expose a different tie.
        dominated = any((other["moves"] <= point[0] and timestamp_minute(other["start"]) <= point[1])
                        and (other["moves"], timestamp_minute(other["start"])) != point for other in feasible)
        row["frontier"] = not dominated and point not in seen
        seen.add(point)
        previous = row
    all_counts = len(caps) == relevant_limit + 1 + int(maximum > relevant_limit)
    return {
        "maximum_cap": maximum, "movable_event_count": movable, "time_limit_seconds": settings.time_limit_seconds,
        "sampled": not all_counts, "complete": all_counts and all(
            row["status"] in ("optimal", "infeasible_in_scope") for row in rows
        ), "caps": rows,
    }, [witness for _, witness in pairs]
