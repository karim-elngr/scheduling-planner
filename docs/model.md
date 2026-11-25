# Turn calendar evidence into a scheduling model

The planner chooses one start for a requested call and one placement for each existing meeting in scope. Every attendee of a shared meeting sees the same placement. The result can reveal a dependency outside the invite list, such as an earlier start that requires moving an external reviewer’s other appointment.

## Capture a snapshot

Build the snapshot for a specific request and observation time. Use offset-aware, whole-minute instants. Expand recurring meetings and daylight-saving exceptions in the source time zone before converting them to dated occurrences.

| Input | What the agent supplies |
|---|---|
| `request` | The required attendee IDs, duration, and candidate `starts` or search `windows`. |
| `people` | Each person's confirmed working windows in `available`, additional plausible windows in `possible`, observed commitment coverage in `busy_coverage`, and required `rest_minutes`. Include attendees of proposed replacements. |
| `events` | One stable ID per occurrence, its original start, duration and roster. Include every busy event that overlaps the observed interval, even one that started before it. |
| Move permissions | Give movable occurrences authorized `alternatives` or `move_windows`. An occurrence with neither stays fixed. Set `attendees_complete: false` to lock an occurrence with an unresolved roster. |
| Policy | Record facts and provenance. Hard rules, soft penalties and the preferred objective give selected facts scheduling meaning. `max_moves` caps the number of existing occurrences that may change. |

A window produces starts from its own left endpoint in increments of `step_minutes` (default 15). The entire meeting must fit inside one candidate window. Generated grids share identical instants. Explicit lists require distinct instants after offset conversion, and an event's explicit alternatives must differ from its original start. The generated domain limit is 20,000 starts per occurrence. See the [input schema](../src/scheduling_planner/schemas/snapshot-v1.schema.json) and [parser](../src/scheduling_planner/domain.py).

## Decide whether a placement is verified

Consider a 09:00–09:30 call. One attendee works from 09:00 and requires 15 minutes between appointments. Two different observations matter.

| Evidence | Interval needed for this attendee | Why |
|---|---|---|
| Working availability | 09:00–09:30 | Confirms eligibility for the meeting itself. |
| Observed busy calendar | 08:45–09:45 | Covers commitments close enough to break the rest requirement on either side. |

If the busy feed covers only 09:00–09:30, the optimistic plan can identify exactly which periods need checking: 08:45–09:00 and 09:30–09:45. A known neighboring conflict excludes the placement. Missing calendar coverage and missing working availability produce separate questions for each affected person and occurrence.

Use `"schema_version": "1.1"` for new integrations. It defaults to explicit evidence. Omitting a person's `busy_coverage` leaves their occupancy unobserved. Unversioned snapshots and version 1.0 default to legacy evidence, under which an omitted field means all relevant busy events were supplied. An explicitly supplied list, including an empty list, is enforced under either policy. A snapshot can override the default with `evidence_policy`.

Verification applies to the request and to every changed occurrence. An existing meeting may retain its original start when current working windows would prevent scheduling a new meeting there. A verified option can be presented for agreement. Calendar evidence alone grants no authority to change attendees' schedules.

## How placements interact

For a meeting starting at minute `s` with duration `d`, an attendee with rest `r` reserves the half-open interval `[s, s + d + r)`. Their next meeting may start exactly at the endpoint. Each person has a separate resource calendar. When two existing appointments already conflict, both may stay at their original times. Any new or moved meeting must still clear both, including rest.

The model shares each occurrence's start across all its attendees. Its move flag is true precisely when that start changes. Moving one meeting with three attendees counts as one move and can disrupt three people. Someone whose two appointments both move contributes once to `people_disrupted`. A protected move is permitted and contributes to `protected_moves`.

A finite `decisions` domain can represent a room or standby responder. A rule connects the chosen value to capacity, availability or other requirements. A decision that changes meeting length or required attendance needs corresponding model constraints or its own scheduling scenario. See [writing a rule](writing-rules.md).

## What the solver optimizes

The verified search requires zero missing evidence. The optimistic search also admits eligible placements with unresolved facts. A missing rule cost receives an optimistic value of zero. An optimistic optimum therefore bounds what confirmation could buy under the same ranking. Compare the actual score vectors and proof records before claiming a gain.

The default preferred ranking minimizes protected moves, total weighted soft penalty, moved meetings, people disrupted, then start time. The solver minimizes each tier with the preceding proven tiers fixed. A custom objective changes the preferred order. All rankings end with the number of unresolved evidence items. A time-limited search can still return a feasible placement.

| Implementation | Responsibility |
|---|---|
| [Calendar model](../src/scheduling_planner/model.py) | Creates start variables, intervals, move flags and evidence counts. Uses interval separation for each person's calendar and additional guards for pre-existing conflicts. |
| [Rule registry](../src/scheduling_planner/rules/registry.py) | Binds named policies to their measured violation and missing-evidence flags. |
| [Engine](../src/scheduling_planner/engine.py) | Solves the evidence pools and rankings, retains feasible assignments, and records proofs and diagnostics. |
| [Advisor](../src/scheduling_planner/advisor.py) | Selects distinct options, reports what moves, and identifies the evidence worth confirming. |

The engine models local relationships among choices. Fine grids, linked move chains, existing conflicts and long objective lists all increase work. With a zero move cap the parser still validates the alternatives, counts generated grids without expanding them, and gives each existing meeting a single effective start. Rule authors can inspect the original declarations in `ctx.problem.raw` and effective starts in `ctx.slots`. Use the [benchmark](benchmark.md) to measure a change and the [proof guide](proofs.md) to interpret the result.
