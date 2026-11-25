"""Standalone JSON Schemas for public Scheduling Planner outputs."""
from importlib.resources import files
import json


def output_schema(view: str | None = None) -> dict:
    """Return a Draft 2020-12 contract for a result, advice, brief or all three."""
    if view not in (None, "result", "advice", "brief"):
        raise ValueError("output schema view must be result, advice or brief")
    resource = files("scheduling_planner").joinpath("schemas", "outputs-v1.schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    if view is not None:
        del schema["oneOf"]
        schema["$ref"] = f"#/$defs/{view}"
        schema["title"] = f"Scheduling Planner {view} v1.0"
    return schema
