"""Solve and explain group meeting plans using Google OR-Tools CP-SAT."""

from ._version import __version__
from .brief import brief
from .contracts import output_schema
from .profiles import resolve_profile, validate_profile

__all__ = ["__version__", "advise", "brief", "output_schema", "plan", "resolve_profile", "validate_profile"]


def __getattr__(name):
    """Load the native solver only for public APIs that use it."""
    if name == "plan":
        from .engine import plan
        return plan
    if name == "advise":
        from .advisor import advise
        return advise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
