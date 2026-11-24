"""Check deliberately invalid physical state with and without Python optimization.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
The tests corrupt separate valid state on purpose. Subprocess probes use
explicit outcomes because python -O removes ordinary assert statements.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from simulation.engine import build_dynamic_state, service_dynamic_stop
from simulation.plant import check_plant_network, finish_inventory_operation, start_inventory_operation
from simulation.state import ScenarioDefinitions
from simulation.transport import accept_recycling_products, retry_inventory_admissions
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]


def probe_invalid_state(name: str) -> dict:
    """Corrupt an owned valid scenario/state, call real physics, and return its explicit outcome.

    Subprocess tests call this without test-script assertions (which -O removes).
    No files are written. These are invalid internal-API probes, not valid inputs.
    """
    scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4b/scenario.json').read_bytes())
    scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [0, 0]}]
    definitions = ScenarioDefinitions.from_scenario(scenario)
    state = build_dynamic_state(scenario, definitions)
    vehicle, product = state.vehicles['AV1'], state.products['P1']
    product.update(state='onboard_av', location='AV1', assigned_vehicle='AV1', planned_destination='RC1')
    vehicle.update(location='RC1', load=['P1'], remaining_stops=[
        {'site_id': 'RC1', 'pickups': [], 'deliveries': ['P1']}])
    try:
        if name == 'recycling':
            accept_recycling_products(definitions, state, 'AV1')
        elif name.startswith('pickup') or name == 'payload':
            product.update(state='at_collection_point', location='CP1', planned_destination='RF1')
            vehicle.update(location='CP1', load=[], remaining_stops=[
                {'site_id': 'CP1', 'pickups': ['P1'], 'deliveries': []}])
            if name == 'pickup_owner':
                product['assigned_vehicle'] = 'OTHER'
            elif name == 'pickup_location':
                vehicle['location'] = 'RF1'
            else:
                scenario['vehicles'][0]['payload_capacity'] = 1
                vehicle['load'] = ['P2']
                state.products['P2'].update(state='onboard_av', location='AV1', assigned_vehicle='AV1')
            service_dynamic_stop(scenario, definitions, state, 'AV1')
            check_plant_network(scenario, state)
        elif name in ('duplicate', 'missing', 'capacity'):
            if name == 'duplicate':
                state.inventory['P1'] = 0
            elif name == 'missing':
                vehicle['load'] = []
            else:
                scenario['plant']['inventory_capacity'] = 1
                state.inventory.update(P1=0, P2=0)
                vehicle['load'] = []
                for part in state.products.values():
                    part.update(state='in_plant_inventory', location='RF1')
            check_plant_network(scenario, state)
        elif name == 'completion':
            product.update(state='processing', location='WS1', actual_destination='RF1')
            vehicle['load'] = []
            state.workstations['WS1'] = {
                'product_id': 'P1', 'plant_id': 'RF1', 'workstation_id': 'WS1',
                'operation_id': 1, 'operation_index': 0, 'start_time': 0, 'end_time': 1, 'window': [0, 12]}
            finish_inventory_operation(scenario, definitions, state, 'WS1')
        elif name == 'start':
            state.inventory['P1'] = 0  # Deliberately still onboard, with an inventory alias.
            start_inventory_operation(scenario, definitions, state, scenario['plant']['workstations'][0])
        elif name == 'unauthorized_visit':
            vehicle.update(location='RF1', remaining_stops=[
                {'site_id': 'CP1', 'pickups': [], 'deliveries': []}])
            product['planned_destination'] = 'RF1'
            state.waiting_vehicles['AV1'] = 0
            retry_inventory_admissions(scenario, definitions, state)
        else:
            raise ValueError(f'Unknown probe {name}')
    except Exception as error:  # Observe the probe outcome; production must propagate it.
        return {'error': type(error).__name__, 'message': str(error), 'events': len(state.events)}
    return {'error': None, 'product_state': product['state'], 'events': len(state.events)}


class PhysicalGuardTests(unittest.TestCase):
    """Check mandatory entry and conservation guards in separate interpreter modes."""

    # Prepare separate test data and inspect results

    def check_probe(self, name: str) -> None:
        """Run one corruption under normal, -O and PYTHONOPTIMIZE=1 and require a contextual error."""
        for flags, optimize in (([], None), (['-O'], None), ([], '1')):
            with self.subTest(probe=name, flags=flags, optimize=optimize):
                environment = os.environ.copy()
                environment.pop('PYTHONOPTIMIZE', None)
                if optimize:
                    environment['PYTHONOPTIMIZE'] = optimize
                command = "import json,sys; sys.path.insert(0,'tests'); from test_physical_guards import probe_invalid_state; print(json.dumps(probe_invalid_state(%r)))" % name
                result = subprocess.run([sys.executable, *flags, '-c', command], cwd=ROOT,
                                        env=environment, capture_output=True, text=True, check=True)
                outcome = json.loads(result.stdout)
                self.assertEqual(outcome['error'], 'PhysicalStateError', outcome)
                self.assertIn('time=0', outcome['message'])
                self.assertIn('rule=', outcome['message'])

    # Check the expected model or evidence behavior

    def test_invalid_recycling_disposition(self) -> None:
        """Remanufacturable load must never be accepted by a recycler."""
        self.check_probe('recycling')

    def test_pickup_ownership_and_location(self) -> None:
        """A pickup needs the assigned AV physically at the product's origin."""
        self.check_probe('pickup_owner')
        self.check_probe('pickup_location')

    def test_payload_capacity(self) -> None:
        """A pickup may not exceed vehicle payload."""
        self.check_probe('payload')

    def test_inventory_capacity(self) -> None:
        """Waiting stock plus reserved slots may not exceed finite capacity."""
        self.check_probe('capacity')

    def test_physical_membership(self) -> None:
        """Released products require exactly one physical membership."""
        self.check_probe('duplicate')
        self.check_probe('missing')

    def test_processing_state(self) -> None:
        """Starting onboard load or completing before the active job's end is invalid."""
        self.check_probe('start')
        self.check_probe('completion')

    def test_current_stop_authorization(self) -> None:
        """Queued service needs an actual current visit, even under optimized Python."""
        self.check_probe('unauthorized_visit')

    def test_valid_runs_match_optimized_execution(self) -> None:
        """Compare saved physical evidence and independently verify fixed and both dynamic paths."""
        with tempfile.TemporaryDirectory() as temporary:
            for fixture, policy in (('checkpoint_3a', 'static_quality_unaware'),
                                    ('checkpoint_4a', 'static_quality_aware'),
                                    ('checkpoint_4b', 'dynamic_quality_unaware'),
                                    ('checkpoint_4c', 'dynamic_quality_aware')):
                scenario = ROOT / 'tests/fixtures' / fixture / 'scenario.json'
                outputs = []
                for mode, flags in (('normal', []), ('optimized', ['-O'])):
                    output = Path(temporary) / f'{fixture}-{mode}'
                    environment = os.environ.copy()
                    environment.pop('PYTHONOPTIMIZE', None)
                    result = subprocess.run([sys.executable, *flags, '-m', 'simulation', '--resolved-scenario',
                                             str(scenario), '--policy', policy, '--output', str(output)],
                                            cwd=ROOT, env=environment, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(verify_run(output)['status'], 'PASS')
                    outputs.append(output)
                for artifact in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl',
                                 'summary.json', 'final_state.json'):
                    self.assertEqual((outputs[0] / artifact).read_bytes(), (outputs[1] / artifact).read_bytes())
