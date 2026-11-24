"""Reject retired scalar inputs while preserving inventory/network tools and runs.

Historical fixture files are copied read only. Altered evidence and reports stay
in temporary folders. These cases test deliberate retirement, not a converter.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from simulation.config import load_scenario, validate_scenario
from simulation.runner import run_once
from tests.single_product import single_product_scenario
from tools.experiments import refresh_verification, run_experiments
from tools.kpis import export_kpis, measure_run
from tools.replay import RunReplay
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / 'tests/fixtures/checkpoint_1'
POLICIES = ('static_quality_unaware', 'static_quality_aware',
            'dynamic_quality_unaware', 'dynamic_quality_aware')


class FormatRetirementTests(unittest.TestCase):
    """Use real entry points and check explicit unsupported-scope outcomes."""

    def setUp(self) -> None:
        """Create automatically cleaned scratch paths; never edit archived evidence."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def copy_history(self, name: str) -> Path:
        """Return private historical evidence; each test chooses any prior report."""
        folder = self.root / name
        shutil.copytree(HISTORICAL, folder)
        return folder

    def save_scenario(self, name: str, scenario: dict) -> Path:
        """Write owned inputs as UTF-8 JSON and return the temporary path."""
        source = self.root / (name + '.json')
        source.write_text(json.dumps(scenario) + '\n', encoding='utf-8')
        return source

    def test_simulation_rejects_format_one_before_output_creation(self) -> None:
        """Every policy override rejects archived scalar inputs clearly, without a traceback."""
        source = HISTORICAL / 'scenario.json'
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'format 1 is retired'):
            load_scenario(source)
        for policy in POLICIES:
            with self.subTest(policy=policy):
                folder = self.root / policy
                with self.assertRaisesRegex(ValueError, 'format 1 is retired'):
                    run_once(source, folder, policy=policy)
                self.assertFalse(folder.exists())
                result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario',
                    str(source), '--policy', policy, '--output', str(folder)], cwd=ROOT,
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('format 1 is retired', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(folder.exists())
        config_output = self.root / 'generated-retired'
        result = subprocess.run([sys.executable, '-m', 'simulation', '--config', str(source),
            '--seed', '42', '--output', str(config_output)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('format 1 is retired', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(config_output.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_missing_and_unsupported_formats_reject_before_output_creation(self) -> None:
        """Missing or unusable format declarations never fall through to an inventory engine."""
        for index, version in enumerate((None, 0, 99, [], {}, '2', True)):
            with self.subTest(version=version):
                scenario = single_product_scenario()
                if version is None:
                    scenario.pop('format_version')
                else:
                    scenario['format_version'] = version
                source = self.save_scenario(f'unsupported-{index}', scenario)
                folder = self.root / f'rejected-{index}'
                with self.assertRaisesRegex(ValueError, 'supported formats are 2 and 3'):
                    validate_scenario(scenario)
                result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario',
                    str(source), '--output', str(folder)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('formats are 2 and 3', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(folder.exists())
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            validate_scenario([])

    def test_verifier_refreshes_retired_runs_from_every_previous_status(self) -> None:
        """Archived format-one evidence becomes fresh INCOMPLETE/2, including over old PASS."""
        for previous in (None, 'PASS', 'FAIL', 'INCOMPLETE'):
            with self.subTest(previous=previous):
                folder = self.copy_history(str(previous))
                before = {p.name: p.read_bytes() for p in folder.iterdir()
                          if p.name not in ('verification.json', 'verification.txt')}
                for name in ('verification.json', 'verification.txt'):
                    (folder / name).unlink(missing_ok=True)
                if previous:
                    (folder / 'verification.json').write_text(json.dumps({'status': previous}))
                    (folder / 'verification.txt').write_text('OLD REPORT ' + previous)
                report = verify_run(folder)
                self.assertEqual((report['status'], report['exit_code'], report['replay_complete']),
                                 ('INCOMPLETE', 2, False))
                finding = next(f for f in report['findings'] if f['rule'] == 'scenario.scope')
                self.assertIn('format 1 is retired', finding['observed'])
                self.assertIn('INCOMPLETE (exit 2)', (folder / 'verification.txt').read_text())
                self.assertNotIn('OLD REPORT', (folder / 'verification.txt').read_text())
                reports = [(folder / name).read_bytes() for name in ('verification.json', 'verification.txt')]
                self.assertEqual(verify_run(folder), report)
                self.assertEqual(reports, [(folder / name).read_bytes() for name in ('verification.json', 'verification.txt')])
                for name, contents in before.items():
                    self.assertEqual((folder / name).read_bytes(), contents)

    def test_retired_verification_commands_and_standalone_exit_two(self) -> None:
        """Preferred, root and isolated verifier commands all classify retired evidence."""
        folder = self.copy_history('commands')
        standalone = self.root / 'verify.py'
        shutil.copyfile(ROOT / 'tools/verify.py', standalone)
        for command in (['-m', 'tools.verify'], [str(ROOT / 'verify_simulation.py')], ['-I', str(standalone)]):
            result = subprocess.run([sys.executable, *command, str(folder)], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('INCOMPLETE (exit 2)', result.stdout)
            self.assertEqual(json.loads((folder / 'verification.json').read_bytes())['exit_code'], 2)

    def test_proved_hash_violation_precedes_unsupported_scope(self) -> None:
        """A raw-byte hash contradiction is still FAIL even when physical replay is unsupported."""
        folder = self.copy_history('hash')
        source = folder / 'scenario.json'
        source.write_bytes(source.read_bytes() + b'\n')
        report = verify_run(folder)
        self.assertEqual((report['status'], report['exit_code']), ('FAIL', 1))
        self.assertEqual({f['category'] for f in report['findings']}, {'violation', 'incomplete'})
        self.assertTrue(any(f['rule'] == 'manifest.scenario_sha256' for f in report['findings']))
        self.assertIn('FAIL (exit 1)', (folder / 'verification.txt').read_text())

    def test_replay_and_viewer_reject_history_without_writing_an_image(self) -> None:
        """Replay rejects scope before rendering; the viewer CLI reports the same clear error."""
        folder = self.copy_history('viewer')
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        with self.assertRaisesRegex(ValueError, 'format 1 is retired'):
            RunReplay(folder)
        image = self.root / 'unsupported.png'
        result = subprocess.run([sys.executable, '-m', 'tools.viewer', str(folder)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('format 1 is retired', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(image.exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})

    def test_kpi_mixed_batch_keeps_supported_rows_and_names_retired_skip(self) -> None:
        """Two supported runs remain in Excel CSV while the archived scalar run is explicitly skipped."""
        batch = self.root / 'batch'
        batch.mkdir()
        retired = batch / 'retired'
        shutil.copytree(HISTORICAL, retired)
        source = self.save_scenario('single', single_product_scenario())
        run_once(source, batch / 'inventory')
        shutil.copytree(ROOT / 'tests/fixtures/checkpoint_4b', batch / 'network')
        before = {p.relative_to(batch).as_posix(): p.read_bytes() for p in batch.rglob('*') if p.is_file()}
        output = self.root / 'kpis.csv'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(export_kpis(batch, output), (2, 1))
        self.assertIn('retired', stderr.getvalue())
        self.assertIn('format 1 is retired', stderr.getvalue())
        with output.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({r['Run'] for r in rows}, {'inventory', 'network'})
        self.assertEqual(next(r for r in rows if r['Run'] == 'inventory')['Verification'], 'NOT_VERIFIED')
        with self.assertRaisesRegex(ValueError, 'format 1 is retired'):
            measure_run(retired, json.loads((retired / 'run_manifest.json').read_bytes()))
        self.assertEqual(before, {p.relative_to(batch).as_posix(): p.read_bytes() for p in batch.rglob('*') if p.is_file()})
        result = subprocess.run([sys.executable, str(ROOT / 'export_simulation_kpis.py'), str(batch),
                                 '--output', str(self.root / 'cli.csv')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_bytes(), (self.root / 'cli.csv').read_bytes())
        standalone = self.root / 'kpis.py'
        shutil.copyfile(ROOT / 'tools/kpis.py', standalone)
        isolated_csv = self.root / 'isolated.csv'
        result = subprocess.run([sys.executable, '-I', str(standalone), str(batch), '--output', str(isolated_csv)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('format 1 is retired', result.stderr)
        self.assertEqual(output.read_bytes(), isolated_csv.read_bytes())
        saved_csv = output.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(export_kpis(retired, output), (0, 1))
        self.assertEqual(output.read_bytes(), saved_csv)

    def test_experiment_refresh_excludes_retired_attempt_and_keeps_attempt_records(self) -> None:
        """A retired run cannot retain PASS or join a matched policy comparison."""
        batch = self.root / 'experiment'
        manifest = run_experiments([ROOT / 'tests/inputs/checkpoint_5.json'], 1, 2026, batch)
        self.assertTrue(manifest['comparisons'][0]['complete'])
        original_attempts = copy.deepcopy([r['simulation'] for r in manifest['runs']])
        folder = batch / manifest['runs'][0]['run_directory']
        shutil.rmtree(folder)
        shutil.copytree(HISTORICAL, folder)
        refreshed = refresh_verification(batch)
        self.assertEqual(len(refreshed['runs']), 4)
        self.assertEqual([r['simulation'] for r in refreshed['runs']], original_attempts)
        self.assertEqual(refreshed['runs'][0]['verification']['status'], 'INCOMPLETE')
        self.assertEqual(refreshed['runs'][0]['verification']['exit_code'], 2)
        self.assertFalse(refreshed['comparisons'][0]['complete'])
        self.assertEqual(refreshed['comparisons'][0]['missing_policies'], [refreshed['runs'][0]['policy']])
        self.assertTrue(all(not r['included_in_verified_comparison'] for r in refreshed['runs']))
        self.assertEqual(json.loads((folder / 'verification.json').read_bytes())['status'], 'INCOMPLETE')

    def test_single_product_retained_formats_and_all_permitted_policies(self) -> None:
        """Single-product inventory and network inputs still execute and verify, without renumbering formats."""
        inventory = single_product_scenario()
        network = {key: copy.deepcopy(value) for key, value in inventory.items()
                   if key not in ('plant', 'collection_point', 'trips')}
        network.update(format_version=3, policy='dynamic_quality_unaware', plants=[inventory['plant']],
                       collection_points=[inventory['collection_point']], trips=[])
        for version, scenario, policies in ((2, inventory, POLICIES), (3, network, POLICIES[2:])):
            source = self.save_scenario(f'format-{version}', scenario)
            for policy in policies:
                with self.subTest(version=version, policy=policy):
                    folder = self.root / f'{version}-{policy}'
                    summary = run_once(source, folder, policy=policy)
                    self.assertEqual((summary['remanufactured'], summary['vehicle_distance'], summary['plant_inventory_wait']), (1, 4, 3))
                    self.assertEqual(verify_run(folder)['status'], 'PASS')
                    self.assertEqual((folder / 'scenario.json').read_bytes(), source.read_bytes())
                    self.assertEqual(json.loads((folder / 'scenario.json').read_bytes())['format_version'], version)

    def test_tools_classify_missing_or_malformed_format_declarations(self) -> None:
        """Coherent hashing lets malformed format declarations reach explicit tool scope checks."""
        for index, version in enumerate((None, [], {}, 'two', True, 99)):
            with self.subTest(version=version):
                folder = self.root / f'malformed-format-{index}'
                shutil.copytree(ROOT / 'tests/fixtures/checkpoint_3a', folder)
                path = folder / 'scenario.json'
                scenario = json.loads(path.read_bytes())
                if version is None:
                    scenario.pop('format_version')
                else:
                    scenario['format_version'] = version
                path.write_text(json.dumps(scenario) + '\n', encoding='utf-8')
                manifest_path = folder / 'run_manifest.json'
                manifest = json.loads(manifest_path.read_bytes())
                manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest) + '\n', encoding='utf-8')
                (folder / 'verification.txt').write_text('OLD REPORT PASS')
                report = verify_run(folder)
                self.assertEqual((report['status'], report['exit_code']), ('INCOMPLETE', 2))
                self.assertTrue(any(f['rule'] == 'scenario.scope' for f in report['findings']))
                self.assertNotIn('OLD REPORT', (folder / 'verification.txt').read_text())
                with self.assertRaisesRegex(ValueError, 'Replay supports scenario formats 2 and 3'):
                    RunReplay(folder)
                with self.assertRaisesRegex(ValueError, 'KPI export supports scenario formats 2 and 3'):
                    measure_run(folder, manifest)

    def test_network_static_overrides_remain_unsupported(self) -> None:
        """Retiring scalar execution does not expand static routing into network support."""
        source = ROOT / 'tests/fixtures/checkpoint_4c/scenario.json'
        for policy in POLICIES[:2]:
            folder = self.root / policy
            with self.assertRaisesRegex(ValueError, 'Format three requires a supported dynamic policy'):
                run_once(source, folder, policy=policy)
            self.assertFalse(folder.exists())


if __name__ == '__main__':
    unittest.main()
