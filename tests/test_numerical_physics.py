"""Check that physical times and distances remain representable numbers.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from simulation.config import validate_scenario
from simulation.engine import build_dynamic_state, run_dynamic_simulation
from simulation.forecasting import forecast_dynamic_insertion
from simulation.plant import fitting_window
from simulation.runner import run_once
from simulation.state import ScenarioDefinitions
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]


def numerical_scenario(*, clock: float = 0, distance: float = 0, speed: float = 1,
                       duration: float = 1) -> dict:
    """Return owned format-two inputs; tests vary only the physical scale, without generating files."""
    scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4a/scenario.json').read_bytes())
    scenario['horizon'] = clock + 10
    scenario['collection_point']['coordinates'] = [distance, 0]
    scenario['vehicles'][0]['speed'] = speed
    scenario['products'] = scenario['products'][:1]
    scenario['products'][0]['release_time'] = clock
    scenario['trips'][0]['departure_time'] = clock
    scenario['plant']['workstations'] = scenario['plant']['workstations'][:1]
    scenario['plant']['workstations'][0].update(duration=duration, windows=[[clock, clock + 10]])
    validate_scenario(scenario)
    return scenario


class NumericalPhysicsTests(unittest.TestCase):
    """Use accepted primitive inputs and test observable results or explicit derived-arithmetic rejection."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Own temporary scenarios and run artifacts, removed after each test."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def run_case(self, scenario: dict, policy: str = 'static_quality_unaware') -> Path:
        """Write a temporary declaration, execute the actual runner, and return its saved folder."""
        self.counter += 1
        source = self.root / f'input-{self.counter}.json'
        source.write_text(json.dumps(scenario) + '\n', encoding='utf-8')
        output = self.root / f'run-{self.counter}'
        run_once(source, output, policy=policy)
        return output

    # Check the expected model or evidence behavior

    def test_tiny_speed_rejected_before_departure(self) -> None:
        """Finite positive primitive speed must not create an infinite duration in any inventory policy."""
        for policy in ('static_quality_unaware', 'static_quality_aware',
                       'dynamic_quality_unaware', 'dynamic_quality_aware'):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*travel.duration'):
                    self.run_case(numerical_scenario(distance=1, speed=1e-320), policy)

    def test_extreme_finite_coordinates(self) -> None:
        """Unrepresentable geometry fails explicitly rather than leaking infinity to JSON."""
        scenario = numerical_scenario(distance=1e308)
        scenario['plant']['coordinates'] = [-1e308, 0]
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*distance'):
            self.run_case(scenario)

    def test_positive_travel_must_advance(self) -> None:
        """Both duration underflow and rounded absolute arrival must reject positive-distance travel."""
        for scenario in (numerical_scenario(distance=1e-320, speed=1e308),
                         numerical_scenario(clock=1e16, distance=1)):
            with self.subTest(scenario=scenario):
                with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*travel'):
                    self.run_case(scenario)

    def test_positive_processing_must_advance(self) -> None:
        """A positive operation at a coarse absolute clock cannot become instantaneous."""
        for policy in ('static_quality_unaware', 'static_quality_aware',
                       'dynamic_quality_unaware', 'dynamic_quality_aware'):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*processing'):
                    self.run_case(numerical_scenario(clock=1e16), policy)

    def test_small_positive_and_zero_distance_are_valid(self) -> None:
        """Tiny representable operations and immediate zero-distance legs retain normal settlement."""
        for duration in (1, 1e-12):
            with self.subTest(duration=duration):
                output = self.run_case(numerical_scenario(duration=duration))
                report = verify_run(output)
                self.assertEqual(report['status'], 'PASS', report)
                events = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
                starts = [event for event in events if event['event_type'] == 'operation_start']
                self.assertEqual(starts[0]['end_time'], duration)
                self.assertGreater(starts[0]['end_time'], starts[0]['start_time'])

    def test_exact_horizon_and_window_boundaries(self) -> None:
        """An operation ending at closing/horizon is allowed; arrival/start at horizon stays unfinished."""
        for horizon, distance, duration, completed in ((2, 0, 2, 1), (2, 1, 1, 0), (3, 1, 1, 1)):
            scenario = numerical_scenario(distance=distance, duration=duration)
            scenario['horizon'] = horizon
            scenario['plant']['workstations'][0]['windows'] = [[0, 3 if horizon == 2 and distance else horizon]]
            output = self.run_case(scenario)
            self.assertEqual(verify_run(output)['status'], 'PASS')
            self.assertEqual(json.loads((output / 'summary.json').read_bytes())['remanufactured'], completed)

    def test_partial_leg_and_arrival_beyond_horizon(self) -> None:
        """A finite arrival after the horizon is valid and accrues only actual traveled distance."""
        scenario = numerical_scenario(distance=100)
        scenario['horizon'] = 2
        output = self.run_case(scenario)
        self.assertEqual(verify_run(output)['status'], 'PASS')
        self.assertEqual(json.loads((output / 'summary.json').read_bytes())['vehicle_distance'], 2)

    def test_future_closed_calendar_is_no_opportunity(self) -> None:
        """An irrelevant future window is not an attempted unrepresentable operation."""
        scenario = numerical_scenario()
        scenario['plant']['workstations'][0]['windows'] = [[1e16, 1e16 + 10]]
        output = self.run_case(scenario)
        self.assertEqual(verify_run(output)['status'], 'PASS')
        self.assertEqual(json.loads((output / 'summary.json').read_bytes())['unfinished'], 1)

    def test_processing_completion_overflow(self) -> None:
        """Finite start/duration must not create an infinite end during a calendar-fit calculation."""
        station = {'id': 'WS1', 'duration': 1e308, 'windows': [[1e308, 1.7e308]]}
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*processing.end'):
            fitting_window(station, 1e308)

    def test_accumulated_distance_overflow(self) -> None:
        """Two individually finite traveled legs cannot produce an infinite cumulative distance."""
        scenario = numerical_scenario(distance=1e308, speed=1e308)
        scenario['plant']['workstations'][0]['windows'] = []
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*distance'):
            self.run_case(scenario)

    def test_accumulated_inventory_wait_overflow(self) -> None:
        """The aggregate wait must stay finite even when each unfinished product's wait is finite."""
        scenario = numerical_scenario()
        scenario['horizon'] = 1e308
        scenario['plant']['workstations'][0]['windows'] = []
        scenario['vehicles'][0]['payload_capacity'] = 2
        scenario['products'].append({**copy.deepcopy(scenario['products'][0]), 'id': 'P2'})
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*plant_inventory_wait'):
            self.run_case(scenario)


    def test_verifier_checks_observed_positive_duration_without_tolerance(self) -> None:
        """Even an end within physical tolerance cannot equal a positive operation's start."""
        output = self.run_case(numerical_scenario(duration=1e-12))
        path = output / 'events.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        event = next(event for event in records if event['event_type'] == 'operation_start')
        event['end_time'] = event['start_time']
        path.write_text(''.join(json.dumps(event) + '\n' for event in records))
        report = verify_run(output)
        self.assertEqual(report['exit_code'], 1)
        finding = next(f for f in report['findings'] if f['rule'] == 'physical.processing_progress')
        self.assertEqual(finding['event_sequence'], event['sequence'])

    def test_verifier_checks_actual_completion_clock(self) -> None:
        """A declared positive end does not authorize an actual completion at the active job's start."""
        output = self.run_case(numerical_scenario(duration=1e-12))
        path = output / 'events.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for event in records:
            if event['event_type'] in ('operation_complete', 'remanufactured'):
                event['time'] = 0
        path.write_text(''.join(json.dumps(event) + '\n' for event in records))
        report = verify_run(output)
        self.assertEqual(report['exit_code'], 1, report)
        self.assertTrue(any(f['rule'] == 'physical.processing_progress' for f in report['findings']), report)

    def test_verifier_checks_actual_arrival_clock(self) -> None:
        """Tiny positive travel cannot arrive at departure merely because time differences fall within tolerance."""
        scenario = numerical_scenario(distance=1e-12)
        output = self.run_case(scenario)
        path = output / 'events.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        arrival = next(event for event in records if event['event_type'] == 'leg_arrival')
        arrival['time'] = 0
        path.write_text(''.join(json.dumps(event) + '\n' for event in records))
        report = verify_run(output)
        self.assertEqual(report['exit_code'], 1, report)
        self.assertTrue(any(f['rule'] == 'physical.travel_progress' for f in report['findings']), report)

    def test_subnormal_geometry_can_still_be_valid(self) -> None:
        """Independent geometry must not lose a valid tiny distance by squaring it to zero."""
        scenario = numerical_scenario(distance=1e-200, speed=1e-200)
        output = self.run_case(scenario)
        self.assertEqual(verify_run(output)['status'], 'PASS')
        for policy in ('static_quality_aware', 'dynamic_quality_unaware', 'dynamic_quality_aware'):
            with self.subTest(policy=policy):
                output = self.run_case(scenario, policy)
                self.assertEqual(verify_run(output)['status'], 'PASS')

    def test_copied_forecast_rejects_without_mutation(self) -> None:
        """Invalid candidate timing propagates, without modifying caller-owned context or live state."""
        from simulation.config import as_dynamic_scenario
        scenario = as_dynamic_scenario(numerical_scenario(clock=1e16))
        scenario['policy'] = 'dynamic_quality_aware'
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        state.time = 1e16
        state.products['P1'].update(state='at_collection_point', location='CP1')
        from simulation.routing import build_dynamic_policy_input
        boundary = build_dynamic_policy_input(scenario, definitions, state)
        context = boundary['forecast_context']
        candidate = {'product_id': 'P1', 'vehicle_id': 'AV1', 'destination': 'RF1',
                     'new_remaining_stops': [{'site_id': 'CP1', 'pickups': ['P1'], 'deliveries': []},
                                             {'site_id': 'RF1', 'pickups': [], 'deliveries': ['P1']}]}
        before_context, before_state = copy.deepcopy(context), copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*processing'):
            forecast_dynamic_insertion(boundary, candidate)
        self.assertEqual(context, before_context)
        self.assertEqual(state, before_state)

    def test_resumed_forecast_rejects_nonadvancing_active_job(self) -> None:
        """A copied already-running job cannot bypass the same positive-duration clock guard."""
        from simulation.config import as_dynamic_scenario
        scenario = as_dynamic_scenario(numerical_scenario(clock=1e16))
        state = build_dynamic_state(scenario, ScenarioDefinitions.from_scenario(scenario))
        state.time = 1e16
        state.products['P1'].update(state='processing', location='WS1', actual_destination='RF1')
        state.workstations['WS1'] = {'product_id': 'P1', 'plant_id': 'RF1', 'workstation_id': 'WS1',
            'operation_id': 1, 'operation_index': 0, 'start_time': 1e16, 'end_time': 1e16, 'window': [1e16, 1e16 + 10]}
        copied = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'Physical arithmetic.*processing'):
            run_dynamic_simulation(scenario, initial_state=copied, dispatch_enabled=False)
        self.assertEqual(state.products['P1']['state'], 'processing')
        self.assertEqual(state.events, [])

    def test_unused_station_needs_no_completion_calculation(self) -> None:
        """A calendar alone does not attempt an unrepresentable job when there is no released work."""
        scenario = numerical_scenario(clock=1e16)
        scenario['products'] = []
        scenario['trips'] = []
        output = self.run_case(scenario)
        self.assertEqual(verify_run(output)['status'], 'PASS')

    def test_verifier_rejects_rounded_positive_processing(self) -> None:
        """Independently reject old zero-time evidence, even when start+duration repeats the same float."""
        output = self.run_case(numerical_scenario())
        shift_fields = {'time', 'release_time', 'departure_time', 'arrival_time', 'start_time', 'end_time', 'horizon'}
        def shift_times(value: object, field: str = '') -> object:
            """Shift only physical clocks in an owned saved-evidence copy; keep durations/seeds exact."""
            if isinstance(value, dict):
                return {key: shift_times(item, key) for key, item in value.items()}
            if isinstance(value, list):
                return [shift_times(item, field) for item in value]
            if isinstance(value, (int, float)) and (field in shift_fields or field in ('windows', 'window', 'release', 'pickup', 'admission', 'completion')):
                return value + 1e16
            return value
        for name in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'final_state.json'):
            path = output / name
            if name.endswith('jsonl'):
                path.write_text(''.join(json.dumps(shift_times(json.loads(line))) + '\n' for line in path.read_text().splitlines()))
            else:
                path.write_text(json.dumps(shift_times(json.loads(path.read_bytes()))) + '\n')
        manifest = json.loads((output / 'run_manifest.json').read_bytes())
        manifest['scenario_sha256'] = hashlib.sha256((output / 'scenario.json').read_bytes()).hexdigest()
        (output / 'run_manifest.json').write_text(json.dumps(manifest) + '\n')
        report = verify_run(output)
        self.assertEqual(report['exit_code'], 1, report)
        self.assertTrue(any(finding['rule'] == 'physical.processing_progress' for finding in report['findings']), report)
