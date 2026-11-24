"""Check the full network CLI, independent streams and saved-input replay.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from simulation.generator import prepare_scenario
from simulation.generator import generate_windows
from simulation.config import validate_schedule_generation
import random

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs' / 'three_site_pilot.json'


class NetworkGenerationTests(unittest.TestCase):
    """Keep runs and edited generation evidence in automatically cleaned directories."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Read the reusable pilot and allocate per-test temporary paths and case numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.configuration = json.loads(CONFIG.read_text())
        self.number = 0

    def resolve(self, configuration: dict, seed: int = 42) -> tuple[dict, bytes]:
        """Resolve a temporary configuration through the public preparation boundary without running physics."""
        self.number += 1
        path = self.root / f'configuration-{self.number}.json'
        path.write_text(json.dumps(configuration))
        return prepare_scenario(path, seed)

    def run_cli(self, seed: int = 42, resolved: Path | None = None,
                policy: str = 'dynamic_quality_aware') -> Path:
        """Execute the actual single-run CLI and return its fresh output folder."""
        self.number += 1
        directory = self.root / f'run-{self.number}'
        source = ['--config', str(CONFIG), '--seed', str(seed)] if resolved is None else ['--resolved-scenario', str(resolved)]
        result = subprocess.run([sys.executable, '-m', 'simulation', *source, '--policy', policy,
            '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return directory

    def verify_cli(self, directory: Path, expected_exit: int = 0) -> dict:
        """Run the independent checker in a child process and return its asserted JSON report."""
        result = subprocess.run([sys.executable, str(ROOT / 'verify_simulation.py'), str(directory)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        return json.loads((directory / 'verification.json').read_text())

    # Check the expected model or evidence behavior

    def test_full_cli_repeat_replay_and_both_dynamic_policies(self) -> None:
        """Repeat seed 42 exactly, replay saved inputs, and independently verify both policies on identical bytes."""
        first, second = self.run_cli(), self.run_cli()
        replay = self.run_cli(resolved=first / 'scenario.json')
        unaware = self.run_cli(resolved=first / 'scenario.json', policy='dynamic_quality_unaware')
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
            self.assertEqual((first / filename).read_bytes(), (replay / filename).read_bytes())
        self.assertEqual((first / 'scenario.json').read_bytes(), (unaware / 'scenario.json').read_bytes())
        for directory in (first, second, replay, unaware):
            self.assertEqual(self.verify_cli(directory)['status'], 'PASS')
        scenario = json.loads((first / 'scenario.json').read_text())
        self.assertEqual((len(scenario['collection_points']), len(scenario['vehicles']), len(scenario['plants'])), (3, 3, 3))
        self.assertTrue(all(len(plant['workstations']) == 3 for plant in scenario['plants']))
        self.assertEqual(len(scenario['products']), 6)
        self.assertEqual(scenario['seed_provenance']['master_seed'], 42)
        self.assertNotIn(b'\r', (first / 'scenario.json').read_bytes())
        self.assertEqual(len({product['id'] for product in scenario['products']}), len(scenario['products']))

    def test_cli_seed_43_changes_samples_but_not_fleet_or_durations(self) -> None:
        """Exercise seed 43 through the real CLI; preserve fleet and durations while sampled inputs change."""
        first, second = self.run_cli(), self.run_cli(43)
        before = json.loads((first / 'scenario.json').read_text())
        after = json.loads((second / 'scenario.json').read_text())
        self.assertNotEqual(before['arrival_batches'], after['arrival_batches'])
        self.assertNotEqual(before['products'], after['products'])
        self.assertNotEqual(before['plants'][0]['workstations'][0]['windows'], after['plants'][0]['workstations'][0]['windows'])
        self.assertEqual(before['vehicles'], after['vehicles'])
        self.assertEqual([{key: value for key, value in point.items() if key != 'coordinates'} for point in before['collection_points']],
                         [{key: value for key, value in point.items() if key != 'coordinates'} for point in after['collection_points']])
        for old_plant, new_plant in zip(before['plants'], after['plants']):
            self.assertEqual(old_plant['inventory_capacity'], new_plant['inventory_capacity'])
            for old_station, new_station in zip(old_plant['workstations'], new_plant['workstations']):
                self.assertEqual(old_station['duration'], new_station['duration'])
        self.verify_cli(second)

    def test_quality_and_schedule_rules_cannot_shift_arrivals(self) -> None:
        """Changing quality weights leaves arrivals/calendars intact; schedule edits leave all products intact."""
        original, _ = self.resolve(self.configuration)
        qualities = copy.deepcopy(self.configuration)
        qualities['collection_points'][0]['quality_weights'] = {'Q132': 0, 'Q3': 1, 'Q21': 0}
        changed, _ = self.resolve(qualities)
        self.assertEqual(original['arrival_batches'], changed['arrival_batches'])
        self.assertEqual(original['plants'], changed['plants'])
        self.assertTrue(all(product['quality_class'] == 'Q3' for product in changed['products'] if product['origin'] == 'CP1'))
        schedules = copy.deepcopy(self.configuration)
        schedules['schedule_generation']['open_duration'] = [5, 7]
        changed, _ = self.resolve(schedules)
        self.assertEqual(original['arrival_batches'], changed['arrival_batches'])
        self.assertEqual(original['products'], changed['products'])
        self.assertNotEqual(original['plants'], changed['plants'])

    def test_site_and_station_streams_are_independent_of_other_sites_and_order(self) -> None:
        """Changes at CP1/RF1-WS1 and reversed input lists do not shift other local streams."""
        original, _ = self.resolve(self.configuration)
        arrivals = copy.deepcopy(self.configuration)
        arrivals['collection_points'][0]['generation']['arrival_rate'] *= 2
        changed, _ = self.resolve(arrivals)
        self.assertEqual(original['plants'], changed['plants'])
        self.assertEqual([product for product in original['products'] if product['origin'] != 'CP1'],
                         [product for product in changed['products'] if product['origin'] != 'CP1'])
        schedules = copy.deepcopy(self.configuration)
        schedules['plants'][0]['workstations'][0]['window_generation'] = {'open_duration': [5, 7]}
        changed, _ = self.resolve(schedules)
        self.assertEqual(original['products'], changed['products'])
        self.assertEqual(original['plants'][1:], changed['plants'][1:])
        self.assertEqual(original['plants'][0]['workstations'][1:], changed['plants'][0]['workstations'][1:])
        reordered = copy.deepcopy(self.configuration)
        reordered['collection_points'].reverse()
        reordered['plants'].reverse()
        for plant in reordered['plants']:
            plant['workstations'].reverse()
        changed, _ = self.resolve(reordered)
        self.assertEqual(original['products'], changed['products'])
        self.assertEqual(original['arrival_batches'], changed['arrival_batches'])
        self.assertEqual(original['seed_provenance'], changed['seed_provenance'])
        self.assertEqual({plant['id']: {station['id']: station['windows'] for station in plant['workstations']} for plant in original['plants']},
                         {plant['id']: {station['id']: station['windows'] for station in plant['workstations']} for plant in changed['plants']})

    def test_calendar_algorithm_clipping_and_fixed_window_override(self) -> None:
        """Pin simple hand-calculated windows, empty intervals and a fixed station beside generated stations."""
        settings = {'start_time': 0, 'end_time': 10, 'initial_delay': [1, 1],
                    'open_duration': [3, 3], 'closed_duration': [2, 2]}
        validate_schedule_generation(settings, 10)
        self.assertEqual(generate_windows(settings, random.Random(42)), [[1, 4], [6, 9]])
        self.assertEqual(generate_windows({**settings, 'end_time': 8}, random.Random(42)), [[1, 4], [6, 8]])
        self.assertEqual(generate_windows({**settings, 'end_time': 0}, random.Random(42)), [])
        configuration = copy.deepcopy(self.configuration)
        configuration['plants'][0]['workstations'][0]['windows'] = [[1, 10], [12, 40]]
        scenario, _ = self.resolve(configuration)
        self.assertEqual(scenario['plants'][0]['workstations'][0]['windows'], [[1, 10], [12, 40]])
        self.assertNotIn('WS1', scenario['seed_provenance']['stream_seeds']['workstation_schedules']['RF1'])

    def test_invalid_generation_is_rejected_before_creating_cli_output(self) -> None:
        """Reject invalid calendar/batch/quality/route declarations and ambiguous fixed/generated windows."""
        for field, value in [('open_duration', [0, 1]), ('closed_duration', [0, 1]),
                             ('initial_delay', [-1, 2]), ('open_duration', [2, math.inf])]:
            configuration = copy.deepcopy(self.configuration)
            configuration['schedule_generation'][field] = value
            with self.assertRaises(ValueError):
                self.resolve(configuration)
        for mutation in ('batch', 'quality', 'route', 'both'):
            configuration = copy.deepcopy(self.configuration)
            if mutation == 'batch':
                configuration['collection_points'][0]['generation']['batch_size'] = 0
            elif mutation == 'quality':
                configuration['collection_points'][0]['quality_weights']['unknown'] = 1
            elif mutation == 'route':
                configuration['quality_classes'][0]['operations'] = [99]
            else:
                configuration['plants'][0]['workstations'][0].update(windows=[[0, 40]], window_generation={})
            with self.assertRaises(ValueError):
                self.resolve(configuration)
        invalid = self.root / 'invalid.json'
        invalid.write_text(json.dumps({**self.configuration, 'products': []}))
        output = self.root / 'invalid-run'
        result = subprocess.run([sys.executable, '-m', 'simulation', '--config', str(invalid), '--seed', '42',
            '--output', str(output)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())

    def test_independent_verifier_reconstructs_generated_schedules_and_seed_provenance(self) -> None:
        """Reject a plausible forged calendar and forged schedule seed even after updating scenario fingerprints."""
        original = self.run_cli()
        for mutation in ('calendar', 'seed'):
            directory = self.root / f'forged-{mutation}'
            shutil.copytree(original, directory)
            path = directory / 'scenario.json'
            scenario = json.loads(path.read_text())
            if mutation == 'calendar':
                scenario['plants'][0]['workstations'][0]['windows'][0][0] += 0.1
            else:
                scenario['seed_provenance']['stream_seeds']['workstation_schedules']['RF1']['WS1'] += 1
            path.write_text(json.dumps(scenario))
            manifest_path = directory / 'run_manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest.update(scenario_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), seed_provenance=scenario['seed_provenance'])
            manifest_path.write_text(json.dumps(manifest))
            report = self.verify_cli(directory, 1)
            expected_rule = 'generation.workstation_windows' if mutation == 'calendar' else 'generation.network_seed_provenance'
            self.assertTrue(any(finding['rule'] == expected_rule for finding in report['findings']))

    def test_network_verifier_remains_standalone_and_detects_product_forgery(self) -> None:
        """Run a copied checker outside the repository and audit a forged generated quality through existing checks."""
        original = self.run_cli()
        isolated = self.root / 'isolated'
        isolated.mkdir()
        checker = isolated / 'verify_simulation.py'
        shutil.copy2(ROOT / 'tools' / 'verify.py', checker)
        result = subprocess.run([sys.executable, '-I', str(checker), str(original)], cwd=isolated, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        scenario = json.loads((original / 'scenario.json').read_text())
        scenario['products'][0]['quality_class'] = 'forged'
        path = original / 'scenario.json'
        path.write_text(json.dumps(scenario))
        manifest = json.loads((original / 'run_manifest.json').read_text())
        manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (original / 'run_manifest.json').write_text(json.dumps(manifest))
        report = self.verify_cli(original, 1)
        self.assertTrue(any(finding['rule'] == 'generation.products' for finding in report['findings']))

    def test_zero_arrivals_and_filtered_recycling_routes_reuse_legacy_rules(self) -> None:
        """No-recycler networks filter recycling classes before sampling and zero-rate sites create no products."""
        configuration = copy.deepcopy(self.configuration)
        configuration['quality_classes'].append({'id': 'QR', 'weight': 100, 'disposition': 'recycle', 'operations': []})
        configuration['collection_points'][0]['generation']['arrival_rate'] = 0
        scenario, _ = self.resolve(configuration)
        self.assertFalse(any(product['origin'] == 'CP1' for product in scenario['products']))
        self.assertTrue(all(product['disposition'] == 'remanufacture' for product in scenario['products']))
        self.assertEqual(scenario['seed_provenance']['generation_version'], 2)


if __name__ == '__main__':
    unittest.main()
