"""Regression tests for complete-plan rules, configurable objectives and trusted extensions."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from examples.team_rules import RULES
from scheduling_planner import plan
from scheduling_planner.rules import RuleEffect


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "examples" / "release-review.json"
RULES_FILE = ROOT / "examples" / "team_rules.py"
TEN_DUBAI = "2026-12-01T06:00Z"
ELEVEN_DUBAI = "2026-12-01T07:00Z"


def load_example():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


class ConstraintTests(unittest.TestCase):
    def test_checkout_example_runs_through_cli(self):
        command = [sys.executable, str(ROOT / "planner.py"), str(SNAPSHOT),
                   "--rules-file", str(RULES_FILE)]
        result = json.loads(subprocess.check_output(command, cwd=ROOT, text=True))
        self.assertEqual(result["verified"]["preferred"]["start"], ELEVEN_DUBAI)
        self.assertEqual(result["verified"]["preferred"]["decisions"]["standby_responder"], "Lin")
        api_result = plan(load_example(), registry=RULES)
        self.assertEqual(result["verified"]["preferred"]["start"], api_result["verified"]["preferred"]["start"])

    def test_external_responder_changes_selected_start(self):
        data = load_example()
        self.assertEqual(plan(data, registry=RULES)["verified"]["earliest"]["start"], ELEVEN_DUBAI)
        data["constraints"] = data["constraints"][:2]
        self.assertEqual(plan(data)["verified"]["earliest"]["start"], TEN_DUBAI)

    def test_two_competing_soft_preferences_change_winner_with_weight(self):
        data = load_example()
        coverage = data["constraints"][-1]
        coverage.update(mode="soft", weight=1)
        data["constraints"].append({
            "id": "early-target", "kind": "finish_by", "mode": "soft", "weight": 1,
            "params": {"event": "$request", "deadline": "2026-12-01T10:30+04:00"}
        })
        cheap_gap = plan(data, registry=RULES)["verified"]["preferred"]
        self.assertEqual(cheap_gap["start"], TEN_DUBAI)
        self.assertEqual(cheap_gap["metrics"]["soft_penalty"], 1)

        coverage["weight"] = 61
        costly_gap = plan(data, registry=RULES)["verified"]["preferred"]
        self.assertEqual(costly_gap["start"], ELEVEN_DUBAI)
        self.assertEqual(costly_gap["metrics"]["soft_penalty"], 60)

    def test_missing_fact_is_a_question_and_never_verified(self):
        for name in ("release_cutoff", "release_responders"):
            with self.subTest(fact=name):
                data = load_example()
                del data["facts"][name]
                result = plan(data, registry=RULES)
                self.assertIsNone(result["verified"]["preferred"])
                conditional = result["optimistic"]["preferred"]
                self.assertEqual(conditional["availability"], "conditional")
                self.assertTrue(conditional["unresolved"])

    def test_rule_priority_can_rank_incomparable_costs_individually(self):
        data = load_example()
        data["constraints"][-1]["mode"] = "soft"
        data["constraints"].append({
            "id": "finish-early", "kind": "finish_by", "mode": "soft",
            "params": {"event": "$request", "deadline": "2026-12-01T10:30+04:00"},
        })
        for priority, start in [("finish-early", TEN_DUBAI), ("incident-cover", ELEVEN_DUBAI)]:
            with self.subTest(priority=priority):
                data["objective"] = [f"rule:{priority}", "soft_penalty", "start"]
                winner = plan(data, registry=RULES)["verified"]["preferred"]
                self.assertEqual(winner["start"], start)
                self.assertEqual(winner["rule_penalties"][priority], 0)

    def test_old_nondefault_enumeration_budget_has_an_actionable_error(self):
        data = load_example()
        data["max_states"] = 2
        with self.assertRaisesRegex(ValueError, "time.limit"):
            plan(data, registry=RULES)

    def test_custom_rule_checks_external_meeting_after_relocation(self):
        data = load_example()
        data["events"][1]["alternatives"] = ["2026-12-01T11:00+04:00"]
        result = plan(data, registry=RULES)
        early = result["verified"]["earliest"]
        self.assertEqual(early["start"], TEN_DUBAI)
        self.assertEqual([m["event_id"] for m in early["moves"]], ["vendor-call"])
        self.assertEqual(result["verified"]["preferred"]["moves"], [])

    def test_precedence_uses_relocated_predecessor(self):
        data = load_example()
        data["request"]["starts"] = data["request"]["starts"][:1]
        data["constraints"] = data["constraints"][:2]
        check = data["events"][0]
        check["start"] = "2026-12-01T11:00+04:00"
        check["alternatives"] = ["2026-12-01T09:15+04:00"]
        recommended = plan(data)["verified"]["preferred"]
        self.assertEqual(recommended["start"], TEN_DUBAI)
        self.assertEqual([m["event_id"] for m in recommended["moves"]], ["smoke-check"])

    def test_no_registered_rule_fails_loudly_even_with_no_viable_slots(self):
        data = load_example()
        data["request"]["starts"] = ["2026-12-10T10:00+04:00"]
        with self.assertRaisesRegex(ValueError, "standby_cover"):
            plan(data)

    def test_room_capacity_can_select_a_room_as_part_of_the_plan(self):
        data = load_example()
        data["decisions"]["room"] = ["booth", "studio"]
        data["facts"]["seats"] = {"booth": 2, "studio": 8}
        data["constraints"].append({
            "id": "room-fit", "kind": "room_capacity", "mode": "hard", "params": {}
        })

        def room_capacity(ctx, params):
            room = ctx.decisions["room"]
            sizes = ctx.problem.raw["facts"]["seats"]
            headcount = len(ctx.slots["$request"].attendees)
            shortage = [max(0, headcount - sizes[name]) for name in ctx.decision_values["room"]]
            units = ctx.model.new_int_var(0, max(shortage), "seat_shortage")
            ctx.model.add_element(room, shortage, units)
            return RuleEffect(units=units, reason="Too few seats.")

        winner = plan(data, registry={**RULES, "room_capacity": room_capacity})["verified"]["preferred"]
        self.assertEqual(winner["decisions"], {"room": "studio", "standby_responder": "Lin"})


if __name__ == "__main__":
    unittest.main()
