"""Offline sharing of an existing solve across evidence states and portfolio move budgets."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scheduling_planner import advise, brief
from scheduling_planner.cli import demo_snapshot
from scheduling_planner.domain import snapshot_digest
from scheduling_planner.portfolio import solve_portfolio
from scheduling_planner.visualize import export_pack, render_html


def page_data(html):
    prefix = '<script id="calendar-data" type="application/json">'
    return json.loads(html.split(prefix, 1)[1].split("</script>", 1)[0])


class VisualizeTests(unittest.TestCase):
    def test_advice_and_brief_show_the_same_slots_moves_attendees_and_cap_gains(self):
        source = demo_snapshot()
        title = "Design </script> & review"
        source["events"][0]["metadata"] = {"title": title, "organizer": "Asha"}
        source["people"]["Asha"]["metadata"] = {"timezone": "Asia/Dubai"}
        report = advise(source, compare_moves=True)
        html = render_html(report, source)
        self.assertEqual(html, render_html(brief(report), source))
        self.assertNotIn(title, html)
        data = page_data(html)
        self.assertEqual(data["current"][0]["title"], title)
        self.assertEqual(data["timezones"], ["Asia/Dubai"])
        self.assertEqual([r["cells"][0] for r in data["comparison"]], [0, 1, 2])
        self.assertEqual(data["comparison"][1]["cells"][-1], "330 min (proved)")
        choice = next(o for o in data["options"] if len(o["moves"]) == 1)
        move = choice["moves"][0]
        self.assertEqual(move["before"]["start"], "2026-12-01T09:00Z")
        self.assertEqual(move["before"]["end"], "2026-12-01T10:00Z")
        self.assertEqual(move["after"]["start"], "2026-12-01T12:00Z")
        self.assertEqual(move["after"]["attendees"], ["Asha", "Eli"])
        self.assertEqual(move["after"]["organizer"], "Asha")
        bare = page_data(render_html(brief(report)))
        self.assertFalse(bare["fullSnapshot"])
        self.assertEqual(len(bare["options"]), len(data["options"]))

    def test_conditional_no_witness_and_wrong_snapshot(self):
        source = demo_snapshot()
        source["people"]["Asha"]["busy_coverage"] = []
        result = brief(advise(source))
        view = page_data(render_html(result))
        self.assertIsNone(view["primary"])
        self.assertIn("Conditional", view["options"][0]["evidence"])
        self.assertTrue(any("Asha" in note for note in view["options"][0]["notes"]))
        wrong = {**source, "snapshot_id": "changed"}
        with self.assertRaisesRegex(ValueError, "snapshot_sha256"):
            render_html(result, wrong)
        source["people"]["Asha"]["available"] = []
        view = page_data(render_html(brief(advise(source)), source))
        self.assertEqual(view["options"], [])
        self.assertIn("Infeasible", view["status"])

    def test_export_matching_profile_resolved_snapshot_and_selected_proposal(self):
        source = demo_snapshot()
        profile = {"name": "Early start", "profile_version": "1.0", "objective": ["start", "moved_meetings"]}
        report = advise(source, profile=profile)
        with TemporaryDirectory() as directory:
            pack = Path(directory) / "week"
            pack.mkdir()
            export_pack(report, source, pack)
            self.assertEqual({p.name for p in pack.iterdir()}, {"plan.html", "snapshot.json", "proposal.txt"})
            self.assertEqual(snapshot_digest(json.loads((pack / "snapshot.json").read_text(encoding="utf-8"))),
                             report["result"]["scope"]["snapshot_sha256"])
            proposal = (pack / "proposal.txt").read_text(encoding="utf-8")
            for detail in ("09:00–09:30 UTC", "Design review", "Team sync", "Eli", "Dana", "Please confirm"):
                self.assertIn(detail, proposal)
            self.assertEqual((pack / "plan.html").read_text(encoding="utf-8"), render_html(report, source))
            (pack / "proposal.txt").write_text("Edited proposal", encoding="utf-8")
            with self.assertRaises(ValueError):
                export_pack(report, source, pack)
            self.assertEqual((pack / "proposal.txt").read_text(encoding="utf-8"), "Edited proposal")

    def test_portfolio_current_and_proposed_week_and_maker_only_stages(self):
        source = demo_snapshot()
        request = source.pop("request")
        source["requests"] = [{"id": "decision", **request}, {"id": "follow-up", **deepcopy(request)}]
        source["portfolio"] = {"movable_rituals": ["design-review", "team-sync"],
                               "cluster": {"requests": ["decision", "follow-up"]}}
        result = solve_portfolio(source, compare_moves=True)
        view = page_data(render_html(result, source))
        self.assertEqual({p["id"] for p in view["options"][0]["placements"]}, {"decision", "follow-up"})
        self.assertEqual(view["kind"], "portfolio")
        self.assertEqual(view["participants"], request["attendees"])
        self.assertEqual([r["cells"][0] for r in view["comparison"]], [0, 1, 2])
        ids = {option["id"] for option in view["options"]}
        for row in view["comparison"]:
            if row["cells"][1] == "optimal":
                self.assertIn(row["option"], ids)
        source["requests"] = []
        del source["portfolio"]["cluster"]
        result = solve_portfolio(source, compare_moves=True)
        view = page_data(render_html(result, source))
        self.assertEqual(view["options"][0]["placements"], [])
        baseline = view["comparison"][0]["option"]
        self.assertEqual(next(opt for opt in view["options"] if opt["id"] == baseline)["moves"], [])

    def test_identical_portfolio_choices_share_one_selectable_schedule(self):
        from scheduling_planner.ingestion import build_snapshot

        example = Path(__file__).resolve().parents[1] / "examples/squad-week-batch.json"
        source = build_snapshot(json.loads(example.read_text(encoding="utf-8")))
        report = solve_portfolio(source, compare_moves=True)
        data = page_data(render_html(report, source))
        self.assertEqual(data["headline"], "Where do the 1:1s fit?")
        self.assertEqual(data["defaultTimezone"], "Asia/Dubai")
        self.assertEqual(len(data["options"]), 1)
        self.assertTrue(all(row["option"] == data["primary"] for row in data["comparison"]))
        self.assertEqual(data["primary"], data["baseline"])
        self.assertEqual(data["options"][0]["proofStatus"], "optimal")
        self.assertIn("1:1 with Amal", data["options"][0]["proposal"])
        self.assertIn("35h 30m", data["options"][0]["facts"][1])

    def test_offline_handoff_and_embedded_code_are_scoped_to_selected_witness(self):
        source = demo_snapshot()
        source["request"]["metadata"] = {"title": "Budget {{REVIEW_JS}} </script>"}
        html = render_html(brief(advise(source, compare_moves=True)), source)
        data = page_data(html)
        self.assertEqual(data["participants"], source["request"]["attendees"])
        for option in data["options"]:
            self.assertIn("Budget {{REVIEW_JS}} </script>", option["proposal"])
            self.assertEqual(option["proposal"].count("Move "), len(option["moves"]))
        self.assertNotIn("Budget {{REVIEW_JS}} </script>", html)
