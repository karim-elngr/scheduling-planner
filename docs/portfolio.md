# Planning a week

The [squad week fixture](../examples/squad-week-batch.json) contains the complete input to `schedule-plan batch-snapshot`. All commands also accept `-` for the positional JSON input.

## Calendar evidence

The [batch input guide](batch-snapshot.md) covers recurring series, exceptions, shared event IDs and partial reads. The host supplies a dated `window`, a `roster` keyed by stable person ID, and one authorized calendar response per person in `calendars`. Each profile has an IANA timezone, local working hours and local weekend days. For example, 10:00 in Dubai is 06:00 UTC, while 10:00 in Riyadh is 07:00 UTC. Friday is excluded when a person's profile names Friday as a weekend day. The resulting snapshot contains dated availability and explanations. Busy-calendar coverage must come from the actual read. Working hours alone cannot verify a meeting.

An authorized reader can use `scheduling_planner.ingestion.collect_snapshot(batch, read_calendar)`. The callback receives a person ID and two UTC timestamps and returns that person's feed, including observed coverage. The collector calls it once per roster member.

Use stable event IDs, and include the shared UID when provider IDs differ across views. Supply only the fields the reader can see. Preserve unknown attendees and incomplete coverage when collecting free/busy calendars. Events carry the title, organizer, optional attendance, out-of-office and priority information the host provides. Only give replacement times when the organizer has allowed them to be considered.

## Portfolio input

Inspect the input contract with `schedule-plan --portfolio-schema`. Use `requests` to specify several meetings. Give each a unique ID, attendees, duration and candidate starts or windows as in a single-meeting request. Existing commitments remain in `events`. The `portfolio` object configures planning policy.

| Setting | Meaning |
| --- | --- |
| `timezone` | IANA timezone for the team's cluster window and weekday roles. |
| `focus_people` | People whose observed free time should count toward the focus objective. |
| `movable_rituals` | Existing event IDs eligible to move to one of their supplied alternatives. |
| `cluster` | New meeting IDs in `requests`, incumbent IDs in `events`, or recurring IDs in `series` (matched against `metadata.series_id`), plus the local `after` / `before` times of the desired block. |
| `week_template` | Rules matching request `role` or event `metadata.role`, with `weekdays` (Monday = 0), and `mode` of `hard` or `soft`. |
| `preferences` | Per-person soft preferences including earliest meeting time, protected lunch and maximum meetings per day. |

On a request, `representatives` supplies eligible people for one delegate seat. The selected delegate appears in the placement. Set `max_moves` on the snapshot. With `--compare-moves` the portfolio command evaluates the allowed move budgets and returns placements, moves and focus metrics. Use `--time-limit SECONDS` to bound search and `--proof-lite` for a first feasible plan. Quote a gain as proved only when the comparison reports proof. An infeasible or timed-out search produces no verified improvement claim.

### What the solver optimizes

Unversioned portfolios default to v1.1 explicit busy coverage. Supplying `schema_version: "1.0"` or `evidence_policy: "legacy"` selects the legacy assumption that a missing busy feed is complete. The model schedules all requests together. It respects the required attendees' confirmed working windows, occupancy coverage, and rest buffers, including those of the selected delegate. An incumbent may keep an existing calendar conflict. Hard day-role policy applies to every local date a tagged meeting touches, including the original time of an incumbent. An event with incomplete attendees can only stay. Use an empty `requests` array to evaluate moving existing rituals alone. The [portfolio schema](../src/scheduling_planner/schemas/portfolio-v1.schema.json) references the adjacent snapshot schema for shared calendar fields.

The solver optimizes the following metrics in order. It holds each optimum fixed while solving the next one.

1. Minimize moves of protected incumbents.
2. Minimize soft policy violations. A request outside its role's soft weekdays costs one unit. Each attendee's early start, lunch overlap, and each meeting start above their daily cap costs one unit. Preferences use the person's `metadata.timezone`, falling back to the portfolio timezone.
3. For a cluster, minimize meetings outside its local clock window, then the number of distinct occupied dates, then elapsed minutes from first start to last end. Set `after` and `before` to target an afternoon.
4. Maximize `focus_minutes`, the sum of the longest free interval for each selected person on each local day. Gaps in observed working time or busy coverage split a block. A meeting occupies its duration. Rest buffers constrain scheduling. With `focus_people` omitted, all roster members count.
5. Minimize the count of free blocks across those people and days, then the number of moved incumbents, then the sum of each occurrence's start offset from its earliest candidate.

For the scatter comparison, the solver maximizes distinct cluster dates under the same move budget and preceding preferences, then optimizes focus on that spread-out schedule. The report includes both witnesses, days saved and the signed difference in focus minutes. A proved comparison requires both optima. Stage gains compare each feasible move budget with the previous feasible budget. The entire command shares one time limit. A `FEASIBLE` result gives a witness whose full ranking is unproved, and `UNKNOWN` means the solver found no witness in time. `INFEASIBLE` applies to verified evidence in the supplied domains. With `--proof-lite`, search stops at the first witness for each requested comparison.

## Diagnose, solve and discuss

See the [Calendar OS guide](calendar-os.md) for the scoring and template rules. Run `schedule-plan calendar-os snapshot.json` to inspect observed load by person and weekday, recurring series due for review, and possible invite changes. Review the suggested day roles before copying them to `portfolio.week_template` and running the solver. Hosts can use the profiles under [examples/profiles](../examples/profiles) and the [skill workflow](../.agents/skills/plan-meetings/SKILL.md).

Run `schedule-plan visualize decision.json --snapshot snapshot.json --output plan.html` to render a decision for review, or use `export-pack` to include the snapshot and proposal text. Run [prepare-apply](apply.md) only for a selected proposal. The organizer reviews the exact changes before authorizing a write.
