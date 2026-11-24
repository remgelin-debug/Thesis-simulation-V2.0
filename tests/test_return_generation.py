"""Check seeded arrivals/qualities, recycling eligibility and resolved-input replay.

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
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from tests.verifier_cli import verify_run_via_cli
from simulation.config import validate_generation_config

Record = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
ENABLED = ROOT / 'tests' / 'inputs' / 'checkpoint_3b_recycling.json'
DISABLED = ROOT / 'tests' / 'inputs' / 'checkpoint_3b_no_recycling.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_3b'


class SeededRecyclingTests(unittest.TestCase):
    """Run seeded CLI cases in temporary folders; independently verify their saved evidence."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate scratch space with automatic cleanup and reset unique-case numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case_count = 0

    def run_configuration(self, configuration: Path | Record, seed: int = 42) -> Path:
        """Invoke the real seeded CLI with a path or local configuration copy; return its output folder."""
        self.case_count += 1
        if isinstance(configuration, dict):
            path = self.root / f'config-{self.case_count}.json'
            path.write_text(json.dumps(configuration))
        else:
            path = configuration
        directory = self.root / f'run-{self.case_count}'
        result = subprocess.run([sys.executable, '-m', 'simulation', '--config', str(path), '--seed', str(seed),
                                 '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return directory

    # Check the expected model or evidence behavior

    def test_repeat_and_saved_replay_are_identical(self) -> None:
        """Repeat both seeded configurations and replay each frozen scenario; compare bytes and all physical outputs."""
        for config in (ENABLED, DISABLED):
            with self.subTest(config=config.name):
                first, second = self.run_configuration(config), self.run_configuration(config)
                replay = self.root / f'replay-{self.case_count}'
                result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario',
                    str(first / 'scenario.json'), '--output', str(replay)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
                    self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
                    self.assertEqual((first / filename).read_bytes(), (replay / filename).read_bytes())
                verify_run_via_cli(self, first)
                verify_run_via_cli(self, replay)

    def test_recycling_switch_preserves_batches_and_filters_before_sampling(self) -> None:
        """Check seed-42 batch equality, fourteen generated products, no disabled recycling and seven actual acceptances."""
        enabled, disabled = self.run_configuration(ENABLED), self.run_configuration(DISABLED)
        first = json.loads((enabled / 'scenario.json').read_text())
        second = json.loads((disabled / 'scenario.json').read_text())
        self.assertEqual(first['arrival_batches'], second['arrival_batches'])
        self.assertEqual([b['batch_size'] for b in first['arrival_batches']], [2, 2, 1, 2, 2, 1, 1, 1, 2])
        self.assertEqual(len(first['products']), 14)
        self.assertEqual(len(second['products']), 14)
        self.assertTrue(all(product['disposition'] == 'remanufacture' and product['quality_class'] == 'QM' for product in second['products']))
        for directory, expected_recycled in ((enabled, 7), (disabled, 0)):
            report = verify_run_via_cli(self, directory)
            self.assertEqual(report['recalculated_summary']['recycled'], expected_recycled)
            self.assertEqual(report['recalculated_summary']['unfinished'], 0)

    def test_quality_weights_do_not_change_arrival_stream(self) -> None:
        """Change only quality weights and verify identical arrivals/batches with distinct named seed streams."""
        baseline = self.run_configuration(ENABLED)
        configuration = json.loads(ENABLED.read_text())
        configuration['collection_point']['quality_weights'] = {'QM': 1, 'QR': 0}
        changed = self.run_configuration(configuration)
        first = json.loads((baseline / 'scenario.json').read_text())
        second = json.loads((changed / 'scenario.json').read_text())
        self.assertEqual(first['arrival_batches'], second['arrival_batches'])
        self.assertEqual(first['seed_provenance'], second['seed_provenance'])
        seeds = first['seed_provenance']['stream_seeds']
        self.assertNotEqual(seeds['arrivals_batches'], seeds['qualities'])
        self.assertTrue(all(product['disposition'] == 'remanufacture' for product in second['products']))
        verify_run_via_cli(self, changed)

    def test_fixed_batches_and_zero_arrivals(self) -> None:
        """Verify positive fixed batch sizes and a zero-rate scenario with no products or acceptance events."""
        configuration = json.loads(ENABLED.read_text())
        configuration['generation']['batch_size'] = 2
        fixed = self.run_configuration(configuration)
        scenario = json.loads((fixed / 'scenario.json').read_text())
        self.assertTrue(all(batch['batch_size'] == 2 for batch in scenario['arrival_batches']))
        verify_run_via_cli(self, fixed)
        configuration['generation']['arrival_rate'] = 0
        empty = self.run_configuration(configuration)
        scenario = json.loads((empty / 'scenario.json').read_text())
        self.assertEqual((scenario['arrival_batches'], scenario['products']), ([], []))
        verify_run_via_cli(self, empty)

    def test_invalid_sampling_settings_are_rejected(self) -> None:
        """Reject bad rates, batch distributions, missing effective mass and unsupported quality routes."""
        baseline = json.loads(DISABLED.read_text())
        mutations = [('rate', -1), ('rate', float('nan')), ('batch', 0),
                     ('batch', {'values': [0], 'weights': [1]}),
                     ('batch', {'values': [1], 'weights': [-1]}),
                     ('batch', {'values': [1], 'weights': [0]}),
                     ('batch', {'values': [1, 2], 'weights': [1]}),
                     ('quality', 0), ('quality', -1), ('route', [99])]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                configuration = copy.deepcopy(baseline)
                if field == 'rate':
                    configuration['generation']['arrival_rate'] = value
                elif field == 'batch':
                    configuration['generation']['batch_size'] = value
                elif field == 'quality':
                    configuration['quality_classes'][0]['weight'] = value
                else:
                    configuration['quality_classes'][0]['operations'] = value
                with self.assertRaises(ValueError):
                    validate_generation_config(configuration)

    def test_cli_rejects_seed_overrides_and_missing_seed(self) -> None:
        """Reject config without seed, conflicting source arguments and resolved-scenario seed overrides."""
        valid = self.run_configuration(ENABLED)
        for arguments in (['--config', str(ENABLED)],
                          ['--resolved-scenario', str(valid / 'scenario.json'), '--seed', '42'],
                          ['--config', str(ENABLED), '--resolved-scenario', str(valid / 'scenario.json'), '--seed', '42']):
            result = subprocess.run([sys.executable, '-m', 'simulation', *arguments, '--output', str(self.root / 'invalid')],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.root / 'invalid').exists())

    def test_remanufacturable_product_cannot_be_accepted_by_recycler(self) -> None:
        """Replace an acceptance product ID with a remanufacturable ID and require the disposition rule."""
        directory = self.run_configuration(ENABLED)
        scenario = json.loads((directory / 'scenario.json').read_text())
        remanufacturable = next(p['id'] for p in scenario['products'] if p['disposition'] == 'remanufacture')
        path = directory / 'events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        acceptance = next(event for event in events if event['event_type'] == 'recycler_acceptance')
        acceptance['product_id'] = remanufacturable
        with path.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(event) for event in events) + '\n')
        report = verify_run_via_cli(self, directory, 1)
        finding = next(f for f in report['findings'] if f['rule'] == 'recycling.disposition')
        self.assertEqual((finding['expected'], finding['observed']), ('recycle', 'remanufacture'))
        self.assertEqual(finding['event_sequence'], acceptance['sequence'])
        self.assertEqual(finding['ids']['product_id'], remanufacturable)

    def test_dispatch_inputs_exclude_individual_quality_and_operations(self) -> None:
        """Check boundary fields and fixed-destination eligibility, then reject an injected operation list."""
        directory = self.run_configuration(ENABLED)
        scenario = json.loads((directory / 'scenario.json').read_text())
        products = {product['id']: product for product in scenario['products']}
        path = directory / 'dispatch_decisions.jsonl'
        decisions = [json.loads(line) for line in path.read_text().splitlines()]
        for decision in decisions:
            for request in decision['policy_input']['requests']:
                self.assertEqual(set(request), {'product_id', 'collection_point', 'release_time', 'disposition'})
            target_disposition = 'remanufacture' if decision['decision']['stops'][-1] == 'RF1' else 'recycle'
            self.assertTrue(all(products[product_id]['disposition'] == target_disposition for product_id in decision['decision']['assigned_products']))
        decisions[0]['policy_input']['requests'][0]['operations'] = [1]
        path.write_text('\n'.join(json.dumps(decision) for decision in decisions) + '\n')
        verify_run_via_cli(self, directory, 1)

    def test_horizon_retains_recycler_bound_payload(self) -> None:
        """Stop before recycler arrival and verify carried products remain onboard and unfinished."""
        configuration = json.loads(ENABLED.read_text())
        configuration['horizon'] = 6
        directory = self.run_configuration(configuration)
        final = json.loads((directory / 'final_state.json').read_text())
        self.assertEqual(final['summary']['recycled'], 0)
        self.assertEqual(len(final['vehicles']['AV2']['load']), 7)
        self.assertTrue(all(final['products'][product_id]['state'] == 'onboard_av' for product_id in final['vehicles']['AV2']['load']))
        verify_run_via_cli(self, directory)

    def test_generation_evidence_is_audited_independently(self) -> None:
        """Forge a saved batch and update its hash; the independent seed reconstruction must still fail."""
        directory = self.run_configuration(ENABLED)
        path = directory / 'scenario.json'
        scenario = json.loads(path.read_text())
        scenario['arrival_batches'][0]['batch_size'] += 1
        path.write_text(json.dumps(scenario))
        manifest_path = directory / 'run_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        report = verify_run_via_cli(self, directory, 1)
        self.assertTrue(any(f['rule'] == 'generation.arrival_batches' for f in report['findings']))

    def test_missing_generation_evidence_is_incomplete(self) -> None:
        """Remove saved batches while keeping a matching input hash; require named generation evidence."""
        directory = self.run_configuration(ENABLED)
        path = directory / 'scenario.json'
        scenario = json.loads(path.read_text())
        del scenario['arrival_batches']
        path.write_text(json.dumps(scenario))
        manifest_path = directory / 'run_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        report = verify_run_via_cli(self, directory, 2)
        self.assertTrue(any(f['rule'] == 'generation.evidence' for f in report['findings']))
        del scenario['generation'], scenario['quality_classes']
        path.write_text(json.dumps(scenario))
        manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        verify_run_via_cli(self, directory, 2)

    def test_saved_generated_fixture_has_lf_and_verifies_in_isolation(self) -> None:
        """Verify the LF-pinned frozen seeded fixture using only a copied standalone verifier; preserve all bytes."""
        directory = self.root / 'fixture'
        shutil.copytree(FIXTURE, directory)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.assertNotIn(b'\r', before['scenario.json'])
        manifest = json.loads(before['run_manifest.json'])
        self.assertEqual(hashlib.sha256(before['scenario.json']).hexdigest(), manifest['scenario_sha256'])
        script = self.root / 'verify_simulation.py'
        shutil.copyfile(ROOT / 'tools' / 'verify.py', script)
        result = subprocess.run([sys.executable, '-I', str(script), str(directory)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for filename, contents in before.items():
            self.assertEqual((directory / filename).read_bytes(), contents)

    def test_distinct_seeds_change_generated_streams(self) -> None:
        """Check that seed forty-three changes the generated stream while preserving verifier conformance."""
        first, second = self.run_configuration(ENABLED, 42), self.run_configuration(ENABLED, 43)
        before = json.loads((first / 'scenario.json').read_text())
        after = json.loads((second / 'scenario.json').read_text())
        self.assertNotEqual(before['arrival_batches'], after['arrival_batches'])
        self.assertNotEqual(before['products'], after['products'])
        self.assertNotEqual(before['seed_provenance']['stream_seeds'], after['seed_provenance']['stream_seeds'])
        verify_run_via_cli(self, second)

    def test_multiple_recyclers_and_all_recycling_quality(self) -> None:
        """Accept all fourteen eligible products at a second configured recycler while the plant trip is empty."""
        configuration = json.loads(ENABLED.read_text())
        configuration['recycling_facilities'].append({'id': 'RC2', 'coordinates': [1, 1]})
        configuration['trips'][1]['stops'][-1] = 'RC2'
        configuration['quality_classes'][0]['weight'] = 0
        directory = self.run_configuration(configuration)
        final = json.loads((directory / 'final_state.json').read_text())
        self.assertEqual((final['summary']['recycled'], final['summary']['remanufactured']), (14, 0))
        self.assertTrue(all(product['actual_destination'] == 'RC2' for product in final['products'].values()))
        verify_run_via_cli(self, directory)
