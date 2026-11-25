"""Validate extension configuration and bind rule effects to reportable IDs."""

from collections.abc import Mapping

from ortools.sat.python import cp_model

from ..domain import whole_number
from .api import RuleBinding, RuleEffect, require_keys
from .builtin import finish_by, precedence


BUILTINS = {"precedence": precedence, "finish_by": finish_by}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _upper(model, value, label, *, boolean=False):
    if type(value) in (int, bool):
        low = upper = int(value)
    elif isinstance(value, cp_model.IntVar):
        if value.model_proto is not model.proto:
            raise ValueError(f"{label} must belong to the current model")
        domain = value.proto.domain
        if not len(domain):
            raise ValueError(f"{label} has an empty domain")
        low, upper = domain[0], domain[len(domain) - 1]
    else:
        raise ValueError(f"{label} must be an integer or IntVar; bind expressions to a bounded variable")
    if low < 0 or (boolean and upper > 1):
        raise ValueError(f"{label} requires {'a Boolean domain' if boolean else 'a nonnegative domain'}")
    return upper


def _bind(ctx, spec, compiler):
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    mode = spec["mode"]
    if mode not in ("hard", "soft"):
        raise ValueError("mode must be hard or soft")
    if mode == "hard" and "weight" in spec:
        raise ValueError("only a soft rule accepts a weight")
    weight = whole_number(spec.get("weight", 1), "weight", 1)
    source = _text(spec["source"], "source") if "source" in spec else None
    effect = compiler(ctx, params)
    if not isinstance(effect, RuleEffect):
        raise ValueError("compiler must return a RuleEffect")
    model = ctx.model
    upper = _upper(model, effect.units, "units")
    variable_unknown = _upper(model, effect.unknown, "unknown", boolean=True)
    if upper:
        _text(effect.reason, "reason")
    if variable_unknown:
        _text(effect.question, "question")
    # Mask unresolved costs in both hard filtering and optimistic scoring.
    units = effect.units
    if variable_unknown and upper:
        if isinstance(effect.unknown, (int, bool)):
            units = 0
        else:
            units = model.new_int_var(0, upper, f"known_units[{spec['id']}]")
            model.add(units == 0).only_enforce_if(effect.unknown)
            model.add(units == effect.units).only_enforce_if(~effect.unknown)
    if int(weight) * upper >= 2**63:
        raise ValueError("weighted units must fit into signed 64-bit arithmetic")
    return RuleBinding(spec["id"], spec["kind"], mode, weight, source,
                       units, effect.unknown, effect.reason, effect.question)


def compile_rules(ctx, specs, registry=None):
    if not isinstance(specs, list):
        raise ValueError("constraints must be an array")
    compilers = dict(BUILTINS)
    if registry is not None:
        if not isinstance(registry, Mapping):
            raise ValueError("registry must map names to CP-SAT rule compilers")
        for name, compiler in registry.items():
            _text(name, "custom rule kind")
            if name in compilers:
                raise ValueError(f"{name} would replace a built-in rule")
            if not callable(compiler):
                raise ValueError(f"{name} must be a callable rule compiler")
            compilers[name] = compiler
    bound, seen = [], set()
    for spec in specs:
        require_keys(spec, required=["id", "kind", "mode"], optional=["weight", "params", "source"])
        rule_id = _text(spec["id"], "rule id")
        if rule_id in seen:
            raise ValueError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)
        kind = _text(spec["kind"], "rule kind")
        if kind not in compilers:
            raise ValueError(f"{rule_id}: unknown rule kind {kind!r}")
        try:
            bound.append(_bind(ctx, spec, compilers[kind]))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise ValueError(f"rule {rule_id!r} failed to compile: {exc}") from exc
    return bound
