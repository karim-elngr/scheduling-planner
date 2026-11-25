"""Exercise the documented squad-week workflow through its installed entry point."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SquadWeekCommandTests(unittest.TestCase):
    def test_batch_to_review_pack_and_organizer_manifest(self):
        def run(*args):
            return subprocess.check_output([sys.executable, "-m", "scheduling_planner", *args], text=True)

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            snapshot = work / "snapshot.json"
            snapshot.write_text(run("batch-snapshot", ROOT / "examples/squad-week-batch.json"), encoding="utf-8")
            diagnosis = json.loads(run("calendar-os", snapshot))
            self.assertEqual(diagnosis["people"]["Amal"]["timezone"], "Asia/Riyadh")

            report = work / "decision.json"
            report.write_text(run("portfolio", snapshot, "--compare-moves"), encoding="utf-8")
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "OPTIMAL")
            self.assertEqual([stage["budget"] for stage in result["stages"]], [0, 1])
            self.assertEqual(sorted(p["start"] for p in result["placements"]),
                             ["2026-12-03T09:00Z", "2026-12-03T09:30Z"])

            pack = work / "pack"
            run("export-pack", report, "--snapshot", snapshot, "--output", pack)
            self.assertEqual(json.loads((pack / "snapshot.json").read_text(encoding="utf-8")),
                             json.loads(snapshot.read_text(encoding="utf-8")))
            self.assertTrue((pack / "plan.html").is_file())
            self.assertTrue((pack / "proposal.txt").read_text(encoding="utf-8").strip())

            bindings = work / "bindings.json"
            bindings.write_text(json.dumps({
                "organizer": "lead@example.org",
                "people": {p: p.lower() + "@example.org" for p in ["Lead", "Amal", "Sam"]},
                "requests": {p["id"]: {"title": p["id"]} for p in result["placements"]},
            }), encoding="utf-8")
            manifest = json.loads(run("prepare-apply", report, "--snapshot", snapshot, "--bindings", bindings))
            self.assertEqual([op["action"] for op in manifest["operations"]], ["create", "create"])
            self.assertEqual(manifest["snapshot_sha256"], result["snapshot_sha256"])
