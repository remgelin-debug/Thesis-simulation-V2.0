"""Check malformed evidence classifications and replacement of stale reports.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
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
from unittest.mock import patch

from simulation.runner import run_once
from tools import experiments
from tools.kpis import export_kpis
from tools.verify import main, verify_run

ROOT = Path(__file__).resolve().parents[1]


class EvidenceStructureTests(unittest.TestCase):
    """Keep malformed artifacts/reports in private directories and preserve source fixtures."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate automatically cleaned evidence roots and a case counter."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def fixture(self, name: str = 'checkpoint_4b') -> Path:
        """Return a fresh copied run with its historical report available for refresh tests."""
        self.counter += 1
        folder = self.root / f'run-{self.counter}'
        shutil.copytree(ROOT / 'tests/fixtures' / name, folder)
        return folder

    def save_json(self, path: Path, value: object) -> None:
        """Replace only owned temporary JSON evidence using UTF-8/LF."""
        path.write_text(json.dumps(value) + '\n', encoding='utf-8')

    def corrupt_provenance(self, folder: Path, value: object, where: str = 'both') -> None:
        """Make specified provenance unusable while keeping scenario hash coherent."""
        scenario = json.loads((folder / 'scenario.json').read_bytes())
        manifest = json.loads((folder / 'run_manifest.json').read_bytes())
        if where in ('scenario', 'both'):
            scenario['seed_provenance'] = copy.deepcopy(value)
        if where in ('manifest', 'both'):
            manifest['seed_provenance'] = copy.deepcopy(value)
        self.save_json(folder / 'scenario.json', scenario)
        manifest['scenario_sha256'] = hashlib.sha256((folder / 'scenario.json').read_bytes()).hexdigest()
        self.save_json(folder / 'run_manifest.json', manifest)

    def assert_fresh_report(self, folder: Path, exit_code: int) -> dict:
        """Run the actual preferred CLI and require matching fresh JSON/text status without traceback."""
        process = subprocess.run([sys.executable, '-m', 'tools.verify', str(folder)],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, exit_code, process.stdout + process.stderr)
        self.assertNotIn('Traceback', process.stderr)
        report = json.loads((folder / 'verification.json').read_bytes())
        status = {0: 'PASS', 1: 'FAIL', 2: 'INCOMPLETE'}[exit_code]
        self.assertEqual((report['status'], report['exit_code']), (status, exit_code))
        self.assertIn(f'{status} (exit {exit_code})', (folder / 'verification.txt').read_text())
        self.assertNotIn('OLD REPORT', (folder / 'verification.txt').read_text())
        return report

    # Check the expected model or evidence behavior

    def test_provenance_shapes_replace_any_previous_status_and_repeat(self) -> None:
        """Scenario/manifest list, string or null provenance yields stable INCOMPLETE from every prior status."""
        for where in ('scenario', 'manifest', 'both'):
            for value in ([], 'broken', None):
                for previous in (None, 'PASS', 'FAIL', 'INCOMPLETE'):
                    with self.subTest(where=where, value=value, previous=previous):
                        folder = self.fixture()
                        if previous is None:
                            for name in ('verification.json', 'verification.txt'):
                                (folder / name).unlink(missing_ok=True)
                        else:
                            self.save_json(folder / 'verification.json', {'status': previous, 'exit_code': {'PASS': 0, 'FAIL': 1, 'INCOMPLETE': 2}[previous]})
                            (folder / 'verification.txt').write_text('OLD REPORT ' + previous)
                        self.corrupt_provenance(folder, value, where)
                        report = self.assert_fresh_report(folder, 2)
                        self.assertTrue(any('provenance' in item['rule'] for item in report['findings']))
                        first = (folder / 'verification.json').read_bytes(), (folder / 'verification.txt').read_bytes()
                        self.assertEqual(verify_run(folder)['exit_code'], 2)
                        self.assertEqual(first, ((folder / 'verification.json').read_bytes(), (folder / 'verification.txt').read_bytes()))

    def test_seeded_format_two_malformed_provenance_reaches_safe_boundary(self) -> None:
        """The original AttributeError reproduction must be INCOMPLETE even with old PASS evidence present."""
        folder = self.root / 'generated'
        run_once(ROOT / 'tests/inputs/checkpoint_5.json', folder, seed=42)
        self.assertEqual(verify_run(folder)['status'], 'PASS')
        self.corrupt_provenance(folder, [])
        self.assert_fresh_report(folder, 2)
        for name in ('checkpoint_4c', 'checkpoint_3a'):
            copied = self.fixture(name)
            self.corrupt_provenance(copied, [])
            self.assert_fresh_report(copied, 2)

    def test_nested_stream_mapping_shapes_are_not_treated_as_valid_provenance(self) -> None:
        """Declared stream maps must remain objects, including inner per-site stream groups."""
        for network in (False, True):
            for number, value in enumerate(([], 'broken', None, {})):
                with self.subTest(network=network, value=value):
                    folder = self.root / f'streams-{network}-{number}'
                    run_once(ROOT / ('configs/three_site_pilot.json' if network else 'tests/inputs/checkpoint_5.json'), folder, seed=42)
                    scenario = json.loads((folder / 'scenario.json').read_bytes())
                    provenance = scenario['seed_provenance']
                    if network:
                        provenance['stream_seeds']['workstation_schedules']['RF1'] = value
                    else:
                        provenance['stream_seeds'] = value
                    self.corrupt_provenance(folder, provenance)
                    report = self.assert_fresh_report(folder, 2)
                    self.assertTrue(any('stream_seeds' in str(item) for item in report['findings']))

    def test_malformed_dispatch_objects_are_incomplete_and_replayed_totals_still_available(self) -> None:
        """Reject malformed policy/candidate/proposal/context containers without losing usable physics."""
        for location in ('policy_input', 'requests', 'candidate', 'proposal', 'candidate_missing', 'proposal_missing', 'forecast_context', 'vehicle'):
            with self.subTest(location=location):
                folder = self.fixture('checkpoint_4c')
                path = folder / 'dispatch_decisions.jsonl'
                records = [json.loads(line) for line in path.read_text().splitlines()]
                record = records[0]
                if location == 'policy_input': record['policy_input'] = []
                elif location == 'requests': record['requests'] = None
                elif location == 'candidate': record['requests'][0]['candidates'][0] = []
                elif location == 'proposal': record['proposals'][0] = 'broken'
                elif location == 'candidate_missing': del record['requests'][0]['candidates'][0]['candidate_id']
                elif location == 'proposal_missing': del record['proposals'][0]['candidate_id']
                elif location == 'forecast_context': record['policy_input']['forecast_context']['state'] = []
                else: record['policy_input']['vehicles'][0] = None
                path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
                report = self.assert_fresh_report(folder, 2)
                self.assertTrue(report['replay_complete'])
                self.assertIsNotNone(report['recalculated_summary'])

    def test_malformed_event_and_nested_final_state_containers(self) -> None:
        """Lists where products/jobs are objects must produce classified reports without AttributeError."""
        for artifact in ('events.jsonl', 'final_state.json', 'summary.json'):
            folder = self.fixture('checkpoint_4c')
            path = folder / artifact
            if artifact == 'events.jsonl':
                records = [json.loads(line) for line in path.read_text().splitlines()]
                next(record for record in records if record['event_type'] == 'return_release')['product'] = []
                path.write_text(''.join(json.dumps(record) + '\n' for record in records))
            else:
                record = json.loads(path.read_bytes())
                if artifact == 'final_state.json': record['products']['P1'] = []
                else: record['generated'] = []
                self.save_json(path, record)
            self.assert_fresh_report(folder, 2)

    def test_proved_violation_precedes_malformed_dispatch_evidence(self) -> None:
        """Independent replay still proves a wrong terminal count while dispatch is unusable."""
        folder = self.fixture('checkpoint_4c')
        path = folder / 'dispatch_decisions.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        del records[0]['requests'][0]['candidates'][0]['candidate_id']
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        summary = json.loads((folder / 'summary.json').read_bytes())
        summary['remanufactured'] += 1
        self.save_json(folder / 'summary.json', summary)
        report = self.assert_fresh_report(folder, 1)
        self.assertEqual({item['category'] for item in report['findings']}, {'incomplete', 'violation'})

    def test_proved_violation_precedes_malformed_provenance(self) -> None:
        """A hash violation remains proved even when both provenance declarations are malformed."""
        folder = self.fixture()
        self.corrupt_provenance(folder, [])
        with (folder / 'scenario.json').open('ab') as stream:
            stream.write(b'\n')
        report = self.assert_fresh_report(folder, 1)
        self.assertEqual({item['category'] for item in report['findings']}, {'incomplete', 'violation'})
        self.assertTrue(any(item['rule'] == 'manifest.scenario_sha256' for item in report['findings']))

    def test_report_write_io_errors_remain_explicit(self) -> None:
        """A write failure propagates to the caller; it must never claim that reports were refreshed."""
        folder = self.fixture()
        with patch('tools.verify.write_reports', side_effect=OSError('read-only report destination')):
            with self.assertRaisesRegex(OSError, 'read-only report'):
                verify_run(folder)

    def test_cli_write_failure_is_nonzero_and_does_not_claim_fresh_reports(self) -> None:
        """Exercise the CLI I/O boundary and retain prior report bytes when writing cannot start."""
        folder = self.fixture()
        self.assertEqual(verify_run(folder)['status'], 'PASS')
        previous = (folder / 'verification.json').read_bytes()
        output = io.StringIO()
        with patch.object(sys, 'argv', ['verify', str(folder)]), patch('sys.stdout', output), patch(
                'tools.verify.write_reports', side_effect=OSError('read-only report destination')):
            self.assertEqual(main(), 2)
        self.assertIn('unable to read evidence or write reports', output.getvalue())
        self.assertNotIn('PASS (exit 0)', output.getvalue())
        self.assertEqual((folder / 'verification.json').read_bytes(), previous)

    def test_raw_hash_violation_survives_unusable_scenario_declarations(self) -> None:
        """Unusable workstation structure must not hide an independently proved raw-byte hash mismatch."""
        folder = self.fixture('checkpoint_4c')
        scenario = json.loads((folder / 'scenario.json').read_bytes())
        scenario['plants'][0]['workstations'] = None
        self.save_json(folder / 'scenario.json', scenario)  # Intentionally leave hash stale.
        report = self.assert_fresh_report(folder, 1)
        self.assertEqual({item['category'] for item in report['findings']}, {'incomplete', 'violation'})
        self.assertTrue(any(item['rule'] == 'manifest.scenario_sha256' for item in report['findings']))

    def test_network_product_identity_shapes_do_not_escape_generation_boundary(self) -> None:
        """Catch malformed batch IDs before network generation calls string methods on them."""
        folder = self.root / 'network-id'
        run_once(ROOT / 'configs/three_site_pilot.json', folder, seed=42)
        scenario = json.loads((folder / 'scenario.json').read_bytes())
        scenario['products'][0]['batch_id'] = []
        self.save_json(folder / 'scenario.json', scenario)
        manifest = json.loads((folder / 'run_manifest.json').read_bytes())
        manifest['scenario_sha256'] = hashlib.sha256((folder / 'scenario.json').read_bytes()).hexdigest()
        self.save_json(folder / 'run_manifest.json', manifest)
        self.assert_fresh_report(folder, 2)

    def test_experiment_refresh_excludes_malformed_run_and_kpis_read_fresh_status(self) -> None:
        """Refresh keeps attempts, marks one run INCOMPLETE, excludes its pair and exports the fresh label."""
        root = self.root / 'experiment'
        manifest = experiments.run_experiments([ROOT / 'tests/inputs/checkpoint_5.json'], 1, 2026, root,
                                             policies=['static_quality_unaware', 'static_quality_aware'])
        folder = root / manifest['runs'][0]['run_directory']
        dispatch_path = folder / 'dispatch_decisions.jsonl'
        records = [json.loads(line) for line in dispatch_path.read_text().splitlines()]
        records[0]['policy_input'] = []
        dispatch_path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        refreshed = experiments.refresh_verification(root)
        self.assertEqual(len(refreshed['runs']), 2)
        failed = refreshed['runs'][0]
        self.assertEqual(failed['simulation']['status'], 'COMPLETED')
        self.assertEqual(failed['verification']['status'], 'INCOMPLETE')
        self.assertEqual(refreshed['comparisons'][0]['missing_policies'], ['static_quality_unaware'])
        self.assertTrue(all(not item['included_in_verified_comparison'] for item in refreshed['runs']))
        # KPI export does not audit dispatch. It must report the newly written
        # INCOMPLETE label while leaving the malformed evidence untouched.
        csv_path = self.root / 'kpis.csv'
        self.assertEqual(export_kpis(folder, csv_path), (1, 0))
        with csv_path.open(encoding='utf-8-sig') as stream:
            self.assertEqual(next(csv.DictReader(stream))['Verification'], 'INCOMPLETE')


if __name__ == '__main__':
    unittest.main()
