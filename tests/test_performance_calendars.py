"""Check prepared-calendar reuse against explicit full-operation window examples.

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

from simulation.config import as_dynamic_scenario
from simulation.engine import build_dynamic_state, run_dynamic_simulation
from simulation.plant import fitting_window, start_inventory_operation
from simulation.routing import build_dynamic_policy_input, choose_dynamic_insertions
from simulation.state import ScenarioDefinitions, WorkstationCalendar, merge_adjacent_windows, prepare_calendars

ROOT = Path(__file__).resolve().parents[1]


class PreparedCalendarTests(unittest.TestCase):
    """Compare cached results with direct fitting and check cache ownership at execution boundaries."""

    # Prepare separate test data and inspect results

    def scenario(self) -> dict:
        """Return owned two-plant inputs with the same local WS1 ID at both plants."""
        scenario = as_dynamic_scenario(json.loads((ROOT / 'tests/fixtures/checkpoint_4c/scenario.json').read_bytes()))
        scenario['policy'] = 'dynamic_quality_aware'
        return scenario

    # Check the expected model or evidence behavior

    def test_explicit_adjacency_only_merge_in_input_order(self) -> None:
        """Pin expected intervals independently of cached/direct searches sharing the merger."""
        cases = [
            ([], ()),
            ([[0, 2], [2, 4], [6, 10]], ((0, 4), (6, 10))),
            ([[0, 4], [2, 6]], ((0, 4), (2, 6))),
            ([[6, 10], [0, 2], [2, 4]], ((6, 10), (0, 4))),
            ([[0, 2], [2 + 1e-12, 4]], ((0, 2), (2 + 1e-12, 4))),
        ]
        for windows, expected in cases:
            with self.subTest(windows=windows):
                before = copy.deepcopy(windows)
                self.assertEqual(merge_adjacent_windows(windows), expected)
                self.assertEqual(windows, before)

    def test_merged_bounds_preserve_types_and_own_interval_storage(self) -> None:
        """Retain outer int/float bounds while detaching mutable input window lists."""
        windows = [[0, 2.0], [2, 4.0], [6.0, 8]]
        result = merge_adjacent_windows(windows)
        self.assertEqual(result, ((0, 4.0), (6.0, 8)))
        self.assertIsInstance(result, tuple)
        self.assertTrue(all(isinstance(window, tuple) for window in result))
        self.assertEqual([[type(bound) for bound in window] for window in result],
                         [[int, float], [float, int]])
        windows[0][0] = 99
        windows[1][1] = 99
        self.assertEqual(result, ((0, 4.0), (6.0, 8)))

    def test_explicit_first_fit_and_fresh_outputs_in_both_paths(self) -> None:
        """Check full fit, overlap order, empty input and exact-closing results explicitly.

        Do not use direct/prepared agreement as the only expected answer: both paths
        now call the same adjacency merger. Returned window lists must be separate.
        """
        cases = [([[0, 2], [2, 4]], 1, 3, [0, 4]),
                 ([[0, 4], [2, 6]], 2, 2, [0, 4]),
                 ([[0, 4], [2, 6]], 3, 2, [2, 6]),
                 ([[6, 10], [0, 4]], 1, 2, [0, 4]),
                 ([], 0, 1, None)]
        for windows, time, duration, expected in cases:
            original_windows = copy.deepcopy(windows)
            scenario = self.scenario()
            station = scenario['plants'][0]['workstations'][0]
            station.update(windows=windows, duration=duration)
            prepared = prepare_calendars(scenario)[('RF1', 'WS1')]
            for calendar in (None, prepared):
                with self.subTest(windows=windows, time=time, cached=calendar is not None):
                    result = fitting_window(station, time, calendar=calendar)
                    self.assertEqual(result, expected)
                    if result is not None:
                        result[0] = 999
                        self.assertEqual(fitting_window(station, time, calendar=calendar), expected)
                        self.assertEqual(station['windows'], original_windows)

    def test_boundaries_adjacency_and_first_fit(self) -> None:
        """Compare direct/prepared searches including adjacent and unchanged overlapping-input semantics."""
        for windows in ([[0, 2], [2, 4], [6, 10]], [[0, 4], [2, 6]], [], [[0, 1e16 + 10]]):
            scenario = self.scenario()
            station = scenario['plants'][0]['workstations'][0]
            station.update(duration=2, windows=windows)
            calendar = prepare_calendars(scenario)[('RF1', 'WS1')]
            for time in (0, 1, 2, 3, 4, 6, 8, 10):
                with self.subTest(windows=windows, time=time):
                    self.assertEqual(fitting_window(station, time, calendar=calendar), fitting_window(station, time))
            result = fitting_window(station, 0, calendar=calendar)
            if result:
                original = calendar.merged_windows
                result[0] = 999
                self.assertEqual(calendar.merged_windows, original)

    def test_busy_or_ineligible_station_does_not_search(self) -> None:
        """Retain F4/F5's early rejection of busy/no-work starts before any calendar arithmetic."""
        scenario = self.scenario()
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        station = scenario['plants'][0]['workstations'][0]
        with patch('simulation.plant.fitting_window', side_effect=RuntimeError('Unnecessary search')):
            self.assertIsNone(start_inventory_operation(scenario, definitions, state, station))
            state.workstations['WS1'] = {'product_id': 'P1'}
            self.assertIsNone(start_inventory_operation(scenario, definitions, state, station))

    def test_qualified_calendars_and_independent_events(self) -> None:
        """Different plants' WS1 calendars and returned event-window lists remain independent."""
        scenario = self.scenario()
        scenario['plants'][0]['workstations'][0]['windows'] = [[8, 10]]
        scenario['plants'][1]['workstations'][0]['windows'] = [[4, 10]]
        index = prepare_calendars(scenario)
        self.assertEqual(index[('RF1', 'WS1')].merged_windows, ((8, 10),))
        self.assertEqual(index[('RF2', 'WS1')].merged_windows, ((4, 10),))
        before = copy.deepcopy(scenario)
        result = run_dynamic_simulation(scenario, calendars=index)
        start = next(event for event in result.events if event['event_type'] == 'operation_start')
        self.assertEqual((start['plant_id'], start['time']), ('RF2', 4))
        start['window'][0] = 999
        self.assertEqual(index[('RF2', 'WS1')].merged_windows, ((4, 10),))
        self.assertEqual(scenario, before)

    def test_compatible_reuse_and_changed_calendars(self) -> None:
        """Reuse immutable bounds only when plant/station, values and JSON numeric types match."""
        scenario = self.scenario()
        original = prepare_calendars(scenario)
        compatible = prepare_calendars(copy.deepcopy(scenario), original)
        self.assertIsNot(compatible, original)
        self.assertIs(compatible[('RF1', 'WS1')], original[('RF1', 'WS1')])
        for windows in ([[0, 10]], [[8.0, 10.0]]):
            changed = copy.deepcopy(scenario)
            changed['plants'][0]['workstations'][0]['windows'] = windows
            prepared = ScenarioDefinitions.from_scenario(changed, calendars=original).calendars
            self.assertIsNot(prepared[('RF1', 'WS1')], original[('RF1', 'WS1')])
            self.assertEqual(prepared[('RF1', 'WS1')].source_windows, tuple(map(tuple, windows)))
        changed = copy.deepcopy(scenario)
        changed['plants'][0]['workstations'][0]['windows'] = [[0, 10]]
        first = run_dynamic_simulation(scenario, calendars=original)
        second = run_dynamic_simulation(changed, calendars=original)
        self.assertNotEqual(first.decisions, second.decisions)

    def test_one_merge_per_decision_across_private_projections(self) -> None:
        """Share two fixed calendars across aware candidate copies within one decision.

        Check the preparation call count and that observation/live data stay unchanged;
        this protects reuse without sharing changing forecast state.
        """
        scenario = self.scenario()
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        state.products['P1'].update(state='at_collection_point', location='CP1')
        boundary = build_dynamic_policy_input(scenario, definitions, state)
        original_boundary, original_state = copy.deepcopy(boundary), copy.deepcopy(state)
        with patch('simulation.state.WorkstationCalendar', wraps=WorkstationCalendar) as constructor:
            proposals, audits = choose_dynamic_insertions(boundary)
        self.assertTrue(proposals)
        self.assertEqual(constructor.call_count, 2)
        self.assertGreater(len(audits[0]['candidates']), 1)
        self.assertEqual(boundary, original_boundary)
        self.assertEqual(state, original_state)

    def test_cached_fitting_keeps_arithmetic_rejection(self) -> None:
        """Prepared windows must not permit the nonadvancing positive operation fixed by F5."""
        scenario = self.scenario()
        station = scenario['plants'][0]['workstations'][0]
        station.update(duration=1, windows=[[1e16, 1e16 + 10]])
        calendar = prepare_calendars(scenario)[('RF1', 'WS1')]
        for prepared in (None, calendar):
            with self.subTest(cached=prepared is not None):
                with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*processing'):
                    fitting_window(station, 1e16, calendar=prepared)
