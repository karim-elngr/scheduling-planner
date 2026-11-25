# Give an agent team-specific scheduling preferences

The [conditional scheduling snapshot](../sample.json) offers a choice. Five people can meet on Wednesday at 14:00 Dubai time with their calendars intact. Moving Bob and Eli's sync makes Tuesday at 11:00 possible. A team that values an earlier decision can recommend Tuesday and ask the organizer to coordinate with Eli. The meeting evidence stays the same throughout.

## Run the example

From the checkout, compare the default recommendation with the deadline-sensitive profile:

```sh
uv run --locked schedule-plan sample.json --brief
uv run --locked schedule-plan sample.json --brief --profile examples/team-profile.json --heuristics-file examples/team_heuristics.py
```

Match `primary_id` to an entry in `options`. The default selects Wednesday. The profile ranks `start` ahead of ordinary meeting moves and selects Tuesday. The named hook adds a coordination note for Eli under `annotations`. The possible Tuesday 10:00 option still depends on confirming Dana's availability.

## Choose where a preference belongs

| Team request | Implement it with | Observable effect |
|---|---|---|
| “Keep the customer meeting fixed.” | An event with no replacements, or a hard rule for a conditional restriction | Conflicting placements become infeasible. |
| “Prefer rooms with enough seats.” | A soft rule with a measured shortfall and weight | Plans receive a cost. The preferred objective can give `rule:<id>` its own priority. |
| “Choose an earlier decision over moving an ordinary meeting.” | A JSON profile's `objective` | Changes how the preferred ranking orders feasible assignments. |
| “Ask which deadline is firm. Explain results in both office time zones.” | `guidance.gather` and `guidance.explain` | The host agent receives instructions at the corresponding stage. |
| “Flag moves involving people outside the call.” | A registered post-solve heuristic | Adds notes or questions to specific returned plans. |

Use a [native rule](writing-rules.md) for policy that must hold during optimization. A profile selects registered hooks by name. A host explicitly supplies the trusted implementation. Python extensions execute with the process's permissions and can read the supplied snapshot.

## Configure and inspect a profile

Only `profile_version` and `name` are required. This data-only example changes the preferred objective and gives the agent one question to ask while collecting evidence.

```json
{
  "profile_version": "1.0",
  "name": "Deadline-sensitive coordination",
  "objective": ["protected_moves", "start", "moved_meetings", "people_disrupted", "soft_penalty"],
  "guidance": {
    "gather": ["Which decision depends on this call, and when is it due?"]
  }
}
```

Inspect the [complete team profile](../examples/team-profile.json) with `uv run --locked schedule-plan --show-profile --profile examples/team-profile.json`. The CLI validates these settings and prints them without executing a hook. Use `--profile-schema` for the JSON Schema.

| Setting | Precedence and scope |
|---|---|
| `objective` | The snapshot's explicit objective takes precedence over the profile, followed by the engine default. It controls the preferred ranking. |
| `rankings`, `workers`, `time_limit_seconds` | An explicit CLI or API value takes precedence over the profile, followed by engine defaults. |
| `min_gain_minutes` | Default 15. Controls which earlier alternatives are worth surfacing when time is the deciding improvement. The raw rankings remain available. |
| `guidance` | Ordered instructions for `gather`, `solve` and `explain`. The host interprets them within the user's permissions. |
| `heuristics` | Names and parameters selecting post-solve annotations. Solve mode with full advice or a brief is required to run them. |

The advisor compares an earlier verified alternative with the primary choice. It weighs a conditional plan against the best verified witness for the *same ranking*. A confirmation can earn attention by improving a higher-priority non-time metric, even with no time gain. An alternative that reduces disruption can also be worth presenting.

## Write a post-solve hook

For example, save this code in a trusted `team_notes.py` and add `"heuristics": [{"name": "protected_owner"}]` to a profile:

```python
def protected_owner(context, params):
    return [
        {
            "plan_id": option["id"],
            "kind": "question",
            "text": "Which organizer can approve moving the protected meeting?",
        }
        for option in context["options"]
        if option["plan"]["metrics"]["protected_moves"] > 0
    ]


HEURISTICS = {"protected_owner": protected_owner}
```

Pass `--heuristics-file team_notes.py` with the profile and `--brief` or `--advise`. An embedding can use `advise(snapshot, profile=profile, heuristics=HEURISTICS)`. Rule compilers use a separate `registry` argument.

Each hook receives independent copies of the effective snapshot, selected options, `recommended_id`, configured parameters, and the move comparison when requested. Return a list of records with an existing plan ID, a `kind` of `note` or `question`, and nonempty text. Use each option's `moves_to_coordinate` to identify every attendee of each move. For a comparison-only option, consult `comparison_caps` and its comparison row. Its main-scenario `proofs` map can be empty.

A local extension file can import from its own directory. For a packaged extension, use a qualified module name with `--heuristics-module` or `--rules-module`. Package-relative imports keep same-named helpers in different teams' packages distinct. The CLI routes extension diagnostics to stderr and writes the JSON result to stdout.

## Give the organizer the next decision

1. Present the verified primary choice and the affected people. If only conditional choices exist, `primary_id` is null.
2. Use `question_source` and `questions` for evidence that could change the decision. The engine breaks ties on modeled priorities by minimizing unresolved items. Team-authored questions about approval appear in `annotations`.
3. If comparing disruption, request `--compare-moves`. Extra earliest solves share the budget with the main report, which retains the requested rankings. Large ranges sample up to nine caps. Built-in policies can collapse caps above the number of movable events. A custom cap-dependent policy can make every permitted cap relevant.
4. Re-solve after the user supplies a new fact, deadline or permission. Preserve the old option's cap and fingerprint when describing what changed. Use the [proof guide](proofs.md) to distinguish exact marginal gains from observed improvements.
