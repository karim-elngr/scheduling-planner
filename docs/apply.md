# Apply an approved plan

The Google Calendar adapter books timed meetings on the signed-in organizer's primary calendar. It can create invitations, reschedule individual occurrences, and add guests as optional. Invites and move notifications go to all guests via Google's `sendUpdates=all`.

## Prepare the organizer's review

Save the verified brief, full advice or feasible portfolio output and its source snapshot. Resolve names against the host's authorized directory. In a separate bindings file, map the exact people to their calendar email addresses and the exact moves to Google **occurrence** IDs and ETags from the organizer's calendar. A recurring master ID would change the series and is rejected. Use the ETag observed when gathering the evidence used to plan the move. Refreshing it to bypass a mismatch would discard the concurrency check.

For example, this file binds a new group meeting and an existing review to its organizer's account. `optional_attendees` accepts roster IDs in the `people` map, including an additional optional guest. For a move, the adapter preserves existing guests and their response states and sets the named guests to optional.

```json
{
  "organizer": "asha@example.org",
  "people": {
    "Asha": "asha@example.org",
    "Eli": "eli@example.org",
    "Noor": "noor@example.org"
  },
  "requests": {
    "$request": {"title": "Group planning", "optional_attendees": ["Noor"]}
  },
  "events": {
    "review": {"event_id": "google-occurrence-id", "etag": "\"provider-etag\""}
  }
}
```

Include entries for every participant and every moved occurrence in the selected plan. For a portfolio, key `requests` by each placed request's ID. The selected representative receives the invitation. For a single meeting, use `$request` and optionally select a specific verified option with `--option-id`. Preparing a portfolio uses its top-level placements and moves. If a comparison stage is preferred, solve again with its move cap and prepare that result.

```sh
schedule-plan prepare-apply brief.json --snapshot snapshot.json --bindings bindings.json > review.json
```

Show the organizer the operations in `review.json`, including the organizer, people, times, optional guests, occurrence IDs and notification setting. The manifest includes a SHA-256 digest of the exact batch. Record the organizer's explicit approval of this digest before passing it to the executor. A digest identifies content. The host is responsible for obtaining consent from the authenticated organizer.

## Rehearse with a read-only calendar

For an organizer's first real-calendar rehearsal, use the host's authorized read-only calendar connection and finish at the local manifest. A schedule that recommends keeping all incumbents and has no new requests has no operations to prepare. Record that outcome as the proposal.

- [ ] Identify the organizer's primary calendar and the dated window. For each proposed move, find its compiled ID in `plan.json` and the matching `events[].metadata.sources` in the snapshot. Resolve the **organizer's occurrence copy** to its Google event ID and ETag, including the original instance date for a recurring meeting. Put the compiled ID in the bindings key. Put the provider event ID and captured ETag in its value.
- [ ] Read the organizer's current occurrence and compare ownership, original start and duration, guests, optional status and ETag with the captured evidence. With the host's read connection, check destination intervals and rest on each affected calendar, including the organizing calendar. Follow pagination. If ownership, revisions, attendance or availability changed, refresh the evidence and solve again.
- [ ] Run the local preparation command below for the chosen portfolio. It reads the three JSON files and computes `review.json` without calling Google. The host reads above supply the live checks for this rehearsal.

```sh
schedule-plan prepare-apply local/plan.json --snapshot local/week.json --bindings local/bindings.json > local/review.json
```

- [ ] Compare each manifest operation with the selected proposal in the organizer's timezone. Check original and replacement times, titles of new meetings, recipient addresses, optional invitations and `send_updates: "all"`. Verify that `snapshot_sha256` matches the selected report. Record `approval_sha256` as the identifier of the batch reviewed.
- [ ] End the rehearsal after review. The organizer can request a separate booking and approve the exact digest when ready. A change to the operations requires reviewing the replacement manifest.

## Book and reconcile

Use an organizer-authorized OAuth token with Google Calendar event read/write access, access to the primary calendar's metadata, and read visibility on the affected calendars. The Google Calendar OAuth scope `https://www.googleapis.com/auth/calendar` covers the adapter's requests within the organizer's permissions. Supply the token through the environment. Keep it outside the manifest and command history. Supply the digest the organizer actually approved.

```sh
schedule-plan apply-plan review.json --approve "$APPROVED_DIGEST"
```

Before writing, the adapter checks the authenticated account, each occurrence's organizer, ETag, original interval and roster. It checks for new conflicts in each participant's destination interval including their rest allowance, and checks the organizing calendar even when the organizer is outside the meeting roster. It requires access to individual event listings to recognize the exact occurrences that will move. Missing visibility, a new conflict or a changed revision interrupts the batch. Changes to working hours, external constraints or attendee eligibility require fresh host evidence and a new solve. Ask the organizer of any separately owned occurrence to approve and make their change before refreshing the snapshot.

Google's individual writes can partially succeed. The response lists confirmed operations, and an interrupted batch exits with status 1. If a transport error leaves a write's outcome uncertain, run the **same approved manifest** again. Stable create IDs, per-operation markers and conditional ETag updates let a retry recognize completed operations. If someone edits a target, review fresh evidence and approve a revised plan. Google Calendar has no lock across all attendees' calendars, so a concurrent outside booking can appear after preflight.
