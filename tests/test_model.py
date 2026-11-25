"""Cross-check the native calendar resource against independent pairwise semantics."""
from itertools import combinations, product
from random import Random
import unittest

from ortools.sat.python import cp_model as cp

from scheduling_planner.domain import covers, gap_count, gaps, iso, merge, parse_snapshot
from scheduling_planner.model import build_model, calendar_questions


class CollectStarts(cp.CpSolverSolutionCallback):
    def __init__(self, variables):
        super().__init__()
        self.variables = variables
        self.rows = set()

    def on_solution_callback(self):
        self.rows.add(tuple(self.value(var) for var in self.variables))


def expected_placements(problem):
    meetings = (problem.request, *problem.events)
    domains = [problem.request.starts] + [(e.original, *e.starts) for e in problem.events]
    result = set()
    for starts in product(*domains):
        if sum(s != e.original for s, e in zip(starts[1:], meetings[1:], strict=True)) > problem.max_moves:
            continue
        valid = True
        for left, right in combinations(range(len(meetings)), 2):
            a, b = meetings[left], meetings[right]
            if starts[left] == a.original and starts[right] == b.original:
                continue
            for pid in a.people & b.people:
                rest = problem.people[pid].rest
                if (starts[left] < starts[right] + b.duration + rest and
                        starts[right] < starts[left] + a.duration + rest):
                    valid = False
        if valid:
            result.add(starts)
    return result


class CalendarModelTests(unittest.TestCase):
    def test_indexed_evidence_matches_minute_by_minute_coverage(self):
        for supplied in ([], [(2, 4)], [(1, 3), (3, 4), (6, 7), (8, 10)]):
            windows = merge(supplied)
            for start, end in combinations(range(-1, 13), 2):
                missing = [t for t in range(start, end) if not any(a <= t < b for a, b in supplied)]
                expected = merge([(t, t + 1) for t in missing])
                self.assertEqual(covers(windows, start, end), not missing)
                self.assertEqual(gap_count(windows, start, end), len(expected))
                self.assertEqual(tuple(gaps(windows, start, end)), expected)

    def test_seeded_native_search_matches_every_feasible_placement(self):
        # Tiny independent oracle includes preexisting conflicts, exact gap boundaries,
        # shared occurrences, fixed events, move caps and multiple simultaneous moves.
        rng = Random(887)
        for case in range(24):
            with self.subTest(case=case):
                people = {pid: {"available": [[iso(0), iso(600)]],
                                "rest_minutes": rng.choice([0, 5, 10])} for pid in ("A", "B", "C")}
                times = list(range(0, 130, 10))
                request = {"attendees": ["A", "B"], "duration_minutes": 15,
                           "starts": [iso(t) for t in rng.sample(times, 4)]}
                events = []
                for i in range(4):
                    original, *alternates = rng.sample(times, 1 + rng.randrange(3))
                    events.append({
                        "id": str(i), "attendees": rng.sample(sorted(people), rng.randint(1, 2)),
                        "duration_minutes": rng.choice([10, 20, 30]), "start": iso(original),
                        "alternatives": list(map(iso, alternates)),
                    })
                problem = parse_snapshot({"request": request, "people": people, "events": events,
                                          "max_moves": rng.randrange(4)})
                ctx = build_model(problem)
                self.assertEqual(ctx.model.validate(), "")
                solver = cp.CpSolver()
                solver.parameters.enumerate_all_solutions = True
                solver.parameters.num_workers = 1
                output = CollectStarts([s.start for s in ctx.slots.values()])
                self.assertIn(solver.solve(ctx.model, output), (cp.OPTIMAL, cp.INFEASIBLE))
                self.assertEqual(output.rows, expected_placements(problem))

    def test_unknown_coverage_fragments_and_preserved_old_commitment(self):
        snapshot = {
            "request": {"attendees": ["A"], "starts": [iso(30)], "duration_minutes": 30},
            "people": {"A": {"available": [[iso(40), iso(50)]],
                             "possible": [[iso(30), iso(60)]]}},
            "events": [{"id": "earlier", "attendees": ["A"], "duration_minutes": 5, "start": iso(0)}],
        }
        ctx = build_model(parse_snapshot(snapshot))
        solver = cp.CpSolver()
        self.assertEqual(solver.solve(ctx.model), cp.OPTIMAL)
        questions = calendar_questions(ctx, solver.value)
        self.assertEqual([(q["event_id"], q["start"], q["end"]) for q in questions],
                         [("$request", iso(30), iso(40)), ("$request", iso(50), iso(60))])
        self.assertEqual(sum(solver.value(n) for n, _ in ctx.unknowns), 2)
        ctx.model.add(sum(n for n, _ in ctx.unknowns) == 0)
        self.assertEqual(solver.solve(ctx.model), cp.INFEASIBLE)

    def test_oversized_move_and_rest_limits_use_finite_horizon_semantics(self):
        snapshot = {
            "request": {"attendees": ["A"], "starts": [iso(100)], "duration_minutes": 10},
            "people": {"A": {"available": [[iso(0), iso(300)]], "rest_minutes": 10**40}},
            "events": [{"id": "existing", "attendees": ["A"], "duration_minutes": 10,
                        "start": iso(10), "alternatives": [iso(200)]}],
            "max_moves": 10**40,
        }
        ctx = build_model(parse_snapshot(snapshot))
        self.assertEqual(ctx.model.validate(), "")
        self.assertEqual(cp.CpSolver().solve(ctx.model), cp.INFEASIBLE)
