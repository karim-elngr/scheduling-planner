"""Compile one calendar snapshot and optimize exact integer objectives with CP-SAT."""

from copy import deepcopy
from dataclasses import dataclass
from time import monotonic

import ortools
from ortools.sat.python import cp_model

from ._version import __version__
from .config import (
    DEFAULT_OBJECTIVE as DEFAULT, DEFAULT_TIME_LIMIT_SECONDS, DEFAULT_WORKERS, RANKINGS,
    ranking_names, time_limit, worker_count,
)
from .domain import Problem, iso, parse_snapshot, snapshot_digest
from .model import BuildContext, build_model, calendar_questions
from .rules import compile_rules
from .rules.api import RuleBinding


DISRUPTION = ("protected_moves", "moved_meetings", "people_disrupted", "soft_penalty", "start")
EARLIEST = ("start", "protected_moves", "moved_meetings", "people_disrupted", "soft_penalty")
UNCERTAINTY = "unresolved_count"


def _constant(expression):
    """Recognize literal and singleton-domain constants without testing a CP expression as bool."""
    if isinstance(expression, int):
        return int(expression)
    if isinstance(expression, cp_model.IntVar):
        domain = expression.proto.domain
        if len(domain) == 2 and domain[0] == domain[1]:
            return int(domain[0])
    return None


@dataclass
class _Model:
    context: BuildContext
    rules: list[RuleBinding]
    metrics: dict
    has_unknowns: bool


def _compile(problem: Problem, registry) -> _Model:
    ctx = build_model(problem)
    rules = compile_rules(ctx, problem.raw.get("constraints", []), registry)
    metrics = dict(ctx.metrics)
    penalties = []
    max_soft_cost = 0
    unknowns = [expression for expression, _ in ctx.unknowns]
    for rule in rules:
        unknowns.append(rule.unknown)
        if rule.mode == "soft":
            upper = (rule.units if isinstance(rule.units, int)
                     else rule.units.proto.domain[len(rule.units.proto.domain) - 1])
            max_soft_cost += rule.weight * upper
            if max_soft_cost > (1 << 63) - 1:
                raise ValueError("total weighted soft cost exceeds signed 64-bit integer bounds")
            cost = rule.weight * rule.units
            penalties.append(cost)
            metrics[f"rule:{rule.id}"] = cost
    metrics["soft_penalty"] = sum(penalties)
    uncertainty = sum(unknowns)
    metrics[UNCERTAINTY] = uncertainty
    error = ctx.model.validate()
    if error:
        raise ValueError(f"invalid CP-SAT model: {error}")
    return _Model(ctx, rules, metrics, any(_constant(x) != 0 for x in unknowns))


def _restrict(model, compiled, verified, track=False):
    """Enforce policy and evidence. Diagnostic solves can name every such requirement."""
    labels = {}

    def require_zero(expression, label):
        if _constant(expression) == 0:
            return
        constraint = model.add(expression == 0)
        if track:
            flag = model.new_bool_var(f"assume:{len(labels)}")
            constraint.only_enforce_if(flag)
            model.add_assumption(flag)
            labels[flag.index] = label

    for rule in compiled.rules:
        label = {"rule_id": rule.id, "kind": rule.kind}
        if rule.source is not None:
            label["source"] = rule.source
        if rule.mode == "hard":
            require_zero(rule.units, {"type": "hard_rule", **label})
        if verified:
            require_zero(rule.unknown, {"type": "verified_rule", **label, "question": rule.question})
    if verified:
        for count, metadata in compiled.context.unknowns:
            require_zero(count, {"type": "verified_calendar", **metadata})
    return labels


@dataclass
class _Solution:
    report: dict
    scores: dict
    hints: dict
    origin: tuple[str, str] | None = None

    def witness(self, order):
        mode, ranking = self.origin
        return {"origin": {"evidence_mode": mode, "ranking": ranking},
                "scores": {metric: self.scores[metric] for metric in order}}

    def key(self, order):
        return tuple(self.scores[name] for name in order)


def _read_solution(compiled, solver):
    ctx = compiled.context
    value = solver.value
    requested = ctx.slots["$request"]
    moves = []
    questions = calendar_questions(ctx, value)
    counts = {k: value(v) for k, v in compiled.metrics.items()}
    penalties = {}
    notes = []
    for meeting in ctx.problem.events:
        slot = ctx.slots[meeting.id]
        if value(slot.moved):
            moves.append({"event_id": meeting.id, "from_start": iso(slot.original),
                          "to_start": iso(value(slot.start)), "to_end": iso(value(slot.end))})
    for rule in compiled.rules:
        record = {"rule_id": rule.id, "mode": rule.mode, "kind": rule.kind}
        if rule.source is not None:
            record["source"] = rule.source
        if value(rule.unknown):
            questions.append({**record, "question": rule.question})
        if rule.mode == "soft":
            units = value(rule.units)
            penalties[rule.id] = cost = rule.weight * units
            if units:
                notes.append({**record, "reason": rule.reason, "units": units,
                              "weight": rule.weight, "penalty": cost})
    decisions = {name: ctx.decision_values[name][value(var)] for name, var in ctx.decisions.items()}
    variables = [slot.start for slot in ctx.slots.values()] + list(ctx.decisions.values())
    hints = {var.index: value(var) for var in variables if isinstance(var, cp_model.IntVar)}
    report = {
        "start": iso(value(requested.start)), "end": iso(value(requested.end)),
        "moves": moves, "decisions": decisions,
        "metrics": {name: counts[name] for name in
                    ("protected_moves", "moved_meetings", "people_disrupted", "soft_penalty")},
        "unresolved": questions, "availability": "conditional" if questions else "verified",
        "rule_notes": notes, "rule_penalties": penalties,
    }
    return _Solution(report, counts, hints)


def _finish(proof, incumbent, status):
    proof["status"] = status
    return incumbent, proof


def _solve_order(compiled, verified, order, deadline, workers, incumbent):
    # Solving a ranking must not fix values in any other ranking or evidence mode.
    proof = {"order": list(order), "stages": []}
    if deadline <= monotonic():
        proof["stages"].append({"metric": order[0], "status": "TIME_LIMIT", "proven": False})
        return _finish(proof, incumbent, "best_found" if incumbent is not None else "unknown")
    model = compiled.context.model.clone()
    _restrict(model, compiled, verified)
    for name in order:
        expression = compiled.metrics[name]
        known = _constant(expression)
        if known is not None and incumbent is not None:
            proof["stages"].append({"metric": name, "status": "CONSTANT", "value": known,
                                    "best_bound": known, "proven": True})
            continue
        lower = min(compiled.context.slots["$request"].starts) if name == "start" else 0
        if incumbent is not None and incumbent.scores[name] == lower:
            # All cost/count terms are nonnegative. The minimum declared start is
            # an integer lower bound for time. An existing witness meeting the
            # bound certifies this tier without launching another native search.
            model.add(expression == lower)
            proof["stages"].append({"metric": name, "status": "LOWER_BOUND", "value": lower,
                                    "best_bound": lower, "proven": True})
            continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            proof["stages"].append({"metric": name, "status": "TIME_LIMIT", "proven": False})
            break

        # The proto stores objective offsets as doubles. Strip the exact integer
        # offset before search and restore it using integer arithmetic for bounds.
        offset = expression if isinstance(expression, int) else cp_model.FlatIntExpr(expression).offset
        model.minimize(expression - offset)
        model.clear_hints()
        if incumbent is not None:
            for index, previous in incumbent.hints.items():
                model.add_hint(model.get_int_var_from_proto_index(index), previous)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1e-9, deadline - monotonic())
        solver.parameters.num_search_workers = workers
        solver.parameters.relative_gap_limit = 0.0
        solver.parameters.absolute_gap_limit = 0.0
        status = solver.solve(model)
        stage = {"metric": name, "status": solver.status_name(status), "proven": False,
                 "branches": solver.num_branches, "conflicts": solver.num_conflicts,
                 "wall_seconds": solver.wall_time}
        proof["stages"].append(stage)
        if status == cp_model.MODEL_INVALID:
            raise ValueError(f"invalid CP-SAT model or objective for {name}: {model.validate()}")
        if status == cp_model.INFEASIBLE:
            if incumbent is not None:
                raise RuntimeError("CP-SAT rejected a model with an existing feasible incumbent")
            return _finish(proof, None, "infeasible_in_scope")
        if status == cp_model.UNKNOWN:
            break

        current = _read_solution(compiled, solver)
        if incumbent is None or current.key(order) < incumbent.key(order):
            incumbent = current
        stage["value"] = incumbent.scores[name]
        if status == cp_model.FEASIBLE:
            stage["best_bound"] = solver.response_proto.inner_objective_lower_bound + offset
            break
        if status != cp_model.OPTIMAL:
            raise RuntimeError(f"unexpected CP-SAT status {status!r}")
        # The integer variable/expression value is exact; objective_value is a double.
        optimum = current.scores[name]
        stage.update(value=optimum, best_bound=optimum, proven=True)
        if known is None:
            model.add(expression == optimum)
    else:
        return _finish(proof, incumbent, "optimal")
    return _finish(proof, incumbent, "best_found" if incumbent is not None else "unknown")


def _ranking_orders(problem, rankings, compiled):
    preferred = problem.objective or DEFAULT
    for metric in preferred:
        if metric == UNCERTAINTY or metric not in compiled.metrics:
            raise ValueError(f"unknown objective metric {metric!r}; select a metric or rule:<soft rule id>")
    orders = {"preferred": tuple(preferred), "earliest": EARLIEST, "least_disruption": DISRUPTION}
    return {key: (*orders[key], UNCERTAINTY) for key in rankings}


def _group_status(proofs):
    statuses = {proof["status"] for proof in proofs.values()}
    if len(statuses) == 1:
        return next(iter(statuses))
    if statuses <= {"unknown", "infeasible_in_scope"}:
        return "unknown"
    return "best_found"


def _reconcile(result, orders, solutions):
    """Share witnesses and valid proofs between objectives and nested evidence sets."""
    for mode in ("optimistic", "verified"):
        group = result[mode]
        # Feasibility is independent of the objective. A single infeasibility
        # proof covers every ranking, and the optimistic set contains the verified set.
        source = next(((pool, rank) for pool in dict.fromkeys((mode, "optimistic"))
                       for rank, proof in result[pool]["proofs"].items()
                       if proof["status"] == "infeasible_in_scope"), None)
        witnesses = [s for s in solutions if mode == "optimistic" or s.scores[UNCERTAINTY] == 0]
        for rank, order in orders.items():
            proof = group["proofs"][rank]
            if proof["status"] not in ("unknown", "best_found"):
                continue
            if source is not None:
                pool, source_rank = source
                group[rank] = None
                group["proofs"][rank] = {
                    **proof, "status": "infeasible_in_scope", "witness": None,
                    "basis": "infeasible under the same or less restrictive evidence requirements",
                    "derived_from": {"evidence_mode": pool, "ranking": source_rank},
                }
                continue
            optimistic = result["optimistic"]
            if (mode == "verified" and optimistic["proofs"][rank]["status"] == "optimal"
                    and optimistic[rank]["availability"] == "verified"):
                group[rank] = optimistic[rank]
                group["proofs"][rank] = {
                    **proof, "status": "optimal",
                    "basis": "same ranking optimized over a superset, with a fully verified optimum",
                    "witness": optimistic["proofs"][rank]["witness"],
                    "derived_from": {"evidence_mode": "optimistic", "ranking": rank},
                }
            elif witnesses:
                best = min(witnesses, key=lambda s: s.key(order))
                group[rank] = best.report
                group["proofs"][rank] = {**proof, "status": "best_found", "witness": best.witness(order)}
        group["status"] = _group_status(group["proofs"])


def _count_native_call(stats, report):
    if "wall_seconds" in report:
        stats["native_solves"] += 1
        stats["branches"] += report["branches"]
        stats["conflicts"] += report["conflicts"]
        stats["native_wall_seconds"] += report["wall_seconds"]


def _diagnose(compiled, mode, deadline):
    """Ask for an objective-free sufficient assumption core only after proven infeasibility."""
    report = {"evidence_mode": mode, "assumptions": [], "minimality": "not_established"}
    if deadline <= monotonic():
        return {**report, "status": "no_diagnostic_time", "native_status": "SKIPPED"}
    model = compiled.context.model.clone()
    # An extension can add native assumptions while building the base model.
    # CP-SAT encodes the negation of variable i as literal -(i + 1).
    labels = {}
    for literal in model.proto.assumptions:
        index = literal if literal >= 0 else -literal - 1
        labels[literal] = {"type": "extension_assumption", "literal": literal,
                           "variable": model.proto.variables[index].name, "value": literal >= 0}
    labels.update(_restrict(model, compiled, mode == "verified", track=True))
    if not labels:
        return {**report, "status": "base_infeasible", "native_status": "ALREADY_PROVEN"}
    remaining = deadline - monotonic()
    if remaining <= 0:
        return {**report, "status": "no_diagnostic_time", "native_status": "SKIPPED"}
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    report.update(native_status=solver.status_name(status), branches=solver.num_branches,
                  conflicts=solver.num_conflicts, wall_seconds=solver.wall_time)
    if status == cp_model.UNKNOWN:
        return {**report, "status": "no_diagnostic_time"}
    if status != cp_model.INFEASIBLE:
        raise RuntimeError(f"diagnostic feasibility model disagreed with proven infeasibility: {status}")
    core = [labels[index] for index in solver.sufficient_assumptions_for_infeasibility()]
    return {**report, "assumptions": core, "status": "sufficient_core" if core else "base_infeasible"}


def plan(data: dict, registry=None, *, time_limit_seconds: float = DEFAULT_TIME_LIMIT_SECONDS,
         workers: int = DEFAULT_WORKERS, rankings=None) -> dict:
    """Optimize the requested rankings under verified and optimistic evidence.

    The wall-time budget is shared across the entire call. Each unattempted
    evidence/ranking combination receives a share of the remaining time. Input
    parsing, Python rule compilation and a native call already in progress cannot
    be forcibly preempted. A feasible incumbent survives a later search timeout.
    """
    time_limit_seconds = time_limit(time_limit_seconds)
    workers = worker_count(workers)
    rankings = RANKINGS if rankings is None else ranking_names(rankings)
    started = monotonic()
    deadline = started + time_limit_seconds
    # Scenarios regularly reuse and edit input dictionaries. The model, proof
    # scope and fingerprint all refer to the same detached input for this run.
    data = deepcopy(data)
    problem = parse_snapshot(data)
    fingerprint = snapshot_digest(data)
    compiled = _compile(problem, registry)
    orders = _ranking_orders(problem, rankings, compiled)
    modes = ("verified", "optimistic") if compiled.has_unknowns else ("verified",)
    jobs_left = len(modes) * len(set(orders.values()))
    search_started = monotonic()
    result = {
        "result_version": "1.0", "planner_version": __version__,
        "backend": {"name": "ortools.cp_sat", "version": ortools.__version__},
        "statistics": {"model_build_seconds": search_started - started,
                       "base_variables": len(compiled.context.model.proto.variables),
                       "base_constraints": len(compiled.context.model.proto.constraints),
                       "native_solves": 0, "branches": 0, "conflicts": 0, "native_wall_seconds": 0.0},
        "scope": {"snapshot_id": data.get("snapshot_id"), "snapshot_sha256": fingerprint,
                  "evidence_policy": problem.evidence_policy,
                  "locked_incomplete_rosters": sorted(e.id for e in problem.events if not e.attendees_complete),
                  "max_moves": problem.max_moves,
                  "objective": list(data.get("objective", DEFAULT)), "rankings": list(orders),
                  "time_limit_seconds": time_limit_seconds, "workers": workers,
                  "decision_domains": problem.decisions, "constraints": data.get("constraints", []),
                  "request_start_count": problem.request.domain_count,
                  "request_starts_after_window_pruning": compiled.context.slots["$request"].domain_size,
                  "event_count": len(problem.events),
                  "movable_event_count": sum(compiled.context.slots[e.id].domain_size > 1
                                             for e in problem.events),
                  "time_precision": "whole minutes; supplied starts or window-anchored grids",
                  "migration_notes": list(problem.migration_notes)},
    }
    verified_solutions = []
    for mode in modes:
        solutions = list(verified_solutions)
        cached = {}
        group = {"proofs": {}}
        impossible = False
        for label, order in orders.items():
            if order not in cached:
                now = monotonic()
                allocation = max(0.0, deadline - now) / jobs_left
                jobs_left -= 1
                if impossible:
                    solution = None
                    proof = {"order": list(order), "stages": [], "status": "infeasible_in_scope",
                             "basis": "same evidence model already proved infeasible"}
                else:
                    seed = min(solutions, key=lambda s: s.key(order), default=None)
                    solution, proof = _solve_order(compiled, mode == "verified", order,
                                                    now + allocation, workers, seed)
                    for stage in proof["stages"]:
                        _count_native_call(result["statistics"], stage)
                    impossible = proof["status"] == "infeasible_in_scope"
                    if solution is not None:
                        if solution.origin is None:
                            solution.origin = (mode, label)
                        solutions.append(solution)
                cached[order] = (solution, proof)
            solution, proof = cached[order]
            group[label] = solution.report if solution is not None else None
            group["proofs"][label] = {**proof, "witness": solution.witness(order) if solution else None}
        result[mode] = group
        if mode == "verified":
            verified_solutions = solutions
    if not compiled.has_unknowns:
        result["optimistic"] = result["verified"]
    _reconcile(result, orders, solutions)
    # An infeasible optimistic model is also infeasible with stricter evidence.
    # Its sufficient core is enough to explain both; never spend a second pass.
    diagnoses = []
    for mode in ("optimistic", "verified"):
        if result[mode]["status"] != "infeasible_in_scope":
            continue
        if diagnoses:
            result[mode]["infeasibility"] = diagnoses[0]
            continue
        diag = _diagnose(compiled, mode, min(deadline, monotonic() + 1.0))
        diagnoses.append(diag)
        result[mode]["infeasibility"] = diag
        _count_native_call(result["statistics"], diag)
    result["search_status"] = (
        "complete" if all(result[mode]["status"] in ("optimal", "infeasible_in_scope")
                          for mode in ("verified", "optimistic")) else "limit_reached"
    )
    finished = monotonic()
    result["statistics"]["search_seconds"] = finished - search_started
    result["statistics"]["elapsed_seconds"] = finished - started
    return result
