"""Public release commands, portable extensions and stdout isolation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstalledCommandTests(unittest.TestCase):
    def test_utf8_pipe_and_file_preserve_identical_names_under_legacy_locale(self):
        document = {"attendees": ["José", "كريم"]}
        raw = json.dumps(document, ensure_ascii=False).encode("utf-8")
        command = [sys.executable, "-c", "import json, sys; from scheduling_planner.cli import _read_json; "
                   "print(json.dumps(_read_json(sys.argv[1])))"]
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_bytes(raw)
            for arg in ("-", str(path)):
                result = subprocess.run([*command, arg], env=env, input=raw, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout), document)
        bad = subprocess.run([sys.executable, "-m", "scheduling_planner", "-"], env=env,
                             input=b'{"name":"\xff"}', capture_output=True)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, b"")

    def test_native_cli_preserves_utf8_snapshot_identity(self):
        fixture = Path(__file__).resolve().parents[1] / "src/scheduling_planner/data/demo.json"
        snapshot = json.loads(fixture.read_bytes())
        snapshot["snapshot_id"] = "José · كريم"
        raw = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        command = [sys.executable, "-m", "scheduling_planner", "--brief", "--compare-moves"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_bytes(raw)
            for arg in ("-", str(path)):
                result = subprocess.run([*command, arg], env=env, input=raw, capture_output=True, check=True)
                self.assertEqual(json.loads(result.stdout)["scope"]["snapshot_id"], snapshot["snapshot_id"])

    def test_profile_integer_keeps_its_json_value(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            command = [sys.executable, "-m", "scheduling_planner", "--show-profile", "--profile", str(profile)]
            prefix = '{"profile_version":"1.0","name":"team","min_gain_minutes":'
            profile.write_text(prefix + "9007199254740993.0}", encoding="utf-8")
            result = json.loads(subprocess.check_output(command, text=True))
            self.assertEqual(result["min_gain_minutes"], 9007199254740993)
            profile.write_text(prefix + "0.99999999999999999}", encoding="utf-8")
            invalid = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")
            self.assertIn("fraction", invalid.stderr)

    def test_inspection_and_solver_diagnostic_without_native_backend(self):
        # Exercise the same module entry point used by downstream integrations.
        launcher = [sys.executable, "-c",
                    "import sys, runpy; sys.modules['ortools'] = None; "
                    "runpy.run_module('scheduling_planner', run_name='__main__')"]
        schema = subprocess.check_output([*launcher, "--schema"], text=True)
        self.assertIn("request", json.loads(schema)["properties"])
        failed = subprocess.run([*launcher, "--demo"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "")
        self.assertIn("solving requires OR-Tools", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_version_and_packaged_demo_from_any_directory(self):
        with tempfile.TemporaryDirectory() as path:
            command = [sys.executable, "-m", "scheduling_planner"]
            version = subprocess.check_output([*command, "--version"], cwd=path, text=True).strip()
            payload = json.loads(subprocess.check_output([*command, "--demo"], cwd=path, text=True))
        self.assertIn(payload["planner_version"], version)
        self.assertEqual(payload["view"], "brief")
        rows = payload["move_comparison"]["caps"]
        self.assertEqual([(r["cap"], r["minutes_gained"]) for r in rows], [(0, None), (1, 330), (2, 30)])
        self.assertTrue(all(row["status"] == "optimal" for row in rows))

    def test_package_and_sibling_extensions_preserve_json(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            package = tmp / "trusted_review_policy"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "helpers.py").write_text('LABEL = "test helper"\n', encoding="utf-8")
            body = ('print("loaded policy")\n'
                    'def annotate(context, params):\n'
                    '    print("checked option")\n'
                    '    return [{"kind": "note", "text": LABEL, "plan_id": context["recommended_id"]}]\n'
                    'HEURISTICS = {"annotate": annotate}\n')
            (package / "module.py").write_text("from .helpers import LABEL\n" + body, encoding="utf-8")
            (package / "extension.py").write_text("from helpers import LABEL\n" + body, encoding="utf-8")
            profile = tmp / "profile.json"
            profile.write_text(json.dumps({"profile_version": "1.0", "name": "CI",
                                           "heuristics": [{"name": "annotate"}]}), encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join([str(tmp), env.get("PYTHONPATH", "")])
            for switch, name in [("--heuristics-module", "trusted_review_policy.module"),
                                 ("--heuristics-file", str(package / "extension.py"))]:
                with self.subTest(switch=switch):
                    result = subprocess.run([sys.executable, "-m", "scheduling_planner", "--demo", "--profile",
                                             str(profile), switch, name], cwd=tmp, env=env,
                                            capture_output=True, text=True, check=True)
                    self.assertEqual(json.loads(result.stdout)["annotations"][0]["text"], "test helper")
                    self.assertIn("loaded policy", result.stderr)
                    self.assertIn("checked option", result.stderr)
