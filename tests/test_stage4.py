"""Check shared execution ordering, exact horizon events and plant ownership.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import engine
from simulation.config import as_dynamic_scenario
from simulation.state import (
    InventoryRunState, Record, ScenarioDefinitions, initialize_plant_states, physical_snapshot,
)
from simulation.runner import run_once
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]


class StageFourTests(unittest.TestCase):
    """Run crafted boundary cases through production execution and independent verification."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate automatically cleaned run evidence for each test."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case_number = 0

    def run_case(self, scenario: Record) -> tuple[list[Record], Record, list[Record]]:
        """Save temporary inputs, execute the runner and require independently valid artifacts."""
        self.case_number += 1
        source = self.root / f'case-{self.case_number}.json'
        source.write_text(json.dumps(scenario), encoding='utf-8')
        folder = self.root / f'run-{self.case_number}'
        run_once(source, folder)
        self.assertEqual(verify_run(folder)['status'], 'PASS')
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        final = json.loads((folder / 'final_state.json').read_text())
        decisions = [json.loads(line) for line in (folder / 'dispatch_decisions.jsonl').read_text().splitlines()]
        return events, final, decisions

    def simultaneous_static_scenario(self) -> Record:
        """Build arrivals, releases, completion and scheduled dispatch at time four."""
        scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4a/scenario.json').read_bytes())
        scenario['horizon'] = 8
        scenario['plant'].update(inventory_capacity=1, workstations=[
            {'id': 'WS1', 'operation_id': 1, 'duration': 2, 'windows': [[0, 20]]},
        ])
        vehicle = scenario['vehicles'][0]
        product = scenario['products'][0]
        scenario['vehicles'] = [{**vehicle, 'id': f'AV{index}'} for index in range(3)]
        scenario['products'] = [
            {**product, 'id': f'P{index}', 'release_time': release}
            for index, release in enumerate((0, 1, 4))
        ]
        scenario['trips'] = [
            {'vehicle_id': f'AV{index}', 'departure_time': departure, 'stops': ['CP1', 'RF1']}
            for index, departure in enumerate((0, 2, 4))
        ]
        return scenario

    # Check the expected model or evidence behavior

    def test_static_settlement_precedes_same_time_departure(self) -> None:
        """Complete P0, release P2, admit/start P1, then dispatch P2 at time four."""
        for policy in ('static_quality_unaware', 'static_quality_aware'):
            with self.subTest(policy=policy):
                scenario = self.simultaneous_static_scenario()
                scenario['policy'] = policy
                events, final, decisions = self.run_case(scenario)
                expected = [
                    ('operation_complete', 'P0'), ('return_release', 'P2'),
                    ('leg_arrival', None), ('plant_admission', 'P1'),
                    ('operation_start', 'P1'), ('assignment', 'P2'),
                    ('leg_departure', None),
                ]
                observed = [(event['event_type'], event.get('product_id', event.get('product', {}).get('id')))
                            for event in events if event['time'] == 4]
                # Slot-release and terminal events keep their positions between these milestones.
                relevant = [item for item in observed if item in expected]
                self.assertEqual(relevant, expected)
                start = next(event for event in events if event['event_type'] == 'operation_start' and event['product_id'] == 'P1')
                self.assertEqual(decisions[-1]['last_event_sequence'], start['sequence'] + 1)
                self.assertEqual(final['summary']['remanufactured'], 3)

    def test_completion_return_release_and_unload_retry_at_one_time(self) -> None:
        """Finish held work, start its final operation, admit the queued part and start it at eight."""
        scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_3a/scenario.json').read_bytes())
        scenario['products'][1]['release_time'] = 1
        scenario['plant']['workstations'][0].update(duration=4, windows=[[4, 8], [8, 12]])
        events, final, _ = self.run_case(scenario)
        at_eight = [(event['event_type'], event.get('product_id')) for event in events if event['time'] == 8]
        expected = [
            ('operation_complete', 'P1'), ('inventory_return', 'P1'),
            ('operation_start', 'P1'), ('inventory_slot_release', 'P1'),
            ('unload_retry', None), ('plant_admission', 'P2'), ('operation_start', 'P2'),
            ('inventory_slot_release', 'P2'),
        ]
        self.assertEqual(at_eight, expected)
        self.assertEqual(final['summary']['waiting_to_unload'], 3)
        self.assertEqual(final['summary']['remanufactured'], 2)
        self.assertNotIn('plant_states', final)

    def test_zero_distance_static_departures_keep_the_batch_barrier(self) -> None:
        """Assign both trips before same-time arrivals and retain waiting stock at a zero horizon."""
        scenario = self.simultaneous_static_scenario()
        scenario['horizon'] = 0
        scenario['collection_point']['coordinates'] = [0, 0]
        scenario['vehicles'] = scenario['vehicles'][:2]
        scenario['products'] = scenario['products'][:2]
        for product in scenario['products']:
            product['release_time'] = 0
        scenario['trips'] = scenario['trips'][:2]
        for trip in scenario['trips']:
            trip['departure_time'] = 0
        events, final, _ = self.run_case(scenario)
        assignments = [event['sequence'] for event in events if event['event_type'] == 'assignment']
        pickups = [event['sequence'] for event in events if event['event_type'] == 'pickup']
        self.assertLess(max(assignments), min(pickups))
        self.assertEqual(final['products']['P0']['state'], 'processing')
        self.assertEqual(final['inventory'], {'P1': 0})
        self.assertEqual(final['summary']['vehicle_distance'], 0)

    def test_exact_horizon_arrival_start_and_completion(self) -> None:
        """Include boundary events once for finite/unlimited travel and unfinished processing."""
        for maximum in (None, 6):
            for horizon, completed in ((2, 0), (4, 1), (6, 2), (8, 3)):
                with self.subTest(maximum=maximum, horizon=horizon):
                    scenario = self.simultaneous_static_scenario()
                    scenario['horizon'] = horizon
                    scenario['products'] = [part for part in scenario['products'] if part['release_time'] <= horizon]
                    scenario['trips'] = [trip for trip in scenario['trips'] if trip['departure_time'] <= horizon]
                    for vehicle in scenario['vehicles']:
                        vehicle['maximum_range'] = maximum
                    events, final, _ = self.run_case(scenario)
                    self.assertEqual(events[-1]['event_type'], 'horizon')
                    self.assertEqual(events[-1]['time'], horizon)
                    self.assertEqual(final['summary']['remanufactured'], completed)
                    self.assertEqual(final['summary']['vehicle_distance'], min(horizon, 6))

    def test_stale_arrival_never_advances_physical_time(self) -> None:
        """Run the mid-leg reroute with a motion spy and reject the obsolete time-eight clock advance."""
        scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4b/scenario.json').read_bytes())
        with patch.object(engine, 'advance_vehicle_travel', wraps=engine.advance_vehicle_travel) as advance:
            events, final, _ = self.run_case(scenario)
        times = [call.args[2] for call in advance.call_args_list]
        self.assertNotIn(8, times)
        self.assertFalse(any(event['time'] == 8 for event in events))
        self.assertAlmostEqual(final['summary']['vehicle_distance'], 8.650281539872886)

    def test_single_and_network_container_ownership_and_projection_isolation(self) -> None:
        """Keep one authoritative stock map per plant while exporting historical single-plant fields."""
        for fixture in ('checkpoint_3a', 'checkpoint_4c'):
            with self.subTest(fixture=fixture):
                scenario = as_dynamic_scenario(json.loads((ROOT / 'tests/fixtures' / fixture / 'scenario.json').read_bytes()))
                definitions = ScenarioDefinitions.from_scenario(scenario)
                state = engine.build_dynamic_state(scenario, definitions)
                primary = state.plant_states['RF1']
                for field in ('inventory', 'held_slots', 'waiting_vehicles', 'workstations'):
                    self.assertIs(getattr(state, field), primary[field])
                context = physical_snapshot(state, released_only=False)
                candidate = copy.deepcopy(context)
                projected = InventoryRunState(**candidate)
                initialize_plant_states(scenario, projected)
                projected.held_slots.append('private-reservation')
                self.assertEqual(projected.plant_states['RF1']['held_slots'], ['private-reservation'])
                self.assertEqual(state.held_slots, [])
                self.assertEqual('plant_states' in context, fixture == 'checkpoint_4c')


if __name__ == '__main__':
    unittest.main()
