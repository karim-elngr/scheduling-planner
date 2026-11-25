# Scheduling Planner

Scheduling Planner gives an AI agent a way to answer “Can we meet earlier, what would need to move, and who still needs to confirm?” The agent collects dated evidence. A local Python solver compares placements using Google OR-Tools CP-SAT and returns structured JSON with proposed times, affected attendees, missing evidence and ranking-specific proofs.

## Run the packaged example

In a CPython 3.11–3.14 virtual environment on a platform with a compatible OR-Tools wheel, install the downloaded release wheel. For version 0.4.0, run these commands from the directory containing the wheel.

```sh
python -m pip install ./meeting_scheduling_planner-0.4.0-py3-none-any.whl
schedule-plan --demo
```

Five reviewers need 30 minutes. In `move_comparison.caps`, compare the earliest verified starts at 15:00, 09:30 and 09:00 UTC. The first reschedule buys five hours and thirty minutes. A second buys another thirty minutes. The options name each person who would have to rearrange.

## Connect an agent

The **source distribution** includes the `plan-meetings` skill under `.agents/skills/`, an offline decision explorer, example snapshots, team policies and host evaluation scenarios. Open the source checkout in a host that discovers repository skills, or follow the skill instructions when integrating your own host.

The **wheel** supplies the `schedule-plan` command, Python API, schemas and demo snapshot. Run these commands against a snapshot prepared by the agent.

```sh
schedule-plan --schema
schedule-plan snapshot.json --brief --compare-moves
```

Parse stdout as JSON. Keep the evidence mode, move cap and proof status with each option when presenting it to the organizer. Scheduling Planner is distributed under the Apache License 2.0.
