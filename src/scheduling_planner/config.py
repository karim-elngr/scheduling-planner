"""Solve settings shared by the Python APIs, team profiles and command line."""

from math import isfinite


DEFAULT_TIME_LIMIT_SECONDS = 30.0
DEFAULT_WORKERS = 1
DEFAULT_OBJECTIVE = ("protected_moves", "soft_penalty", "moved_meetings", "people_disrupted", "start")
RANKINGS = ("preferred", "earliest", "least_disruption")


def time_limit(value) -> float:
    """Require a positive finite number representable by the native solver."""
    try:
        valid = type(value) in (int, float) and value > 0 and isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("time_limit_seconds must be a positive finite number")
    return float(value)


def worker_count(value) -> int:
    if type(value) is not int or not 1 <= value <= 256:
        raise ValueError("workers must be an integer between 1 and 256")
    return value


def ranking_names(value) -> list[str]:
    if (not isinstance(value, (list, tuple)) or not value
            or any(not isinstance(name, str) or name not in RANKINGS for name in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"rankings must be a nonempty selection of distinct names from {RANKINGS}")
    return list(value)
