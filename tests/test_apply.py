"""Calendar apply against an in-memory Google Calendar transport (no OAuth or network)."""
from copy import deepcopy
import unittest
from urllib.parse import unquote

from scheduling_planner.apply import _apply, _CalendarError, prepare_apply
from scheduling_planner.domain import snapshot_digest


HOST = "host@example.org"
GUEST = "guest@example.org"
OBSERVER = "observer@example.org"


def stamp(hour):
    return f"2026-12-01T{hour}:00Z"


def inputs():
    snapshot = {"people": {"host": {"rest_minutes": 15}, "guest": {}},
                "request": {"attendees": ["host", "guest"], "duration_minutes": 60},
                "events": [{"id": "ritual", "attendees": ["host", "guest"],
                            "start": stamp(10), "duration_minutes": 60}]}
    report = {"view": "brief", "scope": {"snapshot_sha256": snapshot_digest(snapshot)},
              "primary_id": "choice", "options": [{"id": "choice", "availability": "verified",
              "start": stamp(10), "end": stamp(11), "moves": [{"event_id": "ritual",
               "from_start": stamp(10), "to_start": stamp(13), "to_end": stamp(14)}]}]}
    bindings = {"organizer": HOST, "people": {"host": HOST, "guest": GUEST, "observer": OBSERVER},
                "requests": {"$request": {"title": "Group meeting", "optional_attendees": ["observer"]}},
                "events": {"ritual": {"etag": '"revision"', "event_id": "occurrence"}}}
    return snapshot, report, bindings


class Calendar:
    def __init__(self):
        self.account = HOST
        self.events = {"occurrence": {"id": "occurrence", "iCalUID": "ritual", "etag": '"revision"',
                       "organizer": {"self": True, "email": HOST}, "attendees": [{"email": GUEST}],
                       "originalStartTime": {"dateTime": stamp(10)}, "recurringEventId": "master",
                       "start": {"dateTime": stamp(10)}, "end": {"dateTime": stamp(11)}}}
        self.busy = []
        self.writes = []
        self.reads = []
        self.lose_create_response = False
        self.role = "reader"

    def request(self, method, path, *, query=None, body=None, etag=None):
        if method == "GET":
            self.reads.append((path, deepcopy(query)))
            if path == "/calendars/primary":
                return {"id": self.account}
            if path.endswith("/events"):
                # Return shared copies and injected conflicts on a second page.
                if "pageToken" not in query:
                    return {"accessRole": self.role, "items": [], "nextPageToken": "page2"}
                return {"accessRole": self.role, "items": list(self.events.values()) + self.busy}
            key = unquote(path.rsplit("/", 1)[1])
            if key not in self.events:
                raise _CalendarError(404, method)
            return self.events[key]
        self.writes.append((method, path, deepcopy(body)))
        assert query == {"sendUpdates": "all"}
        if method == "POST":
            self.events[body["id"]] = {**body, "iCalUID": "new", "organizer": {"self": True, "email": HOST}}
            if self.lose_create_response:
                raise TimeoutError("write response lost")
        else:
            assert method == "PATCH"
            assert etag == self.events["occurrence"]["etag"]
            self.events["occurrence"].update(**body, etag='"new"')


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.args = inputs()
        self.manifest = prepare_apply(*self.args)
        self.provider = Calendar()

    def apply(self):
        return _apply(self.manifest, self.manifest["approval_sha256"], self.provider)

    def test_verified_exact_snapshot_and_approval(self):
        with self.assertRaisesRegex(ValueError, "exact manifest approval"):
            _apply(self.manifest, None, self.provider)
        self.manifest["operations"][0]["start"] = stamp(15)
        with self.assertRaisesRegex(ValueError, "exact manifest approval"):
            self.apply()
        self.assertEqual(self.provider.reads, [])
        self.args[0]["request"]["duration_minutes"] = 30
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare_apply(*self.args)
        self.args[1]["options"][0]["availability"] = "conditional"
        with self.assertRaisesRegex(ValueError, "verified"):
            prepare_apply(*self.args)

    def test_profile_injected_objective_matches_only_the_original_evidence(self):
        snapshot, report, bindings = self.args
        report["scope"]["objective"] = ["start", "move_count"]
        report["scope"]["snapshot_sha256"] = snapshot_digest(dict(snapshot, objective=report["scope"]["objective"]))
        self.assertEqual(prepare_apply(snapshot, report, bindings)["snapshot_sha256"], report["scope"]["snapshot_sha256"])
        snapshot["request"]["duration_minutes"] = 30
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare_apply(snapshot, report, bindings)

    def test_partial_completion_is_reconciled_without_duplicate_invites(self):
        self.provider.lose_create_response = True
        interrupted = self.apply()
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual([r["action"] for r in interrupted["completed"]], ["move"])
        self.assertEqual([w[0] for w in self.provider.writes], ["PATCH", "POST"])
        self.assertEqual(self.provider.writes[1][2]["attendees"][-1], {"email": OBSERVER, "optional": True})
        self.assertNotIn("attendees", self.provider.writes[0][2])
        self.assertEqual(self.provider.writes[1][2]["start"]["dateTime"], "2026-12-01T10:00:00+00:00")
        self.assertIn("2026-12-01T09:45:00+00:00", [q["timeMin"] for _, q in self.provider.reads if q])
        done = self.apply()
        self.assertEqual(done["status"], "applied")
        self.assertEqual([r["action"] for r in done["completed"]], ["move", "create"])
        self.assertEqual(len(self.provider.writes), 2)
        self.provider.events["occurrence"]["start"]["dateTime"] = stamp(12)
        self.assertEqual(self.apply()["status"], "interrupted")
        self.assertEqual(len(self.provider.writes), 2)

    def test_only_owner_with_fresh_occurrence_and_complete_calendar_access(self):
        for change in ("login", "organizer", "etag", "roster", "series", "visibility", "other-instance"):
            with self.subTest(change=change):
                self.provider = Calendar()
                event = self.provider.events["occurrence"]
                if change == "login":
                    self.provider.account = GUEST
                elif change == "organizer":
                    event["organizer"]["self"] = False
                elif change == "etag":
                    event["etag"] = "changed"
                elif change == "roster":
                    event["attendees"].append({"email": OBSERVER})
                elif change == "series":
                    event["recurrence"] = ["RRULE:FREQ=WEEKLY"]
                elif change == "visibility":
                    self.provider.role = "freeBusyReader"
                else:
                    self.provider.busy.append(dict(event, originalStartTime={"dateTime": stamp(15)}))
                self.assertEqual(self.apply()["status"], "interrupted")
                self.assertEqual(self.provider.writes, [])

    def test_optional_attendee_patch_preserves_existing_responses(self):
        self.args[2]["events"]["ritual"]["optional_attendees"] = ["observer"]
        self.manifest = prepare_apply(*self.args)
        self.provider.events["occurrence"]["attendees"][0]["responseStatus"] = "accepted"
        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(self.provider.writes[0][2]["attendees"],
                         [{"email": GUEST, "responseStatus": "accepted"}, {"email": OBSERVER, "optional": True}])
        self.assertEqual(self.apply()["status"], "applied")
        self.assertEqual(len(self.provider.writes), 2)

    def test_portfolio_invites_selected_representative(self):
        snapshot, _, bindings = self.args
        snapshot["requests"] = [dict(snapshot.pop("request"), id="forum")]
        report = {"kind": "portfolio", "status": "FEASIBLE", "snapshot_sha256": snapshot_digest(snapshot),
                  "placements": [{"id": "forum", "start": stamp(10), "end": stamp(11),
                                  "attendees": ["guest"], "representative": "guest"}], "moves": []}
        bindings["requests"]["forum"] = {"title": "Forum"}
        manifest = prepare_apply(snapshot, report, bindings)
        self.assertEqual(manifest["operations"][0]["rest_minutes"], {GUEST: 0})

    def test_organizer_outside_roster_is_checked_for_rest_and_overlapping_placements(self):
        snapshot, _, bindings = self.args
        snapshot["requests"] = [dict(snapshot.pop("request"), id="one"), {"id": "two", "duration_minutes": 60}]
        report = {"kind": "portfolio", "status": "OPTIMAL", "snapshot_sha256": snapshot_digest(snapshot),
                  "placements": [{"id": "one", "start": stamp(10), "end": stamp(11), "attendees": ["guest"]}],
                  "moves": []}
        bindings["requests"].update(one={"title": "One"}, two={"title": "Two"})
        manifest = prepare_apply(snapshot, report, bindings)
        self.provider.events.clear()
        _apply(manifest, manifest["approval_sha256"], self.provider)
        owner_checks = [q for path, q in self.provider.reads if path == "/calendars/host%40example.org/events"]
        self.assertEqual(owner_checks[0]["timeMin"], "2026-12-01T09:45:00+00:00")
        report["placements"].append({"id": "two", "start": stamp(11), "end": stamp(12), "attendees": []})
        with self.assertRaisesRegex(ValueError, "rest on host@example.org"):
            prepare_apply(snapshot, report, bindings)
