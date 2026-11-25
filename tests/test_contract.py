"""Public input, packaging, and format version regressions."""
from importlib.resources import files
import json
import subprocess
import sys
import unittest

from scheduling_planner import plan
from scheduling_planner.domain import parse_snapshot


def example():
    return {"request": {"attendees": ["Ada"], "duration_minutes": 30,
                        "starts": ["2026-12-01T09:00+04:00"]},
            "people": {"Ada": {"available": [["2026-12-01T08:00+04:00", "2026-12-01T17:00+04:00"]]}}}


class PublicContractTests(unittest.TestCase):
    def test_packaged_schema_and_cli_agree(self):
        path = files("scheduling_planner").joinpath("schemas", "snapshot-v1.schema.json")
        expected = json.loads(path.read_text(encoding="utf-8"))
        output = subprocess.check_output([sys.executable, "-m", "scheduling_planner", "--schema"])
        self.assertEqual(json.loads(output), expected)
        self.assertEqual(expected["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_unsupported_version_fails_and_legacy_defaults_are_reported(self):
        data = example()
        with self.assertRaisesRegex(ValueError, "schema_version"):
            parse_snapshot(dict(data, schema_version="2.0"))
        result = plan(dict(data, max_states=200000), rankings=["preferred"])
        self.assertTrue(result["scope"]["migration_notes"])
        self.assertEqual(result["verified"]["preferred"]["start"], "2026-12-01T05:00Z")

    def test_instants_must_be_offset_aware_and_minute_aligned(self):
        for invalid in ["2026-12-01T09:00", "2026-12-01T09:00:07Z", "2026-12-01T09:00:00.1Z",
                        "2026-12-01T09:00+05:89", "2026-12-01T09:00+24:00"]:
            with self.subTest(invalid=invalid):
                data = example()
                data["request"]["starts"] = [invalid]
                with self.assertRaisesRegex(ValueError, "minute"):
                    parse_snapshot(data)
