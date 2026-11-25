"""Data-only team defaults for solving and explaining schedules."""

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
import re

from .config import (
    DEFAULT_OBJECTIVE, DEFAULT_TIME_LIMIT_SECONDS, DEFAULT_WORKERS, ranking_names, time_limit, worker_count,
)
from .domain import arr, integer_value, nonempty_text, obj, whole_number


PHASES = ("gather", "solve", "explain")
HOOK_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")


def _fields(value, allowed, label):
    obj(value, label)
    unknown = set(value).difference(allowed)
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise ValueError(f"{label} has unsupported fields: {names}")


def _text_list(value, label):
    values = [nonempty_text(item, label) for item in arr(value, label, nonempty=True)]
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicates")
    return values


def _json_value(value, label):
    if type(value) in (str, int, bool) or value is None:
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, label)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item, label)
        return
    if type(value) is float and isfinite(value):
        return
    raise ValueError(f"{label} must contain finite JSON data")


def validate_profile(profile: dict) -> dict:
    """Return an independent validated profile, rejecting unsupported settings."""
    _fields(profile, {"profile_version", "name", "objective", "rankings", "workers",
                      "time_limit_seconds", "min_gain_minutes", "guidance", "heuristics"}, "profile")
    if profile.get("profile_version") != "1.0":
        raise ValueError('profile_version must be "1.0"')
    checked = {"profile_version": "1.0", "name": nonempty_text(profile.get("name"), "profile.name")}
    if "objective" in profile:
        objective = _text_list(profile["objective"], "objective")
        for metric in objective:
            if metric not in DEFAULT_OBJECTIVE and not (metric.startswith("rule:") and metric[5:].strip()):
                raise ValueError(f"unknown profile objective metric {metric!r}")
        checked["objective"] = objective
    if "rankings" in profile:
        checked["rankings"] = ranking_names(arr(profile["rankings"], "rankings"))
    if "workers" in profile:
        checked["workers"] = worker_count(integer_value(profile["workers"]))
    if "time_limit_seconds" in profile:
        checked["time_limit_seconds"] = time_limit(profile["time_limit_seconds"])
    if "min_gain_minutes" in profile:
        checked["min_gain_minutes"] = whole_number(profile["min_gain_minutes"],
                                                  "min_gain_minutes", 0)
    if "guidance" in profile:
        guidance = profile["guidance"]
        _fields(guidance, PHASES, "guidance")
        checked["guidance"] = {phase: _text_list(notes, f"guidance.{phase}")
                               for phase, notes in guidance.items()}
    if "heuristics" in profile:
        hooks = []
        names = set()
        for i, spec in enumerate(arr(profile["heuristics"], "heuristics")):
            label = f"heuristics[{i}]"
            _fields(spec, {"name", "params"}, label)
            name = nonempty_text(spec.get("name"), f"{label}.name")
            if not HOOK_NAME.fullmatch(name):
                raise ValueError(f"{label}.name must be a named heuristic identifier")
            if name in names:
                raise ValueError(f"duplicate heuristic name {name!r}")
            names.add(name)
            params = obj(spec.get("params", {}), f"{label}.params")
            _json_value(params, f"{label}.params")
            hooks.append({"name": name, "params": deepcopy(params)})
        checked["heuristics"] = hooks
    return checked


@dataclass(frozen=True)
class ResolvedProfile:
    """Effective inputs for a single invocation."""

    snapshot: dict
    time_limit_seconds: float
    workers: int
    rankings: list[str] | None
    min_gain_minutes: int
    guidance: dict[str, list[str]]
    heuristics: list[dict]
    name: str | None


def resolve_profile(snapshot: dict, profile: dict | None = None, *, time_limit_seconds=None,
                    workers=None, rankings=None) -> ResolvedProfile:
    """Apply a profile per call. Explicit objectives and solver options take precedence."""
    data = deepcopy(obj(snapshot, "snapshot"))
    config = validate_profile(profile) if profile is not None else {}
    if "objective" not in data and "objective" in config:
        data["objective"] = config["objective"]
    if rankings is not None:
        rankings = ranking_names(rankings)
    else:
        rankings = config.get("rankings")
    return ResolvedProfile(
        snapshot=data,
        time_limit_seconds=(time_limit(time_limit_seconds) if time_limit_seconds is not None
                            else config.get("time_limit_seconds", DEFAULT_TIME_LIMIT_SECONDS)),
        workers=worker_count(workers) if workers is not None else config.get("workers", DEFAULT_WORKERS),
        rankings=rankings,
        min_gain_minutes=config.get("min_gain_minutes", 15),
        guidance=config.get("guidance", {}),
        heuristics=config.get("heuristics", []),
        name=config.get("name"),
    )
