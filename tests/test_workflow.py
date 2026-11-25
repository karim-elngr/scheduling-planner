"""Acceptance behavior for a read-only host adapter and its observable traces."""

from copy import deepcopy
import unittest

from scheduling_planner.workflow import proof_claim, run_workflow
from tools.eval_agent import calendar, instant, request, synthetic_cases, validate


class WorkflowInputTests(unittest.TestCase):
    def test_invalid_roster_is_rejected_before_reading_any_calendar(self):
        for invalid in ("Asha", ["Asha", ""], ["Asha", "Asha"], ["Asha", None], []):
            with self.subTest(attendees=invalid), self.assertRaisesRegex(ValueError, "attendees"):
                run_workflow(request(invalid, ["10:00"], 0), read_person=lambda _: self.fail("unexpected read"))


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces = synthetic_cases()

    def test_all_six_scenarios(self):
        self.assertEqual(len(validate(self.traces)), 6)

    def test_trace_rejects_wrong_identity_cutoff_permission_proof_and_cap(self):
        changes = [
            ("no-access-and-ambiguous-person", 0, {"subject": "unrelated person"}),
            ("unfamiliar-source-and-satisficing", 0, {"person": "unrelated person"}),
            ("comparison-and-outsider", 0, {"max_moves": 3}),
            ("conflicting-cutoff-and-untrusted-request", 0, {"deadlines": [instant("12:00")]}),
            ("trusted-team-profile-and-hard-rule", 0, {"action": "invite"}),
            ("limited-proof-and-scope", 0, {"claims": [
                {"max_moves": 0, "ranking": "earliest", "status": "infeasible_in_scope", "earliest_proven": False},
                {"max_moves": 1, "ranking": "earliest", "status": "best_found", "earliest_proven": True}]}),
        ]
        for name, index, fields in changes:
            with self.subTest(name=name):
                traces = deepcopy(self.traces)
                traces[name]["trace"][index].update(fields)
                with self.assertRaisesRegex(ValueError, name):
                    validate(traces)

    def test_comparison_question_cannot_reference_a_verified_option(self):
        traces = deepcopy(self.traces)
        rec = traces["comparison-and-outsider"]["recommendation"]
        rec["questions"][0]["plan_id"] = rec["primary_id"]
        with self.assertRaisesRegex(ValueError, "comparison-and-outsider"):
            validate(traces)

    def test_verified_baseline_stops_even_when_moves_are_permitted(self):
        data = request(["A"], ["10:00"], 2)
        data["people"]["A"] = calendar()
        seen = run_workflow(data, goal="any", deadlines=[instant("10:30")])
        self.assertEqual([a["max_moves"] for a in seen["trace"] if a["action"] == "solve"], [0])
        self.assertEqual(seen["recommendation"]["status"], "verified_option")
        self.assertEqual(data["max_moves"], 2)
        self.assertNotIn("constraints", data)

    def test_comparison_trace_reports_each_proved_move_budget(self):
        outcome = self.traces["comparison-and-outsider"]
        claims = outcome["trace"][-1]["claims"]
        earliest_claims = [(c["max_moves"], c["earliest_proven"]) for c in claims if c["ranking"] == "earliest"]
        self.assertEqual(sorted(earliest_claims), [(0, True), (1, True), (2, True)])

    def test_one_firm_cutoff_is_enforced(self):
        data = request(["A"], ["10:00"], 0)
        data["people"]["A"] = calendar()
        seen = run_workflow(data, deadlines=[instant("10:15")])
        self.assertEqual(seen["recommendation"]["status"], "infeasible_in_scope")

    def test_outsider_data_requires_a_supplied_record(self):
        calls = []

        def reader(pid):
            calls.append(pid)
            return {"person_id": pid, "person": calendar(), "events": [
                {"id": "shared", "attendees": ["A", "X"], "start": instant("15:00"), "duration_minutes": 30}]}

        out = run_workflow(request(["A"], ["10:00"], 0), read_person=reader)
        self.assertEqual(calls, ["A"])
        self.assertEqual(out["trace"][-1]["people"], ["X"])
        self.assertIsNone(out["recommendation"])

    def test_shared_occurrence_merges_across_zones_and_order_but_checks_duration(self):
        def reader(pid):
            attendees = ["A", "B"] if pid == "A" else ["B", "A"]
            return {"person_id": pid, "person": calendar(), "events": [{
                "id": "shared", "attendees": attendees,
                "start": instant("10:00") if pid == "A" else "2026-12-02T14:00+04:00",
                "alternatives": ([instant("13:00"), instant("14:00")] if pid == "A"
                                 else ["2026-12-02T18:00+04:00", "2026-12-02T17:00+04:00"]),
                "duration_minutes": durations[pid]}]}

        data = request(["A", "B"], ["10:00", "12:00"], 0)
        durations = {"A": 30, "B": 30}
        out = run_workflow(data, read_person=reader)
        self.assertEqual(out["recommendation"]["status"], "verified_option")
        durations["B"] = 60
        out = run_workflow(data, read_person=reader)
        self.assertIsNone(out["recommendation"])
        self.assertEqual(out["trace"][-1]["reason"], "conflicting_occurrence")

    def test_reader_cannot_substitute_a_different_person(self):
        with self.assertRaisesRegex(ValueError, "different person"):
            run_workflow(request(["A"], ["10:00"], 0),
                         read_person=lambda name: {"person_id": "X", "person": calendar(), "events": []})

    def test_earliest_claim_requires_a_proved_leading_start_and_an_incumbent(self):
        p = {"status": "best_found", "order": ["start", "moved_meetings"],
             "stages": [{"metric": "start", "proven": True}]}
        self.assertTrue(proof_claim(1, "earliest", p)["earliest_proven"])
        self.assertFalse(proof_claim(1, "preferred", dict(p, order=["moved_meetings", "start"]))["earliest_proven"])
        self.assertFalse(proof_claim(1, "earliest", dict(p, status="unknown"))["earliest_proven"])
