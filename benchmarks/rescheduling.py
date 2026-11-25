"""Reproduce a two-week synthetic scheduling case with a four-move optimum."""
import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
from math import prod
from time import perf_counter

ZONE = timezone(timedelta(hours=4))


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), ZONE).isoformat(timespec="minutes")


def event(day, label, who, hour, times):
    return {"id": f"{day}-{label}", "attendees": who, "start": at(day, hour),
            "duration_minutes": 30, "alternatives": [at(day, h, m) for h, m in times]}


def fixture(profile="compact"):
    start_day = date(2026, 11, 30)
    days = [start_day + timedelta(days=i) for i in range(14)
            if (start_day + timedelta(days=i)).weekday() < 5]
    reviewers = [f"reviewer-{i}" for i in range(5)]
    delegates = [f"delegate-{i}" for i in range(20)]
    windows = [[at(d, 9), at(d, 17)] for d in days]
    people = {p: {"available": windows, "rest_minutes": 15} for p in reviewers + delegates}
    events = []
    for i, day in enumerate(days):
        peer = [delegates[(2 * i + j) % len(delegates)] for j in range(4)]

        events.extend([
            event(day, "design", [reviewers[0], peer[0]], 10, [(11, 0)]),
            event(day, "product", [reviewers[1], peer[1]], 10, [(11, 0), (11, 45), (14, 0)]),
            event(day, "quality", [reviewers[2], peer[2]], 10, [(11, 0), (11, 45), (14, 0)]),
            event(day, "handover", [peer[0], peer[3]], 11, [(12, 0), (14, 45), (15, 30)]),
            event(day, "planning", [reviewers[4], peer[1], peer[3]], 14, [(15, 0), (16, 0)]),
        ])
    if profile == "flexible":
        # Design has one contractual replacement. Other events may move anywhere
        # on the fortnight's working grid, even across days and teams.
        grid = [at(day, minute // 60, minute % 60) for day in days
                for minute in range(9 * 60, 16 * 60 + 31, 15)]
        for meeting in events:
            if not meeting["id"].endswith("-design"):
                meeting["alternatives"] = [t for t in grid if t != meeting["start"]]
    return {
        "snapshot_id": f"synthetic-linked-two-weeks-{profile}-v1",
        "people": people,
        "request": {"attendees": reviewers, "duration_minutes": 30, "step_minutes": 15,
                    "windows": [[at(d, 10), at(d, 10, 45)] for d in days]},
        "events": events, "max_moves": 4,
    }


def candidate_count(data):
    """Count complete assignments within the move cap before checking conflicts."""
    cap = data["max_moves"]
    ways = [1] + [0] * cap
    for event in data["events"]:
        for k in range(cap, 0, -1):
            ways[k] += ways[k - 1] * len(event["alternatives"])
    starts = 2 * len(data["request"]["windows"])
    return starts * sum(ways) * prod(map(len, data.get("decisions", {}).values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-limit", type=float, default=30)
    parser.add_argument("--profile", choices=["compact", "flexible"], default="compact")
    parser.add_argument("--dump-snapshot", action="store_true", help="print this fixture as planner input and exit")
    args = parser.parse_args()
    data = fixture(args.profile)
    if args.dump_snapshot:
        print(json.dumps(data, indent=2))
        return
    from scheduling_planner import plan

    start = perf_counter()
    result = plan(data, time_limit_seconds=args.time_limit, workers=1, rankings=["preferred"])
    elapsed = perf_counter() - start
    best = result["verified"]["preferred"]
    summary = {
        "profile": args.profile, "backend": result["backend"], "people": len(data["people"]), "movable_occurrences": len(data["events"]),
        "call_starts": 2 * len(data["request"]["windows"]), "move_cap": data["max_moves"],
        "unfiltered_assignments_within_cap": candidate_count(data),
        "elapsed_seconds": round(elapsed, 4), "verified_status": result["verified"]["status"],
        "start_utc": best["start"] if best else None, "moved_events": best["moves"] if best else [],
        "statistics": result["statistics"],
        "proof": result["verified"]["proofs"]["preferred"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
