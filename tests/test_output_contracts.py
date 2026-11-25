"""The published output schemas describe actual results across evidence and proof states."""

from copy import deepcopy
import json
from importlib.resources import files
import unittest

from scheduling_planner import advise, plan
from scheduling_planner.brief import brief
from scheduling_planner.contracts import output_schema
from scheduling_planner.rules import RuleEffect
from tests.test_engine import snapshot

try:
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:  # Standard-library-only engine users can run the rest of the suite.
    Draft202012Validator = None


class SchemaResourcesTests(unittest.TestCase):
    def test_selected_schema_is_standalone_and_independent(self):
        root = output_schema()
        path = files("scheduling_planner").joinpath("schemas", "outputs-v1.schema.json")
        self.assertEqual(root, json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual([s["$ref"] for s in root["oneOf"]], ["#/$defs/result", "#/$defs/advice", "#/$defs/brief"])
        one = output_schema("advice")
        self.assertEqual(one["$ref"], "#/$defs/advice")
        self.assertNotIn("oneOf", one)
        one["$defs"].clear()
        self.assertTrue(output_schema("brief")["$defs"])
        with self.assertRaisesRegex(ValueError, "schema view"):
            output_schema("snapshot")


@unittest.skipUnless(Draft202012Validator, "Install the dev extra to validate output contracts")
class OutputContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validators = {}
        for view in (None, "result", "advice", "brief"):
            schema = output_schema(view)
            Draft202012Validator.check_schema(schema)
            cls.validators[view] = Draft202012Validator(schema)

    def assert_views(self, report):
        for view, instance in (("result", report["result"]), ("advice", report), ("brief", brief(report))):
            with self.subTest(view=view):
                self.validators[view].validate(instance)
                self.validators[None].validate(instance)

    def test_valid_reports_cover_winners_proofs_frontier_and_unattempted_ranking(self):
        self.assert_views(advise(snapshot(), compare_moves=True))
        self.assert_views(advise(snapshot(), rankings=["earliest"], time_limit_seconds=1e-12, compare_moves=True))

    def test_conditional_gain_relative_to_a_verified_baseline(self):
        data = snapshot()
        person = data["people"]["A"]
        starts = data["request"]["starts"]
        data["request"]["duration_minutes"] = 15
        data["request"]["starts"] = ["2026-12-01T08:00Z", starts[0]]
        person["possible"] = [["2026-12-01T08:00Z", "2026-12-01T08:15Z"]]
        person["available"] = [[starts[0], "2026-12-01T16:00Z"]]
        report = advise(data)
        self.assertIsNotNone(report["advice"]["question_source"]["baseline_max_moves"])
        self.assert_views(report)

    def test_conditional_calendar_work_and_rule_questions(self):
        case = snapshot()
        person = case["people"]["A"]
        person["possible"] = person.pop("available")
        person.update(available=[], busy_coverage=[], rest_minutes=10)
        self.assert_views(advise(case, compare_moves=True))
        case["constraints"] = [{"kind": "finish_by", "id": "cutoff", "source": "Owner",
                                "mode": "hard", "params": {"event": "$request", "fact": "missing_date"}}]
        self.assert_views(advise(case))

    def test_extension_assumption_core_and_nonempty_annotations(self):
        case = snapshot()
        case["constraints"] = [{"kind": "local", "id": "policy", "mode": "hard"}]

        def policy(ctx, params):
            var = ctx.model.new_bool_var("local_enabled")
            ctx.model.add_assumption(var)
            return RuleEffect(units=var, reason="Conflicting requirement.")

        self.assert_views(advise(case, registry={"local": policy}, compare_moves=True))
        del case["constraints"]
        self.assert_views(advise(case, profile={"profile_version": "1.0", "name": "team",
            "heuristics": [{"name": "note"}]}, heuristics={"note": lambda ctx, params: [
                {"plan_id": ctx["recommended_id"], "text": "Call the organizer.", "kind": "note"}
            ]}))

    def test_malformed_results_are_rejected(self):
        result = plan(snapshot(), rankings=["earliest"])
        for path, value in [
            (("scope", "snapshot_sha256"), "not-a-digest"),
            (("verified", "earliest", "availability"), "certain"),
            (("verified", "proofs", "earliest", "witness"), None),
            (("verified", "proofs", "earliest", "status"), "possibly_optimal"),
            (("verified", "proofs", "earliest", "stages", 0, "best_bound"), 1.5),
        ]:
            with self.subTest(path=path):
                invalid = deepcopy(result)
                node = invalid
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                with self.assertRaises(ValidationError):
                    self.validators["result"].validate(invalid)
        self.assertFalse(self.validators["brief"].is_valid(result))
        self.assertFalse(self.validators["advice"].is_valid(result))
