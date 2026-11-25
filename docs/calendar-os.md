# Diagnose a squad's week

Collect a dated snapshot of the roster and existing occurrences, including periods actually checked for busy time. Then run the diagnosis locally.

```sh
schedule-plan calendar-os squad-week.json > diagnosis.json
```

The report groups days in `portfolio.timezone` (UTC by default). A person's own timezone, when supplied, appears under `people`. For a batch snapshot, `calendar_explainers` also lists local working hours and weekends. The report uses the actual elapsed duration of local days across daylight-saving changes.

| Report field | Interpretation |
| --- | --- |
| `people` | Per-person organized, invited, unknown-organizer and out-of-office minutes. These count the duration of each occurrence, so two overlapping invitations both contribute to invitation load. |
| `days` | Aggregated person-minutes. `covered_work_minutes` measures the intersection of eligible working time and observed busy coverage. `busy_minutes` counts occupied time in that intersection once per person. |
| `free_blocks`, `free_minutes` | Count and duration of contiguous unoccupied periods in covered working time. Missing coverage breaks a block. |
| `sum_longest_free_minutes` | Sum of each person's longest observed free block for the day. The per-person daily rows show the individual values. |
| `invite_hygiene` | Invitations with at least one review signal, sorted by score. Optional attendance, tentative status and cross-squad participation each contribute one point. Ordinary invitations still contribute to each attendee’s `invited_minutes`. |
| `recurring_sunsets` | Series represented in the snapshot with no recorded review date, renewal date, end date or finite occurrence count. Each entry lists its observed occurrences and organizer when known. |

Check `evidence_warnings` for failed calendar reads or conflicting shared occurrences before drawing conclusions from the totals.

For example, a tentative optional invitation to a cross-squad forum scores three. Ask whether one delegate can cover the squad. For a tentative optional invitation within the squad, the suggested question is whether to decline. Unknown organizers and out-of-office entries have separate load totals. Keep any decisions with the organizer and attendee.

## Try a weekly template

Inspect the team's eligible and covered minutes alongside `template_proposal.week_template`. The proposal gives soft day-role preferences for maker time, alignment and 1:1s using observed weekdays. Its rationale identifies the metric used for each choice. In an uncovered week the proposal is empty.

After the team chooses its day roles, put those rules under `portfolio.week_template` in the multi-request snapshot. Set each request's `role` to its corresponding name, such as `one_on_one`. To test potential changes to existing rituals, put only organizer-authorized occurrence IDs under `portfolio.movable_rituals` and supply their candidate replacement times. Compare the candidate plans before requesting calendar changes.

```sh
schedule-plan portfolio squad-portfolio.json --compare-moves > plans.json
```

## Team profile gallery

These profiles supply the single-request advisor with objective priorities and guidance for the host. Put each person's actual timezone, weekend and eligible hours in the roster snapshot. A regional example alone cannot establish an individual's working hours.

| Scenario | Behavior |
| --- | --- |
| [Dubai and Riyadh squad](../examples/profiles/mixed-timezones.json) | Favor preserving commitments. Collect each attendee's weekend and explain the chosen slot in both offices' timezones. |
| [Launch week](../examples/profiles/launch-week.json) | Prioritize an earlier decision after protecting sensitive meetings. Surface gains starting at 15 minutes. |
| [Steady state](../examples/profiles/steady-state.json) | Prioritize fewer affected people, then fewer moved meetings. Surface time gains of an hour or more. |

```sh
schedule-plan sample.json --brief --profile examples/profiles/launch-week.json
```
