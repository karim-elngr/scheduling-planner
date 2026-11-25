"""Translate one solver report into a short list of evidenced scheduling choices."""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from time import monotonic
import json

from ._version import __version__
from .brief import brief as brief
from .domain import timestamp_minute
from .engine import plan
from .profiles import resolve_profile
from .move_comparison import compare_move_caps


def _plan_id(candidate):
    # Two rankings can choose the same instant and different meeting moves or decisions.
    identity = [
        candidate["start"], candidate["end"],
        sorted((m["event_id"], m["from_start"], m["to_start"], m["to_end"])
               for m in candidate["moves"]),
        candidate["decisions"],
    ]
    packed = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "plan-" + sha256(packed.encode("utf-8")).hexdigest()


def _score(candidate, order):
    def value(metric):
        if metric == "start":
            return timestamp_minute(candidate["start"])
        if metric == "unresolved_count":
            return len(candidate["unresolved"])
        if metric.startswith("rule:"):
            return candidate["rule_penalties"][metric[5:]]
        return candidate["metrics"][metric]

    return tuple(value(metric) for metric in order)


def _option(candidate, events):
    return {"id": _plan_id(candidate), "plan": candidate, "proofs": {},
            "moves_to_coordinate": [
                {**move, "attendees": sorted(events[move["event_id"]]["attendees"])}
                for move in candidate["moves"]
            ]}


def _rankings(report):
    return sorted(report["scope"]["rankings"], key=lambda rank: rank != "preferred")


def _collect(report, events):
    """Index schedules separately from the proofs for each ranking and evidence mode."""
    options = {}
    for mode in ("verified", "optimistic"):
        for ranking in _rankings(report):
            candidate = report[mode][ranking]
            if candidate is None:
                continue
            key = _plan_id(candidate)
            if key not in options:
                options[key] = _option(candidate, events)
            options[key]["proofs"].setdefault(mode, {})[ranking] = report[mode]["proofs"][ranking]
    return options


def _questions(option, snapshot):
    rows = []
    for gap in option["plan"]["unresolved"]:
        if gap.get("evidence") == "busy_coverage":
            text = (f"Can the busy calendar for {gap['person']} be checked from {gap['start']} "
                    f"to {gap['end']}, including any commitments affecting the required rest?")
            rows.append({**gap, "type": "busy_coverage", "plan_id": option["id"], "text": text})
        elif "question" in gap:
            rows.append({**gap, "type": "rule", "plan_id": option["id"], "text": gap["question"]})
        else:
            person, event = gap["person"], gap["event_id"]
            rest = snapshot["people"][person].get("rest_minutes", 0)
            meeting = "the requested call" if event == "$request" else event
            text = f"Is {person} available for {meeting} from {gap['start']} to {gap['end']}?"
            if rest:
                text += f" Check adjacent commitments for the required {rest}-minute gap."
            rows.append({**gap, "type": "availability", "plan_id": option["id"],
                         "rest_minutes": rest, "text": text})
    return rows


def _primary_source(primary, requested_rankings, comparison):
    if primary is None:
        return None, "no_verified_plan"
    if not primary["proofs"]:
        rows = [r for r in comparison["caps"] if r["plan_id"] == primary["id"]]
        best = min(rows, key=lambda r: r["status"] != "optimal")
        return {"evidence_mode": "verified", "ranking": "earliest", "max_moves": best["cap"],
                "snapshot_sha256": best["snapshot_sha256"]}, "verified_comparison_scope"
    mode = "verified" if "verified" in primary["proofs"] else "optimistic"
    ranking = next(iter(primary["proofs"][mode]))
    if ranking == "preferred":
        selection = "verified_preferred"
    elif "preferred" in requested_rankings:
        selection = "fallback_available_witness"
    else:
        selection = "fallback_requested_ranking"
    return {"evidence_mode": mode, "ranking": ranking}, selection


def _compare(options, verified, primary, objective, min_gain_minutes):
    """Classify the returned witnesses relative to the verified choices available in this run."""
    if not primary:
        for option in options:
            option["comparison"] = None
        return
    earliest = min(timestamp_minute(o["plan"]["start"]) for o in verified)
    best_score = min(_score(o["plan"], objective) for o in verified)
    first = primary["plan"]
    first_start = timestamp_minute(first["start"])
    disruption = ("protected_moves", "moved_meetings", "people_disrupted")
    material_minutes = max(1, min_gain_minutes)
    for option in options:
        candidate = option["plan"]
        start = timestamp_minute(candidate["start"])
        gain = earliest - start
        score = _score(candidate, objective)
        improved_metric = None
        if score < best_score:
            improved_metric = next(name for name, cost, best in zip(objective, score, best_score, strict=True)
                                   if cost != best)
        option["comparison"] = {
            "minutes_earlier_than_primary": first_start - start,
            "minutes_earlier_than_earliest_verified": gain,
            "improves_on_best_verified_objective": improved_metric is not None,
            "first_improved_objective_metric": improved_metric,
            "metric_deltas_from_primary": {key: val - first["metrics"][key]
                                           for key, val in candidate["metrics"].items()},
        }
        if option is primary:
            option["role"] = "primary"
        elif candidate["availability"] == "verified":
            if first_start - start >= material_minutes or _score(candidate, disruption) < _score(first, disruption):
                option["role"] = "verified_alternative"


def _reason_to_confirm(report, ranking, candidate, verified, min_gain_minutes):
    """Compare witnesses on the actual ranking, including the native evidence tie-break."""
    source = {"evidence_mode": "optimistic", "ranking": ranking, "plan_id": _plan_id(candidate),
              "max_moves": report["scope"]["max_moves"],
              "proof_status": report["optimistic"]["proofs"][ranking]["status"]}
    if not verified:
        return {**source, "baseline_id": None, "metric": None,
                "reason": "Confirmation is needed for the selected scheduling option."}
    order = report["optimistic"]["proofs"][ranking]["order"]
    score = _score(candidate, order)
    baseline = min(verified, key=lambda o: _score(o["plan"], order))
    known = _score(baseline["plan"], order)
    if score >= known:
        return None
    index = next(i for i in range(len(order)) if known[i] != score[i])
    metric, gain = order[index], known[index] - score[index]
    if metric == "start" and gain < max(1, min_gain_minutes):
        return None
    if metric == "start":
        reason = f"Confirmation could let the group meet {gain} minutes earlier."
    elif metric == "moved_meetings":
        reason = f"Confirmation could avoid moving {gain} existing meeting{'s' if gain != 1 else ''}."
    elif metric == "people_disrupted":
        reason = f"Confirmation could reduce the number of people affected by {gain}."
    elif metric == "protected_moves":
        reason = f"Confirmation could spare {gain} protected meeting{'s' if gain != 1 else ''}."
    else:
        reason = f"Confirmation could reduce {metric} by {gain}."
    baseline_cap = (report["scope"]["max_moves"] if baseline["proofs"] else baseline["comparison_caps"][0])
    return {**source, "baseline_id": baseline["id"], "baseline_max_moves": baseline_cap,
            "metric": metric, "gain": gain, "reason": reason}


def _merge_comparison(options, events, comparison, witnesses):
    frontier = set()
    for row, witness in zip(comparison["caps"], witnesses, strict=True):
        if witness is None:
            continue
        key = _plan_id(witness)
        if key in options and options[key]["plan"] != witness:
            # A team compiler may inspect max_moves, changing evidence or cost
            # even for identical placements. Preserve both scoped claims.
            encoded = json.dumps(witness, sort_keys=True, separators=(",", ":"), allow_nan=False)
            key = "plan-" + sha256(f"{key}:{encoded}".encode("utf-8")).hexdigest()
        if key not in options:
            options[key] = {**_option(witness, events), "id": key}
        row["plan_id"] = key
        options[key].setdefault("comparison_caps", []).append(row["cap"])
        if row["frontier"]:
            frontier.add(key)
    return frontier


def _select(report, snapshot, min_gain_minutes, comparison=None, witnesses=()):
    events = {event["id"]: event for event in snapshot.get("events", [])}
    options = _collect(report, events)
    frontier = _merge_comparison(options, events, comparison, witnesses) if comparison else set()
    verified = [option for option in options.values() if option["plan"]["availability"] == "verified"]
    # A smaller cap can find a verified witness while the full-budget search is
    # unresolved. Select by the caller's actual order and cite the scope that found it.
    first_rank = _rankings(report)[0]
    order = report["verified"]["proofs"][first_rank]["order"]
    primary = min(verified, key=lambda o: _score(o["plan"], order), default=None)
    _compare(options.values(), verified, primary, report["scope"]["objective"], min_gain_minutes)
    source, selection = _primary_source(primary, report["scope"]["rankings"], comparison)
    for key in frontier:
        if "role" not in options[key] or (options[key] is not primary and not options[key]["proofs"]):
            options[key]["role"] = "move_tradeoff"
    selected = [option for option in verified if "role" in option]
    if primary is not None:
        selected.sort(key=lambda o: o is not primary)
    questions = []
    question_source = None

    for ranking in _rankings(report):
        candidate = report["optimistic"][ranking]
        if candidate is None or candidate["availability"] == "verified":
            continue
        reason = _reason_to_confirm(report, ranking, candidate, verified, min_gain_minutes)
        if reason is None:
            continue
        option = options[reason["plan_id"]]
        if "role" not in option:
            option["role"] = "conditional"
            selected.append(option)
        option.setdefault("confirmation_reasons", []).append(reason)
        if question_source is None:
            question_source = reason
            questions = _questions(option, snapshot)

    if primary:
        status = "verified_option"
    elif question_source:
        status = "conditional_only"
    elif report["optimistic"]["status"] == "infeasible_in_scope":
        status = "infeasible_in_scope"
    else:
        status = "no_solution_found"
    advice = {"status": status, "primary_id": primary["id"] if primary else None,
              "primary_source": source, "primary_selection": selection,
              "options": selected, "questions": questions, "question_source": question_source}
    if comparison:
        # The full report retains the placement even for a sampled/dominated
        # witness absent from the human shortlist. Every referenced ID is traceable.
        selected_ids = {o["id"] for o in selected}
        for row in comparison["caps"]:
            if row["plan_id"] is not None and row["plan_id"] not in selected_ids:
                row["witness"] = options[row["plan_id"]]["plan"]
        advice["move_comparison"] = comparison
    return advice


def _selected_hooks(registry, selections):
    if registry is None:
        registry = {}
    if not isinstance(registry, Mapping):
        raise ValueError("heuristics must be a mapping of names to trusted callables")
    hooks = []
    for entry in selections:
        name = entry["name"]
        hook = registry.get(name)
        if not callable(hook):
            raise ValueError(f"no registered callable for heuristic {name!r}")
        hooks.append((name, hook, entry["params"]))
    return hooks


def _annotations(hooks, snapshot, advice):
    context = {"snapshot": snapshot, "options": advice["options"],
               "recommended_id": advice["primary_id"]}
    if "move_comparison" in advice:
        context["move_comparison"] = advice["move_comparison"]
    ids = {option["id"] for option in advice["options"]}
    annotations = []
    for name, hook, params in hooks:
        try:
            notes = hook(deepcopy(context), deepcopy(params))
        except Exception as error:
            raise ValueError(f"heuristic {name!r} failed: {error}") from error
        if not isinstance(notes, list):
            raise ValueError(f"heuristic {name!r} must return a list")
        for note in notes:
            if (not isinstance(note, dict) or set(note) != {"plan_id", "kind", "text"}
                    or not isinstance(note["plan_id"], str) or note["plan_id"] not in ids
                    or note["kind"] not in ("note", "question")
                    or not isinstance(note["text"], str) or not note["text"].strip()):
                raise ValueError(f"heuristic {name!r} returned an invalid annotation "
                                 "(expected an existing plan_id, note/question kind and nonempty text)")
            annotations.append({"heuristic": name, **note})
    return annotations


def advise(snapshot: dict, *, profile: dict | None = None, registry=None, heuristics=None,
           time_limit_seconds=None, workers=None, rankings=None, compare_moves=False) -> dict:
    """Return the raw CP-SAT result and advisory choices for a single shared solve budget.

    Rule compilers go in registry. Trusted presentation hooks go in heuristics and
    run only when selected by the profile. No hook result participates in optimization.
    """
    settings = resolve_profile(snapshot, profile, time_limit_seconds=time_limit_seconds,
                               workers=workers, rankings=rankings)
    hooks = _selected_hooks(heuristics, settings.heuristics)
    if type(compare_moves) is not bool:
        raise ValueError("compare_moves must be a Boolean")
    deadline = monotonic() + settings.time_limit_seconds
    # Preserve the full ordinary budget when the caller wants just one scenario.
    single_scope = (settings.snapshot.get("max_moves") == 0
                    and (settings.rankings is None or "earliest" in settings.rankings))
    budget = settings.time_limit_seconds
    if compare_moves and not single_scope:
        budget /= 2
    result = plan(settings.snapshot, registry=registry, time_limit_seconds=budget,
                  workers=settings.workers, rankings=settings.rankings)
    comparison, witnesses = (compare_move_caps(settings, registry, result, deadline) if compare_moves
                             else (None, ()))
    advice = _select(result, settings.snapshot, settings.min_gain_minutes, comparison, witnesses)
    advice.update(profile=settings.name, min_gain_minutes=settings.min_gain_minutes,
                  guidance=settings.guidance, annotations=_annotations(hooks, settings.snapshot, advice))
    return {"output_version": "1.0", "planner_version": __version__, "result": result, "advice": advice}
