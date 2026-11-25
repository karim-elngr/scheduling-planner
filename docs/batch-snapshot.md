# Build a roster snapshot

Run `schedule-plan batch-snapshot examples/squad-week-batch.json > week.json` to compile one dated batch into a v1.1 snapshot. The fixture includes several requests. To plan a single meeting, supply the usual `request` object in place of `requests`. For a calendar diagnosis, capture the roster and window alone. The resulting portfolio snapshot has an empty `requests` list that the host can populate after reviewing the week.

## Collect a dated window

A batch contains a half-open `window` with offset-aware timestamps, a `roster` object keyed by person ID, and a `calendars` object keyed by the same IDs. A calendar entry holds `events`, `series` and `busy_coverage`. Include all busy occurrences overlapping the read, even when their starts precede the window. Set `busy_coverage` to the intervals for which the host completed the read, including all pages. A fully read empty calendar can cover the whole window. A missing entry or omitted coverage leaves occupancy unknown.

A host with an authorized calendar reader can call the collector directly. Supply the same batch with the `calendars` key omitted.

```python
from scheduling_planner.ingestion import collect_snapshot

def read_calendar(person_id, start, end):
    # Host adapter reads all pages and returns normalized events, series and
    # the busy_coverage intervals established by that read.
    return host.read_scheduling_evidence(person_id, start, end)

snapshot = collect_snapshot(batch, read_calendar)
```

The collector invokes the reader once per person with canonical UTC instants. An OS or permission error leaves occupancy unknown for the affected person and adds a diagnostic to `metadata.ingestion_warnings`. The host can supply partial reads by returning the observed events and the intervals actually covered. The compiler clips coverage to the batch window.

## Describe working availability

Each roster member supplies an IANA `timezone` and can provide `working_hours`, a pair of local `HH:MM` times within one day. `weekend` lists local weekday names such as `["Fri", "Sat"]`. A weekly working-hours schedule defaults to a Saturday/Sunday weekend. Supply `[]` for seven working days. For split shifts or dated changes, supply explicit `available` windows. When both are supplied, the dated availability is intersected with the weekly hours. Additional plausible windows go in `possible`. Local-zone conversion applies the offset for each date in the window.

The result contains dated availability, explicit busy coverage, person metadata and one local schedule explanation per person. `rest_minutes` carries through to the solver. To verify a meeting at a read boundary, include the rest buffer in the batch and the provider read.

## Normalize provider events

Supply a provider occurrence `id`, `start` and `end` or `duration_minutes`. Timed endpoints use whole-minute timestamps with explicit offsets. An all-day event can use local dates with an exclusive end. Its timezone defaults to the calendar owner's. The compiler can only surface details returned by the authorized reader.

| Fields | Meaning |
| --- | --- |
| `host_id` | Optional stable name for the host to use in its portfolio policy, for example `harsh-1on1`. Keep the provider occurrence ID in `id`. |
| `uid`, `calendar_id`, `etag` | A shared UID unifies copies with different provider event IDs. Set `calendar_id` on the calendar feed. Each source retains its provider reference. |
| `title`, `organizer`, `squads` | Visible event facts used in displays and week diagnosis. Organizers use roster IDs when known. |
| `attendees`, `attendees_complete` | Required and optional invitees both occupy their calendars. The feed owner is included. An omitted attendee list, external invitee or an explicitly incomplete roster keeps the occurrence fixed unless all participants are resolved in another view. |
| `optional_attendees`, `tentative_attendees` | Lists of person IDs. `optional: true` and `status: "tentative"` describe the feed owner's copy. |
| `priority`, `move_tier`, `ooo` | High priority defaults to the protected move tier. An explicit tier can be ordinary or protected. Out-of-office time always stays fixed. |
| `alternatives`, `move_windows`, `step_minutes` | Authorized replacement starts or windows. **The original start is implicit.** If it appears in `alternatives`, the batch compiler removes it and adds an ingestion warning. A title or low priority alone never authorizes movement. |

A transparent, canceled or declined copy contributes no busy interval. The host should omit declined individuals from other copies' attendee lists. Matching shared occurrences merge their known attendees and source references. Conflicting times for one identity block all reported intervals and produce an ingestion warning. Distinct private busy slots need distinct occurrence IDs.

## Refer to the same ritual after compilation

Suppose the organizer labels a 1:1 with `host_id: "harsh-1on1"`. Their calendar reader supplies `id: "organizer-instance"` and `uid: "shared-occurrence"`. The coworker’s copy has `id: "guest-instance"` and the same UID. The host can send this policy with the batch:

```json
{"portfolio": {"movable_rituals": ["harsh-1on1"], "cluster": {"events": ["harsh-1on1"]}}}
```

The compiler emits one event with ID `shared-occurrence` and writes that ID into both lists. It returns the lookup in `metadata.event_id_map`:

```json
{
  "guest-instance": ["shared-occurrence"],
  "harsh-1on1": ["shared-occurrence"],
  "organizer-instance": ["shared-occurrence"],
  "shared-occurrence": ["shared-occurrence"]
}
```

Each value lists the compiled occurrence IDs matching a name within this snapshot’s window. The batch accepts host IDs, provider IDs, and compiled occurrence IDs in either event list when the name has exactly one match. If several aliases select the same event, it appears once in the compiled list. An unknown or ambiguous reference reports an error at compilation. To select a particular recurring occurrence, use the stable dated form `host_id@YYYY-MM-DD` or `master_id@YYYY-MM-DD`, where the date is the original occurrence’s local date. For unrelated events sharing an alias, use a unique source ID or assign distinct host IDs. To group an entire series, use its compiled `metadata.series_id` in `cluster.series`.

When adding policy to an already compiled snapshot, copy the desired compiled IDs from the map into `movable_rituals` and `cluster.events`. Apply bindings also use the compiled ID as their key. Obtain the organizer’s actual provider occurrence ID and current revision from the authorized read. The snapshot retains observed provider references in `events[].metadata.sources`.

The incumbent remains eligible even if every supplied alternative equals the original time. Offset-equivalent timestamps count as the same instant. When authoring a solver snapshot directly, omit the incumbent from `alternatives`; duplicate replacement instants are invalid.

## Expand local recurrences

A series has the same fields as an event, plus a `recurrence` object. Use a daily or weekly `frequency`, a positive `interval`, and optionally `count` or an inclusive local `until` date. Weekly `weekdays` uses the same day names as `weekend` and includes the anchor weekday. A local date exception is either `null` to cancel that occurrence or a partial replacement, including a new start, duration or title.

```json
{
  "id": "review-master",
  "uid": "review",
  "title": "Squad review",
  "organizer": "Amal",
  "attendees": ["Amal", "Lead"],
  "timezone": "Asia/Riyadh",
  "start": "2026-11-23T12:00+03:00",
  "duration_minutes": 30,
  "recurrence": {"frequency": "weekly", "weekdays": ["Mon", "Wed"], "until": "2027-01-31"},
  "review_date": "2027-01-15",
  "exceptions": {
    "2026-11-25": null,
    "2026-11-30": {"id": "review-nov30", "start": "2026-12-01T13:00+03:00"}
  }
}
```

The stable occurrence ID uses the shared UID or series ID and the **original** local date, for example `review@2026-11-30`. The moved occurrence keeps that identity. For provider-expanded instances, supply the shared `series_id` and `original_date` to match this identity. For other recurrence patterns, the host can pass its provider's dated instances directly in `events`. Timed series repeat at the same local clock time through offset changes, with a fixed elapsed duration. All-day series preserve calendar-day length. Supply a dated exception when a local start falls in a daylight-saving gap. Applying a move requires the organizer's current occurrence ID and revision.
