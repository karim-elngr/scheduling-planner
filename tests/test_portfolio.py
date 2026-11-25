import itertools
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from scheduling_planner.domain import snapshot_digest
from scheduling_planner.portfolio import solve_portfolio


BASE = datetime(2026, 11, 23, 9, tzinfo=timezone.utc)  # Monday 09:00 UTC


def at(offset):
    return (BASE + timedelta(minutes=offset)).isoformat(timespec="minutes").replace("+00:00", "Z")


def person(*windows, rest=0):
    ranges = [[at(lo), at(hi)] for lo, hi in windows]
    return {"available": ranges, "busy_coverage": ranges, "rest_minutes": rest}


def request(mid, starts, attendees=("a",), duration=30, **extra):
    return dict(id=mid, attendees=list(attendees), starts=list(map(at, starts)), duration_minutes=duration, **extra)


def event(mid, start, *alternatives, duration=30, **extra):
    return dict(id=mid, attendees=["a"], start=at(start), alternatives=list(map(at, alternatives)),
                duration_minutes=duration, **extra)


class PortfolioTests(unittest.TestCase):
    def test_joint_allocation_and_representative_respect_verified_evidence(self):
        data = {"schema_version": "1.1", "people": {"a": person((0, 120)), "b": person((0, 120)),
                                                     "c": person((0, 120))},
                "requests": [request("forum", [0, 30], representatives=["b", "c"]), request("must", [0])]}
        data["people"]["b"]["busy_coverage"] = []
        result = solve_portfolio(data)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertEqual([(r["id"], r["start"]) for r in result["placements"]], [("forum", at(30)), ("must", at(0))])
        forum = result["placements"][0]
        self.assertEqual(forum["attendees"], ["a", "c"])
        self.assertEqual(forum["representative"], "c")
        self.assertEqual(result["snapshot_sha256"], snapshot_digest(data))

        data["people"]["a"]["rest_minutes"] = 1
        self.assertEqual(solve_portfolio(data)["status"], "INFEASIBLE")

    def test_cluster_vs_best_scattered_focus(self):
        days = (0, 1440, 2880)
        data = {"people": {"a": person(*[(d, d + 9 * 60) for d in days])},
                "requests": [request(mid, [d + 240 + i * 30 for d in days])
                             for i, mid in enumerate(("design", "ops", "research"))],
                "portfolio": {"cluster": {"requests": ["design", "ops", "research"], "after": "13:00",
                                         "before": "18:00"}}}
        result = solve_portfolio(data)
        self.assertEqual(result["metrics"]["cluster_days"], 1)
        self.assertEqual(result["metrics"]["cluster_span_minutes"], 90)
        scatter = result["scatter_comparison"]
        self.assertEqual(scatter["metrics"]["cluster_days"], 3)
        self.assertEqual(scatter["days_saved"], 2)
        self.assertGreater(scatter["focus_minutes_gained"], 0)
        self.assertTrue(scatter["proved"])
        quick = solve_portfolio(data, proof_lite=True)
        self.assertEqual(quick["status"], "FEASIBLE")
        self.assertFalse(quick["scatter_comparison"]["proved"])

    def test_cluster_named_incumbent_series_with_new_request(self):
        data = {"people": {"a": person((0, 480), (1440, 1920))}, "max_moves": 1,
                "requests": [request("new", [240])],
                "events": [event("instance", 1680, 270, metadata={"series_id": "one-to-ones", "role": "align"})],
                "portfolio": {"cluster": {"requests": ["new"], "series": ["one-to-ones"], "after": "13:00"},
                              "movable_rituals": ["instance"]}}
        result = solve_portfolio(data)
        self.assertEqual(result["metrics"]["cluster_days"], 1)
        self.assertEqual(result["moves"][0]["id"], "instance")
        self.assertEqual(result["scatter_comparison"]["days_saved"], 1)
        data["portfolio"]["week_template"] = [{"role": "align", "weekdays": [1], "mode": "hard"}]
        result = solve_portfolio(data)
        self.assertEqual(result["metrics"]["cluster_days"], 2)
        self.assertEqual(result["moves"], [])

    def test_overnight_meeting_must_clear_entire_template_and_cluster(self):
        data = {"people": {"a": person((0, 1080))}, "requests": [request("late", [870], duration=120, role="align")],
                "portfolio": {"cluster": {"requests": ["late"]},
                              "week_template": [{"role": "align", "weekdays": [0], "mode": "hard"}]}}
        self.assertEqual(solve_portfolio(data)["status"], "INFEASIBLE")
        data["portfolio"]["week_template"][0]["mode"] = "soft"
        result = solve_portfolio(data)
        self.assertEqual(result["metrics"]["preference_penalty"], 1)
        self.assertEqual(result["metrics"]["cluster_days"], 2)

    def test_unversioned_portfolio_requires_observed_busy_feed(self):
        data = {"people": {"a": {"available": [[at(0), at(60)]]}}, "requests": [request("new", [0])]}
        self.assertEqual(solve_portfolio(data)["status"], "INFEASIBLE")
        data["evidence_policy"] = "legacy"
        self.assertEqual(solve_portfolio(data)["status"], "OPTIMAL")

    def test_focus_and_fragmentation_match_exhaustive_oracle_on_irregular_grids(self):
        starts = ([5, 33, 73], [12, 31, 93], [0, 47, 69])
        durations = (17, 28, 16)
        data = {"people": {"a": person((0, 140))},
                "requests": [request(str(i), options, duration=durations[i]) for i, options in enumerate(starts)],
                "events": [event("fixed", 115, duration=10)]}
        candidates = []
        for times in itertools.product(*starts):
            intervals = sorted([*(zip(times, [s + d for s, d in zip(times, durations, strict=True)], strict=True)),
                                (115, 125)])
            if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:], strict=False)):
                continue
            gaps = [b - a for a, b in zip([0, *(y for _, y in intervals)], [*(x for x, _ in intervals), 140],
                                           strict=True) if a < b]
            candidates.append((-max(gaps), len(gaps), sum(times) - sum(min(s) for s in starts)))
        result = solve_portfolio(data)
        self.assertEqual(result["status"], "OPTIMAL")
        m = result["metrics"]
        self.assertEqual((-m["focus_minutes"], m["free_blocks"], m["start_offset_minutes"]), min(candidates))

    def test_stage_budgets_prove_gains_and_enforce_named_rituals(self):
        data = {"schema_version": "1.1", "max_moves": 2, "people": {"a": person((0, 180))}, "requests": [],
                "events": [event("sync", 30, 0), event("review", 90, 30), event("client", 150, 60)],
                "portfolio": {"movable_rituals": ["sync", "review"]}}
        result = solve_portfolio(data, compare_moves=True)
        self.assertEqual([r["budget"] for r in result["stages"]], [0, 1, 2])
        self.assertEqual([r["metrics"]["focus_minutes"] for r in result["stages"]], [30, 60, 90])
        self.assertEqual([m["id"] for m in result["moves"]], ["review", "sync"])
        self.assertTrue(result["stages"][2]["gain_proved"])
        self.assertEqual(result["stages"][1]["focus_minutes_gained"], 30)
        # Events may originally overlap. A new request cannot double-book the incumbents.
        data["events"][2]["start"] = at(30)
        data["portfolio"]["movable_rituals"] = []
        data["requests"] = [request("new", [30, 120])]
        self.assertEqual(solve_portfolio(data)["placements"][0]["start"], at(120))

    def test_incomplete_attendees_and_protected_meetings(self):
        data = {"people": {"a": person((0, 180))}, "requests": [], "events": [
            event("unknown", 30, 0, attendees_complete=False), event("critical", 90, 60, move_tier="protected")]}
        self.assertEqual(solve_portfolio(data)["moves"], [])

    def test_template_hard_and_soft_use_team_weekday(self):
        data = {"people": {"a": person((0, 2880))}, "requests": [request("sync", [0, 1440], role="align")],
                "portfolio": {"timezone": "Asia/Riyadh", "week_template": [
                    {"role": "align", "weekdays": [1], "mode": "hard"}]}}
        self.assertEqual(solve_portfolio(data)["placements"][0]["start"], at(1440))
        data["requests"][0]["starts"] = [at(0)]
        self.assertEqual(solve_portfolio(data)["status"], "INFEASIBLE")
        data["portfolio"]["week_template"][0]["mode"] = "soft"
        self.assertEqual(solve_portfolio(data)["metrics"]["preference_penalty"], 1)

    def test_local_clock_preferences_and_daily_cap(self):
        data = {"people": {"a": {**person((0, 1710)), "metadata": {"timezone": "Asia/Dubai"}}},
                "requests": [request("first", [0, 30, 90]), request("next", [120, 1530])],
                "portfolio": {"preferences": {"a": {"not_before": "13:30", "lunch": ["13:45", "14:30"],
                                                  "max_meetings_per_day": 1}}}}
        result = solve_portfolio(data)
        self.assertEqual([r["start"] for r in result["placements"]], [at(90), at(1530)])
        self.assertEqual(result["metrics"]["preference_penalty"], 0)

    def test_focus_counts_confirmed_minutes_and_resets_at_local_midnight(self):
        data = {"schema_version": "1.1", "people": {"a": person((0, 1440))}, "requests": []}
        data["people"]["a"]["busy_coverage"] = [[at(60), at(180)], [at(660), at(780)]]
        data["people"]["a"]["metadata"] = {"timezone": "Asia/Dubai"}  # midnight = base +660
        self.assertEqual(solve_portfolio(data)["metrics"]["focus_minutes"], 240)
        data["people"]["a"]["busy_coverage"] = [[at(0), at(60)], [at(90), at(120)]]
        self.assertEqual(solve_portfolio(data)["metrics"]["focus_minutes"], 60)

    def test_timezone_year_limits_are_validation_errors(self):
        for lo, hi, zone in [("9999-12-31T21:00Z", "9999-12-31T22:00Z", "Asia/Dubai"),
                            ("0001-01-01T00:00Z", "0001-01-01T01:00Z", "America/New_York")]:
            data = {"requests": [], "portfolio": {"timezone": zone},
                    "people": {"a": {"available": [[lo, hi]], "busy_coverage": [[lo, hi]]}}}
            with self.subTest(zone=zone), self.assertRaisesRegex(ValueError, "supported date range"):
                solve_portfolio(data)

    def test_new_constraints_and_ids_are_validated(self):
        base = {"people": {"a": person((0, 180))}, "requests": [request("x", [0])]}
        for change in [dict(constraints=[{}]), dict(events=[event("x", 0)]),
                       dict(portfolio={"cluster": {"requests": ["unknown"]}}),
                       dict(portfolio={"preferences": {"a": {"lunch": ["14:00", "13:00"]}}}),
                       dict(portfolio={"timezone": "Mars/Nowhere"})]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                solve_portfolio({**deepcopy(base), **change})
