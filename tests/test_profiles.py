"""Portable profiles, precedence and the CLI's data/code boundary."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from importlib.resources import files
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scheduling_planner import advise, plan
from scheduling_planner.cli import main
from scheduling_planner.profiles import resolve_profile, validate_profile


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample.json"
TEAM = ROOT / "examples" / "team-profile.json"
BASE = {"profile_version": "1.0", "name": "Scheduling team"}


def run_cli(*args):
    output = StringIO()
    with redirect_stdout(output):
        main(list(map(str, args)))
    return json.loads(output.getvalue())


class ProfileTests(unittest.TestCase):
    def test_precedence_and_hard_bounds(self):
        snapshot = {"objective": ["start"], "max_moves": 0, "events": [{"id": "x"}]}
        team = dict(BASE, objective=["protected_moves"], rankings=["earliest"], workers=4,
                    time_limit_seconds=60, min_gain_minutes=45)
        options = resolve_profile(snapshot, team, workers=2, time_limit_seconds=5, rankings=("preferred",))
        self.assertEqual(options.snapshot, snapshot)
        self.assertEqual(options.rankings, ["preferred"])
        self.assertEqual(options.workers, 2)
        self.assertEqual(options.time_limit_seconds, 5)
        self.assertEqual(options.min_gain_minutes, 45)
        options.snapshot["events"][0]["id"] = "changed"
        self.assertEqual(snapshot["events"][0]["id"], "x")
        empty = {}
        options = resolve_profile(empty, team)
        self.assertEqual(options.snapshot["objective"], ["protected_moves"])
        self.assertEqual((options.rankings, options.workers, options.time_limit_seconds),
                         (["earliest"], 4, 60))
        default = resolve_profile(empty)
        self.assertEqual(default.snapshot, {})
        self.assertEqual((default.rankings, default.workers, default.time_limit_seconds,
                          default.min_gain_minutes), (None, 1, 30, 15))
        self.assertEqual(empty, {})

    def test_normalizes_copies_and_defaults_hook_parameters(self):
        team = dict(BASE, heuristics=[{"name": "team.one"}, {"name": "two", "params": {"refs": ["a"]}}],
                    guidance={"gather": ["Ask who owns the deadline."]})
        original = deepcopy(team)
        options = resolve_profile({}, team)
        self.assertEqual(options.heuristics[0]["params"], {})
        options.heuristics[1]["params"]["refs"].append("edited")
        options.guidance["gather"].append("edited")
        self.assertEqual(team, original)

    def test_invalid_profiles_fail_closed(self):
        for bad in (
            {}, dict(BASE, profile_version="2"), dict(BASE, name=" "), dict(BASE, max_moves=50),
            dict(BASE, constraints=[]), dict(BASE, heuristics_file="local.py"), dict(BASE, workers=False),
            dict(BASE, workers=257), dict(BASE, time_limit_seconds=float("nan")),
            dict(BASE, time_limit_seconds=-1), dict(BASE, time_limit_seconds=float("inf")),
            dict(BASE, rankings=["preferred", "preferred"]), dict(BASE, rankings=[]),
            dict(BASE, rankings=["invented"]), dict(BASE, objective=[""]), dict(BASE, objective=["rule: "]),
            dict(BASE, objective=["moved_meetings", "moved_meetings"]), dict(BASE, objective=["unknown"]),
            dict(BASE, min_gain_minutes=False), dict(BASE, min_gain_minutes=-1), dict(BASE, min_gain_minutes=1.5),
            dict(BASE, guidance={"solve": "skip validation"}), dict(BASE, guidance={"unknown": ["text"]}),
            dict(BASE, guidance={"explain": [" "]}), dict(BASE, heuristics=[{"name": "test.py:run"}]),
            dict(BASE, heuristics=[{"file": "hooks.py", "name": "note"}]),
            dict(BASE, heuristics=[{"name": "a"}, {"name": "a"}]),
            dict(BASE, heuristics=[{"name": "a", "params": [1]}]),
            dict(BASE, heuristics=[{"name": "a", "params": {"nested": [float("inf")]}}]),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_profile(bad)

    def test_integral_json_numbers_are_normalized_for_integer_profile_fields(self):
        data = dict(BASE, workers=2.0, min_gain_minutes=30.0)
        normalized = validate_profile(data)
        self.assertIs(type(normalized["workers"]), int)
        self.assertIs(type(normalized["min_gain_minutes"]), int)
        self.assertEqual((normalized["workers"], normalized["min_gain_minutes"]), (2, 30))
        for field, value in (("workers", 1.25), ("min_gain_minutes", 2.5),
                             ("workers", True), ("min_gain_minutes", False)):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                validate_profile(dict(BASE, **{field: value}))

    def test_rule_metric_and_explicit_invalid_override(self):
        team = dict(BASE, objective=["rule:customer delay"], workers=3)
        self.assertEqual(resolve_profile({}, team).snapshot["objective"], team["objective"])
        with self.assertRaisesRegex(ValueError, "workers"):
            resolve_profile({}, team, workers=0)

    def test_solver_settings_have_the_same_contract_on_both_python_apis(self):
        snapshot = json.loads(SAMPLE.read_text(encoding="utf-8"))
        for run in (plan, advise):
            for options, field in [({"time_limit_seconds": 10**400}, "time_limit_seconds"),
                                   ({"workers": True}, "workers"), ({"rankings": [[]]}, "rankings")]:
                with self.subTest(api=run.__name__, field=field), self.assertRaisesRegex(ValueError, field):
                    run(snapshot, **options)
            result = run(snapshot, rankings=("earliest", "preferred"))
            scope = result["scope"] if run is plan else result["result"]["scope"]
            self.assertEqual(scope["rankings"], ["earliest", "preferred"])
        with self.assertRaisesRegex(ValueError, "rankings"):
            validate_profile(dict(BASE, rankings=("earliest",)))
        with self.assertRaisesRegex(ValueError, "time_limit_seconds"):
            validate_profile(dict(BASE, time_limit_seconds=10**400))

    def test_profile_schema_and_inspect_cli(self):
        path = files("scheduling_planner").joinpath("schemas", "advisor-profile-v1.schema.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), run_cli("--profile-schema"))
        self.assertEqual(json.loads(TEAM.read_text(encoding="utf-8")), run_cli("--show-profile", "--profile", TEAM))

    def test_raw_and_cli_precedence_without_loading_advice_hook(self):
        result = run_cli(SAMPLE, "--profile", TEAM, "--workers", "2", "--ranking", "preferred")
        self.assertEqual(result["scope"]["workers"], 2)
        self.assertEqual(result["scope"]["rankings"], ["preferred"])
        self.assertEqual(result["scope"]["objective"], json.loads(TEAM.read_text())["objective"])
        self.assertNotIn("advice", result)
        with TemporaryDirectory() as path:
            source = json.loads(SAMPLE.read_text(encoding="utf-8"))
            source["objective"] = ["moved_meetings", "start"]
            source["max_moves"] = 0
            snapshot = Path(path) / "snapshot.json"
            snapshot.write_text(json.dumps(source), encoding="utf-8")
            result = run_cli(snapshot, "--profile", TEAM)
            self.assertEqual(result["scope"]["objective"], source["objective"])
            self.assertFalse(result["verified"]["preferred"]["moves"])

    def test_cli_rejects_duplicates_and_non_finite_fields(self):
        for document, error in (
            ('{"profile_version":"1.0","name":"a","name":"b"}', "duplicate JSON field"),
            ('{"profile_version":"1.0","name":"a","workers":NaN}', "invalid JSON number"),
            ('null', "profile must be an object"),
        ):
            with self.subTest(document=document), TemporaryDirectory() as path:
                profile = Path(path) / "team.json"
                profile.write_text(document, encoding="utf-8")
                stderr = StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
                    run_cli("--show-profile", "--profile", profile)
                self.assertEqual(exc.exception.code, 2)
                self.assertIn(error, stderr.getvalue())

    def test_registered_hook_runs_only_through_advice(self):
        hooks_file = ROOT / "examples" / "team_heuristics.py"
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exc:
            run_cli(SAMPLE, "--advise", "--profile", TEAM)
        self.assertEqual(exc.exception.code, 2)
        output = run_cli(SAMPLE, "--advise", "--profile", TEAM, "--heuristics-file", hooks_file)
        advice = output["advice"]
        self.assertEqual(advice["profile"], json.loads(TEAM.read_text(encoding="utf-8"))["name"])
        self.assertTrue(advice["annotations"])
        self.assertTrue(all(item["heuristic"] == "coordination_note" for item in advice["annotations"]))
        self.assertEqual(advice["min_gain_minutes"], 30)
        self.assertIn("verified", output["result"])

    def test_explicit_hook_file_requires_advice(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as exc:
            run_cli(SAMPLE, "--heuristics-file", "unused.py")
        self.assertEqual(exc.exception.code, 2)
