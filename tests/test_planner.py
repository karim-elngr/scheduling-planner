import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scheduling_planner import plan


def utc(clock):
    return f"2026-12-01T{clock}:00+00:00"


def fixture(names, required, starts):
    return {
        "people": {
            name: {"available": [[utc("08:00"), utc("18:00")]], "rest_minutes": 15}
            for name in names
        },
        "request": {
            "attendees": required,
            "duration_minutes": 30,
            "starts": [utc(clock) for clock in starts],
        },
        "events": [],
        "max_moves": 2,
    }


def event(event_id, names, start, alternative=None, tier="ordinary"):
    return {
        "id": event_id,
        "attendees": names,
        "duration_minutes": 30,
        "start": utc(start),
        "alternatives": [utc(alternative)] if alternative else [],
        "move_tier": tier,
    }


def load_sample():
    return json.loads((Path(__file__).resolve().parents[1] / "sample.json").read_text())


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PlannerTests(unittest.TestCase):
    def assert_utc_start(self, result, expected):
        actual = parse_time(result["start"])
        self.assertEqual(actual.utcoffset(), timedelta(0))
        self.assertEqual(actual, parse_time(expected))

    def test_dubai_sample_offers_distinct_plans(self):
        result = plan(load_sample())
        self.assertEqual(result["search_status"], "complete")
        verified = result["verified"]
        self.assertEqual(verified["status"], "optimal")
        self.assert_utc_start(verified["least_disruption"], "2026-12-02T10:00:00Z")
        self.assertEqual(verified["least_disruption"]["moves"], [])

        early = verified["earliest"]
        self.assert_utc_start(early, utc("07:00"))
        self.assertEqual([move["event_id"] for move in early["moves"]], ["bob-eli-weekly-sync"])
        self.assertEqual(early["unresolved"], [])
        self.assertEqual(early["availability"], "verified")

        conditional = result["optimistic"]["earliest"]
        self.assert_utc_start(conditional, utc("06:00"))
        self.assertEqual(conditional["availability"], "conditional")
        self.assertEqual(
            [(item["person"], item["event_id"]) for item in conditional["unresolved"]],
            [("Dana", "alice-dana-design-review")],
        )

    def test_cascade_requires_two_moves(self):
        data = fixture(["Alice", "Jo", "Kim"], ["Alice"], ["10:00"])
        data["events"] = [
            event("design", ["Alice", "Jo"], "10:00", "11:00"),
            event("review", ["Jo", "Kim"], "11:00", "12:00"),
        ]
        verified = plan(data)["verified"]
        self.assertEqual(verified["status"], "optimal")
        moves = verified["least_disruption"]["moves"]
        self.assertEqual({move["event_id"] for move in moves}, {"design", "review"})

        data["max_moves"] = 1
        self.assertEqual(plan(data)["verified"]["status"], "infeasible_in_scope")

    def test_required_rest_inclusive_boundary(self):
        for previous_start, expected in [("09:16", "infeasible_in_scope"), ("09:15", "optimal")]:
            with self.subTest(previous_start=previous_start):
                data = fixture(["Alice"], ["Alice"], ["10:00"])
                data["events"] = [event("previous", ["Alice"], previous_start)]
                self.assertEqual(plan(data)["verified"]["status"], expected)

    def test_unverified_window_and_absent_window_differ(self):
        data = load_sample()
        data["request"]["starts"] = data["request"]["starts"][:1]
        result = plan(data)
        self.assertEqual(result["verified"]["status"], "infeasible_in_scope")
        self.assertEqual(result["optimistic"]["earliest"]["availability"], "conditional")

        del data["people"]["Dana"]["possible"]
        self.assertEqual(plan(data)["optimistic"]["status"], "infeasible_in_scope")

    def test_search_budget_never_reports_unproved_optimum(self):
        data = load_sample()
        result = plan(data, time_limit_seconds=1e-8)
        self.assertEqual(result["search_status"], "limit_reached")
        for mode in ["verified", "optimistic"]:
            self.assertIn(result[mode]["status"], {"best_found", "unknown"})

    def test_two_week_request_window_finds_sparse_availability(self):
        data = fixture(["Alice", "Jo"], ["Alice"], [])
        del data["request"]["starts"]
        data["request"]["windows"] = [[utc("09:07"), "2026-12-15T09:07:00Z"]]
        early = "2026-12-14T09:07:00Z"
        early_end = "2026-12-14T09:37:00Z"
        later = "2026-12-14T11:07:00Z"
        later_end = "2026-12-14T11:37:00Z"
        for person in data["people"].values():
            person["available"] = [[early, early_end], [later, later_end]]
        data["events"] = [{
            "id": "review", "attendees": ["Alice", "Jo"], "duration_minutes": 30,
            "start": early, "move_windows": [[later, later_end]],
        }]

        verified = plan(data)["verified"]
        self.assertEqual(verified["status"], "optimal")
        self.assert_utc_start(verified["earliest"], early)
        self.assertEqual(
            parse_time(verified["earliest"]["moves"][0]["to_start"]), parse_time(later)
        )
        self.assert_utc_start(verified["least_disruption"], later)
        self.assertEqual(verified["least_disruption"]["moves"], [])

    def test_window_end_exact_fit_and_duplicate_candidates(self):
        data = fixture(["Alice"], ["Alice"], [])
        del data["request"]["starts"]
        data["request"]["windows"] = [
            [utc("09:07"), utc("09:36")],  # 29-minute window cannot fit the call
            [utc("09:07"), utc("09:37")],  # one exact fit
            [utc("08:52"), utc("09:37")],  # two starts, one shared with the above
            [utc("09:09"), utc("09:39")],  # independent grid anchored at 09:09
        ]
        # Keep every generated request time inside the attendance window, so
        # deduplication is observed independently of availability pruning.
        data["events"] = [event("fixed", ["Alice"], "08:15")]
        data["events"].append({
            "id": "review", "attendees": ["Alice"], "duration_minutes": 30,
            "start": utc("09:07"),
            "move_windows": [
                [utc("09:07"), utc("09:37")],  # original is already represented
                [utc("11:04"), utc("11:34")],
                [utc("11:04"), utc("11:34")],
                [utc("12:00"), utc("12:29")],
            ],
        })

        result = plan(data)
        self.assertEqual(result["scope"]["request_start_count"], 3)
        best = result["verified"]["least_disruption"]
        self.assert_utc_start(best, utc("09:07"))
        self.assertEqual(parse_time(best["moves"][0]["to_start"]), parse_time(utc("11:04")))

    def test_two_ordinary_moves_outrank_one_protected_move(self):
        data = fixture(["Alice", "Bob"], ["Alice", "Bob"], ["10:00", "14:00"])
        data["events"] = [
            event("critical", ["Alice"], "10:00", "11:00", tier="protected"),
            event("sync-a", ["Alice"], "14:00", "15:00"),
            event("sync-b", ["Bob"], "14:00", "15:00"),
        ]
        verified = plan(data)["verified"]
        self.assert_utc_start(verified["least_disruption"], utc("14:00"))
        self.assertEqual(verified["least_disruption"]["metrics"]["moved_meetings"], 2)
        self.assert_utc_start(verified["earliest"], utc("10:00"))
        self.assertEqual(verified["earliest"]["metrics"]["protected_moves"], 1)


if __name__ == "__main__":
    unittest.main()
