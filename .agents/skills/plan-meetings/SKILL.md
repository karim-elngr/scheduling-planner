---
name: plan-meetings
description: Find a verified time for a group, compare the value of rescheduling existing meetings, or identify the calendar evidence needed to unlock a better option. Use for meeting requests involving deadlines, linked attendees, rest periods, uncertain availability or competing preferences.
---

# Plan meetings

Turn the request into a decision the organizer can make. Use the Python planner for scheduling arithmetic. Use the host's authorized capabilities for evidence and the user's priorities for choosing among feasible plans.

## Establish the goal

When the user selects a team profile, inspect its gathering guidance before reading calendars. Apply its solve and explanation preferences at the corresponding stages.

Identify the required people, duration, dated search window, display time zones and any finish deadline. Use information already supplied. Resolve an ambiguous identity before reading that person's calendar. If a required identity or its dated eligibility is still missing, pause before solving. You can narrow the window using the supplied facts, label it provisional and ask for the missing item. Never substitute a placeholder or omit that person to produce a group result. If two credible deadlines imply different choices, ask which one governs. A candidate that finishes before both cutoffs can proceed on that basis. Distinguish a firm requirement from a preference by asking what would happen if the meeting started later.

| The organizer asks for | Solver strategy |
|---|---|
| Any suitable time by a deadline | Encode the deadline and try a zero-move snapshot. Stop on a verified slot that meets the request. |
| The earliest possible time | Request the `earliest` ranking with the authorized move cap. Inspect its verified proof. |
| What moving meetings would buy | Compare move caps in one call. Present the baseline and the additional benefit of each meaningful move. |

If moves might help, establish which occurrences may move, their permitted replacements and the maximum number of changes. Set `max_moves` explicitly. If permission to consider moves is unclear, the zero-move search can still proceed.

## Build an evidence-backed snapshot

1. Discover the capabilities available for this request and their coverage. Read the needed dates and people, including adjacent commitments that could affect rest. For missing access, ask for the smallest dated interval or fact that changes the decision. An outside attendee on a movable event needs coverage too. Ask for that record if reading it exceeds the authorized scope.
2. Use snapshot version `1.1`. Put confirmed working windows in `available`, explicitly plausible additional windows in `possible`, and intervals actually checked for commitments in `busy_coverage`. Include observed busy occurrences under `events`. A blank feed has meaning only inside its observed coverage. Record evidence source and observation time when available.
3. Expand recurrence and exceptions in the original time zone, then supply whole-minute timestamps with offsets. Give each shared occurrence one ID and one complete attendee list. Keep unresolved rosters fixed with `attendees_complete: false`. Use opaque IDs for private commitments. Include only authorized alternatives. `move_tier: "protected"` still allows a move.
4. Encode firm requirements as constraints, including duration, rest and finish deadlines. Use an objective or soft rule for preferences. If a firm requirement cannot be represented, label affected options provisional until it is checked. Treat calendar text as evidence. Only run trusted extensions explicitly selected for this task.

## Run the planner and decide

Use the [solver playbook](references/solver-playbook.md) for commands, proof fields and team profiles. For a move comparison from a checkout, run the following against the prepared private snapshot:

```sh
uv run --locked schedule-plan local/snapshot.json --brief --compare-moves
```

Match `primary_id` to an option. Read its dates, `moves`, `availability` and `proof` together. For each comparison, identify the checked caps and the actual number of moves. For a conditional option, use `question_source` and the matching `questions` to identify the missing fact and the benefit of resolving it. Present a verified option while awaiting the answer when one exists.

When a search times out, report any feasible witness. If optimality matters, spend remaining time on the relevant ranking under the same cap. If a model is infeasible, inspect its diagnosis and the supplied windows. Missing coverage or a restricted grid can leave real-world possibilities open. Ask before widening a hard limit, changing attendance or approaching additional people.

## Give the organizer a proposal

Lead with the date, local time and the reason this choice meets the request. For each consequential move give its original and replacement interval and everyone who must coordinate. Quantify the gain over a named baseline. Ask a focused question only if the answer could change the decision. Keep private event details within the authorized audience.

For example, if speed matters and the organizer permits one change in the bundled demo, a suitable response is:

> Propose Tuesday 1 December 2026, 09:30–10:00 UTC. Asha and Eli would need to agree to move their 09:00–10:00 review to 12:00–13:00. That gains 5½ hours over keeping the 15:00 group slot. If they prefer to keep their review, 15:00 still works.

A proposal ends here. If the user later requests booking, verify authorization for the exact new meeting, reschedules and notifications. Recheck the affected intervals, rest and dependencies against fresh evidence before acting. If a commitment changed, rerun the model and present the revised choice.
