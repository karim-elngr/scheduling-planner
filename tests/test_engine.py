"""Regression checks for exact multi-objective optimization and partial proofs."""

import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from scheduling_planner import engine
from scheduling_planner.rules import RuleEffect


def snapshot():
    return {
        "request": {"attendees": ["A"], "duration_minutes": 30,
                    "starts": ["2026-12-01T09:00Z", "2026-12-01T11:00Z"]},
        "people": {"A": {"available": [["2026-12-01T08:00Z", "2026-12-01T17:00Z"]]}},
        "events": [{"id": "other", "attendees": ["A"], "duration_minutes": 30,
                    "start": "2026-12-01T09:00Z", "alternatives": ["2026-12-01T14:00Z"]}],
    }


class ProofTests(unittest.TestCase):
    def test_exact_lexicographic_lock_above_double_precision(self):
        data = snapshot()
        huge = 2 ** 53 + 1
        data["decisions"] = {"room": ["small", "large"]}
        data["constraints"] = [{"id": "price", "kind": "tariff", "mode": "soft"}]
        data["objective"] = ["rule:price", "start"]

        def tariff(ctx, params):
            cost = ctx.model.new_int_var(huge, huge + 1, "room_tariff")
            ctx.model.add(cost == huge + ctx.decisions["room"])
            return RuleEffect(units=cost, reason="Room tariff in integer units.")

        report = engine.plan(data, registry={"tariff": tariff}, rankings=["preferred"])
        proof = report["verified"]["proofs"]["preferred"]
        self.assertEqual(proof["stages"][0]["value"], huge)
        self.assertTrue(all(s["proven"] for s in proof["stages"]))
        self.assertEqual(report["verified"]["preferred"]["rule_penalties"]["price"], huge)
        self.assertEqual(report["verified"]["preferred"]["start"], "2026-12-01T09:00Z")
        self.assertEqual(report["statistics"]["native_solves"],
                         sum("wall_seconds" in s for s in proof["stages"]))

    def test_timeout_after_first_tier_retains_feasible_plan_and_partial_proof(self):
        data = snapshot()
        data["objective"] = ["start", "moved_meetings"]
        now = [1.0]
        native_solve = cp_model.CpSolver.solve

        def expire_after_solve(solver, model):
            status = native_solve(solver, model)
            now[0] = 100.0
            return status

        with patch.object(engine, "monotonic", side_effect=lambda: now[0]), \
                patch.object(cp_model.CpSolver, "solve", expire_after_solve):
            result = engine.plan(data, rankings=["preferred"], time_limit_seconds=10)
        group = result["verified"]
        self.assertEqual(result["search_status"], "limit_reached")
        self.assertEqual(group["status"], "best_found")
        self.assertEqual(group["preferred"]["start"], "2026-12-01T09:00Z")
        proof = group["proofs"]["preferred"]
        self.assertTrue(proof["stages"][0]["proven"])
        self.assertEqual(proof["stages"][-1]["status"], "TIME_LIMIT")

    def test_choices_are_isolated_between_rankings(self):
        result = engine.plan(snapshot())
        group = result["verified"]
        self.assertEqual(group["earliest"]["start"], "2026-12-01T09:00Z")
        self.assertEqual(group["least_disruption"]["start"], "2026-12-01T11:00Z")
        self.assertEqual(group["status"], "optimal")
        self.assertEqual(group["proofs"]["earliest"]["stages"][0]["metric"], "start")

    def test_pools_differ_for_unresolved_soft_effect(self):
        data = snapshot()
        data["events"] = []
        data["constraints"] = [{"id": "unread", "kind": "await_room_price", "mode": "soft"}]
        def await_room_price(ctx, params):
            return RuleEffect(units=11, unknown=1, reason="Room hire.", question="What is the rate?")
        result = engine.plan(data, registry={"await_room_price": await_room_price})
        self.assertEqual(result["verified"]["status"], "infeasible_in_scope")
        win = result["optimistic"]["preferred"]
        self.assertEqual(win["availability"], "conditional")
        self.assertEqual(win["rule_penalties"]["unread"], 0)
        self.assertEqual(win["unresolved"][0]["question"], "What is the rate?")

    def test_total_soft_cost_overflow_fails_before_search(self):
        data = snapshot()
        data["constraints"] = [{"id": name, "kind": "fee", "mode": "soft"} for name in ("a", "b")]
        with self.assertRaisesRegex(ValueError, "total weighted soft cost"):
            engine.plan(data, registry={"fee": lambda ctx, params: RuleEffect(
                units=1 << 62, reason="Fee in integer units.")})

    def test_incompatible_policies_return_both_named_core_members(self):
        data = snapshot()
        data["decisions"] = {"room": ["A", "B"]}
        data["constraints"] = [
            {"id": "require-a", "mode": "hard", "kind": "require_a", "source": "Site policy"},
            {"id": "require-b", "mode": "hard", "kind": "require_b", "source": "Customer request"},
        ]
        def require_b(ctx, params):
            missing = ctx.model.new_bool_var("not_B")
            ctx.model.add(missing + ctx.decisions["room"] == 1)
            return RuleEffect(units=missing, reason="Room B is required.")
        result = engine.plan(data, rankings=["preferred"], registry={
            "require_a": lambda ctx, params: RuleEffect(units=ctx.decisions["room"],
                                                        reason="Room A is required."),
            "require_b": require_b,
        })
        diag = result["verified"]["infeasibility"]
        self.assertEqual(diag["status"], "sufficient_core")
        self.assertEqual({r["rule_id"] for r in diag["assumptions"]}, {"require-a", "require-b"})
        self.assertTrue(all(r["type"] == "hard_rule" for r in diag["assumptions"]))

    def test_negative_native_assumption_and_hard_rule_in_sufficient_core(self):
        data = snapshot()
        data["constraints"] = [{"id": "room-bookable", "kind": "office", "mode": "hard"}]

        def office(ctx, params):
            bookable = ctx.model.new_bool_var("office_room_bookable")
            ctx.model.add_assumption(~bookable)
            unavailable = ctx.model.new_bool_var("office_room_unavailable")
            ctx.model.add(unavailable + bookable == 1)
            return RuleEffect(units=unavailable, reason="The room must be bookable.")

        result = engine.plan(data, registry={"office": office}, rankings=["preferred"])
        diag = result["verified"]["infeasibility"]
        self.assertEqual(diag["status"], "sufficient_core")
        self.assertEqual({a["type"] for a in diag["assumptions"]}, {"extension_assumption", "hard_rule"})
        external, = (a for a in diag["assumptions"] if a["type"] == "extension_assumption")
        self.assertLess(external["literal"], 0)
        self.assertEqual(external["variable"], "office_room_bookable")
        self.assertIs(external["value"], False)

    def test_only_native_assumption_is_attributed(self):
        data = snapshot()
        data["constraints"] = [{"id": "office-policy", "kind": "office", "mode": "hard"}]

        def office(ctx, params):
            open_today = ctx.model.new_bool_var("office_open")
            ctx.model.add_assumption(open_today)
            ctx.model.add(open_today == 0)
            return RuleEffect()

        result = engine.plan(data, registry={"office": office}, rankings=["preferred"])
        diag = result["verified"]["infeasibility"]
        self.assertEqual(diag["status"], "sufficient_core")
        external, = diag["assumptions"]
        self.assertEqual(external["type"], "extension_assumption")
        self.assertGreaterEqual(external["literal"], 0)
        self.assertEqual(external["variable"], "office_open")
        self.assertIs(external["value"], True)

    def test_structural_calendar_conflict_is_distinct_from_a_policy_core(self):
        data = snapshot()
        data["request"]["starts"] = data["request"]["starts"][:1]
        data["events"][0]["alternatives"] = []
        diag = engine.plan(data, rankings=["preferred"])["verified"]["infeasibility"]
        self.assertEqual(diag["status"], "base_infeasible")
        self.assertEqual(diag["assumptions"], [])

    def test_missing_evidence_core_identifies_attendee_and_event(self):
        data = snapshot()
        data["events"] = []
        person = data["people"]["A"]
        person["possible"], person["available"] = person["available"], []
        result = engine.plan(data, rankings=["preferred"])
        diag = result["verified"]["infeasibility"]
        self.assertEqual(diag["evidence_mode"], "verified")
        self.assertEqual(diag["assumptions"], [{"type": "verified_calendar", "kind": "calendar",
                                                "person": "A", "event_id": "$request"}])
        self.assertEqual(result["optimistic"]["preferred"]["availability"], "conditional")

    def _expire_verified_only(self, data, rankings):
        """Force the verified allocation to expire, then actually solve the optimistic model."""
        native_order = engine._solve_order

        def interrupt_verified(compiled, verified, order, deadline, workers, incumbent):
            return native_order(compiled, verified, order, 0 if verified else deadline,
                                workers, incumbent)

        with patch.object(engine, "_solve_order", side_effect=interrupt_verified):
            return engine.plan(data, rankings=rankings)

    def test_verified_witness_from_superset_can_prove_same_ranking(self):
        data = snapshot()
        data["events"] = []
        data["people"]["A"] = {"available": [["2026-12-01T09:00Z", "2026-12-01T10:00Z"]],
                               "possible": [["2026-12-01T11:00Z", "2026-12-01T12:00Z"]]}
        result = self._expire_verified_only(data, ["earliest"])
        self.assertEqual(result["search_status"], "complete")
        self.assertEqual(result["verified"]["earliest"], result["optimistic"]["earliest"])
        proof = result["verified"]["proofs"]["earliest"]
        self.assertEqual(proof["status"], "optimal")
        self.assertEqual(proof["stages"][0]["status"], "TIME_LIMIT")
        self.assertEqual(proof["derived_from"], {"evidence_mode": "optimistic", "ranking": "earliest"})

    def test_cross_ranking_witness_is_feasible_only(self):
        data = snapshot()
        data["people"]["A"] = {"available": [["2026-12-01T11:00Z", "2026-12-01T17:00Z"]],
                               "possible": [["2026-12-01T09:00Z", "2026-12-01T10:00Z"]]}
        result = self._expire_verified_only(data, ["least_disruption", "earliest"])
        self.assertEqual(result["optimistic"]["earliest"]["availability"], "conditional")
        self.assertEqual(result["verified"]["earliest"]["start"], "2026-12-01T11:00Z")
        self.assertEqual(result["verified"]["proofs"]["earliest"]["status"], "best_found")
        self.assertEqual(result["verified"]["proofs"]["least_disruption"]["status"], "optimal")
        self.assertEqual(result["search_status"], "limit_reached")

    def test_optimistic_infeasibility_proves_verified_infeasibility(self):
        data = snapshot()
        data["people"]["A"] = {"available": [["2026-12-01T09:00Z", "2026-12-01T10:00Z"]],
                               "possible": [["2026-12-01T11:00Z", "2026-12-01T12:00Z"]]}
        data["events"][0].update(duration_minutes=180, alternatives=[])
        result = self._expire_verified_only(data, ["earliest"])
        self.assertEqual(result["verified"]["status"], "infeasible_in_scope")
        self.assertIsNone(result["verified"]["earliest"])
        self.assertEqual(result["verified"]["proofs"]["earliest"]["derived_from"]["evidence_mode"],
                         "optimistic")
        self.assertEqual(result["verified"]["infeasibility"]["status"], "base_infeasible")
        self.assertEqual(result["search_status"], "complete")
