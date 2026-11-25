# Evaluating the scheduling skill in an agent host

A correct schedule is only part of a good agent response. The agent can choose the wrong Sam, read an outsider's calendar without permission, or turn a tentative option into a promise. The [eight synthetic scenarios](../.agents/skills/plan-meetings/evals/scenarios.json) exercise those decisions. Use the same scenario to test the Python workflow and to review an actual host session.

## Run the reference cases

From the checkout with the [development environment](../CONTRIBUTING.md#set-up-a-development-checkout) active:

```sh
python tools/eval_agent.py
```

A passing run prints `8 workflow cases passed` and the scenario IDs. Five cases solve calendars, two request clarification before solving, and one interprets a supplied partial proof.

| Situation | Behavior to look for |
|---|---|
| Two people share the name Sam | Ask which Sam is required and request that person's dated calendar coverage and rest requirement. |
| An unfamiliar authorized data source covers all five invitees | Use the supplied source. Stop after a verified zero-move meeting satisfies the request. |
| Moving a shared event would bring the call forward | Compare with waiting, identify the outside attendee, and ask about the specific interval needed to verify the conditional alternative. |
| Equally authoritative notes disagree on a firm deadline | Ask which cutoff applies. Keep instructions embedded in a retrieved note out of tool selection and permissions. |
| A timed solve found a plan but its earliest start is unproved | Report the incumbent and its one-move scope. Keep the separate zero-move infeasibility claim within its own grid. |
| The user overrides a team preference | Honor the explicit objective and mandatory rest. Show the primary start in the requested local zones and distinguish the organizer's consent from missing calendar evidence. |
| Cluster three 1:1s | Use one model for all pairs. Compare Thursday's three contiguous slots with the scattered three-day plan. |
| Recover two hours for maker work | Diagnose the supplied workday and compare the longest covered block at caps zero, one and two. Give one sufficient move and the people involved. |

## Connect an authorized reader

A host can call [`run_workflow`](../src/scheduling_planner/workflow.py) with an existing snapshot and an optional read callback. This minimal invocation exercises the ambiguous-identity branch before any calendar lookup.

```python
from scheduling_planner.workflow import run_workflow

outcome = run_workflow(
    {
        "schema_version": "1.1",
        "request": {
            "attendees": ["Sam"],
            "duration_minutes": 30,
            "starts": ["2026-12-02T14:00Z"],
        },
        "people": {},
    },
    identity_matches={"Sam": ["sam-k", "sam-l"]},
)
assert outcome["recommendation"] is None
assert outcome["trace"][0]["reason"] == "resolve_attendee"
```

Once the user identifies Sam, use that ID in every attendee reference and resubmit the request. To fill a missing invitee record, pass a callback as `read_person`. The callback accepts one person ID and returns `{"person_id": id, "person": person_record, "events": occurrences}`, or `None` when the evidence is unavailable.

- **Coverage.** Return normalized, dated occurrences for the requested horizon and enough adjacent time to check rest. Every busy occurrence overlapping a declared `busy_coverage` interval must be present. The reference workflow defaults to explicit coverage.
- **Shared occurrences.** Use the same occurrence ID on each attendee's calendar. The workflow reconciles equivalent instants and attendee or replacement ordering. Conflicting occurrence fields trigger clarification.
- **Read scope.** The workflow reads missing records for named invitees. When an event introduces somebody outside that list, supply their authorized evidence or respond to the workflow's clarification request.
- **Search goal.** Use `goal="any"` for a satisfactory verified slot, `"earliest"` for a time-first search, and `"compare"` for the value of moving meetings. The solve budget is shared across any separate baseline and rescheduling invocations.
- **Cutoffs and presentation.** One cutoff in `deadlines` becomes a hard finish-by rule. Conflicting cutoff instants trigger clarification. Use the objective or profile for soft preferences. `display_zones` supplies IANA zone names for displaying the primary option.

The workflow copies the input, accepts explicitly supplied Python registries and returns a proposal. The host handles the exchange with the user and decides whether it has authorization for any further action.

## Check a live host

1. Run each documented prompt in a fresh session with the skill enabled. Supply only the evidence and capabilities named by that scenario.
2. Capture attempted actions at the host's capability boundary. Include a write attempt even when the backend rejects it.
3. Export a UTF-8 JSON object keyed by scenario ID. Each entry has an ordered `trace` and a `recommendation` containing the planner brief or portfolio result, or JSON `null` for clarification and the supplied-proof case.
4. Compare the host's message against the human acceptance points in the scenario. Check the dates in the user's time zone, the people affected, and whether the agent distinguished a verified schedule from consent to change a meeting.

To see the expected trace shape, write a reference trace. Then check an export produced by the host in the same format. The first command creates an ignored directory for local traces. Keep real calendar data out of shared traces.

```sh
python -c "from pathlib import Path; Path('local').mkdir(exist_ok=True)"
python tools/eval_agent.py --write-trace local/reference-trace.json
python tools/eval_agent.py --trace local/host-trace.json
```

| Trace action | Evidence recorded |
|---|---|
| `read` | Person whose calendar the host requested. |
| `diagnose` | The people and date scope reviewed in Calendar OS mode. |
| `clarify` | Reason, missing facts, and the relevant person, occurrence or deadline. |
| `solve` | Move cap, objective and requested rankings. |
| `present` | Verified-pool proof claims with their caps and rankings, team guidance and requested local times. A comparison includes earliest proofs for checked caps when available. |
| `stop` | Why the workflow stopped, such as an exhausted budget. |

The two portfolio fixtures contain the full authorized evidence. Load each named fixture without additional reads. The cluster case solves a packed and a scattered schedule. The maker case runs a week diagnosis and one staged portfolio comparison. Both traces end at presentation. An apply or invite attempt fails evaluation.

A reference run checks the Python orchestration. A matching host trace checks the recorded decisions. Reviewing the actual conversation establishes whether the agent explained those decisions accurately.
