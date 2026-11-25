# Changelog

## Unreleased

- Bring exported briefs and portfolio plans into the demo’s calendar review interface. Compare options on per-person timelines, inspect original and proposed times, and save the selected proposal.

- Build dated snapshots from roster profiles, recurring occurrences and shared calendar evidence.
- Plan multiple meetings with CP-SAT, compare named ritual move budgets, protect focus time and evaluate cluster packing, weekday roles, delegates and soft preferences.
- Diagnose calendar load, invite hygiene and series review dates with the Calendar OS workflow.
- Render offline calendar comparisons and export a proposal with its source snapshot. Prepare and apply reviewed organizer changes through Google Calendar.

- Include observed calendar coverage in the sample so a host can reproduce the comparison directly from the fixture.

- Keep the one-move comparison and its proof when the authorized limit exceeds the number of movable meetings.
- Validate the request roster before a host reads any calendars.
- Save the selected demo proposal as plain text, including the people affected and any missing evidence.

## 0.4.0

### Agent workflow and advice

- Compare the earliest verified start at several move limits under one time budget. The compact brief identifies the marginal gain, the people affected and the reason a confirmation could improve a ranking.
- Record proof claims for each checked cap in workflow traces. Shared occurrences from different readers reconcile across equivalent UTC offsets, attendee ordering and replacement ordering.
- Exercise agent decisions with synthetic behavioral evaluations. Trusted rule and heuristic extensions support installed modules and multi-file implementations.

### Calendar evidence and proofs

- Snapshot 1.1 defaults to explicit busy-calendar coverage. Verification includes rest around proposed meetings. An incomplete attendee roster fixes the affected occurrence in place. Snapshot 1.0 keeps its evidence defaults.
- Versioned result contracts record the planner version, detached snapshot fingerprint, exact integer stage bounds, final witness origin and scores, and separate model preparation and search timings.
- Accelerated preparation for fragmented availability. The CLI preserves exact integer-valued JSON and rejects fractions that would silently round to integers.

### Running the tools

- The checkout, agent playbook and CI use `uv run --locked schedule-plan --demo`. The Python compatibility launcher also supports installation-free inspection and reuses its prepared solver environment. Piped snapshots preserve UTF-8 names under legacy console encodings.

- Run the full native example through the checkout launcher or the installed `schedule-plan --demo` command. The offline browser demo lets organizers choose a candidate time, preview moves on each person's calendar, and hand the selected proposal and snapshot to an agent.
- Inspect schemas and profiles without initializing the native solver. Expanded CI covers Python 3.14 and macOS.

## 0.3.0

- Added advice with verified and conditional choices, specific reschedules, evidence questions and ranking-specific proofs.
- Introduced versioned team profiles for objective and search defaults, material time gains, agent guidance and registered post-solve hooks.
- Revised the repository skill to discover available capabilities, clarify missing facts and run a staged search. Added worked examples and behavioral scenarios.
- Corrected infeasibility diagnostics for assumptions introduced by native rule extensions. Both Python interfaces now validate solver limits consistently.
- Added contributor and release guides, distribution checks, linting and installed-wheel smoke tests. The snapshot-v1 format and default raw planner interface remain compatible with 0.2.

## 0.2.0

- Introduced OR-Tools CP-SAT scheduling, hard and soft rule compilers, finite choices, evidence modes, lexicographic proofs and time-bounded search.
- Added source-attributed infeasibility explanations, a public snapshot schema and a two-week rescheduling benchmark.

## 0.1.0

- Initial Python enumerator for bounded scheduling and linked reschedules.
