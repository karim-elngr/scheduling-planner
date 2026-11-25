"""Check the built source archive and wheel using the development dependencies."""

import argparse
from configparser import ConfigParser
import json
import tarfile
import tomllib
from email import message_from_bytes
from pathlib import Path
from zipfile import ZipFile

from jsonschema import Draft202012Validator


def only(directory, pattern):
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def check(directory):
    with tarfile.open(only(directory, "*.tar.gz"), "r:gz") as source, ZipFile(only(directory, "*.whl")) as wheel:
        members = {name.partition("/")[2]: name for name in source.getnames()}

        def from_source(name):
            if name not in members:
                raise ValueError(f"Missing file in source distribution: {name}")
            entry = source.extractfile(members[name])
            if entry is None:
                raise ValueError(f"Source entry is not a file: {name}")
            with entry:
                return entry.read()

        required = [
            ".gitignore", "uv.lock", "LICENSE", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "run.py",
            "docs/releasing.md", "docs/preferences.md", "docs/proofs.md", "docs/demo.html",
            "docs/workflow-evaluations.md", "docs/images/one-move.png", "docs/images/two-moves.png",
            "docs/images/conditional-handoff.png", "tools/capture_demo.py",
            "tools/check_release.py", "tools/check_browser_demo.py", "tools/refresh_demo.py", "tools/eval_agent.py",
            ".agents/skills/plan-meetings/SKILL.md",
            ".agents/skills/plan-meetings/references/solver-playbook.md",
            ".agents/skills/plan-meetings/evals/scenarios.json",
            "examples/team_heuristics.py", "examples/team_rules.py", "tests/test_advisor.py",
            "examples/squad-week-batch.json", "docs/batch-snapshot.md", "docs/portfolio.md",
            "docs/calendar-os.md", "docs/apply.md",
        ]
        for filename in required:
            if not from_source(filename).strip():
                raise ValueError(f"Empty file in source distribution: {filename}")
        # Detect mismatched builds even when their names, versions and schemas agree.
        prefix = "src/scheduling_planner/"
        source_files = {
            name.removeprefix("src/") for name, member in members.items()
            if name.startswith(prefix) and source.getmember(member).isfile()
        }
        wheel_files = {n for n in wheel.namelist() if n.startswith("scheduling_planner/") and not n.endswith("/")}
        if source_files != wheel_files:
            raise ValueError(f"Package files differ between wheel and source: {sorted(source_files ^ wheel_files)}")
        for name in sorted(source_files):
            if wheel.read(name) != from_source(f"src/{name}"):
                raise ValueError(f"Package file differs between wheel and source: {name}")
        scenarios = json.loads(from_source(".agents/skills/plan-meetings/evals/scenarios.json"))["cases"]
        for case in scenarios:
            if "fixture_from_repo_root" in case:
                from_source(case["fixture_from_repo_root"])
        for schema_name, examples in [
            ("snapshot-v1", ["sample.json", "examples/release-review.json"]),
            ("advisor-profile-v1", ["examples/team-profile.json", "examples/profiles/steady-state.json",
                                    "examples/profiles/launch-week.json", "examples/profiles/mixed-timezones.json"]),
        ]:
            name = f"scheduling_planner/schemas/{schema_name}.schema.json"
            raw = wheel.read(name)
            schema = json.loads(raw)
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            for example in examples:
                validator.validate(json.loads(from_source(example)))
        outputs = "scheduling_planner/schemas/outputs-v1.schema.json"
        Draft202012Validator.check_schema(json.loads(wheel.read(outputs)))
        demo = "scheduling_planner/data/demo.json"
        input_schema = json.loads(wheel.read("scheduling_planner/schemas/snapshot-v1.schema.json"))
        Draft202012Validator(input_schema).validate(json.loads(wheel.read(demo)))
        dist_info = next(n.rsplit("/", 1)[0] for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
        meta = message_from_bytes(wheel.read(f"{dist_info}/METADATA"))
        sdist_meta = message_from_bytes(from_source("PKG-INFO"))
        for header in ("Name", "Version", "Requires-Python", "Requires-Dist", "Provides-Extra",
                       "License-Expression", "License-File"):
            if sorted(meta.get_all(header, [])) != sorted(sdist_meta.get_all(header, [])):
                raise ValueError(f"{header} differs between wheel and source")
        project = tomllib.loads(from_source("pyproject.toml").decode("utf-8"))["project"]
        entry_points = ConfigParser(interpolation=None)
        entry_points.read_string(wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8"))
        if dict(entry_points["console_scripts"]) != project["scripts"]:
            raise ValueError("Installed console scripts differ from pyproject.toml")
        for name in project["license-files"]:
            if wheel.read(f"{dist_info}/licenses/{name}") != from_source(name):
                raise ValueError(f"License file differs between wheel and source: {name}")
        print(f"{meta['Name']} {meta['Version']}: package files, metadata, entry points, license, schemas and examples passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing exactly one sdist and one wheel")
    check(parser.parse_args().directory)
