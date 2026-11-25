# What can an agent claim about a plan?

“09:30 is feasible” and “09:30 is the earliest verified start with one move” require different evidence. For the second claim, identify the move cap, the verified evidence pool, the earliest ranking, and a proof of its leading start tier.

## Read the placement and its proof together

From the checkout, request the earliest ranking on the bundled example:

```sh
uv run --locked schedule-plan src/scheduling_planner/data/demo.json --ranking earliest
```

The snapshot permits two moves. Its earliest verified start is `2026-12-01T09:00Z`. In the raw output the placement is at `verified.earliest` and its proof is at `verified.proofs.earliest`. A full advice response nests both under `result`. For another move cap, inspect that cap's comparison row or solve that scenario separately.

| Ranking status | What the agent may report |
|---|---|
| `optimal` | Every tier in this ranking was proved for this evidence pool and snapshot. Several schedules can tie. |
| `best_found` | A feasible schedule exists. A leading tier with `proven: true` can support a narrower claim. For example, a proved first `start` tier establishes earliest time even if the solver timed out while minimizing subsequent disruption. |
| `infeasible_in_scope` | Every placement allowed by the supplied domains fails in this evidence pool. Unobserved calendars, excluded times or different permissions require a new scenario. |
| `unknown` | The search returned neither a feasible assignment nor an infeasibility proof for this ranking within budget. |

**Use `witness.scores` for the assignment being returned.** Its `start` score is an integer count of UTC minutes since the Unix epoch. Display the placement's ISO `start` to the organizer. A proof without a placement has a null witness.

`order` lists the objectives in priority order. `stages` records the search history for this ranking. At each stage, `value` is an incumbent score and `best_bound`, when present, is an integer lower bound given the fixed earlier tiers. A later ranking can discover a better eligible assignment. The first ranking's witness can then improve while its stage history still describes the earlier search. `witness.origin` identifies the search that found the assignment. `derived_from`, when present, identifies another proof supporting its status.

## Explain a move comparison

The example's one-move start is 09:30 UTC. A second move permits 09:00 UTC. In a compact response, read `move_comparison.caps`; in full advice, read `advice.move_comparison.caps`. Each row describes a separate cap. `previous_cap` names the preceding cap with a verified witness, and `minutes_gained` measures the change between those two starts.

Report “the second move buys 30 minutes” when `gain_proven` is true. If either start is still an incumbent, describe the observed difference and retain the row's status. A negative observed gain is possible under a time budget. A sampled range leaves unchecked caps open, and `not_run` means that scenario received no solve before the deadline.

Use an option's own proof or `primary_source` to identify its scope. A narrower scenario can provide the best verified option found before timeout. Custom rules may inspect the move limit itself, so even identical placements can carry different evidence or rule costs at different caps.

## Investigate infeasibility

A proved-infeasible pool may include an `infeasibility` record. `sufficient_core` lists hard-rule, evidence or extension assumptions sufficient for a conflict together with the calendar model. Multiple explanations may exist, and the set may include redundant assumptions. `base_infeasible` points to the underlying feasibility model. `no_diagnostic_time` means the diagnostic lacked time to explain an existing infeasibility proof. Test a proposed relaxation by changing the relevant input in a separate scenario and solving again.

## Keep a reproducible decision record

Retain the effective snapshot, `scope.snapshot_sha256`, `result_version`, `planner_version`, native `backend`, and the version of any custom compiler. The fingerprint uses finite UTF-8 JSON with sorted object keys, compact separators and unescaped non-ASCII text. Array order is preserved. Equivalent clock times written with different offsets can produce different fingerprints. The engine detaches its input, so later edits to the caller's snapshot leave the earlier result intact.

For performance investigations, compare `statistics.model_build_seconds` (capture, validation and compilation) with `search_seconds` (ranking solves, diagnostics and reporting). Together they give `elapsed_seconds`. `native_wall_seconds` sums the durations reported by native solves. A tiny shared budget can expire during model preparation.
