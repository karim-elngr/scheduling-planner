# Benchmark linked reschedules over two weeks

Can the planner find a group call that requires moving a meeting *outside* the group as well as three meetings that block the call? This repeatable synthetic case has five reviewers, twenty linked attendees, fifty existing appointments and twenty possible call starts across ten working days. The organizer permits four moves.

## Run both replacement policies

From the repository root in an environment with the package installed:

```sh
uv run --locked python benchmarks/rescheduling.py --profile compact
uv run --locked python benchmarks/rescheduling.py --profile flexible
```

Both fixtures use identical rosters, calendars and meeting lengths. Compact supplies short replacement lists. Flexible permits forty appointments to move across the fortnight on a 15-minute grid. Design reviews retain a single contractual replacement.

**Expected decision.** Both profiles can place the call at 10:00 Dubai time (06:00 UTC) on Monday 30 November 2026. Three meetings block each candidate start. Moving the design review to its sole replacement creates a conflict with an external attendee's handover. Moving that handover frees the replacement, bringing the total to four moved meetings and seven affected people. A cap of three is infeasible. Equivalent replacement times can tie.

The fixture uses the legacy evidence default and assumes the generated busy calendar is complete. The verified and optimistic pools therefore admit the same schedules.

## Inspect the fixture or change the cap

Snapshot export uses the Python standard library. From the checkout, pipe the flexible snapshot to the native CLI with an explicit ranking, worker count and time budget:

```sh
uv run --locked python benchmarks/rescheduling.py --profile flexible --dump-snapshot | uv run --locked schedule-plan - --ranking preferred --workers 1 --time-limit 30
```

To exercise the failure case, save the exported JSON, set the top-level `max_moves` to `3`, and pass that file to the same CLI command. Look for `verified.proofs.preferred.status` in the raw result. The standalone benchmark script reports the backend version, elapsed time, proof stages, selected moves, branch/conflict totals and candidate assignment count.

## Recorded 0.4.0 measurements

| Replacement policy | Candidate assignments within the four-move cap | Median planning time | Verified outcome |
|---|---:|---:|---|
| Compact | 156,248,220 | 0.083 s | Optimal, four moves |
| Flexible | 16,727,573,067,346,720 | 0.173 s | Optimal, four moves |

These historical medians cover five calls to the public Python API with the package already imported, on Linux x86-64 with Python 3.12, OR-Tools 9.15.6755, one worker and the preferred ranking. The clock includes normalization, model preparation, solving and decoding. The assignment count combines candidate domains before conflict pruning. Native branches and conflicts measure actual search work.

With moves disabled, the flexible fixture's median fell to 12.1 ms, including 10.6 ms of preparation. The resulting fixed-event model had 53 variables, compared with 126 at a four-move cap. An earlier Python enumerator on the compact fixture reached its 200,000-assignment limit in about 3.23 s without a feasible incumbent.

For a regression comparison, keep the snapshot, package and solver versions, worker count, ranking and move cap together. Compare feasibility, objective scores and proof status before timing. Report preparation and native search separately. Teams with different move chains, evidence gaps or rule extensions should benchmark their own representative workloads.
