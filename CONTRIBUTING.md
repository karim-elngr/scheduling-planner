# Contributing

Start with a scheduling decision. For example, “When an attendee's calendar is missing, the agent should ask for the dates it needs.” The right place for a change depends on whether the agent lacks evidence, the optimizer places a meeting incorrectly, or the explanation misstates a valid result.

| If the problem is… | Start here | Check it with… |
|---|---|---|
| The agent reads too broadly, asks the wrong question or claims a proposal is approved | [Meeting-planning skill](.agents/skills/plan-meetings/SKILL.md) and [reference workflow](src/scheduling_planner/workflow.py) | [Host scenarios and traces](docs/workflow-evaluations.md) |
| The JSON cannot express an input or the parser interprets it incorrectly | [Snapshot schema](src/scheduling_planner/schemas/snapshot-v1.schema.json) and [domain parser](src/scheduling_planner/domain.py) | [Contract tests](tests/test_contract.py) and [evidence tests](tests/test_evidence.py) |
| An infeasible slot is accepted or a better slot is missed | [Calendar model](src/scheduling_planner/model.py), [engine](src/scheduling_planner/engine.py) and [rule-authoring guide](docs/writing-rules.md) | [Calendar oracle](tests/test_model.py) and [rule tests](tests/test_rules.py) |
| A team wants different priorities, questions or annotations | [Preferences guide](docs/preferences.md) and [example team profile](examples/team-profile.json) | [Profile tests](tests/test_profiles.py) and [advisor tests](tests/test_advisor.py) |
| Advice omits a move, understates uncertainty or quotes the wrong proof | [Advisor](src/scheduling_planner/advisor.py), [brief](src/scheduling_planner/brief.py) and [proof guide](docs/proofs.md) | [Advice regressions](tests/test_advice_release.py) and [output contract tests](tests/test_output_contracts.py) |
| The calendar shows the wrong blocker, preview or handoff | [Interactive calendar](docs/demo.html) and [demo snapshot](src/scheduling_planner/data/demo.json) | [Offline browser check](tools/check_browser_demo.py) |

## Set up a development checkout

From the checkout root, install the locked developer dependencies with `uv sync --locked --extra dev`. The editable installation uses the working tree. All commands below run without shell activation on macOS, Linux and Windows. Keep `pyproject.toml` and `uv.lock` together when updating dependencies and run `uv lock --check` before submitting.

## Trace a change from evidence to recommendation

Run `uv run --locked schedule-plan --demo`. In `move_comparison.caps`, the earliest verified start with one permitted move is 09:30 UTC. Asha and Eli can move their design review to noon.

1. In the [demo snapshot](src/scheduling_planner/data/demo.json), remove the `alternatives` field from `design-review`. This fixes that meeting in place.
2. Run `uv run --locked schedule-plan --demo` again. Both morning candidates now overlap the fixed review, so every reported cap should show `2026-12-01T15:00Z`.
3. Run the test below. It should fail because it checks the published time savings. Restore the field you removed and run it again. It should pass.

```sh
uv run --locked --extra dev python -m unittest tests.test_distribution_cli.InstalledCommandTests.test_version_and_packaged_demo_from_any_directory
```

For a skill change, use the same approach with a [behavioral scenario](.agents/skills/plan-meetings/evals/scenarios.json). Record what the host actually read, asked and presented. Run the prompt in a fresh host session after the edit. The Python evaluator covers the reference workflow and can check an instrumented host trace. A human reviews the host's final wording against the scenario.

## Checks before review

```sh
uv run --locked --extra dev python -m ruff check .
uv run --locked --extra dev python -m unittest discover -s tests -v
uv run --locked --extra dev python tools/eval_agent.py
uv run --locked --extra dev python tools/refresh_demo.py --check
```

The suite checks Python and CLI behavior. The evaluator checks decisions taken by the agent workflow. The freshness check compares the published demo reference with new CP-SAT solves. For calendar UI changes, run `python tools/check_browser_demo.py` with Google Chrome on your path, or pass `--chrome` followed by the browser executable path. It exercises the same journey at 375 and 1280 pixels, including the selected snapshot download. For packaging changes, also run the [release checks](docs/releasing.md).

## Open a pull request

Branch from the current main branch. Include a minimal synthetic input, the command that exercises it, and the expected and observed results. For an optimizer change, include the Python and solver versions, evidence mode, ranking, permitted move count and time budget. A small exhaustive oracle can establish whether a proposed fix preserves the feasible placements.

Update the affected contracts, examples and changelog when public behavior changes. Keep real exports under the ignored `local/` directory. Use the [security policy](SECURITY.md) for sensitive reports. Contributions use the project's [Apache License 2.0](LICENSE).
