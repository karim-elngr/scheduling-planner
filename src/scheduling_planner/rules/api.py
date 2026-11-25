"""The public interface for compiling a scheduling rule into CP-SAT."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from ortools.sat.python import cp_model

if TYPE_CHECKING:
    from ..model import BuildContext


RuleValue = int | cp_model.IntVar


@dataclass(frozen=True)
class RuleEffect:
    """Exact unweighted violation and a flag indicating missing evidence.

    Units must be bounded and nonnegative. Unknown must be Boolean. A rule with
    a potential violation supplies a reason; a rule with a potential unknown
    supplies a question. The registry scores unknown outcomes at zero pending
    the answer. Return variables from the current model only.
    """
    units: RuleValue = 0
    unknown: RuleValue = 0
    reason: str = ""
    question: str = ""


class RuleCompiler(Protocol):
    def __call__(self, context: "BuildContext", params: Mapping[str, Any]) -> RuleEffect: ...


@dataclass(frozen=True)
class RuleBinding:
    id: str
    kind: str
    mode: str
    weight: int
    source: str | None
    units: RuleValue
    unknown: RuleValue
    reason: str
    question: str


def require_keys(params, required=(), optional=()):
    """Validate an exact, named parameter contract for built-ins and custom rules."""
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    missing = set(required) - params.keys()
    extra = params.keys() - (set(required) | set(optional))
    if missing:
        raise ValueError(f"missing parameter(s): {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"unexpected parameter(s): {', '.join(sorted(map(str, extra)))}")
