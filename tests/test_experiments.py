"""Check shared replication inputs, retained attempts and verified comparison exports.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
The reported-exit injection changes evidence after a successful child; it
does not demonstrate complete artifacts from an actual mid-engine crash.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import experiments
from simulation.generator import prepare_scenario

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'inputs' / 'checkpoint_5.json'


class ExperimentTests(unittest.TestCase):
    """Exercise real child CLIs and confine corruption/fault injection to temporary batches."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate cleaned scratch directories and a unique output counter for each test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.number = 0

    def run_batch(self, replications: int = 1, extra: list[str] | None = None) -> tuple[Path, experiments.Record]:
        """Run the actual batch CLI with seed 2026 and return its fresh root and manifest."""
        self.number += 1
        root = self.root / f'batch-{self.number}'
        command = [sys.executable, str(ROOT / 'run_experiments.py'), '--config', str(CONFIG),
                   '--replications', str(replications), '--seed', '2026', '--output', str(root), *(extra or [])]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root, json.loads((root / 'experiment_manifest.json').read_text())

    def refresh(self, root: Path, exit_code: int) -> experiments.Record:
        """Reverify edited evidence through the CLI and assert its batch exit code."""
        result = subprocess.run([sys.executable, str(ROOT / 'run_experiments.py'), '--output', str(root),
                                 '--refresh-verification'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        return json.loads((root / 'experiment_manifest.json').read_text())

    def read_rows(self, root: Path, filename: str) -> list[dict[str, str]]:
        """Read one temporary CSV export as keyed rows without changing files."""
        with (root / filename).open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))

    # Check the expected model or evidence behavior

    def test_stable_seed_derivation(self) -> None:
        """Pin the documented SHA-256 seed protocol and reject ambiguous numeric inputs."""
        expected = 108319335747130872897614666000385150515536794447511437542351997290053699714474
        self.assertEqual(experiments.derive_replication_seed(2026, 1), expected)
        self.assertNotEqual(expected, experiments.derive_replication_seed(2026, 2))
        self.assertNotEqual(expected, experiments.derive_replication_seed(2027, 1))
        for master, replication in [(True, 1), (1, 0), (1, 1.0)]:
            with self.assertRaises(ValueError):
                experiments.derive_replication_seed(master, replication)

    def test_two_replications_four_policies_and_csv_summaries(self) -> None:
        """Check eight verified runs, raw byte pairing, all result rows and paired summary means."""
        root, manifest = self.run_batch(2)
        self.assertEqual(len(manifest['runs']), 8)
        self.assertTrue(all(comparison['complete'] for comparison in manifest['comparisons']))
        for replication in (1, 2):
            records = [record for record in manifest['runs'] if record['replication_id'] == replication]
            frozen = (root / records[0]['resolved_scenario']).read_bytes()
            self.assertEqual(len({record['scenario_sha256'] for record in records}), 1)
            self.assertEqual(hashlib.sha256(frozen).hexdigest(), records[0]['scenario_sha256'])
            for record in records:
                self.assertEqual((root / record['run_directory'] / 'scenario.json').read_bytes(), frozen)
                self.assertEqual(record['verification']['status'], 'PASS')
                self.assertEqual(record['master_seed'], 2026)
                self.assertTrue(record['included_in_verified_comparison'])
                self.assertEqual(record['summary']['generated'], 2)
        rows = self.read_rows(root, 'results.csv')
        self.assertEqual(len(rows), 8)
        self.assertEqual(len(self.read_rows(root, 'paired_results.csv')), 8)
        summaries = self.read_rows(root, 'summaries.csv')
        self.assertEqual(len(summaries), 4)
        for summary in summaries:
            self.assertEqual(summary['matched_replications'], '2')
            selected = [record for record in manifest['runs'] if record['policy'] == summary['policy']]
            self.assertAlmostEqual(float(summary['vehicle_distance_mean']), sum(record['summary']['vehicle_distance'] for record in selected) / 2)

    def test_repeat_and_reversed_policy_order(self) -> None:
        """Repeat two-replication batches and reverse policy order without changing inputs or physical outputs."""
        first_root, first = self.run_batch(2)
        for extra in (None, ['--policies', *reversed(experiments.POLICIES)]):
            root, manifest = self.run_batch(2, extra)
            by_key = {(record['replication_id'], record['policy']): record for record in manifest['runs']}
            for before in first['runs']:
                after = by_key[before['replication_id'], before['policy']]
                self.assertEqual(before['scenario_sha256'], after['scenario_sha256'])
                self.assertEqual(before['summary'], after['summary'])
                for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'final_state.json', 'summary.json'):
                    self.assertEqual((first_root / before['run_directory'] / filename).read_bytes(),
                                     (root / after['run_directory'] / filename).read_bytes())

    def test_multiple_conditions_record_generation_difference(self) -> None:
        """Save same-named condition copies separately and record recycling filtering with matched arrival batches."""
        config = json.loads(CONFIG.read_text())
        config['recycling_facilities'] = []
        config['trips'] = config['trips'][:1]
        disabled = self.root / 'checkpoint_5.json'
        disabled.write_text(json.dumps(config))
        root, manifest = self.run_batch(extra=['--config', str(disabled)])
        self.assertEqual(len(manifest['runs']), 8)
        self.assertEqual([condition['condition_id'] for condition in manifest['conditions']], ['C001', 'C002'])
        first = json.loads((root / manifest['runs'][0]['resolved_scenario']).read_text())
        second = json.loads((root / manifest['runs'][4]['resolved_scenario']).read_text())
        self.assertEqual(first['arrival_batches'], second['arrival_batches'])
        self.assertEqual(second['recycling_facilities'], [])
        self.assertTrue(all(product['disposition'] == 'remanufacture' for product in second['products']))
        self.assertEqual(manifest['conditions'][1]['recycling_facilities'], [])

    def test_policy_subset_has_selected_members_only(self) -> None:
        """Compare only the explicitly selected policies and retain replication keys in CSV."""
        _, manifest = self.run_batch(extra=['--policies', 'dynamic_quality_aware', 'static_quality_unaware'])
        self.assertEqual(len(manifest['runs']), 2)
        self.assertEqual(manifest['policies'], ['dynamic_quality_aware', 'static_quality_unaware'])
        self.assertEqual(manifest['comparisons'][0]['missing_policies'], [])

    def test_missing_file_refresh_excludes_entire_affected_comparison(self) -> None:
        """Remove one events log, keep eight raw rows, and exclude all four members of just that replication."""
        root, manifest = self.run_batch(2)
        record = manifest['runs'][3]
        (root / record['run_directory'] / 'events.jsonl').unlink()
        refreshed = self.refresh(root, 1)
        self.assertEqual(refreshed['runs'][3]['verification']['status'], 'INCOMPLETE')
        self.assertEqual(refreshed['comparisons'][0]['missing_policies'], ['dynamic_quality_aware'])
        self.assertFalse(refreshed['comparisons'][0]['complete'])
        self.assertTrue(refreshed['comparisons'][0]['matching_scenario_hashes'])
        self.assertTrue(refreshed['comparisons'][1]['complete'])
        self.assertEqual(len(self.read_rows(root, 'results.csv')), 8)
        self.assertEqual(len(self.read_rows(root, 'paired_results.csv')), 4)
        self.assertTrue(all(row['replication_id'] == '2' for row in self.read_rows(root, 'paired_results.csv')))
        self.assertTrue(all(row['matched_replications'] == '1' for row in self.read_rows(root, 'summaries.csv')))
        self.assertTrue((root / record['run_directory'] / 'final_state.json').exists())
        self.assertIn('events.jsonl', json.dumps(refreshed['runs'][3]['verification']['findings']))

    def test_forged_totals_refresh_is_failed_not_incomplete(self) -> None:
        """Forge a saved metric, retain verifier findings, and prevent invalid totals entering paired means."""
        root, manifest = self.run_batch()
        directory = root / manifest['runs'][0]['run_directory']
        summary = json.loads((directory / 'summary.json').read_text())
        summary['vehicle_distance'] += 1
        (directory / 'summary.json').write_text(json.dumps(summary))
        refreshed = self.refresh(root, 1)
        self.assertEqual(refreshed['runs'][0]['verification']['status'], 'FAIL')
        self.assertEqual(refreshed['comparisons'][0]['missing_policies'], ['static_quality_unaware'])
        self.assertEqual(len(self.read_rows(root, 'paired_results.csv')), 0)
        self.assertTrue(all(row['vehicle_distance_mean'] == '' for row in self.read_rows(root, 'summaries.csv')))

    def test_self_consistent_wrong_scenario_hash_still_excluded(self) -> None:
        """A verifier-PASS run with different source bytes cannot join the frozen-input comparison."""
        root, manifest = self.run_batch()
        directory = root / manifest['runs'][0]['run_directory']
        path = directory / 'scenario.json'
        path.write_bytes(path.read_bytes() + b'\n')
        run_manifest = json.loads((directory / 'run_manifest.json').read_text())
        run_manifest['scenario_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (directory / 'run_manifest.json').write_text(json.dumps(run_manifest))
        refreshed = self.refresh(root, 1)
        self.assertEqual(refreshed['runs'][0]['verification']['status'], 'PASS')
        self.assertFalse(refreshed['runs'][0]['scenario_hash_matches'])
        self.assertEqual(refreshed['comparisons'][0]['missing_policies'], ['static_quality_unaware'])

    def test_simulation_crash_retains_artifacts_and_continues(self) -> None:
        """Override a successful child's reported exit; retain its evidence and continue policies.

        This tests attempt/exclusion handling, not mid-engine failure-artifact retention.
        """
        original = experiments.execute_command
        def fail_one(command: list[str], log_prefix: Path) -> experiments.Record:
            """Run the real child, then report exit seven for static-aware without deleting its evidence."""
            process = original(command, log_prefix)
            if '--policy' in command and command[command.index('--policy') + 1] == 'static_quality_aware':
                process['exit_code'] = 7
            return process
        root = self.root / 'crash'
        with patch.object(experiments, 'execute_command', side_effect=fail_one):
            manifest = experiments.run_experiments([CONFIG], 1, 2026, root)
        self.assertEqual([record['simulation']['status'] for record in manifest['runs']], ['COMPLETED', 'FAILED', 'COMPLETED', 'COMPLETED'])
        failed = manifest['runs'][1]
        self.assertEqual(failed['verification']['status'], 'NOT_RUN')
        self.assertTrue((root / failed['run_directory'] / 'events.jsonl').exists())
        self.assertTrue(Path(failed['simulation']['stderr_log']).exists())
        self.assertEqual(manifest['comparisons'][0]['missing_policies'], ['static_quality_aware'])
        refreshed = self.refresh(root, 1)
        self.assertEqual(refreshed['runs'][1]['verification']['status'], 'PASS')
        self.assertFalse(refreshed['runs'][1]['included_in_verified_comparison'])

    def test_verifier_crash_is_error_and_does_not_abort_batch(self) -> None:
        """Run a crashing verifier child; retain its stderr and mark all candidates unverified."""
        original = experiments.execute_command
        def crash_verifier(command: list[str], log_prefix: Path) -> experiments.Record:
            """Substitute a real exit-nine process only for verifier invocations."""
            if len(command) > 1 and command[1].endswith('verify.py'):
                return original([sys.executable, '-c', 'import sys; sys.exit(9)'], log_prefix)
            return original(command, log_prefix)
        with patch.object(experiments, 'execute_command', side_effect=crash_verifier):
            manifest = experiments.run_experiments([CONFIG], 1, 2026, self.root / 'verifier-crash')
        self.assertTrue(all(record['verification']['status'] == 'ERROR' for record in manifest['runs']))
        self.assertEqual(manifest['comparisons'][0]['missing_policies'], list(experiments.POLICIES))

    def test_no_verify_never_claims_verified_results_and_can_refresh(self) -> None:
        """Explicit verifier opt-out retains raw metrics but no paired data until actual verification."""
        root, manifest = self.run_batch(extra=['--no-verify'])
        self.assertTrue(all(record['verification']['status'] == 'NOT_RUN' for record in manifest['runs']))
        self.assertEqual(len(self.read_rows(root, 'paired_results.csv')), 0)
        refreshed = self.refresh(root, 0)
        self.assertTrue(refreshed['comparisons'][0]['complete'])
        self.assertEqual(len(self.read_rows(root, 'paired_results.csv')), 4)

    def test_generation_failure_has_rows_and_later_replication_runs(self) -> None:
        """Inject failure in first generation; record all four missing policies and still execute replication two."""
        root = self.root / 'generation-error'
        with patch.object(experiments, 'prepare_scenario', side_effect=[ValueError('injected generation failure'),
                prepare_scenario(CONFIG, experiments.derive_replication_seed(2026, 2))]) as prepare:
            manifest = experiments.run_experiments([CONFIG], 2, 2026, root)
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(len(manifest['runs']), 8)
        self.assertEqual(len(self.read_rows(root, 'results.csv')), 8)
        self.assertTrue(all(record['simulation']['status'] == 'NOT_RUN' for record in manifest['runs'][:4]))
        self.assertEqual(manifest['comparisons'][0]['missing_policies'], list(experiments.POLICIES))
        self.assertTrue(manifest['comparisons'][1]['complete'])

    def test_individual_manifest_command_replays_exact_scenario(self) -> None:
        """Rerun the recorded single-run argv with only a fresh output path and compare its events and totals."""
        root, manifest = self.run_batch()
        record = manifest['runs'][3]
        command = list(record['simulation']['command'])
        directory = self.root / 'individual-replay'
        command[command.index('--output') + 1] = str(directory)
        result = subprocess.run(command, cwd=record['simulation']['working_directory'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'final_state.json', 'summary.json'):
            self.assertEqual((directory / filename).read_bytes(), (root / record['run_directory'] / filename).read_bytes())

    def test_bad_requests_do_not_overwrite_or_create_batches(self) -> None:
        """Reject nonpositive counts, duplicate policies, resolved inputs, bad refresh flags and existing roots."""
        for count, policies in [(0, list(experiments.POLICIES)), (1, ['static_quality_aware'] * 2), (1, [])]:
            directory = self.root / 'invalid'
            with self.assertRaises(ValueError):
                experiments.run_experiments([CONFIG], count, 2026, directory, policies)
            self.assertFalse(directory.exists())
        with self.assertRaises((ValueError, KeyError)):
            experiments.run_experiments([ROOT / 'tests' / 'fixtures' / 'checkpoint_4c' / 'scenario.json'], 1, 2026, self.root / 'resolved')
        existing = self.root / 'exists'
        existing.mkdir()
        marker = existing / 'keep.txt'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            experiments.run_experiments([CONFIG], 1, 2026, existing)
        self.assertEqual(marker.read_text(), 'keep')
        result = subprocess.run([sys.executable, str(ROOT / 'run_experiments.py'), '--output', str(existing),
                                 '--refresh-verification', '--seed', '2026'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
