"""Check event-based KPI calculations against small independent expected totals.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import codecs
import csv
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import kpis as exporter

from tests.single_product import single_product_scenario

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'
PILOT = ROOT / 'configs' / 'three_site_pilot.json'


class KPIExporterTests(unittest.TestCase):
    """Keep generated runs, corrupted evidence and output CSVs in cleaned temporary directories."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate scratch space and reset unique source/run numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.number = 0

    def run_scenario(self, scenario: exporter.Record | Path, *, seed: int | None = None,
                     policy: str | None = None, output: Path | None = None) -> Path:
        """Create one saved run through the existing CLI, returning its directory without exporter dependencies."""
        self.number += 1
        path = scenario
        if isinstance(scenario, dict):
            path = self.root / f'input-{self.number}.json'
            path.write_text(json.dumps(scenario))
        directory = output or self.root / f'run-{self.number}'
        source = ['--resolved-scenario', str(path)] if seed is None else ['--config', str(path), '--seed', str(seed)]
        command = [sys.executable, '-m', 'simulation', *source, '--output', str(directory)]
        if policy is not None:
            command.extend(['--policy', policy])
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return directory

    def export_cli(self, source: Path, output: Path, exit_code: int = 0) -> tuple[list[dict[str, str]], subprocess.CompletedProcess[str]]:
        """Run the real exporter CLI and read its Excel-ready CSV when one was produced."""
        result = subprocess.run([sys.executable, str(ROOT / 'export_simulation_kpis.py'), str(source), '--output', str(output)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        rows = []
        if output.exists():
            with output.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
        return rows, result

    def measure(self, folder: Path) -> exporter.Record:
        """Read the saved manifest and call the public event integration function without writing artifacts."""
        return exporter.measure_run(folder, exporter.read_json(folder / 'run_manifest.json'))

    # Check the expected model or evidence behavior

    def test_blank_event_lines_preserve_metrics_and_error_line_numbers(self) -> None:
        """Exercise streamed parsing through measurement/CLI and retain physical line diagnostics."""
        folder = self.root / 'streamed-run'
        shutil.copytree(FIXTURES / 'checkpoint_3a', folder)
        expected = self.measure(folder)
        path = folder / 'events.jsonl'
        records = path.read_text(encoding='utf-8').splitlines()
        path.write_text('\n' + '\n\n'.join(records) + '\n', encoding='utf-8')
        self.assertEqual(self.measure(folder), expected)
        invalid = json.loads(records[2])
        invalid['sequence'] = 999
        for record, message in (
            (json.dumps(invalid), 'Expected event sequence 3 at line 6'),
            ('[]', 'Event line 6 must be a JSON object'),
        ):
            with self.subTest(record=record):
                changed = [*records[:2], record, *records[3:]]
                path.write_text('\n' + '\n\n'.join(changed) + '\n', encoding='utf-8')
                rows, result = self.export_cli(folder, self.root / 'invalid.csv', 1)
                self.assertEqual(rows, [])
                self.assertIn(message, result.stderr)
                self.assertFalse((self.root / 'invalid.csv').exists())

    def test_hand_calculable_holds_busy_union_and_utilization(self) -> None:
        """Check the two-product one-slot fixture's travel, waits, held capacity and overlapping workstation jobs."""
        row = self.measure(FIXTURES / 'checkpoint_3a')
        self.assertEqual([row[name] for name in ('Generated', 'Remanufactured', 'Recycled', 'Unfinished')], [2, 2, 0, 0])
        self.assertEqual(row['AV travel time'], 8)
        self.assertEqual(row['Vehicle distance'], 8)
        self.assertEqual(row['AV unload wait'], 3)
        self.assertEqual(row['Mean release-to-pickup time'], 2.5)
        self.assertAlmostEqual(row['Mean collection-point inventory'], 5 / 12)
        self.assertEqual(row['Awaiting pickup at horizon'], 0)
        self.assertEqual(row['Plant inventory wait'], 3)
        self.assertEqual(row['Mean waiting inventory'], 3 / 12)
        self.assertEqual(row['Mean occupied slots'], 5 / 12)
        self.assertEqual(row['Peak occupied slots'], 1)
        self.assertAlmostEqual(row['Mean inventory capacity used (%)'], 100 * 5 / 12)
        self.assertAlmostEqual(row['Plant RF1 busy (%)'], 100 * 5 / 12)
        self.assertEqual(row['Workstation utilization of available time (%)'], 100)
        self.assertEqual(row['Plant RF1 mean occupied slots'], 5 / 12)
        self.assertEqual(row['Plant RF1 peak occupied slots'], 1)
        truncated = json.loads((FIXTURES / 'checkpoint_3a' / 'scenario.json').read_text())
        truncated['horizon'] = 6
        partial = self.measure(self.run_scenario(truncated))
        self.assertEqual(partial['AV unload wait'], 1)
        self.assertEqual(partial['Plant inventory wait'], 0)
        self.assertEqual(partial['Mean occupied slots'], 2 / 6)

    def test_reroute_and_partial_legs_use_actual_elapsed_travel(self) -> None:
        """Count a cancelled prefix once and truncate both ordinary and rerouted motion at the horizon."""
        row = self.measure(FIXTURES / 'checkpoint_4b')
        self.assertAlmostEqual(row['AV travel time'], 5 + math.sqrt(2) + math.sqrt(5))
        self.assertAlmostEqual(row['Vehicle distance'], row['AV travel time'])
        partial = self.run_scenario(ROOT / 'tests' / 'inputs' / 'checkpoint_3c_mid_leg.json')
        row = self.measure(partial)
        self.assertEqual((row['AV travel time'], row['Vehicle distance'], row['Unfinished']), (4, 4, 1))
        reroute = json.loads((ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json').read_text())
        reroute['horizon'] = 6
        reroute['plant']['workstations'][0]['windows'] = [[0, 6]]
        row = self.measure(self.run_scenario(reroute))
        self.assertEqual(row['AV travel time'], 6)
        self.assertAlmostEqual(row['Vehicle distance'], 6)
        self.assertEqual(row['Awaiting pickup at horizon'], 1)
        self.assertEqual(row['Mean release-to-pickup time'], 4)
        self.assertEqual(row['Mean collection-point inventory'], 5 / 6)
        single = single_product_scenario()
        single['horizon'] = 1
        row = self.measure(self.run_scenario(single))
        self.assertEqual((row['AV travel time'], row['Vehicle distance']), (1, 1))
        speed_two = self.measure(self.run_scenario(single_product_scenario()))
        self.assertEqual(speed_two['AV travel time'], 4)
        single = single_product_scenario()
        single['vehicles'][0]['speed'] = 2
        speed_two = self.measure(self.run_scenario(single))
        self.assertEqual((speed_two['AV travel time'], speed_two['Vehicle distance']), (2, 4))

    def test_peak_occupied_counts_instantaneous_admissions_before_starts(self) -> None:
        """Two simultaneous final-operation admissions reach peak two even though their time-weighted stock is zero."""
        scenario = json.loads((FIXTURES / 'checkpoint_3a' / 'scenario.json').read_text())
        scenario['horizon'] = 6
        scenario['plant'].update(inventory_capacity=2, workstations=[
            {'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': [[0, 6]]},
            {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[0, 6]]}])
        scenario['collection_point']['coordinates'] = [1, 0]
        for product, operation in zip(scenario['products'], (1, 2)):
            product['operations'] = [operation]
        for trip in scenario['trips']:
            trip['departure_time'] = 0
        row = self.measure(self.run_scenario(scenario))
        self.assertEqual(row['Peak occupied slots'], 2)
        self.assertEqual(row['Plant RF1 peak occupied slots'], 2)
        self.assertEqual(row['Mean occupied slots'], 0)
        self.assertEqual(row['Plant inventory wait'], 0)
        self.assertAlmostEqual(row['Plant RF1 busy (%)'], 100 / 6)
        self.assertAlmostEqual(row['Workstation utilization of available time (%)'], 100 * 2 / 12)

    def test_per_plant_and_global_peaks_mixed_capacity_and_weighted_utilization(self) -> None:
        """Stagger plant stock so global peak is one, preserve finite capacity use beside an unlimited plant."""
        scenario = {'format_version': 3, 'policy': 'dynamic_quality_unaware', 'horizon': 10,
            'seed_provenance': {'source': 'kpi_hand_case', 'seed': None}, 'trips': [], 'recycling_facilities': [],
            'plants': [{'id': 'RF1', 'coordinates': [0, 0], 'inventory_capacity': 1,
                'workstations': [{'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': [[4, 10]]}]},
                {'id': 'RF2', 'coordinates': [10, 0], 'inventory_capacity': None,
                'workstations': [{'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': [[9, 10]]}]}],
            'collection_points': [{'id': 'CP1', 'coordinates': [1, 0]}, {'id': 'CP2', 'coordinates': [9, 0]}],
            'vehicles': [{'id': f'AV{i}', 'initial_site': f'RF{i}', 'speed': 1, 'payload_capacity': 1, 'maximum_range': None}
                         for i in (1, 2)],
            'products': [{'id': f'P{i}', 'origin': f'CP{i}', 'release_time': release, 'quality_class': 'Q',
                          'disposition': 'remanufacture', 'operations': [1]} for i, release in ((1, 0), (2, 5))]}
        row = self.measure(self.run_scenario(scenario))
        self.assertEqual(row['Peak occupied slots'], 1)
        self.assertEqual(row['Mean occupied slots'], 0.4)
        self.assertIsNone(row['Mean inventory capacity used (%)'])
        self.assertEqual(row['Plant RF1 inventory capacity used (%)'], 20)
        self.assertIsNone(row['Plant RF2 inventory capacity used (%)'])
        for plant in ('RF1', 'RF2'):
            self.assertEqual(row[f'Plant {plant} mean occupied slots'], 0.2)
            self.assertEqual(row[f'Plant {plant} peak occupied slots'], 1)
            self.assertEqual(row[f'Plant {plant} busy (%)'], 10)
        self.assertAlmostEqual(row['Workstation utilization of available time (%)'], 200 / 7)

    def test_unpicked_stock_zero_horizon_and_undefined_ratios_are_blank(self) -> None:
        """Uncollected parts contribute collection stock but not mean pickup wait, and zero denominators stay undefined."""
        scenario = json.loads((FIXTURES / 'checkpoint_3a' / 'scenario.json').read_text())
        scenario.update(horizon=4, vehicles=[], trips=[])
        scenario['products'][1]['release_time'] = 2
        for station in scenario['plant']['workstations']:
            station['windows'] = []
        row = self.measure(self.run_scenario(scenario))
        self.assertEqual(row['Mean collection-point inventory'], 1.5)
        self.assertEqual(row['Awaiting pickup at horizon'], 2)
        self.assertIsNone(row['Mean release-to-pickup time'])
        self.assertIsNone(row['Workstation utilization of available time (%)'])
        scenario.update(horizon=0, products=[])
        directory = self.run_scenario(scenario)
        rows, _ = self.export_cli(directory, self.root / 'zero.csv')
        for column in ('Mean release-to-pickup time', 'Mean collection-point inventory', 'Mean occupied slots',
                       'Mean inventory capacity used (%)', 'Plant RF1 busy (%)', 'Workstation utilization of available time (%)'):
            self.assertEqual(rows[0][column], '')

    def test_processing_truncated_at_horizon_and_available_window_union(self) -> None:
        """Use actual busy prefixes, clip offered workstation time and avoid double counting overlapping windows."""
        scenario = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_3c_unlimited.json').read_text())
        scenario['horizon'] = 2
        scenario['collection_point']['coordinates'] = [0, 0]
        scenario['plant']['workstations'][0].update(duration=5, windows=[[0, 10]])
        row = self.measure(self.run_scenario(scenario))
        self.assertEqual(row['Unfinished'], 1)
        self.assertEqual(row['Plant RF1 busy (%)'], 100)
        self.assertEqual(row['Workstation utilization of available time (%)'], 100)
        self.assertEqual(row['Mean occupied slots'], 0)
        self.assertEqual(row['Peak occupied slots'], 1)
        self.assertEqual(exporter.scheduled_time([[0, 2], [1, 4], [4, 8]], 5), 5)
        self.assertEqual(exporter.scheduled_time([[8, 10]], 5), 0)

    def test_current_three_site_seed_loop_batch_excel_encoding_and_read_only(self) -> None:
        """Export three current pilot runs, including an unfinished-but-completed run, with saved PASS statuses."""
        batch = self.root / 'pilot-batch'
        aware = self.run_scenario(PILOT, seed=42, output=batch / 'seed-42')
        different = self.run_scenario(PILOT, seed=43, output=batch / 'seed-43')
        unaware = self.run_scenario(aware / 'scenario.json', policy='dynamic_quality_unaware', output=batch / 'seed-42-unaware')
        for directory in (aware, different, unaware):
            result = subprocess.run([sys.executable, str(ROOT / 'verify_simulation.py'), str(directory)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = {str(path): path.read_bytes() for directory in (aware, different, unaware) for path in directory.iterdir()}
        output = batch / 'kpis.csv'
        rows, result = self.export_cli(batch, output)
        self.assertEqual(len(rows), 3)
        self.assertTrue(output.read_bytes().startswith(codecs.BOM_UTF8))
        self.assertIn('skipped 0', result.stdout)
        self.assertEqual({row['Verification'] for row in rows}, {'PASS'})
        self.assertEqual({row['Seed'] for row in rows}, {'42', '43'})
        self.assertEqual({row['Run'] for row in rows}, {'seed-42', 'seed-43', 'seed-42-unaware'})
        un = next(row for row in rows if row['Policy'] == 'dynamic_quality_unaware')
        # Stop-authorized service retains CP1:P000002 at RF1's earlier visit.
        # Its actual later admission misses the remaining operation windows,
        # so the corrected seed-42 unaware run has two unfinished products.
        self.assertEqual(un['Unfinished'], '2')
        delayed = json.loads((unaware / 'final_state.json').read_text())['products']['CP1:P000002']
        self.assertEqual((delayed['state'], delayed['next_operation']), ('in_plant_inventory', 1))
        self.assertGreater(delayed['milestones']['admission'], 29.66619335673636)
        for row in rows:
            for plant in ('RF1', 'RF2', 'RF3'):
                self.assertIn(f'Plant {plant} busy (%)', row)
                self.assertIn(f'Plant {plant} mean occupied slots', row)
                self.assertIn(f'Plant {plant} peak occupied slots', row)
                self.assertIn(f'Plant {plant} inventory capacity used (%)', row)
                self.assertTrue(0 <= float(row[f'Plant {plant} workstation utilization of available time (%)']) <= 100 + 1e-9)
        self.assertEqual(before, {str(path): path.read_bytes() for directory in (aware, different, unaware) for path in directory.iterdir()})

    def test_experiment_tree_ignores_frozen_inputs_and_finds_nested_runs(self) -> None:
        """A replication's shared scenario/configuration is not a broken run; nested actual policy evidence is exported."""
        root = self.root / 'experiment'
        parent = root / 'conditions' / 'C001' / 'replication_0001'
        parent.mkdir(parents=True)
        (parent / 'scenario.json').write_text('{}')
        (root / 'experiment_manifest.json').write_text('{}')
        shutil.copytree(FIXTURES / 'checkpoint_3a', parent / 'runs' / 'static_quality_unaware')
        rows, result = self.export_cli(root, root / 'kpis.csv')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Run'], 'conditions/C001/replication_0001/runs/static_quality_unaware')
        self.assertEqual(result.stderr, '')

    def test_missing_unreadable_inconsistent_and_incomplete_runs_are_reported(self) -> None:
        """Keep the good row and explicitly name every unusable run, including one missing its manifest."""
        batch = self.root / 'damaged'
        for name in ('good', 'missing-events', 'missing-manifest', 'malformed-summary', 'running', 'hash-mismatch', 'bad-total', 'bad-unload', 'bad-distance'):
            directory = batch / name
            shutil.copytree(FIXTURES / 'checkpoint_3a', directory)
            if name == 'missing-events':
                (directory / 'events.jsonl').unlink()
            elif name == 'missing-manifest':
                (directory / 'run_manifest.json').unlink()
            elif name == 'malformed-summary':
                (directory / 'summary.json').write_text('{bad')
            elif name == 'running':
                path = directory / 'run_manifest.json'
                manifest = json.loads(path.read_text())
                manifest['status'] = 'running'
                path.write_text(json.dumps(manifest))
            elif name == 'hash-mismatch':
                path = directory / 'scenario.json'
                path.write_bytes(path.read_bytes() + b'\n')
            elif name in ('bad-total', 'bad-unload', 'bad-distance'):
                path = directory / 'summary.json'
                summary = json.loads(path.read_text())
                field = {'bad-total': 'plant_inventory_wait', 'bad-unload': 'waiting_to_unload', 'bad-distance': 'vehicle_distance'}[name]
                summary[field] += 1
                path.write_text(json.dumps(summary))
        rows, result = self.export_cli(batch, batch / 'kpis.csv', 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Run'], 'good')
        for name in ('missing-events', 'missing-manifest', 'malformed-summary', 'running', 'hash-mismatch', 'bad-total', 'bad-unload', 'bad-distance'):
            self.assertIn(name, result.stderr)
        self.assertIn('skipped 8', result.stdout)
        self.assertIn('summary.json', result.stderr)

    def test_optional_verification_reports_and_dynamic_column_union_unicode(self) -> None:
        """Export NOT_VERIFIED/FAIL/INCOMPLETE without filtering; preserve Unicode and blank absent plant columns."""
        batch = self.root / 'verification'
        for name, fixture, status in (('échantillon-ß', 'checkpoint_3a', None), ('failed', 'checkpoint_4c', 'FAIL'),
                                      ('incomplete', 'checkpoint_3a', 'INCOMPLETE')):
            directory = batch / name
            shutil.copytree(FIXTURES / fixture, directory)
            if status:
                (directory / 'verification.json').write_text(json.dumps({'status': status}))
        shutil.copytree(FIXTURES / 'checkpoint_3a', batch / 'bad-report')
        (batch / 'bad-report' / 'verification.json').write_text('not json')
        rows, result = self.export_cli(batch, batch / 'kpis.csv', 1)
        self.assertEqual({row['Verification'] for row in rows}, {'NOT_VERIFIED', 'FAIL', 'INCOMPLETE'})
        unverified = next(row for row in rows if row['Verification'] == 'NOT_VERIFIED')
        self.assertEqual(unverified['Run'], 'échantillon-ß')
        self.assertEqual(unverified['Seed'], '')
        self.assertEqual(unverified['Plant RF2 busy (%)'], '')
        self.assertIn('bad-report', result.stderr)
        self.assertIn('verification.json', result.stderr)

    def test_standalone_export_has_no_simulation_dependency(self) -> None:
        """Run a copied exporter with isolated Python outside the repository using only saved fixture files."""
        isolated = self.root / 'isolated'
        isolated.mkdir()
        script = isolated / 'export_simulation_kpis.py'
        shutil.copy2(ROOT / 'tools' / 'kpis.py', script)
        output = isolated / 'kpis.csv'
        result = subprocess.run([sys.executable, '-I', str(script), str(FIXTURES / 'checkpoint_3a'), '--output', str(output)],
                                cwd=isolated, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(output.exists())

    def test_empty_batches_setup_errors_and_old_csv_preserved(self) -> None:
        """No usable rows produce exit one without overwriting old output, while bad setup produces exit two."""
        empty = self.root / 'empty'
        empty.mkdir()
        output = self.root / 'old.csv'
        output.write_text('keep\n')
        _, result = self.export_cli(empty, output, 1)
        self.assertIn('no CSV written', result.stderr)
        self.assertEqual(output.read_text(), 'keep\n')
        self.export_cli(self.root / 'missing', self.root / 'missing.csv', 2)
        self.export_cli(empty, self.root / 'wrong.json', 2)


if __name__ == '__main__':
    unittest.main()
