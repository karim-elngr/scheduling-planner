"""Scheduling choices that differ in human cost, evidence and scope."""

import json
from unittest import TestCase
from unittest.mock import patch

from scheduling_planner import advisor
from scheduling_planner.brief import brief
from scheduling_planner.move_comparison import _caps
from scheduling_planner.rules import RuleEffect


def at(clock):
    return f"2026-12-01T{clock}Z"


def data(people, starts):
    return {"request": {"attendees": list(people), "starts": [at(t) for t in starts],
                        "duration_minutes": 30},
            "people": {p: {"available": [[at("08:00"), at("18:00")]]} for p in people}}


def event(name, person, minutes, replacement):
    return {"id": name, "attendees": [person], "duration_minutes": minutes,
            "start": at("09:00"), "alternatives": [at(replacement)]}


def move_example():
    s = data("AB", ["09:00", "09:30", "15:00"])
    s["events"] = [event("handoff", "A", 60, "12:00"), event("checkin", "B", 30, "14:00")]
    s["max_moves"] = 2
    return s


class DecisionQualityTests(TestCase):
    def test_conditional_only_prefers_one_confirmation_over_five_under_actual_rank_order(self):
        s = data("ABCDE", ["09:00", "10:00"])
        s["objective"] = ["moved_meetings"]
        for name in s["people"]:
            s["people"][name] = {"available": [] if name == "A" else [[at("10:00"), at("10:30")]],
                                 "possible": [[at("09:00"), at("10:30")]]}
        report = advisor.advise(s)
        question, = report["advice"]["questions"]
        self.assertEqual((question["person"], question["start"]), ("A", at("10:00")))
        self.assertEqual(report["advice"]["question_source"]["ranking"], "preferred")
        # Earlier conditional options remain inspectable if the caller needs them.
        by_rank = advisor.advise(s, rankings=["earliest", "least_disruption"])
        self.assertEqual(len(by_rank["advice"]["questions"]), 5)
        self.assertEqual(by_rank["advice"]["question_source"]["ranking"], "earliest")

    def test_question_can_avoid_a_move_under_requested_disruption_ranking(self):
        s = data("AB", ["09:00", "11:00"])
        s["people"]["B"] = {"available": [[at("09:00"), at("09:30")]],
                            "possible": [[at("11:00"), at("11:30")]]}
        s["events"] = [event("handoff", "A", 30, "14:00")]
        s["objective"] = ["start", "moved_meetings"]
        report = advisor.advise(s, rankings=["preferred", "least_disruption"])
        primary, alternative = report["advice"]["options"]
        source = report["advice"]["question_source"]
        self.assertEqual((alternative["plan"]["start"], alternative["plan"]["moves"]), (at("11:00"), []))
        self.assertEqual((source["baseline_id"], source["metric"], source["gain"]),
                         (primary["id"], "moved_meetings", 1))
        self.assertIn("avoid moving 1 existing meeting", source["reason"])
        self.assertEqual(report["advice"]["questions"][0]["person"], "B")
        self.assertEqual(source["ranking"], "least_disruption")
        self.assertEqual(source["baseline_max_moves"], report["result"]["scope"]["max_moves"])

    def test_one_move_frontier_and_compact_proof_scope(self):
        report = advisor.advise(move_example(), compare_moves=True)
        comparison = report["advice"]["move_comparison"]
        self.assertTrue(comparison["complete"])
        self.assertFalse(comparison["sampled"])
        rows = comparison["caps"]
        self.assertEqual([(r["cap"], r["minutes_gained"]) for r in rows], [(0, None), (1, 330), (2, 30)])
        self.assertTrue(all(r["gain_proven"] for r in rows[1:]))
        by_id = {o["id"]: o for o in report["advice"]["options"]}
        middle = by_id[rows[1]["plan_id"]]
        self.assertEqual(middle["comparison_caps"], [1])
        self.assertEqual(middle["proofs"], {})  # Never import a cap-one proof into the cap-two report.
        self.assertEqual(middle["plan"]["start"], at("09:30"))
        self.assertEqual(len(middle["moves_to_coordinate"]), 1)
        view = brief(report)
        compact = next(o for o in view["options"] if o["id"] == middle["id"])
        self.assertEqual(compact["proof"]["max_moves"], 1)
        self.assertEqual(compact["proof"]["status"], "optimal")
        self.assertEqual(compact["moves"][0]["attendees"], ["A"])
        self.assertLess(len(json.dumps(view)), len(json.dumps(report)) / 2)
        compact["moves"].clear()
        self.assertTrue(middle["moves_to_coordinate"])

    def test_compare_does_not_override_explicit_raw_rankings(self):
        report = advisor.advise(move_example(), compare_moves=True, rankings=["preferred"])
        self.assertEqual(report["result"]["scope"]["rankings"], ["preferred"])
        rows = report["advice"]["move_comparison"]["caps"]
        self.assertEqual(rows[-1]["start"], at("09:00"))
        self.assertTrue(all(r["proof"]["order"][0] == "start" for r in rows))

    def test_shared_deadline_marks_unattempted_caps_without_false_infeasibility(self):
        with patch("scheduling_planner.move_comparison.monotonic", return_value=float("inf")):
            report = advisor.advise(move_example(), compare_moves=True)
        rows = report["advice"]["move_comparison"]["caps"]
        self.assertEqual([r["status"] for r in rows], ["not_run", "not_run", "optimal"])
        self.assertFalse(report["advice"]["move_comparison"]["complete"])
        self.assertIsNone(rows[0]["proof"])
        self.assertIsNone(rows[-1]["minutes_gained"])

    def test_caps_with_duplicate_placements_do_not_duplicate_recommendations(self):
        s = move_example()
        s["request"]["starts"] = [at("15:00")]
        report = advisor.advise(s, compare_moves=True)
        option, = report["advice"]["options"]
        self.assertEqual(option["comparison_caps"], [0, 1, 2])
        flags = [row["frontier"] for row in report["advice"]["move_comparison"]["caps"]]
        self.assertEqual(flags, [True, False, False])

    def test_cap_selection_stays_bounded_and_includes_first_move(self):
        self.assertEqual(_caps(2, 50), [0, 1, 2])
        self.assertEqual(_caps(0, 50), [0])
        self.assertEqual(_caps(5, 1), [0, 1, 5])
        self.assertEqual(_caps(5, 2), [0, 1, 2, 5])
        self.assertEqual(len(_caps(90, 8)), 9)
        caps = _caps(10 ** 50, 5000)
        self.assertEqual(caps, sorted(set(caps)))
        self.assertEqual(caps[:2], [0, 1])
        self.assertEqual(caps[-1], 10 ** 50)
        self.assertLessEqual(len(caps), 9)

    def test_surplus_move_budget_preserves_the_one_move_proof(self):
        s = move_example()
        s["events"] = s["events"][:1]
        s["max_moves"] = 5
        comparison = brief(advisor.advise(s, compare_moves=True))["move_comparison"]
        self.assertTrue(comparison["complete"])
        rows = [(r["cap"], r["start"], r["minutes_gained"]) for r in comparison["caps"]]
        self.assertEqual(rows, [(0, at("15:00"), None), (1, at("09:00"), 360), (5, at("09:00"), 0)])

    def test_disruption_hook_receives_intermediate_tradeoff(self):
        s = move_example()
        seen = []
        def hook(ctx, params):
            seen.extend(o["plan"]["start"] for o in ctx["options"])
            middle = next(o for o in ctx["options"] if o["plan"]["start"] == at("09:30"))
            self.assertEqual(middle["proofs"], {})
            row = next(r for r in ctx["move_comparison"]["caps"] if r["plan_id"] == middle["id"])
            self.assertEqual(row["proof"]["status"], "optimal")
            return [{"plan_id": middle["id"], "kind": "note", "text": "Coordinate one move."}]
        report = advisor.advise(s, compare_moves=True, heuristics={"note": hook},
                                profile={"profile_version": "1.0", "name": "demo", "heuristics": [{"name": "note"}]})
        self.assertIn(at("09:30"), seen)
        note, = brief(report)["annotations"]
        self.assertEqual(note["text"], "Coordinate one move.")

    def test_usable_smaller_scope_witness_when_primary_search_expires(self):
        s = move_example()
        incomplete = advisor.plan(s, time_limit_seconds=1e-12)
        self.assertEqual(incomplete["verified"]["status"], "unknown")
        with patch.object(advisor, "plan", return_value=incomplete):
            result = advisor.advise(s, compare_moves=True)
        brief_result = brief(result)
        self.assertEqual(result["result"]["verified"]["status"], "unknown")
        self.assertEqual(result["advice"]["status"], "verified_option")
        self.assertEqual(result["advice"]["primary_source"]["max_moves"], 0)
        self.assertEqual(result["advice"]["options"][0]["proofs"], {})
        self.assertEqual(brief_result["options"][0]["proof"]["max_moves"], 0)
        self.assertEqual(brief_result["options"][0]["start"], at("15:00"))

    def test_confirmations_reconsidered_against_verified_frontier_with_different_cap_policy(self):
        s = move_example()
        s["request"]["starts"] = [at("15:00")]
        s["constraints"] = [{"id": "check", "kind": "cap_approval", "mode": "hard"}]
        def cap_approval(ctx, params):
            return RuleEffect(unknown=int(ctx.problem.max_moves == 2), question="Is a two-move budget approved?")
        report = advisor.advise(s, compare_moves=True, registry={"cap_approval": cap_approval})
        self.assertEqual(report["result"]["verified"]["status"], "infeasible_in_scope")
        self.assertEqual(report["result"]["optimistic"]["preferred"]["availability"], "conditional")
        self.assertEqual(report["advice"]["status"], "verified_option")
        self.assertEqual(report["advice"]["questions"], [])
        self.assertEqual(brief(report)["options"][0]["availability"], "verified")
        self.assertEqual(report["advice"]["primary_source"]["max_moves"], 0)

    def test_policy_can_require_intermediate_budget_with_no_movable_events(self):
        s = data("A", ["09:00"])
        s.update(max_moves=2, constraints=[{"id": "review", "kind": "one_move_policy", "mode": "hard"}])

        def one_move_policy(ctx, params):
            return RuleEffect(units=int(ctx.problem.max_moves != 1), reason="The permitted budget must equal one.")

        report = advisor.advise(s, registry={"one_move_policy": one_move_policy}, compare_moves=True)
        self.assertEqual(report["result"]["verified"]["status"], "infeasible_in_scope")
        for view in (report["advice"], brief(report)):
            self.assertEqual(view["status"], "verified_option")
            c = view["move_comparison"]
            self.assertEqual(c["movable_event_count"], 0)
            self.assertTrue(c["complete"])
            self.assertEqual([(row["cap"], row["status"]) for row in c["caps"]],
                             [(0, "infeasible_in_scope"), (1, "optimal"), (2, "infeasible_in_scope")])
        self.assertEqual(brief(report)["options"][0]["proof"]["max_moves"], 1)

        s["max_moves"] = 1000000
        large = advisor.advise(s, registry={"one_move_policy": one_move_policy}, compare_moves=True)
        c = large["advice"]["move_comparison"]
        self.assertTrue(c["sampled"])
        self.assertFalse(c["complete"])
        self.assertLessEqual(len(c["caps"]), 9)
        self.assertEqual(c["caps"][-1]["cap"], 1000000)
        # A registered but unused compiler cannot alter a built-in-only snapshot.
        s["constraints"] = []
        ordinary = advisor.advise(s, registry={"one_move_policy": one_move_policy}, compare_moves=True)
        self.assertEqual([r["cap"] for r in ordinary["advice"]["move_comparison"]["caps"]], [0, 1000000])

    def test_brief_marks_disjoint_busy_coverage_and_retains_proof_scope(self):
        s = data("A", ["09:00"])
        s["evidence_policy"] = "explicit"
        s["people"]["A"].update(rest_minutes=15, busy_coverage=[[at("09:00"), at("09:30")]])
        report = advisor.advise(s)
        view = brief(report)
        self.assertEqual(view["scope"]["evidence_policy"], "explicit")
        self.assertEqual([(q["start"], q["end"]) for q in view["questions"]],
                         [(at("08:45"), at("09:00")), (at("09:30"), at("09:45"))])
        self.assertTrue(all(q["type"] == "busy_coverage" and "busy calendar" in q["text"] for q in view["questions"]))

    def test_brief_explains_infeasibility_even_without_options(self):
        s = data("A", ["09:00"])
        s["constraints"] = [{"id": "always", "mode": "hard", "kind": "blocked", "source": "scheduling policy"}]
        report = advisor.advise(s, registry={"blocked": lambda ctx, params: RuleEffect(units=1, reason="Blocked")})
        summary = brief(report)
        self.assertEqual(summary["options"], [])
        self.assertEqual(summary["status"], "infeasible_in_scope")
        self.assertTrue(summary["diagnosis"]["verified"]["assumptions"])
