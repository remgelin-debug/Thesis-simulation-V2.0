"""Check seeded site coordinates without shifting returns or workstation schedules.

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

from simulation.generator import COORDINATE_SITE_GROUPS, derive_coordinate_seed, prepare_scenario
from simulation.state import ScenarioDefinitions
from simulation.config import as_dynamic_scenario
from simulation.engine import build_dynamic_state
from simulation.state import Record
from tools.replay import RunReplay

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs' / 'three_site_pilot.json'


def site_positions(scenario: Record) -> Record:
    """Read site-type/ID coordinates for order-independent geometry comparisons without mutation."""
    return {kind: {site['id']: site['coordinates'] for site in scenario[group]} for kind, group in COORDINATE_SITE_GROUPS}


def workstation_windows(scenario: Record) -> Record:
    """Read saved facility/station windows without including generated geometry or input metadata."""
    return {plant['id']: {station['id']: station['windows'] for station in plant['workstations']} for plant in scenario['plants']}


class CoordinateGenerationTests(unittest.TestCase):
    """Exercise actual child CLIs and retain all edits in automatically cleaned temporary folders."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate scratch space, source configuration copy and unique case numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.configuration = json.loads(CONFIG.read_text())
        self.number = 0

    def resolve(self, configuration: Record, seed: int = 42) -> tuple[Record, bytes]:
        """Save temporary input settings and resolve through the public seeded entry point."""
        self.number += 1
        path = self.root / f'configuration-{self.number}.json'
        path.write_text(json.dumps(configuration))
        return prepare_scenario(path, seed)

    def run_cli(self, seed: int = 42, configuration: Record | None = None, resolved: Path | None = None,
                policy: str = 'dynamic_quality_aware') -> tuple[Path, Record, list[Record]]:
        """Execute one real simulation CLI and return its folder, saved inputs and recorded events."""
        self.number += 1
        directory = self.root / f'run-{self.number}'
        source_path = CONFIG
        if configuration is not None:
            source_path = self.root / f'input-{self.number}.json'
            source_path.write_text(json.dumps(configuration))
        source = ['--config', str(source_path), '--seed', str(seed)] if resolved is None else ['--resolved-scenario', str(resolved)]
        result = subprocess.run([sys.executable, '-m', 'simulation', *source, '--policy', policy,
            '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return directory, json.loads((directory / 'scenario.json').read_text()), [
            json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]

    def verify_cli(self, directory: Path, expected_exit: int = 0) -> Record:
        """Independently verify a saved run and return its report after checking exit status."""
        result = subprocess.run([sys.executable, str(ROOT / 'verify_simulation.py'), str(directory)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        return json.loads((directory / 'verification.json').read_text())

    # Check the expected model or evidence behavior

    def test_cli_same_seed_layout_repeat_different_seed_and_both_policy_replays(self) -> None:
        """Repeat generated coordinates/events exactly, change layout with seed 43 and verify shared-input policies."""
        first, scenario, _ = self.run_cli()
        repeat, repeated, _ = self.run_cli()
        different, changed, _ = self.run_cli(43)
        replay, _, _ = self.run_cli(resolved=first / 'scenario.json')
        unaware, _, _ = self.run_cli(resolved=first / 'scenario.json', policy='dynamic_quality_unaware')
        self.assertEqual(site_positions(scenario), site_positions(repeated))
        self.assertNotEqual(site_positions(scenario), site_positions(changed))
        for name in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((first / name).read_bytes(), (repeat / name).read_bytes())
            self.assertEqual((first / name).read_bytes(), (replay / name).read_bytes())
        self.assertEqual((first / 'scenario.json').read_bytes(), (unaware / 'scenario.json').read_bytes())
        for directory in (first, repeat, different, replay, unaware):
            self.assertEqual(self.verify_cli(directory)['status'], 'PASS')
        self.assertTrue(all(station['windows'][0][0] == 0 for plant in scenario['plants'] for station in plant['workstations']))
        self.assertNotIn(b'\r', (first / 'scenario.json').read_bytes())

    def test_enabling_coordinates_keeps_returns_qualities_and_windows_identical(self) -> None:
        """Toggle geometry sampling only and preserve all return/calendar samples and their original stream seeds."""
        generated, _ = self.resolve(self.configuration)
        fixed_configuration = copy.deepcopy(self.configuration)
        fixed_configuration.pop('coordinate_generation')
        fixed, _ = self.resolve(fixed_configuration)
        self.assertEqual(generated['products'], fixed['products'])
        self.assertEqual(generated['arrival_batches'], fixed['arrival_batches'])
        self.assertEqual(workstation_windows(generated), workstation_windows(fixed))
        self.assertNotEqual(site_positions(generated), site_positions(fixed))
        self.assertEqual(fixed['vehicles'], generated['vehicles'])
        for name in ('returns', 'qualities', 'workstation_schedules'):
            self.assertEqual(generated['seed_provenance']['stream_seeds'][name], fixed['seed_provenance']['stream_seeds'][name])
        self.assertNotIn('coordinates', fixed['seed_provenance']['stream_seeds'])
        self.assertNotIn('coordinate_algorithm', fixed['seed_provenance'])
        # The pre-coordinate pilot remains exactly reproducible when its original settings are restored.
        fixed_configuration['schedule_generation']['initial_delay'] = [0, 3]
        _, original_bytes = self.resolve(fixed_configuration)
        self.assertEqual(hashlib.sha256(original_bytes).hexdigest(), 'f461e6a2d6240410451aaa6f34bc68bcfcee247d1641208ea257c08792e33a3b')

    def test_coordinate_stream_identity_and_axis_bounds(self) -> None:
        """Use master seed/type/ID identities, support degenerate axes and keep samples inside configured bounds."""
        self.assertEqual(derive_coordinate_seed(42, 'plant', 'RF1'), derive_coordinate_seed(42, 'plant', 'RF1'))
        self.assertEqual(len({derive_coordinate_seed(42, 'plant', 'S'), derive_coordinate_seed(42, 'collection_point', 'S'),
                             derive_coordinate_seed(42, 'recycler', 'S'), derive_coordinate_seed(43, 'plant', 'S'),
                             derive_coordinate_seed(42, 'plant', 'T')}), 5)
        scenario, _ = self.resolve(self.configuration)
        for group in site_positions(scenario).values():
            for x, y in group.values():
                self.assertTrue(0 <= x <= 8 and 0 <= y <= 8)
        degenerate = copy.deepcopy(self.configuration)
        degenerate['coordinate_generation'] = {'x_range': [2, 2], 'y_range': [-3, -3]}
        fixed_axis, _ = self.resolve(degenerate)
        self.assertTrue(all(coordinates == [2, -3] for group in site_positions(fixed_axis).values() for coordinates in group.values()))

    def test_stream_isolation_in_both_directions_and_site_order(self) -> None:
        """Coordinate bounds and other generator edits leave unrelated streams and per-ID positions unchanged."""
        original, _ = self.resolve(self.configuration)
        bounds = copy.deepcopy(self.configuration)
        bounds['coordinate_generation'] = {'x_range': [-8, 8], 'y_range': [0, 2]}
        changed, _ = self.resolve(bounds)
        self.assertNotEqual(site_positions(original), site_positions(changed))
        self.assertEqual(original['products'], changed['products'])
        self.assertEqual(original['arrival_batches'], changed['arrival_batches'])
        self.assertEqual(workstation_windows(original), workstation_windows(changed))
        other_rules = copy.deepcopy(self.configuration)
        other_rules['collection_points'][0]['generation']['arrival_rate'] *= 2
        other_rules['collection_points'][1]['quality_weights'] = {'Q132': 1, 'Q3': 0, 'Q21': 0}
        other_rules['schedule_generation']['open_duration'] = [5, 7]
        changed, _ = self.resolve(other_rules)
        self.assertEqual(site_positions(original), site_positions(changed))
        reordered = copy.deepcopy(self.configuration)
        reordered['collection_points'].reverse()
        reordered['plants'].reverse()
        reordered['recycling_facilities'].append({'id': 'RC1', 'coordinates': [0, 0]})
        changed, _ = self.resolve(reordered)
        for group in ('plant', 'collection_point'):
            self.assertEqual(site_positions(original)[group], site_positions(changed)[group])
        self.assertEqual(original['products'], changed['products'])
        self.assertEqual(workstation_windows(original), workstation_windows(changed))

    def test_recyclers_missing_source_coordinates_and_initial_vehicle_positions(self) -> None:
        """Sample all site kinds without source placeholders and start AVs at their generated initial facilities."""
        configuration = copy.deepcopy(self.configuration)
        configuration['recycling_facilities'] = [{'id': 'RC1'}]
        configuration['vehicles'][2]['initial_site'] = 'RC1'
        configuration['quality_classes'] = [{'id': 'QR', 'weight': 1, 'disposition': 'recycle', 'operations': []}]
        for point in configuration['collection_points']:
            point['quality_weights'] = {'QR': 1}
        for _, group in COORDINATE_SITE_GROUPS:
            for site in configuration[group]:
                site.pop('coordinates', None)
        directory, scenario, events = self.run_cli(configuration=configuration)
        self.verify_cli(directory)
        execution = as_dynamic_scenario(scenario)
        state = build_dynamic_state(execution, ScenarioDefinitions.from_scenario(execution))
        sites = {site['id']: site for _, group in COORDINATE_SITE_GROUPS for site in scenario[group]}
        for definition in scenario['vehicles']:
            self.assertEqual(state.vehicles[definition['id']]['position'], sites[definition['initial_site']]['coordinates'])
        self.assertEqual(RunReplay(directory).snapshot(0)['vehicles']['AV3']['position'], sites['RC1']['coordinates'])
        self.assertIn('RC1', scenario['seed_provenance']['stream_seeds']['coordinates']['recycler'])
        self.assertTrue(any(event['event_type'] == 'recycler_acceptance' for event in events))
        self.assertTrue(all('coordinates' not in station for plant in scenario['plants'] for station in plant['workstations']))

    def test_invalid_coordinate_settings_rejected_before_output_creation(self) -> None:
        """Reject absent/reversed/nonfinite/overflowing bounds without creating a partial simulation folder."""
        for settings in (None, {}, {'x_range': [1, 0], 'y_range': [0, 8]},
                         {'x_range': [0, math.nan], 'y_range': [0, 8]},
                         {'x_range': [True, 8], 'y_range': [0, 8]},
                         {'x_range': [-1e308, 1e308], 'y_range': [0, 8]}):
            configuration = copy.deepcopy(self.configuration)
            configuration['coordinate_generation'] = settings
            with self.assertRaises(ValueError):
                self.resolve(configuration)
        path = self.root / 'invalid.json'
        path.write_text(json.dumps({**self.configuration, 'coordinate_generation': None}))
        directory = self.root / 'invalid-output'
        result = subprocess.run([sys.executable, '-m', 'simulation', '--config', str(path), '--seed', '42',
            '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(directory.exists())

    def test_independent_verifier_detects_false_positions_and_coordinate_seed(self) -> None:
        """Recalculate positions and exact seed identities even when a forged scenario's raw fingerprint is updated."""
        original, _, _ = self.run_cli()
        for mutation in ('position', 'seed'):
            directory = self.root / f'forged-{mutation}'
            shutil.copytree(original, directory)
            path = directory / 'scenario.json'
            scenario = json.loads(path.read_text())
            if mutation == 'position':
                scenario['plants'][0]['coordinates'][0] += 0.25
            else:
                scenario['seed_provenance']['stream_seeds']['coordinates']['plant']['RF1'] += 1
            path.write_text(json.dumps(scenario))
            manifest_path = directory / 'run_manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest.update(scenario_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), seed_provenance=scenario['seed_provenance'])
            manifest_path.write_text(json.dumps(manifest))
            report = self.verify_cli(directory, 1)
            rule = 'generation.site_coordinates' if mutation == 'position' else 'generation.network_seed_provenance'
            self.assertTrue(any(finding['rule'] == rule for finding in report['findings']))

    def test_pickups_and_space_available_admissions_match_arrival_timestamps(self) -> None:
        """No artificial service delay is added; positive leg durations still equal actual distance divided by speed."""
        directory, scenario, events = self.run_cli()
        arrivals = {}
        pickups = admissions = 0
        speeds = {vehicle['id']: vehicle['speed'] for vehicle in scenario['vehicles']}
        for event in events:
            if event['event_type'] == 'leg_arrival':
                arrivals[event['vehicle_id']] = event
            elif event['event_type'] in ('pickup', 'plant_admission'):
                arrival = arrivals[event['vehicle_id']]
                target = event['collection_point'] if event['event_type'] == 'pickup' else event['plant_id']
                self.assertEqual(arrival['target'], target)
                self.assertEqual(event['time'], arrival['time'])
                pickups += event['event_type'] == 'pickup'
                admissions += event['event_type'] == 'plant_admission'
            elif event['event_type'] == 'leg_departure':
                self.assertGreater(event['distance'], 0)
                self.assertAlmostEqual(event['arrival_time'] - event['departure_time'], event['distance'] / speeds[event['vehicle_id']])
        self.assertEqual((pickups, admissions), (6, 6))
        self.assertFalse(any(event['event_type'] == 'unload_wait' for event in events))
        self.verify_cli(directory)

    def test_full_inventory_still_keeps_load_onboard_until_slot_freed(self) -> None:
        """A positive-distance two-part delivery waits three units for the first part's held slot, with priority intact."""
        configuration = copy.deepcopy(self.configuration)
        configuration['coordinate_generation'] = {'x_range': [0, 1], 'y_range': [0, 1]}
        for plant in configuration['plants']:
            plant['inventory_capacity'] = 1
        for point in configuration['collection_points']:
            point['generation']['batch_size'] = 2
            point['quality_weights'] = {'Q132': 1, 'Q3': 0, 'Q21': 0}
            if point['id'] != 'CP1':
                point['generation']['arrival_rate'] = 0
        directory, _, events = self.run_cli(configuration=configuration, policy='dynamic_quality_unaware')
        first = next(event for event in events if event['event_type'] == 'plant_admission' and event['product_id'] == 'CP1:P000001')
        second = next(event for event in events if event['event_type'] == 'plant_admission' and event['product_id'] == 'CP1:P000002')
        release = next(event for event in events if event['event_type'] == 'inventory_slot_release' and event['product_id'] == 'CP1:P000001')
        self.assertAlmostEqual(second['time'] - first['time'], 3)
        self.assertEqual(second['time'], release['time'])
        waiting = next(event for event in events if event['event_type'] == 'unload_wait')
        self.assertEqual(waiting['load'], ['CP1:P000002'])
        self.assertEqual(waiting['time'], first['time'])
        before = RunReplay(directory).snapshot(first['time'])
        self.assertIn('CP1:P000002', before['vehicles'][waiting['vehicle_id']]['load'])
        self.assertTrue(any(event['event_type'] == 'leg_departure' and event['distance'] > 0 for event in events))
        self.verify_cli(directory)


if __name__ == '__main__':
    unittest.main()
