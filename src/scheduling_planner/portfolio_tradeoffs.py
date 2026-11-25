"""Explain opposing changes in soft policy and focus across solved move budgets."""
from collections import Counter


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _count(number, singular):
    return f"{number} {singular}" if number == 1 else f"{number} {singular}s"


def _rule_label(rule):
    role = rule["role"]
    if role in ("one_on_one", "one-on-one", "1:1"):
        role = "1:1"
    days = "/".join(_WEEKDAYS[d] for d in sorted(set(rule["weekdays"])))
    return f"the {days} {role} preference"


def _tradeoff(plan, reference, roles, rules, focus_group, focus_by_budget):
    if not (plan["metrics"] and reference["metrics"]):
        return None
    new, old = plan["metrics"], reference["metrics"]
    penalty = new["preference_penalty"] - old["preference_penalty"]
    focus = new["focus_minutes"] - old["focus_minutes"]
    # A lower penalty and a larger focus total are the two improvements.
    if penalty * focus <= 0:
        return None
    cap = reference["budget"]
    baseline = "the zero-move plan" if not cap else f"the plan allowing {_count(cap, 'move')}"
    miss = "miss" if abs(penalty) == 1 else "misses"
    policy_change = f"this schedule has {abs(penalty)} {'fewer' if penalty < 0 else 'more'} soft preference {miss}"

    # Count exactly the selected choices penalized by the solver for their weekday.
    # Other points can come from individual hours, lunch and daily meeting limits.
    before = Counter(roles[mid] for mid in reference.get("week_template_misses", []))
    after = Counter(roles[mid] for mid in plan.get("week_template_misses", []))
    changed = [role for role in sorted(before.keys() | after.keys()) if before[role] != after[role]]
    details = []
    if len(changed) == 1 and after[changed[0]] - before[changed[0]] == penalty:
        who = "meeting follows" if abs(penalty) == 1 else "meetings follow"
        policy_change = f"{abs(penalty)} {'more' if penalty < 0 else 'fewer'} {who} {rules[changed[0]]}"
    else:
        for role in changed:
            details.append(f"Misses for {rules[role]} change from {before[role]} to {after[role]}.")
    summary = (f"Compared with {baseline}, {policy_change}. "
               f"The total of the longest daily focus blocks{focus_group} "
               f"{'falls' if focus < 0 else 'rises'} from {old['focus_minutes']} to {new['focus_minutes']} minutes "
               f"({_count(abs(focus), 'minute')} {'less' if focus < 0 else 'more'}).")
    # Show the largest person-day change in the same direction as the aggregate.
    # The solver computed these maxima for the two returned schedules.
    current = focus_by_budget[plan["budget"]]
    prior = focus_by_budget[cap]
    day_key = max(current, key=lambda k: (1 if focus > 0 else -1) * (current[k] - prior[k]))
    person, day = day_key
    details.append(f"On {day:%A %Y-%m-%d}, the longest block for {person} changes from "
                   f"{prior[day_key]} to {current[day_key]} minutes in these schedules.")
    proved = plan["status"] == reference["status"] == "OPTIMAL"
    details.append("Both schedules are optimal for their move budgets." if proved else
                   "The difference is observed in the returned schedules. At least one optimum is unproved.")
    return {"compared_with_budget": cap, "preference_penalty_delta": penalty, "focus_minutes_delta": focus,
            "proved": proved, "summary": summary, "details": " ".join(details),
            "daily_focus_example": {"person": person, "date": day.isoformat(), "from_minutes": prior[day_key],
                                    "to_minutes": current[day_key]}}


def explain_tradeoffs(report, snapshot, focus_by_budget):
    """Compare each move budget to keeping existing meetings in place."""
    policy = snapshot.get("portfolio", {})
    rules = {r["role"]: _rule_label(r) for r in policy.get("week_template", []) if r.get("mode") == "soft"}
    if not rules and not policy.get("preferences"):
        return
    preference = "soft scheduling preferences"
    if len(rules) == 1 and not policy.get("preferences"):
        preference = next(iter(rules.values()))
    report["objective_summary"] = f"The full ranking gives {preference} priority over longer focus blocks."
    if report.get("proof_lite"):
        report["objective_summary"] += " This exploration stops at the first feasible schedule."
    stages = report.get("stages", [])
    baseline = next((stage for stage in stages if stage["budget"] == 0), None)
    if baseline is None or len(stages) < 2:
        return
    roles = {e["id"]: e.get("metadata", {}).get("role") for e in snapshot.get("events", [])}
    roles.update((r["id"], r.get("role")) for r in snapshot["requests"])
    people = policy.get("focus_people", list(snapshot["people"]))
    group = " for " + " and ".join(people) if len(people) <= 2 else f" across {len(people)} people"
    for stage in stages:
        # Explain the zero-move choice relative to the requested budget as well.
        reference = report if stage is baseline else baseline
        tradeoff = _tradeoff(stage, reference, roles, rules, group, focus_by_budget)
        if tradeoff:
            stage["tradeoff"] = tradeoff
            if stage["budget"] == report["budget"]:
                report["tradeoff"] = tradeoff
