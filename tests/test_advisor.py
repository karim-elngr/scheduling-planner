"""Exercise the advisor through actual solves, including conditional and partial results."""

from copy import deepcopy
import json
from pathlib import Path
import runpy
from unittest import TestCase
from unittest.mock import patch

from scheduling_planner import advisor
from scheduling_planner.rules import RuleEffect


ROOT = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((ROOT / "sample.json").read_text(encoding="utf-8"))


def profile(**fields):
    return {"profile_version": "1.0", "name": "Test preferences", **fields}


def one_start():
    return {
        "people": {"A": {"available": [["2026-12-01T08:00Z", "2026-12-01T17:00Z"]]}},
        "request": {"attendees": ["A"], "duration_minutes": 30, "starts": ["2026-12-01T09:00Z"]},
    }


class AdvisorTests(TestCase):
    def test_sample_distinguishes_material_tradeoffs_and_unknown_from_coordination(self):
        data = sample()
        original = deepcopy(data)
        bundle = advisor.advise(data)
        advice, result = bundle["advice"], bundle["result"]
        self.assertEqual(data, original)
        self.assertEqual(advice["status"], "verified_option")
        self.assertEqual(advice["primary_selection"], "verified_preferred")
        primary, earlier, conditional = advice["options"]
        self.assertEqual(primary["id"], advice["primary_id"])
        self.assertEqual(primary["plan"], result["verified"]["preferred"])
        self.assertEqual(primary["moves_to_coordinate"], [])
        self.assertEqual(earlier["role"], "verified_alternative")
        self.assertEqual(earlier["comparison"]["minutes_earlier_than_primary"], 1620)
        self.assertEqual(earlier["moves_to_coordinate"][0]["attendees"], ["Bob", "Eli"])
        self.assertEqual(earlier["plan"]["unresolved"], [])
        self.assertEqual(conditional["role"], "conditional")
        self.assertEqual(conditional["comparison"]["minutes_earlier_than_earliest_verified"], 60)
        self.assertEqual(conditional["moves_to_coordinate"][0]["attendees"], ["Alice", "Dana"])
        question, = advice["questions"]
        self.assertEqual(question["plan_id"], conditional["id"])
        self.assertEqual((question["type"], question["person"], question["rest_minutes"]),
                         ("availability", "Dana", 15))
        self.assertEqual((question["start"], question["end"]),
                         ("2026-12-01T09:15Z", "2026-12-01T09:45Z"))
        self.assertIn("15-minute gap", question["text"])
        for option in advice["options"]:
            for mode, proofs in option["proofs"].items():
                for ranking, proof in proofs.items():
                    self.assertEqual(proof, result[mode]["proofs"][ranking])
                    self.assertEqual(option["plan"], result[mode][ranking])

    def test_conditional_gain_measured_from_earliest_verified_inclusive_threshold(self):
        for threshold, count in [(60, 1), (61, 0)]:
            with self.subTest(threshold=threshold):
                output = advisor.advise(sample(), profile=profile(min_gain_minutes=threshold))
                self.assertEqual(len(output["advice"]["questions"]), count)

    def test_time_only_objective_gain_respects_minimum_minutes(self):
        data = one_start()
        data["request"]["starts"].append("2026-12-01T09:05Z")
        data["people"]["A"] = {"available": [["2026-12-01T09:05Z", "2026-12-01T17:00Z"]],
                               "possible": [["2026-12-01T09:00Z", "2026-12-01T09:05Z"]]}
        preferences = profile(min_gain_minutes=10, objective=["start"])
        output = advisor.advise(data, profile=preferences)
        self.assertEqual(output["result"]["optimistic"]["preferred"]["start"], "2026-12-01T09:00Z")
        self.assertEqual(output["result"]["verified"]["preferred"]["start"], "2026-12-01T09:05Z")
        self.assertEqual(len(output["advice"]["options"]), 1)
        self.assertEqual(output["advice"]["questions"], [])

    def test_higher_priority_nontime_objective_can_justify_check_at_identical_time(self):
        data = one_start()
        data["decisions"] = {"room": ["rented", "staff"]}
        data["constraints"] = [{"kind": "fee", "id": "rental", "mode": "soft"}]
        data["objective"] = ["rule:rental", "start"]

        def fee(ctx, params):
            return RuleEffect(units=1, unknown=ctx.decisions["room"],
                              reason="Room rental fee.", question="Does the staff room have a fee?")

        output = advisor.advise(data, registry={"fee": fee}, profile=profile(min_gain_minutes=60))
        verified, conditional = output["advice"]["options"]
        self.assertEqual(verified["plan"]["decisions"]["room"], "rented")
        self.assertEqual(conditional["plan"]["decisions"]["room"], "staff")
        self.assertEqual(conditional["comparison"]["minutes_earlier_than_earliest_verified"], 0)
        self.assertEqual(conditional["comparison"]["first_improved_objective_metric"], "rule:rental")
        self.assertEqual(output["advice"]["questions"][0]["text"], "Does the staff room have a fee?")

    def test_one_actual_option_keeps_all_proofs_and_explicit_ranking_fallback(self):
        data = one_start()
        output = advisor.advise(data, rankings=["earliest", "least_disruption"])
        advice = output["advice"]
        option, = advice["options"]
        self.assertEqual(advice["primary_source"], {"evidence_mode": "verified", "ranking": "earliest"})
        self.assertEqual(advice["primary_selection"], "fallback_requested_ranking")
        self.assertEqual(set(option["proofs"]), {"verified", "optimistic"})
        self.assertEqual(set(option["proofs"]["verified"]), {"earliest", "least_disruption"})
        self.assertEqual(advisor.advise(data)["advice"]["primary_id"], option["id"])

    def test_subset_fallback_uses_caller_order_and_preferred_overrides_order_when_present(self):
        data = sample()
        first = advisor.advise(data, rankings=["least_disruption", "earliest"])
        self.assertEqual(first["advice"]["primary_source"]["ranking"], "least_disruption")
        second = advisor.advise(data, rankings=["earliest", "preferred"])
        self.assertEqual(second["advice"]["primary_source"]["ranking"], "preferred")
        self.assertEqual(second["advice"]["primary_id"], first["advice"]["primary_id"])

    def test_schedule_identity_includes_move_destinations_and_decisions(self):
        output = advisor.advise(sample())
        candidate = output["result"]["verified"]["earliest"]
        changed = deepcopy(candidate)
        self.assertEqual(advisor._plan_id(candidate), advisor._plan_id(changed))
        changed["moves"][0]["to_start"] = "2026-12-01T16:00Z"
        self.assertNotEqual(advisor._plan_id(candidate), advisor._plan_id(changed))
        changed = {**candidate, "decisions": {"room": "C"}}
        self.assertNotEqual(advisor._plan_id(candidate), advisor._plan_id(changed))

    def test_uncertain_rule_has_targeted_question_and_no_verified_primary(self):
        data = one_start()
        data["constraints"] = [{"id": "cover", "kind": "on_call", "mode": "hard", "source": "Roster"}]
        output = advisor.advise(data, registry={
            "on_call": lambda ctx, params: RuleEffect(unknown=1, question="Who is covering?")
        })
        advice = output["advice"]
        self.assertEqual(advice["status"], "conditional_only")
        self.assertIsNone(advice["primary_id"])
        candidate, = advice["options"]
        question, = advice["questions"]
        self.assertEqual(question["plan_id"], candidate["id"])
        self.assertEqual((question["type"], question["source"], question["text"]),
                         ("rule", "Roster", "Who is covering?"))
        self.assertEqual(output["result"]["verified"]["status"], "infeasible_in_scope")

    def test_partial_and_infeasible_searches_keep_native_status_and_hard_limits(self):
        data = one_start()
        taken = {"id": "fixed", "attendees": ["A"], "duration_minutes": 30,
                 "start": "2026-12-01T09:00Z", "alternatives": ["2026-12-01T12:00Z"]}
        data.update(events=[taken], max_moves=0)
        output = advisor.advise(data)
        self.assertEqual(output["advice"]["status"], "infeasible_in_scope")
        self.assertEqual(output["advice"]["options"], [])
        self.assertEqual(output["result"]["scope"]["max_moves"], 0)
        self.assertIn("infeasibility", output["result"]["verified"])
        output = advisor.advise(data, time_limit_seconds=1e-12)
        self.assertEqual(output["result"]["search_status"], "limit_reached")
        self.assertEqual(output["advice"]["status"], "no_solution_found")
        self.assertEqual(output["advice"]["questions"], [])

    def test_all_requested_rankings_share_one_solve_budget(self):
        data = sample()
        native = advisor.plan
        with patch.object(advisor, "plan", wraps=native) as solver:
            output = advisor.advise(data, profile=profile(time_limit_seconds=10),
                                    time_limit_seconds=2, rankings=["earliest"])
        solver.assert_called_once()
        self.assertEqual(solver.call_args.kwargs["time_limit_seconds"], 2)
        self.assertEqual(output["result"]["scope"]["rankings"], ["earliest"])

    def test_time_limit_keeps_returned_best_found_proof_on_primary(self):
        from scheduling_planner import engine
        from ortools.sat.python import cp_model

        data = sample()
        now = [1.0]
        native_solve = cp_model.CpSolver.solve

        def expire_after_solve(solver, model):
            status = native_solve(solver, model)
            now[0] += 100.0
            return status

        with patch.object(engine, "monotonic", side_effect=lambda: now[0]), \
                patch.object(cp_model.CpSolver, "solve", expire_after_solve):
            output = advisor.advise(data, rankings=["earliest"], time_limit_seconds=10)
        option, = output["advice"]["options"]
        proof = option["proofs"]["verified"]["earliest"]
        self.assertEqual(proof["status"], "best_found")
        self.assertFalse(proof["stages"][-1]["proven"])
        self.assertEqual(proof, output["result"]["verified"]["proofs"]["earliest"])

    def test_team_example_attaches_coordination_notes_and_no_solver_claims(self):
        hooks = runpy.run_path(str(ROOT / "examples" / "team_heuristics.py"))["HEURISTICS"]
        output = advisor.advise(sample(), profile=profile(heuristics=[{"name": "coordination_note"}]),
                                heuristics=hooks)
        notes = output["advice"]["annotations"]
        self.assertEqual({n["plan_id"] for n in notes},
                         {o["id"] for o in output["advice"]["options"] if o["moves_to_coordinate"]})
        self.assertTrue(any("Dana" in n["text"] for n in notes))
        self.assertTrue(any("Eli" in n["text"] for n in notes))

    def test_hook_output_and_mutations_are_isolated_from_witnesses_and_other_hooks(self):
        data = sample()
        settings = profile(heuristics=[{"name": "first", "params": {"tag": "team"}},
                                       {"name": "second"}])
        pristine_settings, pristine_data = deepcopy(settings), deepcopy(data)
        observations = []

        def first(ctx, params):
            observations.append(params.pop("tag"))
            key = ctx["recommended_id"]
            ctx["snapshot"]["people"].clear()
            ctx["options"][0]["plan"]["start"] = "changed"
            ctx["options"][0]["proofs"].clear()
            return [{"plan_id": key, "kind": "note", "text": "Team reminder"}]

        def second(ctx, params):
            observations.append(sorted(ctx["snapshot"]["people"]))
            self.assertNotEqual(ctx["options"][0]["plan"]["start"], "changed")
            return []

        output = advisor.advise(data, profile=settings, heuristics={"first": first, "second": second})
        self.assertEqual((settings, data), (pristine_settings, pristine_data))
        self.assertEqual(observations, ["team", sorted(data["people"])])
        advice, raw = output["advice"], output["result"]
        self.assertEqual(advice["options"][0]["plan"], raw["verified"]["preferred"])
        self.assertNotEqual(raw["verified"]["preferred"]["start"], "changed")
        self.assertEqual(advice["options"][0]["proofs"]["verified"]["preferred"]["status"], "optimal")
        self.assertEqual(advice["annotations"], [{"plan_id": advice["primary_id"], "kind": "note",
                                                  "text": "Team reminder", "heuristic": "first"}])

    def test_unselected_hook_never_runs_and_unregistered_hook_fails_before_solving(self):
        def never(ctx, params):
            raise AssertionError("ran an undeclared heuristic")

        self.assertEqual(advisor.advise(one_start(), heuristics={"inert": never})["advice"]["annotations"], [])
        with patch.object(advisor, "plan") as solver, self.assertRaisesRegex(ValueError, "no registered"):
            advisor.advise(one_start(), profile=profile(heuristics=[{"name": "typo"}]))
        solver.assert_not_called()

    def test_hook_cannot_add_a_slot_promote_questions_or_claim_certification(self):
        settings = profile(heuristics=[{"name": "team"}])
        for output in ["new-plan", [{"kind": "note", "plan_id": "invented", "text": "test"}],
                       [{"kind": "preferred", "plan_id": "invented", "text": "test"}]]:
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "heuristic 'team'"):
                advisor.advise(one_start(), profile=settings, heuristics={"team": lambda *_, output=output: output})

        def forgery(ctx, params):
            return [{"plan_id": ctx["recommended_id"], "kind": "note", "text": "test", "status": "optimal"}]

        with self.assertRaisesRegex(ValueError, "invalid annotation"):
            advisor.advise(one_start(), profile=settings, heuristics={"team": forgery})
