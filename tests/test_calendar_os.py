from copy import deepcopy
import json
from pathlib import Path
import unittest

from scheduling_planner.calendar_os import diagnose_week
from scheduling_planner.ingestion import build_snapshot
from scheduling_planner.profiles import validate_profile


def week():
    windows = [[f"2026-11-{day}T09:00Z", f"2026-11-{day}T13:00Z"] for day in (23, 24, 25)]
    return {
        "schema_version": "1.1",
        "portfolio": {"timezone": "Asia/Dubai"},
        "people": {
            "ada": {"available": windows, "busy_coverage": windows, "metadata": {"squad": "payments"}},
            "bea": {"available": windows, "busy_coverage": windows, "metadata": {"squads": ["infra"]}},
        },
        "events": [
            {"id": "forum", "start": "2026-11-23T10:00Z", "duration_minutes": 60,
             "attendees": ["ada", "bea"], "metadata": {"organizer": "ada", "series_id": "forum",
                    "optional_attendees": ["bea"], "tentative_attendees": ["bea"]}},
            {"id": "check-in", "start": "2026-11-23T10:30Z", "duration_minutes": 60,
             "attendees": ["ada"], "metadata": {"organizer": "ada", "series_id": "check-in",
                                                 "review_date": "2027-01-01"}},
            {"id": "private", "start": "2026-11-24T10:00Z", "duration_minutes": 15,
             "attendees": ["bea"]},
        ],
    }


class CalendarOsTests(unittest.TestCase):
    def test_accounts_for_overlaps_and_organizer_evidence(self):
        snapshot = week()
        original = deepcopy(snapshot)
        report = diagnose_week(snapshot)
        self.assertEqual(snapshot, original)
        ada = report["people"]["ada"]
        self.assertEqual(ada["organized_minutes"], 120)
        self.assertEqual(ada["days"]["2026-11-23"], {
            "eligible_minutes": 240, "covered_work_minutes": 240, "free_minutes": 150,
            "busy_minutes": 90, "free_blocks": 2, "longest_free_minutes": 90})
        self.assertEqual(report["days"][0]["busy_minutes"], 150)
        self.assertEqual(report["people"]["bea"]["invited_minutes"], 60)
        self.assertEqual(report["people"]["bea"]["unknown_organizer_minutes"], 15)

    def test_scores_and_sunset_are_grounded_in_input(self):
        snapshot = week()
        snapshot["events"].append(dict(snapshot["events"][0], id="forum-2", start="2026-11-24T10:00Z"))
        report = diagnose_week(snapshot)
        self.assertEqual(len(report["invite_hygiene"]), 2)
        bea = report["invite_hygiene"][0]
        self.assertEqual(bea["signals"], ["optional", "tentative", "multi_squad"])
        self.assertEqual(bea["score"], 3)
        self.assertIn("delegate", bea["suggestion"])
        self.assertEqual(len(report["recurring_sunsets"]), 1)
        self.assertEqual(report["recurring_sunsets"][0]["occurrences"], ["forum", "forum-2"])
        for key in ("review_date", "renewal_date", "until"):
            with self.subTest(key=key):
                updated = deepcopy(snapshot)
                updated["events"][-1]["metadata"][key] = "2026-12-31"
                self.assertEqual(diagnose_week(updated)["recurring_sunsets"], [])

    def test_diagnoses_finite_series_and_pending_review_date_from_batch(self):
        window = ["2026-11-23T00:00Z", "2026-11-26T00:00Z"]
        series = {"start": "2026-11-23T12:00Z", "duration_minutes": 30,
                  "attendees": ["ada"], "organizer": "ada"}
        batch = {"window": window, "roster": {"ada": {"timezone": "UTC", "available": [window]}},
                 "calendars": {"ada": {"busy_coverage": [window], "series": [
                     dict(series, id="launch", recurrence={"frequency": "daily", "count": 2}),
                     dict(series, id="sync", review_date="TBD", recurrence={"frequency": "weekly"})]}}}
        snapshot = build_snapshot(batch)
        report = diagnose_week(snapshot)
        self.assertEqual(report["people"]["ada"]["organized_minutes"], 90)
        self.assertEqual(report["calendar_explainers"], snapshot["metadata"]["calendar_explainers"])
        self.assertEqual([s["series_id"] for s in report["recurring_sunsets"]], ["sync"])

    def test_decline_review_never_targets_owner_or_out_of_office(self):
        snapshot = week()
        snapshot["people"]["bea"]["metadata"] = {"squad": "payments"}
        snapshot["events"][1]["metadata"]["ooo"] = True
        report = diagnose_week(snapshot)
        self.assertEqual(report["people"]["ada"]["ooo_minutes"], 60)
        self.assertEqual(report["people"]["ada"]["organized_minutes"], 60)
        self.assertEqual(len(report["invite_hygiene"]), 1)
        self.assertIn("decline", report["invite_hygiene"][0]["suggestion"])

    def test_proposes_soft_template_from_observed_weekdays(self):
        report = diagnose_week(week())
        self.assertEqual(report["template_proposal"]["week_template"], [
            {"role": "maker", "weekdays": [2], "mode": "soft"},
            {"role": "align", "weekdays": [0], "mode": "soft"},
            {"role": "one_on_one", "weekdays": [1], "mode": "soft"},
        ])
        snapshot = week()
        for person in snapshot["people"].values():
            del person["busy_coverage"]
        report = diagnose_week(snapshot)
        self.assertEqual(report["template_proposal"]["week_template"], [])
        for row in report["days"]:
            self.assertEqual(row["eligible_minutes"], 480)
            self.assertEqual(row["covered_work_minutes"], 0)
            self.assertEqual(row["free_minutes"], 0)

    def test_tiny_read_does_not_look_like_a_free_maker_day(self):
        snapshot = week()
        for person in snapshot["people"].values():
            person["busy_coverage"] = [["2026-11-23T09:00Z", "2026-11-23T09:01Z"],
                                       ["2026-11-24T09:00Z", "2026-11-24T13:00Z"]]
        maker = diagnose_week(snapshot)["template_proposal"]["week_template"][0]
        self.assertEqual(maker["weekdays"], [1])

    def test_partial_coverage_and_cross_midnight_local_days(self):
        snapshot = {"people": {"a": {"available": [["2026-11-24T19:00Z", "2026-11-24T21:00Z"]],
                                     "busy_coverage": [["2026-11-24T19:30Z", "2026-11-24T20:30Z"]]}},
                    "portfolio": {"timezone": "Asia/Dubai"}}
        days = diagnose_week(snapshot)["days"]
        self.assertEqual([d["date"] for d in days], ["2026-11-24", "2026-11-25"])
        self.assertEqual([d["sum_longest_free_minutes"] for d in days], [30, 30])
        self.assertEqual([d["eligible_minutes"] for d in days], [60, 60])

    def test_clips_load_and_eligible_time_to_batch_window(self):
        snapshot = week()
        snapshot["metadata"] = {"window": ["2026-11-23T10:45Z", "2026-11-23T11:15Z"]}
        report = diagnose_week(snapshot)
        self.assertEqual(report["people"]["ada"]["organized_minutes"], 45)
        self.assertEqual(report["people"]["bea"]["invited_minutes"], 15)
        self.assertEqual(report["people"]["bea"]["unknown_organizer_minutes"], 0)
        self.assertEqual([d["covered_work_minutes"] for d in report["days"]], [60])

    def test_person_level_tentative_response_takes_precedence(self):
        snapshot = week()
        snapshot["events"][0]["metadata"].update(status="tentative", tentative_attendees=["ada"])
        report = diagnose_week(snapshot)
        self.assertNotIn("tentative", report["invite_hygiene"][0]["signals"])

    def test_fall_back_day_records_elapsed_minutes(self):
        windows = [["2026-11-01T04:00Z", "2026-11-02T05:00Z"]]
        report = diagnose_week({"portfolio": {"timezone": "America/New_York"},
                                "people": {"a": {"available": windows, "busy_coverage": windows}}})
        self.assertEqual(len(report["days"]), 1)
        self.assertEqual(report["days"][0]["covered_work_minutes"], 25 * 60)

    def test_profile_gallery_validates(self):
        gallery = Path(__file__).resolve().parents[1] / "examples" / "profiles"
        for name in ("mixed-timezones", "launch-week", "steady-state"):
            with self.subTest(name=name):
                profile = validate_profile(json.loads((gallery / f"{name}.json").read_text()))
                self.assertIn("objective", profile)
                self.assertIn("gather", profile["guidance"])
