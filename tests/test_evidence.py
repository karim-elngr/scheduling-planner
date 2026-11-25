"""Public scheduling behavior for observed occupancy and normalized JSON numbers."""

from copy import deepcopy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

from scheduling_planner import plan
from scheduling_planner.domain import iso, parse_snapshot


def person(hours, observed=None, rest=0):
    result = {"available": [[iso(a), iso(b)] for a, b in hours], "rest_minutes": rest}
    if observed is not None:
        result["busy_coverage"] = [[iso(a), iso(b)] for a, b in observed]
    return result


def case():
    return {
        "evidence_policy": "explicit",
        "request": {"attendees": ["A"], "duration_minutes": 30, "starts": [iso(100)]},
        "people": {"A": person([(100, 130)], [(100, 130)], rest=15)},
    }


def solve(data, registry=None):
    return plan(data, registry=registry, rankings=["preferred"])


def intervals(candidate):
    return [(q["person"], q["event_id"], q.get("evidence"), q["start"], q["end"])
            for q in candidate["unresolved"]]


class OccupancyEvidenceTests(unittest.TestCase):
    def test_partial_source_coordinates_two_missing_intervals(self):
        data = case()
        for policy in ("explicit", "legacy"):
            with self.subTest(policy=policy):
                report = solve(dict(data, evidence_policy=policy))
                self.assertIsNone(report["verified"]["preferred"])
                self.assertEqual(intervals(report["optimistic"]["preferred"]), [
                    ("A", "$request", "busy_coverage", iso(85), iso(100)),
                    ("A", "$request", "busy_coverage", iso(130), iso(145)),
                ])

    def test_rest_can_be_verified_outside_working_hours(self):
        data = case()
        data["people"]["A"]["busy_coverage"] = [[iso(100), iso(145)], [iso(85), iso(100)]]
        self.assertEqual(solve(data)["verified"]["preferred"]["availability"], "verified")
        data["events"] = [{"id": "adjacent", "start": iso(80), "duration_minutes": 10, "attendees": ["A"]}]
        self.assertEqual(solve(data)["optimistic"]["status"], "infeasible_in_scope")

    def test_omitted_coverage_and_explicit_empty_have_distinct_legacy_meanings(self):
        data = case()
        del data["people"]["A"]["busy_coverage"]
        data.pop("evidence_policy")
        self.assertIsNotNone(solve(data)["verified"]["preferred"])
        explicit = solve(dict(data, evidence_policy="explicit"))
        self.assertEqual(intervals(explicit["optimistic"]["preferred"]),
                         [("A", "$request", "busy_coverage", iso(85), iso(145))])
        data["people"]["A"]["busy_coverage"] = []
        self.assertIsNone(solve(data)["verified"]["preferred"])

    def test_schema_version_selects_default_evidence_policy(self):
        data = case()
        del data["people"]["A"]["busy_coverage"]
        data.pop("evidence_policy")
        for version, expected in (("1.0", "legacy"), ("1.1", "explicit")):
            report = solve(dict(data, schema_version=version))
            self.assertEqual(report["scope"]["evidence_policy"], expected)
            self.assertEqual(report["verified"]["preferred"] is None, version == "1.1")
        self.assertIsNotNone(solve(dict(data, schema_version="1.1", evidence_policy="legacy"))
                             ["verified"]["preferred"])

    def test_choice_uses_coverage_at_the_solved_start(self):
        data = case()
        data["request"]["starts"].append(iso(200))
        data["people"]["A"] = person([(100, 230)], [(100, 130), (185, 245)], rest=15)
        report = solve(data)
        self.assertEqual(report["verified"]["preferred"]["start"], iso(200))
        self.assertEqual(report["optimistic"]["preferred"]["start"], iso(100))

    def test_work_and_observation_are_independent_facts(self):
        data = case()
        data["people"]["A"].update(available=[], possible=[[iso(100), iso(130)]])
        questions = intervals(solve(data)["optimistic"]["preferred"])
        self.assertEqual(len(questions), 3)
        self.assertIn(("A", "$request", None, iso(100), iso(130)), questions)

    def test_outsider_coverage_matters_only_if_their_meeting_moves(self):
        data = case()
        data["people"] = {"A": person([(85, 400)], [(85, 400)]),
                          "X": person([(85, 400)], [(200, 230)], rest=10)}
        data["events"] = [{"id": "linked", "start": iso(100), "alternatives": [iso(200)],
                           "attendees": ["A", "X"], "duration_minutes": 30}]
        report = solve(data)
        self.assertIsNone(report["verified"]["preferred"])
        self.assertEqual(intervals(report["optimistic"]["preferred"]), [
            ("X", "linked", "busy_coverage", iso(190), iso(200)),
            ("X", "linked", "busy_coverage", iso(230), iso(240)),
        ])
        data["request"]["starts"] = [iso(300)]
        self.assertEqual(solve(data)["verified"]["preferred"]["moves"], [])

    def test_partial_roster_prevents_unverified_moves(self):
        data = case()
        data["people"]["A"] = person([(0, 500)], [(0, 500)])
        event = {"id": "private", "start": iso(100), "duration_minutes": 30,
                 "attendees": ["A"], "attendees_complete": False, "alternatives": [iso(300)]}
        data["events"] = [event]
        self.assertEqual(solve(data)["optimistic"]["status"], "infeasible_in_scope")
        event["attendees_complete"] = True
        self.assertEqual(solve(data)["verified"]["preferred"]["moves"][0]["event_id"], "private")

    def test_extreme_rest_reports_representable_calendar_interval(self):
        data = case()
        data["people"]["A"] = person([(100, 130)], [], rest=10**40)
        self.assertEqual(intervals(solve(data)["optimistic"]["preferred"]), [
            ("A", "$request", "busy_coverage", "0001-01-01T00:00Z", "9999-12-31T23:59Z")])

    def test_standby_extension_checks_its_assigned_duty(self):
        data = case()
        data["people"]["A"] = person([(100, 130)], [(100, 130)])
        data["people"]["X"] = person([(100, 130)])
        data["facts"] = {"roster": ["X"]}
        data["constraints"] = [{"id": "duty", "mode": "hard", "kind": "standby_cover",
                                "params": {"roster_fact": "roster"}}]
        rules = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "team_rules.py"))["RULES"]
        report = solve(data, rules)
        self.assertIsNone(report["verified"]["preferred"])
        self.assertEqual(report["optimistic"]["preferred"]["unresolved"][0]["rule_id"], "duty")
        data["people"]["X"]["busy_coverage"] = [[iso(100), iso(130)]]
        self.assertIsNotNone(solve(data, rules)["verified"]["preferred"])

    def test_invalid_policy_and_roster_flags_are_rejected(self):
        data = case()
        with self.assertRaisesRegex(ValueError, "evidence_policy"):
            solve(dict(data, evidence_policy="surmise"))
        data["events"] = [{"id": "private", "start": iso(200), "attendees": ["A"],
                           "duration_minutes": 10, "attendees_complete": 1}]
        with self.assertRaisesRegex(ValueError, "attendees_complete"):
            solve(data)


class SnapshotNumbersTests(unittest.TestCase):
    def test_integral_json_numbers_through_installed_cli(self):
        data = case()
        data["request"].update(duration_minutes=30.0, step_minutes=5.0)
        data["people"]["A"]["rest_minutes"] = 0.0
        data.update(max_moves=2.0, max_states=200000.0, decisions={"floor": [-1.0, 2]})
        result = subprocess.run([sys.executable, "-m", "scheduling_planner", "--ranking", "preferred", "-"],
                                input=json.dumps(data), text=True, capture_output=True, check=True)
        report = json.loads(result.stdout)
        self.assertIs(type(report["scope"]["max_moves"]), int)
        self.assertEqual(report["scope"]["decision_domains"]["floor"], [-1, 2])
        self.assertIs(type(report["scope"]["decision_domains"]["floor"][0]), int)
        self.assertIsNotNone(report["verified"]["preferred"])

    def test_fractions_bools_and_mixed_domains_remain_invalid(self):
        for value in (True, 2.5, float("inf")):
            data = case()
            data["request"]["duration_minutes"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "duration_minutes"):
                parse_snapshot(data)
        for domain in ([1.0, True], [1.5], [1, 1.0]):
            data = dict(deepcopy(case()), decisions={"tier": domain})
            with self.subTest(domain=domain), self.assertRaisesRegex(ValueError, "decisions.tier"):
                parse_snapshot(data)
