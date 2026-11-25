"""The organizer's host IDs survive the complete read, compare and share workflow."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scheduling_planner import brief
from scheduling_planner.ingestion import build_snapshot
from scheduling_planner.portfolio import solve_portfolio
from scheduling_planner.visualize import render_html


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "ritual-day-batch.json"


class RitualDayTests(unittest.TestCase):
    def test_cli_from_host_aliases_to_portable_organizer_brief(self):
        def run(*argv):
            return subprocess.check_output([sys.executable, "-m", "scheduling_planner", *argv])

        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            snapshot_path = work / "week.json"
            snapshot_path.write_bytes(run("batch-snapshot", FIXTURE))
            snapshot = json.loads(snapshot_path.read_bytes())
            aliases = snapshot["metadata"]["event_id_map"]
            self.assertEqual(aliases["amal-check-in"], aliases["amal-copy"])
            self.assertEqual(snapshot["portfolio"]["movable_rituals"], aliases["amal-check-in"] + aliases["sam-check-in"])
            self.assertEqual(json.loads(run("calendar-os", snapshot_path))["invite_hygiene"], [])

            decision_path = work / "decision.json"
            decision_path.write_bytes(run("portfolio", snapshot_path, "--brief", "--compare-moves"))
            decision = json.loads(decision_path.read_bytes())
            self.assertEqual(decision["view"], "brief")
            self.assertEqual([r["metrics"]["focus_minutes"] for r in decision["stages"]], [180, 150, 150])
            self.assertEqual(decision["tradeoff"]["focus_minutes_delta"], -30)
            self.assertEqual(decision["tradeoff"]["preference_penalty_delta"], -2)
            self.assertTrue(decision["tradeoff"]["proved"])
            self.assertEqual(decision["tradeoff"]["daily_focus_example"], {
                "person": "Lead", "date": "2026-11-30", "from_minutes": 120, "to_minutes": 60,
            })
            self.assertIn("Monday", decision["objective_summary"])
            output = work / "export"
            run("export-pack", decision_path, "--snapshot", snapshot_path, "--output", output)
            proposal = (output / "proposal.txt").read_text(encoding="utf-8")
            self.assertIn("Asia/Dubai", proposal)
            self.assertIn("UTC", proposal)
            self.assertIn(decision["tradeoff"]["summary"], proposal)
            self.assertIn(decision["tradeoff"]["details"], proposal)
            self.assertIn("1:1 with Amal", proposal)
            self.assertIn("1:1 with Sam", proposal)

    def test_maker_target_by_date_changes_the_decision(self):
        batch = json.loads(FIXTURE.read_bytes())
        for day, status in (("2026-11-30", "met"), ("2026-12-01", "infeasible")):
            with self.subTest(day=day):
                batch["portfolio"]["maker_target"] = {
                    "person": "Lead", "duration_minutes": 120,
                    "window": [day + "T09:00+04:00", day + "T13:00+04:00"],
                }
                snapshot = build_snapshot(batch)
                result = solve_portfolio(snapshot, compare_moves=True)
                compact = brief(result)
                self.assertEqual(render_html(compact, snapshot), render_html(result, snapshot))
                for stage in compact["stages"]:
                    target = stage["maker_target"]
                    self.assertEqual(target["status"], status)
                    if status == "met":
                        self.assertEqual(stage["moves"], [])
                        self.assertEqual(target["witness"], {"start": day + "T05:00Z", "end": day + "T07:00Z"})
                    else:
                        self.assertIsNone(target["witness"])
                        self.assertEqual(stage["status"], "INFEASIBLE")
