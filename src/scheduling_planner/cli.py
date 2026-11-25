"""Local command-line interface for scheduling and reviewable recommendations."""

import argparse
from contextlib import ExitStack, redirect_stdout
from decimal import Decimal, InvalidOperation
from importlib import import_module
from importlib.resources import files
import json
from math import isfinite
from pathlib import Path
import runpy
import sys

from ._version import __version__
from .brief import brief
from .config import DEFAULT_TIME_LIMIT_SECONDS, DEFAULT_WORKERS, RANKINGS
from .contracts import output_schema
from .profiles import resolve_profile, validate_profile


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"invalid JSON number {value}")


def _json_float(token):
    number = float(token)
    if not isfinite(number):
        raise ValueError("JSON number exceeds the supported floating-point range")
    if number.is_integer():
        # A binary float can collapse adjacent integer decision IDs or turn an
        # invalid fractional move count into a valid integer.
        try:
            exact = Decimal(token)
        except InvalidOperation as error:
            raise ValueError("JSON number has an unsupported exponent") from error
        if exact != number:
            if exact == exact.to_integral_value():
                return int(exact)
            raise ValueError("JSON fraction would round to an integer; use a representable value")
    return number


def _read_json(path):
    options = {"object_pairs_hook": _json_object, "parse_constant": _bad_constant, "parse_float": _json_float}
    if path == "-":
        binary = getattr(sys.stdin, "buffer", None)
        if binary is not None and not sys.stdin.isatty():
            return json.loads(binary.read().decode("utf-8"), **options)
        return json.load(sys.stdin, **options)
    with open(path, encoding="utf-8") as stream:
        return json.load(stream, **options)


def _registry(path, module, name, stack):
    if path is None and module is None:
        return None
    source = path or module
    try:
        if path is None:
            registry = getattr(import_module(module), name, None)
        else:
            parent = str(path.resolve().parent)
            sys.path.insert(0, parent)
            stack.callback(sys.path.remove, parent)
            registry = runpy.run_path(str(path)).get(name)
    except Exception as error:
        raise ValueError(f"could not load trusted extension {source}: {error}") from error
    if not isinstance(registry, dict):
        raise ValueError(f"trusted extension {source} must export a {name} dictionary")
    return registry


def demo_snapshot():
    """Return a new copy of the synthetic five-person scheduling example."""
    data = files("scheduling_planner").joinpath("data", "demo.json")
    return json.loads(data.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="schedule-plan", description="Find group meeting plans with Google OR-Tools CP-SAT.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("snapshot", nargs="?", help="JSON snapshot path, or - for stdin (default)")
    parser.add_argument("--demo", action="store_true", help="use the packaged, five-person scheduling scenario")
    parser.add_argument("--profile", type=Path, help="team preference and advice profile (JSON)")
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument("--rules-file", type=Path, help="trusted Python file exporting RULES")
    rules.add_argument("--rules-module", help="trusted installed Python module exporting RULES")
    hooks = parser.add_mutually_exclusive_group()
    hooks.add_argument("--heuristics-file", type=Path, help="trusted Python file exporting HEURISTICS for advice")
    hooks.add_argument("--heuristics-module", help="trusted installed module exporting HEURISTICS for advice")
    parser.add_argument("--time-limit", type=float,
                        help=f"shared solve budget in seconds (default {DEFAULT_TIME_LIMIT_SECONDS:g})")
    parser.add_argument("--workers", type=int, help=f"CP-SAT search workers (default {DEFAULT_WORKERS})")
    parser.add_argument("--ranking", dest="rankings", action="append", choices=RANKINGS,
                        help="return this ranking; repeat to request several (default all three)")
    parser.add_argument("--advise", action="store_true", help="include full recommendations and solver proofs")
    parser.add_argument("--brief", action="store_true", help="return compact advice JSON")
    parser.add_argument("--compare-moves", action="store_true", help="compare permitted move counts within one budget")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--show-profile", action="store_true", help="validate and print the provided profile")
    for flag, schema in [("--schema", "snapshot-v1"), ("--profile-schema", "advisor-profile-v1"),
                         ("--result-schema", "result-v1"), ("--advice-schema", "advice-v1"),
                         ("--brief-schema", "brief-v1")]:
        modes.add_argument(flag, dest="schema", action="store_const", const=schema,
                           help=f"print the packaged {schema} schema and exit")
    args = parser.parse_args(argv)
    with_advice = args.advise or args.brief or args.compare_moves or args.demo
    if (args.heuristics_file or args.heuristics_module) and not with_advice:
        parser.error("a heuristic extension requires --advise, --brief or --compare-moves")
    if (args.show_profile or args.schema) and with_advice:
        parser.error("choose profile/schema inspection or scheduling output")
    if args.show_profile and args.profile is None:
        parser.error("--show-profile requires --profile")
    if args.demo and args.snapshot is not None:
        parser.error("--demo supplies a snapshot; omit the input path")
    if args.schema:
        if args.schema in ("result-v1", "advice-v1", "brief-v1"):
            print(json.dumps(output_schema(args.schema.removesuffix("-v1")), indent=2))
        else:
            schema = files("scheduling_planner").joinpath("schemas", f"{args.schema}.schema.json")
            print(schema.read_text(encoding="utf-8"), end="")
        return
    try:
        profile = validate_profile(_read_json(args.profile)) if args.profile is not None else None
        if args.show_profile:
            print(json.dumps(profile, indent=2, allow_nan=False))
            return
        snapshot = demo_snapshot() if args.demo else _read_json(args.snapshot or "-")
        controls = {"time_limit_seconds": args.time_limit, "workers": args.workers, "rankings": args.rankings}
        with ExitStack() as stack, redirect_stdout(sys.stderr):
            registry = _registry(args.rules_file, args.rules_module, "RULES", stack)
            if with_advice:
                from .advisor import advise

                heuristics = _registry(args.heuristics_file, args.heuristics_module, "HEURISTICS", stack)
                result = advise(snapshot, profile=profile, registry=registry, heuristics=heuristics,
                                compare_moves=args.compare_moves or args.demo, **controls)
                if args.brief or (args.demo and not args.advise):
                    result = brief(result)
            else:
                from .engine import plan

                settings = resolve_profile(snapshot, profile, **controls)
                result = plan(settings.snapshot, registry=registry, time_limit_seconds=settings.time_limit_seconds,
                              workers=settings.workers, rankings=settings.rankings)
        print(json.dumps(result, indent=2, allow_nan=False))
    except ModuleNotFoundError as error:
        if error.name != "ortools":
            raise
        parser.exit(1, "schedule-plan: solving requires OR-Tools. Install the project and its dependencies "
                       "in this Python environment (see the README quickstart).\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"schedule-plan: {error}\n")
