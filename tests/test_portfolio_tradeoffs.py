"""Evidence behind the policy and maker-time explanation in a saved decision."""
from datetime import date
import json
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scheduling_planner.ingestion import build_snapshot
from scheduling_planner.portfolio import solve_portfolio
from scheduling_planner.portfolio_tradeoffs import explain_tradeoffs
from scheduling_planner.visualize import export_pack


class PortfolioTradeoffTests(unittest.TestCase):
    @staticmethod
    def snapshot():
        fixture = Path(__file__).resolve().parents[1] / "examples/ritual-day-batch.json"
        return build_snapshot(json.loads(fixture.read_text(encoding="utf-8")))

    def test_both_plans_explain_the_policy_and_show_one_local_day(self):
        report = solve_portfolio(self.snapshot(), compare_moves=True)
        selected = report["tradeoff"]
        keep = report["stages"][0]["tradeoff"]
        self.assertEqual(selected["daily_focus_example"],
                         {"person": "Lead", "date": "2026-11-30", "from_minutes": 120, "to_minutes": 60})
        self.assertIn("Monday 1:1 preference", selected["summary"])
        self.assertEqual(keep["compared_with_budget"], 2)
        self.assertEqual((keep["preference_penalty_delta"], keep["focus_minutes_delta"]), (2, 30))
        self.assertIn("60 to 120 minutes", keep["details"])
        self.assertTrue(selected["proved"] and keep["proved"])

    def test_only_paired_witnesses_support_a_claim_and_proof_lite_does_not_rank(self):
        snapshot = {"people": {"Lead": {}}, "requests": [],
                    "portfolio": {"preferences": {"Lead": {"max_meetings_per_day": 2}}}}
        keep = {"budget": 0, "status": "UNKNOWN", "metrics": {}}
        moved = {"budget": 1, "status": "FEASIBLE", "metrics": {"focus_minutes": 30, "preference_penalty": 0}}
        report = {**moved, "stages": [keep, moved], "proof_lite": True}
        day = ("Lead", date(2026, 11, 30))
        focus = {0: {}, 1: {day: 30}}
        explain_tradeoffs(report, snapshot, focus)
        self.assertNotIn("tradeoff", report)
        self.assertIn("first feasible", report["objective_summary"])
        keep.update(status="OPTIMAL", metrics={"focus_minutes": 60, "preference_penalty": 1})
        focus[0][day] = 60
        explain_tradeoffs(report, snapshot, focus)
        tradeoff = report["tradeoff"]
        self.assertFalse(tradeoff["proved"])
        self.assertIn("1 fewer soft preference miss", tradeoff["summary"])
        self.assertNotIn("Monday", tradeoff["summary"])
        self.assertIn("observed", tradeoff["details"])

    def test_maker_target_export_without_a_witness_explains_solver_status(self):
        source = self.snapshot()
        source["portfolio"]["maker_target"] = {"person": "Lead", "duration_minutes": 600}
        infeasible = solve_portfolio(source, compare_moves=True)
        # Expire the shared budget before search, independent of OS clock precision.
        with patch("scheduling_planner.portfolio.monotonic", side_effect=count()):
            unknown = solve_portfolio(source, compare_moves=True, time_limit_seconds=1)
        for report, status, message in [(infeasible, "INFEASIBLE", "No schedule meets all requirements"),
                                        (unknown, "UNKNOWN", "without establishing whether")]:
            with self.subTest(status=status), TemporaryDirectory() as out:
                self.assertEqual(report["status"], status)
                export_pack(report, source, out)
                text = (Path(out) / "proposal.txt").read_text(encoding="utf-8")
                self.assertIn(message, text)
                self.assertIn("10h maker block for Lead", text)
