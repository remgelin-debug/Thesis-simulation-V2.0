"""Keep hand-calculated single-product safeguards on the retained inventory engine.

The four cases replace the original scalar-engine acceptance cases. The expected
physical journey is calculated independently; event details use format-two fields.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from simulation.config import validate_scenario
from simulation.engine import run_inventory_simulation
from simulation.plant import fitting_window
from tests.single_product import single_product_scenario


class SingleProductTests(unittest.TestCase):
    """Exercise one product through inventory execution and the real CLI."""

    def test_expected_journey(self) -> None:
        """Check the same physical journey, waits and ownership using product/vehicle containers."""
        scenario = single_product_scenario()
        validate_scenario(scenario)
        state = run_inventory_simulation(scenario)
        observed = [(event['time'], event['event_type']) for event in state.events
                    if event['event_type'] in {'return_release', 'pickup', 'plant_admission',
                        'operation_start', 'inventory_return', 'remanufactured', 'horizon'}]
        self.assertEqual(observed, [(0, 'return_release'), (2, 'pickup'), (4, 'plant_admission'),
            (4, 'operation_start'), (6, 'inventory_return'), (8, 'operation_start'),
            (11, 'inventory_return'), (12, 'operation_start'), (13, 'remanufactured'), (14, 'horizon')])
        product, vehicle = state.products['P1'], state.vehicles['AV1']
        self.assertEqual([(job['operation_id'], job['start_time'], job['end_time'])
                          for job in product['history']], [(1, 4, 6), (3, 8, 11), (2, 12, 13)])
        self.assertEqual(vehicle['distance'], 4)
        self.assertEqual(product['inventory_wait'], 3)
        self.assertEqual(product['state'], 'remanufactured')
        self.assertEqual(product['next_operation'], 3)
        self.assertEqual((state.inventory, vehicle['load'], list(state.workstations.values())), ({}, [], [None] * 3))
        self.assertEqual((vehicle['location'], vehicle['status']), ('RF1', 'idle'))
        self.assertEqual([e['sequence'] for e in state.events], list(range(1, len(state.events) + 1)))
        self.assertLess(state.events.index(next(e for e in state.events if e['event_type'] == 'return_release')),
                        state.events.index(next(e for e in state.events if e['event_type'] == 'leg_departure')))
        policy_input = state.decisions[0]['policy_input']
        self.assertNotIn('quality', json.dumps(policy_input))
        self.assertNotIn('operations', json.dumps(policy_input))

    def test_window_requires_full_fit(self) -> None:
        """Accept an exact closing-boundary finish, but reject a late start."""
        station = single_product_scenario()['plant']['workstations'][0]
        self.assertEqual(fitting_window(station, 4), [4, 6])
        self.assertIsNone(fitting_window(station, 4.5))

    def test_horizon_preserves_unfinished_work(self) -> None:
        """Keep partial travel, waiting and active work in the retained state layout."""
        for horizon, expected_state, distance in [(1, 'at_collection_point', 1),
                (7, 'in_plant_inventory', 4), (9, 'processing', 4), (13, 'remanufactured', 4)]:
            with self.subTest(horizon=horizon):
                scenario = single_product_scenario()
                scenario['horizon'] = horizon
                state = run_inventory_simulation(scenario)
                self.assertEqual(state.products['P1']['state'], expected_state)
                self.assertEqual(state.vehicles['AV1']['distance'], distance)
                self.assertTrue(all(e['time'] <= horizon for e in state.events))
                if horizon == 7:
                    self.assertEqual((state.inventory, state.products['P1']['inventory_wait']), ({'P1': 6}, 1))
                if horizon == 9:
                    self.assertEqual(state.workstations['WS3']['end_time'], 11)

    def test_cli_writes_reproducible_artifacts(self) -> None:
        """Check exact input bytes, repeatable evidence and refusal to overwrite a run."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'scenario.json'
            source.write_text(json.dumps(single_product_scenario()) + '\n', encoding='utf-8')
            outputs = [Path(temporary) / name for name in ('first', 'second')]
            for output in outputs:
                result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario',
                    str(source), '--output', str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['unfinished'], 0)
                manifest = json.loads((output / 'run_manifest.json').read_text())
                self.assertEqual(manifest['status'], 'completed')
                self.assertEqual(manifest['scenario_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual((output / 'scenario.json').read_bytes(), source.read_bytes())
                final = json.loads((output / 'final_state.json').read_text())
                self.assertEqual(final['summary']['remanufactured'], 1)
                self.assertEqual(final['products']['P1']['milestones'],
                                 {'release': 0, 'pickup': 2, 'admission': 4, 'completion': 13})
                decisions = [json.loads(line) for line in (output / 'dispatch_decisions.jsonl').read_text().splitlines()]
                self.assertEqual(decisions[0]['last_event_sequence'], 1)
            for name in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'final_state.json', 'summary.json'):
                self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes())
            repeated = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario',
                str(source), '--output', str(outputs[0])], capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)


if __name__ == '__main__':
    unittest.main()
