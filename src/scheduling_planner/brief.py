"""Project a full recommendation into a compact, tool-friendly response."""

from copy import deepcopy


def _source(option, advice, result):
    if option["id"] == advice["primary_id"] and advice["primary_source"]:
        source = advice["primary_source"]
        if "max_moves" in source:
            row = next(r for r in advice["move_comparison"]["caps"] if r["cap"] == source["max_moves"])
            return {**source, "status": row["proof_status"]}
        mode, rank = source["evidence_mode"], source["ranking"]
    elif option.get("confirmation_reasons"):
        mode, rank = "optimistic", option["confirmation_reasons"][0]["ranking"]
    elif option["proofs"]:
        mode = "verified" if "verified" in option["proofs"] else "optimistic"
        rank = next(iter(option["proofs"][mode]))
    else:
        cap = option["comparison_caps"][0]
        row = next(r for r in advice["move_comparison"]["caps"] if r["cap"] == cap)
        return {"evidence_mode": "verified", "ranking": "earliest", "max_moves": cap,
                "snapshot_sha256": row["snapshot_sha256"], "status": row["proof_status"]}
    return {"evidence_mode": mode, "ranking": rank, "max_moves": result["scope"]["max_moves"],
            "status": option["proofs"][mode][rank]["status"]}


def brief(report: dict) -> dict:
    """Summarize the stored report without re-solving or transferring proofs between scopes."""
    advice, result = report["advice"], report["result"]
    scope = result["scope"]
    options = []
    for option in advice["options"]:
        candidate = option["plan"]
        summary = {"id": option["id"], "role": option["role"], "availability": candidate["availability"],
                   "start": candidate["start"], "end": candidate["end"], "metrics": candidate["metrics"],
                   "moves": option["moves_to_coordinate"], "decisions": candidate["decisions"],
                   "unresolved_count": len(candidate["unresolved"]),
                   "proof": _source(option, advice, result)}
        if option.get("confirmation_reasons"):
            summary["why_confirm"] = option["confirmation_reasons"][0]["reason"]
        if option.get("comparison") is not None:
            summary["minutes_earlier_than_primary"] = option["comparison"]["minutes_earlier_than_primary"]
        if candidate["rule_penalties"]:
            summary["rule_penalties"] = candidate["rule_penalties"]
        options.append(summary)
    comparison = advice.get("move_comparison")
    if comparison is not None:
        comparison = {**comparison, "caps": [
            {k: v for k, v in row.items() if k not in ("proof", "proof_status", "snapshot_sha256", "witness")}
            for row in comparison["caps"]
        ]}
    diagnosis = {mode: result[mode]["infeasibility"] for mode in ("verified", "optimistic")
                 if "infeasibility" in result[mode]}
    scope_summary = {key: scope[key] for key in ("snapshot_id", "snapshot_sha256", "max_moves", "rankings",
                                                 "objective", "evidence_policy") if key in scope}
    if scope.get("locked_incomplete_rosters"):
        scope_summary["locked_incomplete_rosters"] = scope["locked_incomplete_rosters"]
    return deepcopy({
        "output_version": report["output_version"], "planner_version": report["planner_version"],
        "view": "brief", "status": advice["status"], "primary_id": advice["primary_id"],
        "scope": scope_summary,
        "options": options, "questions": advice["questions"], "question_source": advice["question_source"],
        "annotations": advice["annotations"], "move_comparison": comparison,
        "diagnosis": diagnosis,
    })
