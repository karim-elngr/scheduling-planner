# Add a scheduling rule

Use a native rule when a policy must change which assignments the planner accepts or which assignment wins. The agent supplies facts and names the policy in its snapshot. A trusted Python compiler translates that policy into constraints, a violation quantity and any missing-evidence flag.

## Try the release review

```sh
uv run --locked schedule-plan examples/release-review.json --rules-file examples/team_rules.py --ranking preferred
```

The five reviewers can all attend at 10:00 or 11:00 Dubai time. The duty roster lists Asha and Lin. Asha is required in the review and Lin is on a vendor call at 10:00. At 11:00, Lin can cover incidents and the review can still finish by 11:45. Look for `verified.preferred.start` equal to `2026-12-01T07:00Z` and `decisions.standby_responder` equal to `Lin` on that placement.

The [release snapshot](../examples/release-review.json) combines three constraints:

| Kind | What it measures |
|---|---|
| Built-in `finish_by` | Minutes by which the meeting ends after a deadline. Supply `event` and either `deadline` or the name of a timestamp `fact`. A missing fact produces a question. |
| Built-in `precedence` | Minutes missing from the required separation between `before` and `after`. The optional `gap_minutes` defaults to zero. |
| Team rule `standby_cover` | Whether the selected responder is on duty, off the review and available throughout the interval, taking other events' proposed placements and coverage into account. |

Omit `responder_decision` when the team only needs proof that someone can cover. Supplying it links the check to a finite decision whose selected value appears in the plan. The rule models duty during the meeting itself. A team requiring rest around standby duty can extend its encoder. A second shipped example, `room_capacity`, computes the number of missing seats for a selected room and asks about unobserved capacity. Room occupancy requires a separate resource constraint. See the [team compilers](../examples/team_rules.py).

## Define an effect in units the team understands

Suppose a support follow-up should start within 45 minutes of the review ending. A follow-up exactly 45 minutes later costs zero. A follow-up 60 minutes later costs 15 minutes. Save the following compiler as `team_rules.py`:

```python
from scheduling_planner.rules import RuleEffect, require_keys
from scheduling_planner.rules.modeling import positive_part


def followup_delay(ctx, params):
    require_keys(params, required=["review", "followup", "allow_minutes"])
    review_id, followup_id = params["review"], params["followup"]
    if review_id not in ctx.slots or followup_id not in ctx.slots or review_id == followup_id:
        raise ValueError("supply two different occurrence IDs in the snapshot")
    allowance = params["allow_minutes"]
    if type(allowance) is not int or allowance < 0:
        raise ValueError("allow_minutes must be a nonnegative integer")

    review, followup = ctx.slots[review_id], ctx.slots[followup_id]
    excess = followup.start - review.end - allowance
    upper = max(followup.starts) - (min(review.starts) + review.duration) - allowance
    delay = positive_part(ctx.model, excess, upper, "followup_delay")
    return RuleEffect(units=delay, reason="Follow-up exceeds the preferred response time.")


RULES = {"followup_delay": followup_delay}
```

The helper enforces `delay = max(0, excess)` for every placement. Its upper bound uses the latest follow-up start and earliest review finish. Pair the cost with hard `precedence` if the follow-up must occur after the review.

Add this instance to a snapshot with an occurrence named `support-sync`:

```json
{
  "id": "short-response",
  "kind": "followup_delay",
  "mode": "soft",
  "weight": 2,
  "params": {"review": "$request", "followup": "support-sync", "allow_minutes": 45},
  "source": "Support rotation agreement"
}
```

The 15-minute violation contributes 30 weighted units to `soft_penalty`. Put `rule:short-response` in the preferred objective to prioritize this rule independently. Hard mode requires zero known violation and accepts no weight. Include a precise source so the agent can explain the decision to the organizer.

## Compiler contract

| Interface | Meaning |
|---|---|
| `ctx.model` | The current OR-Tools `CpModel`. The engine owns its objective. |
| `ctx.slots[id]` | `start` is an integer variable in UTC epoch minutes, `end` is start plus duration, and `starts` lists effective placements. `moved` indicates a change from the original start. `$request` identifies the requested call. |
| `ctx.decisions[name]` | An index variable into `ctx.decision_values[name]`. Encode what choosing each value means. |
| `ctx.problem` | Normalized people include confirmed and optimistic working windows. `ctx.problem.raw` retains the effective snapshot, including original replacement declarations, facts and metadata. |
| `RuleEffect.units` | A nonnegative integer or a bounded integer variable belonging to this model. A possible positive value requires a human-readable `reason`. |
| `RuleEffect.unknown` | A Boolean variable or zero/one constant. When true, the proposed assignment needs more evidence. Supply a `question` whenever the flag can be true. |

The verified pool requires every unknown to be false. In the optimistic pool an unresolved rule contributes zero known units, even if its cost could be positive after confirmation. Separate a known cost and an uncertain cost into two rule instances when both must be reported.

Compile deterministically from the supplied snapshot. Each model compiles its rules once, and a move comparison can build several models. Collect new evidence in the host before solving. With the example file above, invoke `uv run --locked schedule-plan snapshot.json --rules-file team_rules.py` or pass `registry=RULES` to `plan`. Use `--rules-module company_policy.rules` for an installed package. The host chooses the trusted code, and the snapshot selects a registered name and supplies parameters.

## Choose an exact encoding

| Relationship | Suitable encoding |
|---|---|
| Lateness, separation or shortage | A bounded linear expression with a positive-part equality. |
| A category selects a cost | A table over the category index and output variable. |
| At least one eligible person can cover | Define a Boolean witness for each person's eligibility, then take their logical OR. |
| Several activities share a capacity limit | Intervals and a cumulative constraint for a structural limit, or a bounded overload variable for a soft penalty. |
| Evidence is missing for certain choices | Tie the unknown flag to the selected choice so that alternatives with adequate evidence can still be verified. |

Python branching on symbolic times cannot evaluate a future placement. Use equivalences such as `at_most(model, a, b, name)` to encode both the true and false cases of a comparison. A one-way implication may let the solver choose a flag that conceals a real violation.

A constraint added directly to the model applies in both hard and soft modes. Put policy-specific violations in the returned effect. If you introduce a native assumption with `ctx.model.add_assumption(literal)`, name the variable. A diagnostic core can then report an `extension_assumption` with the variable, required Boolean value and signed literal index. The core is sufficient and can contain redundant assumptions.

## Test and migrate a rule

Pin the meeting starts and decisions to one concrete assignment. Solve for the effect without an objective, then forbid the observed tuple of units and unknown flag. An infeasible second solve shows that the first tuple is the only possible effect for those inputs. Repeat at the boundary and on both sides, with a moved occurrence and with a missing fact. The [compiler tests](../tests/test_rules.py) exercise this technique.

From an installed development environment:

```sh
python -m unittest discover -s tests -p test_rules.py -v
```

When migrating a legacy `factory(params, snapshot) -> checker(candidate)`, start with the quantity the checker evaluated on a completed assignment. Express it over `ctx.slots` and `ctx.decisions`, bind it to a bounded variable, then return it in a `RuleEffect`. Replace missing-data branches with an unknown flag and question. The follow-up example above implements this pattern for elapsed minutes. This lets CP-SAT propagate the policy while exploring partial assignments.
