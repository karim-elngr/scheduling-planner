import unittest

from scheduling_planner.domain import parse_snapshot
from scheduling_planner.ingestion import build_snapshot, collect_snapshot


def batch():
    window = ["2026-11-25T00:00Z", "2026-12-01T00:00Z"]
    return {
        "window": window,
        "roster": {
            "A": {"timezone": "Asia/Dubai", "working_hours": ["10:00", "17:00"],
                  "weekend": ["Sat", "Sun"]},
            "B": {"timezone": "Asia/Riyadh", "working_hours": ["09:00", "16:00"],
                  "weekend": ["Fri", "Sat"]},
        },
        "calendars": {"A": {"busy_coverage": [window]}, "B": {"busy_coverage": [window]}},
        "request": {"starts": ["2026-11-27T06:00Z"], "attendees": ["A", "B"], "duration_minutes": 30},
    }


class BatchEvidence(unittest.TestCase):
    def test_distinct_local_hours_weekends_and_explicit_coverage(self):
        data = batch()
        del data["calendars"]["B"]
        result = build_snapshot(data)
        parse_snapshot(result)
        a, b = result["people"]["A"], result["people"]["B"]
        self.assertIn(["2026-11-27T06:00Z", "2026-11-27T13:00Z"], a["available"])
        self.assertFalse(any(p[0].startswith("2026-11-27") for p in b["available"]))
        self.assertIn(["2026-11-29T06:00Z", "2026-11-29T13:00Z"], b["available"])
        self.assertEqual(b["busy_coverage"], [])
        self.assertIn("Fri, Sat", result["metadata"]["calendar_explainers"][1])
        self.assertIn("Asia/Riyadh", result["metadata"]["calendar_explainers"][1])

    def test_shared_uid_merges_privacy_and_tracks_source_without_inventing_facts(self):
        data = batch()
        common = {"uid": "ritual", "start": "2026-11-25T09:00Z", "duration_minutes": 30}
        data["calendars"]["A"]["events"] = [{**common, "id": "masked", "title": "Busy"}] * 2
        data["calendars"]["B"]["events"] = [
            {**common, "id": "b-provider", "title": "Weekly review", "attendees": ["B", "A"],
             "optional_attendees": ["A"], "organizer": "B", "priority": "high",
             "alternatives": ["2026-11-25T10:00Z"], "etag": "revision"}]
        event, = build_snapshot(data)["events"]
        self.assertTrue(event["attendees_complete"])
        self.assertEqual(event["attendees"], ["A", "B"])
        self.assertEqual(event["move_tier"], "protected")
        self.assertEqual(event["alternatives"], ["2026-11-25T10:00Z"])
        self.assertEqual(event["metadata"]["title"], "Weekly review")
        self.assertEqual(event["metadata"]["organizer"], "B")
        self.assertEqual(event["metadata"]["optional_attendees"], ["A"])
        self.assertEqual(event["metadata"]["sources"], [
            {"calendar_id": "A", "event_id": "masked"},
            {"calendar_id": "B", "event_id": "b-provider", "etag": "revision"}])

    def test_conflicting_organizers_keep_the_shared_time_fixed(self):
        data = batch()
        common = {"id": "review", "start": "2026-11-25T09:00Z", "duration_minutes": 30,
                  "attendees": ["A", "B"], "alternatives": ["2026-11-25T12:00Z"]}
        for person in ("A", "B"):
            data["calendars"][person]["events"] = [{**common, "organizer": person}]
        snapshot = build_snapshot(data)
        event, = snapshot["events"]
        self.assertNotIn("organizer", event["metadata"])
        self.assertNotIn("alternatives", event)
        self.assertTrue(snapshot["metadata"]["ingestion_warnings"])

    def test_conflicting_duplicate_placements_remain_blocking(self):
        data = batch()
        shared = {"id": "duplicate", "start": "2026-11-25T11:00Z", "duration_minutes": 30,
                  "alternatives": ["2026-11-25T12:00Z"]}
        data["calendars"]["A"]["events"] = [{**shared, "attendees": ["A"]}]
        data["calendars"]["B"]["events"] = [{**shared, "attendees": ["B"], "duration_minutes": 60}]
        result = build_snapshot(data)
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(result["metadata"]["ingestion_warnings"])
        for event in result["events"]:
            self.assertFalse(event["attendees_complete"])
            self.assertNotIn("alternatives", event)
            self.assertEqual(event["attendees"], ["A", "B"])

    def test_all_day_ooo_is_fixed_and_external_attendees_prevent_moves(self):
        data = batch()
        data["calendars"]["B"]["events"] = [
            {"id": "ooo", "ooo": True, "start": "2026-11-25", "end": "2026-11-26",
             "attendees": ["B"], "alternatives": ["2026-11-26T06:00Z"]},
            {"id": "external", "start": "2026-11-25T14:00Z", "duration_minutes": 30,
             "attendees": ["B", "outside"], "alternatives": ["2026-11-26T06:00Z"]}]
        external, ooo = build_snapshot(data)["events"]
        self.assertTrue(ooo["metadata"]["ooo"])
        self.assertEqual(ooo["move_tier"], "protected")
        self.assertEqual(ooo["start"], "2026-11-24T21:00Z")
        self.assertEqual(ooo["duration_minutes"], 1440)
        self.assertNotIn("alternatives", ooo)
        self.assertFalse(external["attendees_complete"])
        self.assertNotIn("alternatives", external)

    def test_reader_failure_and_available_empty_feed_are_distinct(self):
        data = batch()
        del data["calendars"]
        reads = []

        def reader(pid, start, end):
            reads.append((pid, start, end))
            if pid == "B":
                raise PermissionError
            return {"events": [], "busy_coverage": [[start, end]]}

        result = collect_snapshot(data, reader)
        self.assertEqual(reads, [(pid, *data["window"]) for pid in ("A", "B")])
        self.assertEqual(result["people"]["A"]["busy_coverage"], [data["window"]])
        self.assertEqual(result["people"]["B"]["busy_coverage"], [])
        self.assertIn("PermissionError", result["metadata"]["ingestion_warnings"][0])

    def test_diagnosis_can_capture_before_planning_requests_exist(self):
        data = batch()
        del data["request"]
        self.assertEqual(build_snapshot(data)["requests"], [])

    def test_explicit_weekend_at_last_supported_date(self):
        window = ["9999-12-31T02:00Z", "9999-12-31T15:00Z"]
        roster = {"A": {"timezone": "UTC", "weekend": [], "available": [window]}}
        result = build_snapshot({"window": window, "roster": roster})
        self.assertEqual(result["people"]["A"]["available"], [window])

    def test_person_dates_and_coverage_are_clipped_to_query(self):
        data = batch()
        data["roster"]["B"]["available"] = [["2026-11-25T05:00Z", "2026-11-25T08:00Z"]]
        data["calendars"]["B"]["busy_coverage"] = [["2026-11-24T00:00Z", "2026-12-02T00:00Z"]]
        b = build_snapshot(data)["people"]["B"]
        self.assertEqual(b["available"], [["2026-11-25T06:00Z", "2026-11-25T08:00Z"]])
        self.assertEqual(b["busy_coverage"], [data["window"]])


class RecurringEvidence(unittest.TestCase):
    def test_exceptions_cancel_replace_and_move_into_observed_window(self):
        data = batch()
        data["calendars"]["A"]["series"] = [{
            "id": "check", "start": "2026-11-17T12:00+04:00", "duration_minutes": 60,
            "timezone": "Asia/Dubai", "recurrence": {"frequency": "weekly", "weekdays": ["Tue", "Thu"]},
            "exceptions": {"2026-11-24": {"start": "2026-11-25T09:00Z", "duration_minutes": 30},
                           "2026-11-26": None},
        }]
        event, = build_snapshot(data)["events"]
        self.assertEqual(event["id"], "check@2026-11-24")
        self.assertEqual(event["start"], "2026-11-25T09:00Z")
        self.assertEqual(event["duration_minutes"], 30)
        self.assertEqual(event["metadata"]["series_id"], "check")
        self.assertEqual(event["metadata"]["sources"], [
            {"calendar_id": "A", "series_id": "check", "original_date": "2026-11-24"}])

    def test_local_wall_clock_across_dst_and_finite_weekly_rule(self):
        data = batch()
        data["window"] = ["2026-10-15T00:00Z", "2026-12-01T00:00Z"]
        data["calendars"]["A"]["series"] = [{
            "id": "sync", "start": "2026-10-22T10:00-04:00", "duration_minutes": 30,
            "timezone": "America/New_York",
            "recurrence": {"frequency": "weekly", "interval": 2, "weekdays": ["Tue", "Thu"], "count": 3},
        }]
        events = build_snapshot(data)["events"]
        self.assertEqual([e["start"] for e in events],
                         ["2026-10-22T14:00Z", "2026-11-03T15:00Z", "2026-11-05T15:00Z"])

    def test_daily_repeat_until_and_all_day_dst(self):
        data = batch()
        data["window"] = ["2026-10-31T00:00Z", "2026-11-03T00:00Z"]
        data["calendars"]["A"]["series"] = [{
            "id": "away", "start": "2026-10-31", "end": "2026-11-01", "timezone": "America/New_York",
            "recurrence": {"frequency": "daily", "until": "2026-11-01"}, "ooo": True,
        }]
        first, second = build_snapshot(data)["events"]
        self.assertEqual(first["duration_minutes"], 1440)
        self.assertEqual(second["duration_minutes"], 1500)
        self.assertEqual(second["metadata"]["until"], "2026-11-01")

    def test_all_day_recurrence_rejects_unrepresentable_end(self):
        event = {"id": "away", "start": "9999-12-30", "end": "9999-12-31",
                 "recurrence": {"frequency": "daily"}}
        data = {"roster": {"A": {"timezone": "UTC"}},
                "window": ["9999-12-31T00:00Z", "9999-12-31T23:59Z"],
                "calendars": {"A": {"series": [event]}}}
        with self.assertRaisesRegex(ValueError, "supported date range"):
            build_snapshot(data)

    def test_invalid_weekday_or_unsupported_rule_fails_clearly(self):
        data = batch()
        record = {"id": "x", "start": "2026-11-25T10:00Z", "duration_minutes": 30,
                  "recurrence": {"frequency": "weekly", "weekdays": ["Sun"]}}
        data["calendars"]["A"]["series"] = [record]
        with self.assertRaisesRegex(ValueError, "recurrence weekdays"):
            build_snapshot(data)
        record["recurrence"] = {"frequency": "yearly"}
        with self.assertRaisesRegex(ValueError, "frequency"):
            build_snapshot(data)
