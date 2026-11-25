"""Pin decisions before solving: exact rule outputs must hold even without minimization."""

import json
from pathlib import Path
import runpy
import unittest

from ortools.sat.python import cp_model

from scheduling_planner.domain import parse_snapshot, timestamp_minute
from scheduling_planner.model import build_model
from scheduling_planner.rules import RuleEffect, compile_rules


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "release-review.json"
RULES = runpy.run_path(str(ROOT / "examples" / "team_rules.py"))["RULES"]


def snapshot():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def effects(data, starts=None, choices=None):
    ctx = build_model(parse_snapshot(data))
    bindings = compile_rules(ctx, data["constraints"], RULES)
    for event, timestamp in (starts or {}).items():
        ctx.model.add(ctx.slots[event].start == timestamp_minute(timestamp))
    for name, choice in (choices or {}).items():
        ctx.model.add(ctx.decisions[name] == ctx.decision_values[name].index(choice))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    if solver.solve(ctx.model) != cp_model.OPTIMAL:
        raise AssertionError("test assignment must be calendar-feasible")
    results = {rule.id: (solver.value(rule.units), solver.value(rule.unknown)) for rule in bindings}
    # If outputs could take another value with identical primary decisions, the
    # compiler would only define a relaxation and could mislead another ranking.
    if bindings:
        for slot in ctx.slots.values():
            ctx.model.add(slot.start == solver.value(slot.start))
        for var in ctx.decisions.values():
            ctx.model.add(var == solver.value(var))
        variables = [var for binding in bindings for var in (binding.units, binding.unknown)
                     if isinstance(var, cp_model.IntVar)]
        if variables:
            ctx.model.add_forbidden_assignments(variables, [[solver.value(var) for var in variables]])
            if solver.solve(ctx.model) != cp_model.INFEASIBLE:
                raise AssertionError("nonunique rule effect for fixed primary decisions")
    return results


class RuleCompilerTests(unittest.TestCase):
    def test_coverage_for_both_starts_and_the_boundary_of_duty(self):
        data = snapshot()
        for start, expected in [("10:00", (1, 0)), ("11:00", (0, 0))]:
            with self.subTest(start=start):
                result = effects(data, {"$request": f"2026-12-01T{start}+04:00"}, {"standby_responder": "Lin"})
                self.assertEqual(result["incident-cover"], expected)
        data["people"]["Lin"]["possible"] = data["people"]["Lin"]["available"]
        data["people"]["Lin"]["available"] = []
        result = effects(data, {"$request": "2026-12-01T11:00+04:00"}, {"standby_responder": "Lin"})
        self.assertEqual(result["incident-cover"], (0, 1))
        result = effects(data, {"$request": "2026-12-01T10:00+04:00"}, {"standby_responder": "Lin"})
        self.assertEqual(result["incident-cover"], (1, 0))

    def test_coverage_tracks_relocated_event(self):
        data = snapshot()
        data["events"][1]["alternatives"] = ["2026-12-01T11:00+04:00"]
        pinned = {"$request": "2026-12-01T10:00+04:00", "vendor-call": "2026-12-01T11:00+04:00"}
        self.assertEqual(effects(data, pinned, {"standby_responder": "Lin"})["incident-cover"], (0, 0))

    def test_responder_choice_reports_only_the_selected_person(self):
        data = snapshot()
        data["people"]["Jo"] = {"available": data["people"]["Lin"]["available"], "busy_coverage": []}
        data["decisions"]["standby_responder"].append("Jo")
        data["facts"]["release_responders"].append("Jo")
        at_eleven = {"$request": "2026-12-01T11:00+04:00"}
        # Asha is attending, Lin is available and observed, Jo's calendar is unobserved.
        for person, expected in [("Asha", (1, 0)), ("Lin", (0, 0)), ("Jo", (0, 1))]:
            with self.subTest(person=person):
                self.assertEqual(effects(data, at_eleven, {"standby_responder": person})["incident-cover"], expected)
        data["facts"]["release_responders"].remove("Jo")
        self.assertEqual(effects(data, at_eleven, {"standby_responder": "Jo"})["incident-cover"], (1, 0))
        del data["facts"]["release_responders"]
        self.assertEqual(effects(data, at_eleven, {"standby_responder": "Lin"})["incident-cover"], (0, 1))
        self.assertEqual(effects(data, at_eleven, {"standby_responder": "Asha"})["incident-cover"], (1, 0))
        self.assertEqual(effects(data, {"$request": "2026-12-01T10:00+04:00"},
                                 {"standby_responder": "Lin"})["incident-cover"], (1, 0))

    def test_invalid_responder_decision_requires_known_people(self):
        data = snapshot()
        data["constraints"][-1]["params"]["responder_decision"] = "typo"
        with self.assertRaisesRegex(ValueError, "existing finite choice"):
            effects(data)
        data["constraints"][-1]["params"]["responder_decision"] = "standby_responder"
        data["decisions"]["standby_responder"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "known person IDs"):
            effects(data)

    def test_soft_units_and_precedence_are_exact_with_no_objective(self):
        data = snapshot()
        data["constraints"][0]["params"]["deadline"] = "2026-12-01T10:30+04:00"
        del data["constraints"][0]["params"]["fact"]
        data["constraints"][1]["params"]["gap_minutes"] = 95
        pinned = {"$request": "2026-12-01T11:00+04:00"}
        result = effects(data, pinned)
        self.assertEqual(result["release-deadline"], (60, 0))
        self.assertEqual(result["read-smoke-results"], (5, 0))

    def test_room_table_matches_all_choices(self):
        data = snapshot()
        data["decisions"] = {"room": ["booth", "boardroom", "offsite"]}
        data["facts"]["seats"] = {"booth": 2, "boardroom": 8}
        data["constraints"] = [{"id": "seating", "kind": "room_capacity", "mode": "soft",
                                "params": {"decision": "room", "capacity_fact": "seats"}}]
        for room, expected in [("booth", (3, 0)), ("boardroom", (0, 0)), ("offsite", (0, 1))]:
            with self.subTest(room=room):
                self.assertEqual(effects(data, choices={"room": room})["seating"], expected)

    def test_invalid_custom_effect_is_rejected(self):
        data = snapshot()
        ctx = build_model(parse_snapshot(data))
        for value in [-1, cp_model.CpModel().new_bool_var("from another model")]:
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                compile_rules(ctx, [{"id": "bad", "kind": "malformed", "mode": "hard"}],
                              {"malformed": lambda _ctx, _params, units=value: RuleEffect(units=units, reason="Invalid.")})

    def test_dynamic_unknown_masks_units_exactly(self):
        data = snapshot()
        data["decisions"] = {"location": ["office", "offsite"]}

        def location_policy(ctx, params):
            # Office has an observed cost of 3. Offsite still needs a cost quote.
            return RuleEffect(units=3, unknown=ctx.decisions["location"],
                              reason="Office setup cost.", question="What is the offsite quote?")

        for location, expected in enumerate([3, 0]):
            ctx = build_model(parse_snapshot(data))
            rule = compile_rules(ctx, [{"id": "setup", "kind": "setup_cost", "mode": "soft"}],
                                 {"setup_cost": location_policy})[0]
            ctx.model.add(ctx.decisions["location"] == location)
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = 1
            self.assertEqual(solver.solve(ctx.model), cp_model.OPTIMAL)
            self.assertEqual(solver.value(rule.units), expected)
            self.assertEqual(solver.value(rule.unknown), location)

    def test_mutually_exclusive_missing_known_roster(self):
        data = snapshot()
        del data["constraints"][-1]["params"]["responder_decision"]
        del data["decisions"]
        del data["facts"]["release_responders"]
        self.assertEqual(effects(data)["incident-cover"], (0, 1))
        data["facts"]["release_responders"] = []
        self.assertEqual(effects(data)["incident-cover"], (1, 0))


if __name__ == "__main__":
    unittest.main()
