"""Check incremental route generation against the earlier full-list order.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
The reference calculations protect order, ownership or retained-memory rules;
these are regression checks, not general runtime benchmarks.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation.routing import _reset_route_variants, enumerate_dynamic_candidates, merge_route_stops

ROOT = Path(__file__).resolve().parents[1]


def eager_reset_reference(base: list[dict], facilities: list[str]) -> list[tuple[list[dict], int]]:
    """Return the earlier full-list base/one-reset/two-reset routes for small cases.

    CandidateMemoryTests compare the incremental generator against this independent
    ordering reference. Own copied route variants; do not change the source route.
    """
    variants = [(base, 0)]
    frontier = [base]
    for count in (1, 2):
        following = []
        for route in frontier:
            for index in range(len(route) + 1):
                for facility in facilities:
                    variant = [*copy.deepcopy(route[:index]),
                               {'site_id': facility, 'pickups': [], 'deliveries': []},
                               *copy.deepcopy(route[index:])]
                    following.append(variant)
                    variants.append((variant, count))
        frontier = following
    return variants


class CandidateMemoryTests(unittest.TestCase):
    """Protect all candidate evidence while changing only temporary frontier ownership."""

    # Prepare separate test data and inspect results

    def boundary(self) -> dict:
        """Return a separate real mid-leg policy observation with an extra eligible facility.

        Candidate tests use it to check order, complete evidence and route ownership.
        The source fixture is read only; no run files are written.
        """
        records = [json.loads(line) for line in (ROOT / 'tests/fixtures/checkpoint_4b/dispatch_decisions.jsonl').read_text().splitlines()]
        boundary = next(record['policy_input'] for record in records if record['time'] == 5)
        boundary['sites'].append({'id': 'RF2', 'coordinates': [0, 1], 'kind': 'plant'})
        boundary['vehicles'][0]['payload_capacity'] = 1
        return boundary

    # Check the expected model or evidence behavior

    def test_exact_breadth_first_order_including_duplicates(self) -> None:
        """Keep base/one/two-reset order even when coalescing produces duplicate routes."""
        base = [{'site_id': 'CP1', 'pickups': ['P1'], 'deliveries': []},
                {'site_id': 'RF1', 'pickups': [], 'deliveries': ['P1']}]
        for facilities in ([], ['RF1'], ['RF1', 'RF2'], ['RF1', 'RF1']):
            with self.subTest(facilities=facilities):
                expected = eager_reset_reference(copy.deepcopy(base), facilities)
                observed = list(_reset_route_variants(copy.deepcopy(base), facilities))
                self.assertEqual(json.dumps(observed), json.dumps(expected))
                self.assertEqual([merge_route_stops(route) for route, _ in observed],
                                 [merge_route_stops(route) for route, _ in expected])

    def test_complete_candidate_json_matches_eager_reference(self) -> None:
        """Compare IDs, first-duplicate retention, scores, reasons and field order on the actual call path."""
        boundary = self.boundary()
        request = next(request for request in boundary['requests'] if request['assigned_vehicle'] is None)
        with patch('simulation.routing._reset_route_variants', side_effect=eager_reset_reference):
            expected = enumerate_dynamic_candidates(boundary, request)
        observed = enumerate_dynamic_candidates(boundary, request)
        self.assertEqual(json.dumps(observed), json.dumps(expected))
        self.assertTrue(any(not candidate['feasible'] for candidate in observed))
        self.assertTrue(any(candidate['extra_visits'] == 2 for candidate in observed))
        self.assertEqual([candidate['candidate_id'] for candidate in observed],
                         [f'C{index:05d}' for index in range(1, len(observed) + 1)])

    def test_candidate_routes_and_observation_are_independent(self) -> None:
        """Mutating any retained route/service list must leave other candidates and input facts unchanged."""
        boundary = self.boundary()
        original = copy.deepcopy(boundary)
        request = next(request for request in boundary['requests'] if request['assigned_vehicle'] is None)
        candidates = enumerate_dynamic_candidates(boundary, request)
        untouched = copy.deepcopy(candidates[1:])
        candidates[0]['new_remaining_stops'][0]['pickups'].append('ALTERED')
        candidates[0]['old_remaining_stops'][0]['deliveries'].append('ALTERED')
        self.assertEqual(candidates[1:], untouched)
        self.assertEqual(boundary, original)

    def test_unlimited_range_keeps_only_base_variants(self) -> None:
        """Unlimited vehicles must not create reset variants or invoke their construction helper."""
        boundary = self.boundary()
        boundary['vehicles'][0].update(maximum_range=None, remaining_range=None)
        request = next(request for request in boundary['requests'] if request['assigned_vehicle'] is None)
        with patch('simulation.routing._reset_route_variants', side_effect=RuntimeError('Unexpected reset search')):
            candidates = enumerate_dynamic_candidates(boundary, request)
        self.assertTrue(candidates)
        self.assertTrue(all(candidate['extra_visits'] == 0 for candidate in candidates))
