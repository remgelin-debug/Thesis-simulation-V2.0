"""Check that a facility visit unloads only products authorized at that stop.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from simulation.config import validate_scenario
from simulation.engine import build_dynamic_state, run_dynamic_simulation
from simulation.forecasting import forecast_dynamic_insertion
from simulation.generator import prepare_scenario
from simulation.routing import build_dynamic_policy_input
from simulation.runner import run_once
from simulation.state import InventoryRunState, Record, ScenarioDefinitions
from tools.kpis import export_kpis
from tools.replay import RunReplay
from tools.verify import audit_dynamic_forecast, verify_run

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ('dynamic_quality_unaware', 'dynamic_quality_aware')


def repeated_visit_scenario(horizon: float = 10, *, new_release: bool = False,
                            recycling: bool = False) -> Record:
    """Return valid explicit inputs whose distance-ranked insertions revisit a facility.

    Tests own the returned declarations. P1 is collected first; simultaneous P2/P3
    returns force a capacity-two route with P3 delivered before P1/P2. Empty plant
    calendars give both policies the same distance fallback. No files/state change.
    """
    releases = [0, 1.5, 1.5, *([3.5] if new_release else [])]
    scenario: Record = {
        'format_version': 3, 'policy': POLICIES[0], 'horizon': horizon,
        'seed_provenance': {'source': 'Stop-authorized delivery regression', 'seed': None},
        'plant': {'id': 'RF1', 'coordinates': [0, 0], 'inventory_capacity': None,
                  'workstations': [{'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': []}]},
        'collection_points': [{'id': 'CP1', 'coordinates': [1, 0]}],
        'recycling_facilities': [{'id': 'RC1', 'coordinates': [0, 0]}] if recycling else [],
        'vehicles': [{'id': 'AV1', 'initial_site': 'RF1', 'speed': 1,
                      'payload_capacity': 2, 'maximum_range': None}],
        'products': [{'id': f'P{index}', 'origin': 'CP1', 'release_time': time,
                      'disposition': 'recycle' if recycling else 'remanufacture',
                      'operations': [] if recycling else [1]} for index, time in enumerate(releases, 1)],
        'trips': [],
    }
    validate_scenario(scenario)
    return scenario


def loaded_visit_state(*, blocked: bool = False, horizon: float = 9) -> tuple[Record, InventoryRunState]:
    """Create an already-running AV with P1 onboard for a later RF1 visit.

    Projection/physical tests own the scenario and state. Current service is P3
    (plus P4 when blocked); CP1 pickup P2 and RF1 delivery P1/P2 follow. The blocked
    variant starts P0 in intermediate work holding the only inventory slot. No
    files/events are written; callers resume execution with dispatch disabled.
    """
    scenario = repeated_visit_scenario(horizon)
    scenario['plant']['workstations'][0]['windows'] = [[0, 10]]
    scenario['vehicles'][0].update(payload_capacity=3 if blocked else 2, maximum_range=10)
    if blocked:
        scenario['plant']['inventory_capacity'] = 1
        scenario['plant']['workstations'].append(
            {'id': 'WS2', 'operation_id': 2, 'duration': 2, 'windows': [[2, 10]]})
        scenario['products'].extend([
            {'id': 'P0', 'origin': 'CP1', 'release_time': 0, 'disposition': 'remanufacture', 'operations': [1, 2]},
            {'id': 'P4', 'origin': 'CP1', 'release_time': 0, 'disposition': 'remanufacture', 'operations': [1]},
        ])
        scenario['products'][2]['operations'] = [1, 2]
    for product in scenario['products']:
        product['release_time'] = 0
    validate_scenario(scenario)
    definitions = ScenarioDefinitions.from_scenario(scenario)
    state = build_dynamic_state(scenario, definitions)
    for product in state.products.values():
        product.update(state='onboard_av', location='AV1', assigned_vehicle='AV1', planned_destination='RF1')
        product['milestones'].update(release=0, pickup=0)
    state.products['P2'].update(state='at_collection_point', location='CP1')
    state.products['P2']['milestones'].pop('pickup')
    current = ['P3', 'P4'] if blocked else ['P3']
    state.vehicles['AV1'].update(load=['P1', *current], remaining_stops=[
        {'site_id': 'RF1', 'pickups': [], 'deliveries': current},
        {'site_id': 'CP1', 'pickups': ['P2'], 'deliveries': []},
        {'site_id': 'RF1', 'pickups': [], 'deliveries': ['P1', 'P2']},
    ])
    if blocked:
        state.products['P0'].update(state='processing', location='WS1', actual_destination='RF1')
        state.products['P0']['milestones']['admission'] = 0
        state.held_slots.append('P0')
        state.workstations['WS1'] = {
            'product_id': 'P0', 'plant_id': 'RF1', 'workstation_id': 'WS1', 'operation_id': 1,
            'operation_index': 0, 'start_time': 0, 'end_time': 1, 'window': [0, 10],
        }
    return scenario, state


class DeliveryServiceTests(unittest.TestCase):
    """Use temporary saved runs and owned resumed states; preserve historical fixtures."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate automatically cleaned evidence directories and a unique run counter."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def run_case(self, scenario: Record, policy: str) -> tuple[Path, list[Record], Record]:
        """Save owned inputs, execute a real runner path and return its evidence/final state."""
        self.counter += 1
        source = self.root / f'input-{self.counter}.json'
        source.write_text(json.dumps(scenario), encoding='utf-8')
        folder = self.root / f'run-{self.counter}'
        run_once(source, folder, policy=policy)
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        return folder, events, json.loads((folder / 'final_state.json').read_text())

    def assert_authorized_services(self, events: list[Record]) -> None:
        """Independently track route records and require each acceptance to belong to its head visit."""
        routes: dict[str, list[Record]] = {}
        for event in events:
            vehicle = event.get('vehicle_id')
            if event['event_type'] == 'route_revised':
                routes[vehicle] = copy.deepcopy(event['new_remaining_stops'])
            elif event['event_type'] in ('plant_admission', 'recycler_acceptance'):
                stop = routes[vehicle][0]
                self.assertIn(event['product_id'], stop['deliveries'], event)
                self.assertEqual(stop['site_id'], event.get('plant_id', event.get('recycler_id')))
            elif event['event_type'] == 'stop_completed':
                routes[vehicle].pop(0)

    # Check the expected model or evidence behavior

    def test_first_visit_retains_already_onboard_later_delivery(self) -> None:
        """At time three admit only P3; at 3.25 P1 must still be carried toward the next pickup."""
        for policy in POLICIES:
            with self.subTest(policy=policy):
                folder, events, final = self.run_case(repeated_visit_scenario(3.25), policy)
                self.assertEqual(final['products']['P1']['state'], 'onboard_av')
                self.assertEqual(final['vehicles']['AV1']['load'], ['P1'])
                self.assertNotIn('admission', final['products']['P1']['milestones'])
                self.assertEqual(final['products']['P3']['milestones']['admission'], 3)
                self.assertEqual(final['vehicles']['AV1']['remaining_stops'][-1]['deliveries'], ['P1', 'P2'])
                self.assert_authorized_services(events)
                self.assertEqual(verify_run(folder)['status'], 'PASS')

    def test_later_release_replans_without_stale_commitments(self) -> None:
        """Release P4 at 3.5 while P1 remains onboard; both actual policies must finish all service."""
        for policy in POLICIES:
            with self.subTest(policy=policy):
                folder, events, final = self.run_case(repeated_visit_scenario(new_release=True), policy)
                self.assert_authorized_services(events)
                self.assertEqual(final['products']['P3']['milestones']['admission'], 3)
                self.assertGreaterEqual(final['products']['P1']['milestones']['admission'], 5)
                self.assertTrue(any(event['event_type'] == 'dispatch' and event['time'] == 3.5 for event in events))
                self.assertEqual(final['vehicles']['AV1']['remaining_stops'], [])
                self.assertEqual(verify_run(folder)['status'], 'PASS')

    def test_partial_admission_retries_and_keeps_future_load(self) -> None:
        """Held work blocks at zero; admit P3 at two and P4 at four, keeping P1 until six."""
        for policy in POLICIES:
            with self.subTest(policy=policy):
                scenario, state = loaded_visit_state(blocked=True)
                scenario['policy'] = policy
                final = run_dynamic_simulation(scenario, initial_state=state, dispatch_enabled=False)
                admissions = {event['product_id']: event['time'] for event in final.events if event['event_type'] == 'plant_admission'}
                self.assertEqual(admissions, {'P3': 2, 'P4': 4, 'P1': 6, 'P2': 6})
                departure = next(event for event in final.events if event['event_type'] == 'leg_departure' and event['time'] == 4)
                self.assertEqual(departure['load'], ['P1'])
                self.assertEqual(final.vehicles['AV1']['waiting_to_unload'], 4)
                self.assertEqual(final.vehicles['AV1']['distance'], 2)
                resets = [(event['time'], event['range_after']) for event in final.events if event['event_type'] == 'range_reset']
                self.assertEqual(resets, [(0, 10), (6, 10)])
                at_four = [event['event_type'] for event in final.events if event['time'] == 4]
                self.assertLess(at_four.index('operation_complete'), at_four.index('unload_retry'))
                self.assertLess(at_four.index('inventory_slot_release'), at_four.index('plant_admission'))

    def test_horizon_while_current_authorized_load_remains_blocked(self) -> None:
        """At time three P3 has returned to the slot; both P4 and later-stop P1 remain onboard."""
        scenario, state = loaded_visit_state(blocked=True, horizon=3)
        final = run_dynamic_simulation(scenario, initial_state=state, dispatch_enabled=False)
        self.assertEqual(final.vehicles['AV1']['load'], ['P1', 'P4'])
        self.assertEqual(final.vehicles['AV1']['status'], 'waiting_to_unload')
        self.assertEqual(final.vehicles['AV1']['waiting_to_unload'], 3)
        self.assertEqual(final.vehicles['AV1']['remaining_stops'][0]['deliveries'], ['P3', 'P4'])
        self.assertEqual(final.inventory, {'P3': 3})
        self.assertNotIn('admission', final.products['P1']['milestones'])

    def test_forecast_projects_later_service_and_first_start_without_mutation(self) -> None:
        """P2 starts at three normally, or seven after blocked service; independent projection must agree."""
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                scenario, state = loaded_visit_state(blocked=blocked)
                scenario['policy'] = 'dynamic_quality_aware'
                route = copy.deepcopy(state.vehicles['AV1']['remaining_stops'])
                state.vehicles['AV1']['remaining_stops'][1]['pickups'] = []
                state.vehicles['AV1']['remaining_stops'][2]['deliveries'] = ['P1']
                state.products['P2'].update(assigned_vehicle=None, planned_destination=None)
                before = asdict(state)
                boundary = build_dynamic_policy_input(scenario, ScenarioDefinitions.from_scenario(scenario), state)
                observed = copy.deepcopy(boundary)
                candidate = {'product_id': 'P2', 'vehicle_id': 'AV1', 'destination': 'RF1', 'new_remaining_stops': route}
                prediction = forecast_dynamic_insertion(boundary, candidate)
                self.assertEqual((prediction['predicted_admission'], prediction['predicted_first_start']), (6, 7) if blocked else (2, 3))
                self.assertEqual(audit_dynamic_forecast(boundary, candidate), prediction)
                self.assertEqual(forecast_dynamic_insertion(boundary, candidate), prediction)
                self.assertEqual(asdict(state), before)
                self.assertEqual(boundary, observed)

    def test_repeated_recycler_visits_preserve_mixed_future_load(self) -> None:
        """Accept Rnow at zero and Rlater at two, carrying the remanufacturable P3 to RF1 at one."""
        for policy in POLICIES:
            with self.subTest(policy=policy):
                scenario, state = loaded_visit_state()
                scenario['policy'] = policy
                scenario['plant']['coordinates'] = [0, 1]
                scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [0, 0]}]
                scenario['vehicles'][0].update(initial_site='RC1', payload_capacity=3)
                for product in scenario['products'][:2]:
                    product.update(disposition='recycle', operations=[])
                    state.products[product['id']].update(planned_destination='RC1', state='onboard_av', location='AV1')
                state.vehicles['AV1'].update(location='RC1', load=['P1', 'P2', 'P3'], remaining_stops=[
                    {'site_id': 'RC1', 'pickups': [], 'deliveries': ['P2']},
                    {'site_id': 'RF1', 'pickups': [], 'deliveries': ['P3']},
                    {'site_id': 'RC1', 'pickups': [], 'deliveries': ['P1']},
                ])
                validate_scenario(scenario)
                final = run_dynamic_simulation(scenario, initial_state=state, dispatch_enabled=False)
                acceptances = [(event['product_id'], event['time']) for event in final.events if event['event_type'] == 'recycler_acceptance']
                self.assertEqual(acceptances, [('P2', 0), ('P1', 2)])
                self.assertEqual(final.products['P3']['milestones']['admission'], 1)
                departures = [event for event in final.events if event['event_type'] == 'leg_departure']
                self.assertEqual(departures[1]['load'], ['P1', 'P3'])
                self.assertEqual(departures[2]['load'], ['P1'])

    def test_recycler_forecast_retains_candidate_until_its_authorized_visit(self) -> None:
        """Collect P2 at one, visit RC1 at two, and accept P2 only at the later visit at four."""
        scenario, state = loaded_visit_state()
        scenario['policy'] = 'dynamic_quality_aware'
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [0, 0]}]
        scenario['vehicles'][0]['payload_capacity'] = 3
        for definition in scenario['products']:
            definition.update(disposition='recycle', operations=[])
            state.products[definition['id']]['planned_destination'] = 'RC1'
        state.products['P2'].update(assigned_vehicle=None, planned_destination=None)
        state.vehicles['AV1']['remaining_stops'] = [
            {'site_id': 'RC1', 'pickups': [], 'deliveries': ['P3']},
            {'site_id': 'CP1', 'pickups': [], 'deliveries': []},
            {'site_id': 'RC1', 'pickups': [], 'deliveries': ['P1']},
        ]
        route = copy.deepcopy(state.vehicles['AV1']['remaining_stops'])
        route.insert(0, {'site_id': 'CP1', 'pickups': ['P2'], 'deliveries': []})
        route[-1]['deliveries'].append('P2')
        validate_scenario(scenario)
        boundary = build_dynamic_policy_input(scenario, ScenarioDefinitions.from_scenario(scenario), state)
        before = copy.deepcopy(boundary)
        candidate = {'product_id': 'P2', 'vehicle_id': 'AV1', 'destination': 'RC1', 'new_remaining_stops': route}
        prediction = forecast_dynamic_insertion(boundary, candidate)
        self.assertEqual(prediction['predicted_admission'], 4)
        self.assertIsNone(prediction['predicted_first_start'])
        self.assertEqual(audit_dynamic_forecast(boundary, candidate), prediction)
        self.assertEqual(boundary, before)

    def test_verifier_rejects_unauthorized_plant_or_recycler_acceptance(self) -> None:
        """Forge an early P1 acceptance with unchanged aggregate totals and demand the visit-specific rule."""
        for recycling in (False, True):
            with self.subTest(recycling=recycling):
                folder, events, final = self.run_case(repeated_visit_scenario(3.25, recycling=recycling), POLICIES[0])
                self.assertEqual(verify_run(folder)['status'], 'PASS')
                kind = 'recycler_acceptance' if recycling else 'plant_admission'
                admission = next(event for event in events if event['event_type'] == kind)
                self.assertEqual(admission['product_id'], 'P3')
                admission['product_id'] = 'P1'
                # Swap the affected physical exports too; counts and accumulated
                # waits remain unchanged. Authorization must fail before totals.
                final['products']['P1'], final['products']['P3'] = final['products']['P3'], final['products']['P1']
                final['vehicles']['AV1']['load'] = ['P3']
                if not recycling:
                    final['inventory'] = {'P1': final['inventory']['P3']}
                (folder / 'final_state.json').write_text(json.dumps(final), encoding='utf-8')
                (folder / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
                report = verify_run(folder)
                self.assertEqual((report['status'], report['exit_code']), ('FAIL', 1))
                finding = next(item for item in report['findings'] if item['rule'] == 'dynamic.stop_authorized_delivery')
                self.assertEqual(finding['event_sequence'], admission['sequence'])
                self.assertEqual(finding['observed']['product_id'], 'P1')
                self.assertEqual(finding['expected']['product_ids'], ['P3'])

    def test_saved_replay_and_kpis_finish_wait_while_retaining_future_load(self) -> None:
        """A verified blocked visit ends at four with P4 onboard; replay and CSV must end its wait too."""
        scenario = repeated_visit_scenario(new_release=True)
        scenario['vehicles'][0]['payload_capacity'] = 3
        scenario['products'][3]['release_time'] = 1.5
        scenario['plant'].update(inventory_capacity=1)
        scenario['plant']['workstations'][0]['windows'] = [[4, 10]]
        folder, events, _ = self.run_case(scenario, 'dynamic_quality_aware')
        self.assertEqual(verify_run(folder)['status'], 'PASS')
        completed = next(event for event in events if event['event_type'] == 'stop_completed'
                         and event['time'] == 4 and event['stop']['site_id'] == 'RF1')
        state = RunReplay(folder).snapshot(4, completed['sequence'])
        self.assertEqual(state['vehicles']['AV1']['load'], ['P4'])
        self.assertEqual(state['vehicles']['AV1']['status'], 'idle')
        self.assertEqual(state['plants']['RF1']['waiting_vehicles'], [])
        self.assertEqual(export_kpis(folder, self.root / 'kpis.csv'), (1, 0))

    def test_seed54_both_policies_share_resolved_inputs(self) -> None:
        """Resolve seed 54 once, execute both real policy paths and independently verify each."""
        _, data = prepare_scenario(ROOT / 'configs/three_site_pilot.json', 54)
        source = self.root / 'seed54.json'
        source.write_bytes(data)
        for policy in POLICIES:
            folder = self.root / policy
            run_once(source, folder, policy=policy)
            self.assertEqual((folder / 'scenario.json').read_bytes(), data)
            self.assertEqual(verify_run(folder)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
