"""Integration regressions for reusable snapshots, integer bounds and proof provenance."""

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from scheduling_planner import engine
from scheduling_planner._version import __version__
from scheduling_planner.rules import RuleEffect
from tests.test_engine import snapshot


class EngineReleaseTests(unittest.TestCase):
    def test_solved_scope_is_detached_and_fingerprinted(self):
        data = snapshot()
        data["decisions"] = {"room": ["East"]}
        data["constraints"] = [{
            "id": "cutoff", "kind": "finish_by", "mode": "hard",
            "params": {"event": "$request", "deadline": "2026-12-01T12:00Z"},
        }]
        original = deepcopy(data)
        out = engine.plan(data, rankings=["earliest"])
        digest = sha256(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(out["scope"]["snapshot_sha256"], digest)
        self.assertEqual((out["result_version"], out["planner_version"]), ("1.0", __version__))
        data["constraints"][0]["params"]["deadline"] = "2026-11-30T12:00Z"
        data["decisions"]["room"][0] = "West"
        self.assertEqual(out["scope"]["constraints"], original["constraints"])
        self.assertEqual(out["scope"]["decision_domains"], original["decisions"])
        out["scope"]["constraints"].clear()
        self.assertEqual(len(data["constraints"]), 1)
        self.assertNotEqual(engine.plan(data)["scope"]["snapshot_sha256"], digest)

    def test_stage_lower_bound_preserves_both_large_variable_and_large_affine_offset(self):
        huge = 2**53 + 3
        native_solve = cp_model.CpSolver.solve

        def withhold_proof(solver, model):
            # Use genuine native solution, postsolve bounds and presolve.
            # Exercise the FEASIBLE-reporting branch independently of CPU speed.
            status = native_solve(solver, model)
            self.assertEqual(status, cp_model.OPTIMAL)
            return cp_model.FEASIBLE

        for as_offset in (False, True):
            with self.subTest(as_offset=as_offset):
                data = snapshot()
                data["objective"] = ["soft_penalty"]
                data["decisions"] = {"room": ["East", "West"]}
                data["constraints"] = [{"id": "var", "kind": "fee", "mode": "soft", "weight": 3}]
                if as_offset:
                    data["constraints"].append({"id": "offset", "kind": "base", "mode": "soft"})

                def fee(ctx, params, lower=0 if as_offset else huge):
                    var = ctx.model.new_int_var(lower, lower + 1, "fee")
                    ctx.model.add(var == lower + ctx.decisions["room"])
                    return RuleEffect(units=var, reason="Room cost.")

                with patch.object(cp_model.CpSolver, "solve", withhold_proof):
                    out = engine.plan(data, registry={"fee": fee, "base": lambda c, p: RuleEffect(
                        units=huge, reason="Flat charge.")}, rankings=["preferred"])
                expected = huge if as_offset else 3 * huge
                proof = out["verified"]["proofs"]["preferred"]
                stage = proof["stages"][0]
                self.assertEqual(stage["status"], "FEASIBLE")
                self.assertIs(type(stage["best_bound"]), int)
                self.assertEqual(stage["best_bound"], expected)
                self.assertEqual(proof["witness"]["scores"]["soft_penalty"], expected)
                self.assertEqual(proof["status"], "best_found")

    def test_reconciled_witness_identifies_source_and_actual_scores(self):
        data = snapshot()
        data["people"]["A"] = {"available": [["2026-12-01T11:00Z", "2026-12-01T17:00Z"]],
                               "possible": [["2026-12-01T09:00Z", "2026-12-01T10:00Z"]]}
        native = engine._solve_order

        def expire_verified(compiled, verified, order, deadline, workers, incumbent):
            return native(compiled, verified, order, 0 if verified else deadline, workers, incumbent)

        with patch.object(engine, "_solve_order", side_effect=expire_verified):
            report = engine.plan(data, rankings=["least_disruption", "earliest"])
        proof = report["verified"]["proofs"]["earliest"]
        self.assertEqual(proof["status"], "best_found")
        self.assertEqual(proof["stages"][0]["status"], "TIME_LIMIT")
        self.assertEqual(proof["witness"]["origin"], {"evidence_mode": "optimistic", "ranking": "least_disruption"})
        scores = proof["witness"]["scores"]
        plan = report["verified"]["earliest"]
        self.assertEqual(engine.iso(scores["start"]), plan["start"])
        self.assertEqual(scores["unresolved_count"], len(plan["unresolved"]))
        self.assertEqual(scores["moved_meetings"], plan["metrics"]["moved_meetings"])

    def test_zero_cap_elides_movable_variables_but_validates_alternatives(self):
        data = snapshot()
        data["max_moves"] = 0
        with_choices = engine.plan(data)
        fixed = deepcopy(data)
        del fixed["events"][0]["alternatives"]
        without_choices = engine.plan(fixed)
        self.assertEqual(with_choices["verified"]["preferred"], without_choices["verified"]["preferred"])
        for field in ("base_variables", "base_constraints"):
            self.assertEqual(with_choices["statistics"][field], without_choices["statistics"][field])
        timings = with_choices["statistics"]
        self.assertAlmostEqual(timings["elapsed_seconds"], timings["model_build_seconds"] + timings["search_seconds"])
        data["events"][0]["alternatives"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            engine.plan(data)
