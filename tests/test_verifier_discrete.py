"""Check exact integer evidence while keeping tolerance for physical quantities.

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
import tempfile
import unittest

from simulation.runner import run_once
from tools.verify import check_value, verify_run

ROOT = Path(__file__).resolve().parents[1]


class DiscreteEvidenceTests(unittest.TestCase):
    """Alter only owned copies, retaining coherent hashes for provenance counterexamples."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate cleaned scratch space and a unique evidence-copy counter."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def fixture(self, name: str = 'checkpoint_3a') -> Path:
        """Return a private copied audited run without modifying source fixture bytes."""
        self.counter += 1
        folder = self.root / f'run-{self.counter}'
        shutil.copytree(ROOT / 'tests/fixtures' / name, folder)
        return folder

    def save_json(self, path: Path, value: object) -> None:
        """Replace one temporary JSON object with finite UTF-8/LF evidence."""
        path.write_text(json.dumps(value, allow_nan=False) + '\n', encoding='utf-8')

    def rehash_scenario(self, folder: Path, scenario: dict) -> None:
        """Save changed scenario and matching manifest provenance/hash to isolate semantic checks."""
        self.save_json(folder / 'scenario.json', scenario)
        manifest = json.loads((folder / 'run_manifest.json').read_bytes())
        manifest.update(seed_provenance=copy.deepcopy(scenario['seed_provenance']),
                        scenario_sha256=hashlib.sha256((folder / 'scenario.json').read_bytes()).hexdigest())
        self.save_json(folder / 'run_manifest.json', manifest)

    def change_event(self, folder: Path, kind: str, field: str, value: object) -> dict:
        """Replace one matching event field in copied JSONL; return the changed record for diagnostics."""
        path = folder / 'events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        event = next(event for event in events if event['event_type'] == kind)
        event[field] = value
        path.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        return event

    # Check the expected model or evidence behavior

    def test_neighboring_large_stream_seed_is_not_a_tolerant_physical_value(self) -> None:
        """Generate format two, change its stream integer by one and require a precise independent violation."""
        folder = self.root / 'generated'
        run_once(ROOT / 'tests/inputs/checkpoint_5.json', folder, seed=42)
        self.assertEqual(verify_run(folder)['status'], 'PASS')
        scenario = json.loads((folder / 'scenario.json').read_bytes())
        expected = scenario['seed_provenance']['stream_seeds']['arrivals_batches']
        scenario['seed_provenance']['stream_seeds']['arrivals_batches'] += 1
        self.rehash_scenario(folder, scenario)
        report = verify_run(folder)
        self.assertEqual((report['status'], report['exit_code']), ('FAIL', 1))
        finding = next(item for item in report['findings'] if item['rule'] == 'generation.seed_provenance')
        self.assertEqual((finding['expected'], finding['observed']), (expected, expected + 1))
        self.assertIn('stream_seeds.arrivals_batches', finding['ids']['field'])

    def test_fractional_sequences_fail_in_all_retained_policy_paths(self) -> None:
        """A fractional final sequence inside tolerance must be a proved violation with event context."""
        for name in ('checkpoint_3a', 'checkpoint_4a', 'checkpoint_4b', 'checkpoint_4c'):
            with self.subTest(format=name):
                folder = self.fixture(name)
                events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
                sequence = events[-1]['sequence']
                changed = self.change_event(folder, 'horizon', 'sequence', sequence + 1e-10)
                report = verify_run(folder)
                self.assertEqual(report['exit_code'], 1)
                finding = next(item for item in report['findings'] if item['rule'] == 'events.sequence')
                self.assertEqual((finding['expected'], finding['observed']), (sequence, sequence + 1e-10))
                self.assertEqual((finding['time'], finding['event_sequence']), (changed['time'], changed['sequence']))

    def test_integer_fields_reject_integral_floats_booleans_and_small_mismatches(self) -> None:
        """Sequence is a JSON integer, not a bool or an integer-valued float."""
        for value in (1.0, True, 2):
            with self.subTest(value=repr(value)):
                folder = self.fixture()
                self.change_event(folder, 'return_release', 'sequence', value)
                report = verify_run(folder)
                self.assertEqual(report['exit_code'], 1)
                finding = next(item for item in report['findings'] if item['rule'] == 'events.sequence')
                self.assertEqual(finding['expected'], 1)
                self.assertEqual(type(finding['observed']), type(value))

    def test_nested_counts_and_progress_indices_are_exact(self) -> None:
        """Export reconciliation must reject fractional counts and indices nested in product dictionaries."""
        for field in ('generated', 'next_operation'):
            with self.subTest(field=field):
                folder = self.fixture('checkpoint_4b')
                path = folder / ('summary.json' if field == 'generated' else 'final_state.json')
                saved = json.loads(path.read_bytes())
                target = saved if field == 'generated' else saved['products']['P1']
                original = target[field]
                target[field] += 1e-10
                self.save_json(path, saved)
                report = verify_run(folder)
                self.assertEqual(report['exit_code'], 1)
                finding = next(item for item in report['findings'] if item['ids'].get('field', '').endswith(field))
                self.assertEqual((finding['expected'], finding['observed']), (original, original + 1e-10))

    def test_operation_id_and_route_revision_use_discrete_semantics(self) -> None:
        """Numeric operation identity and route counters cannot be fractionally changed inside tolerance."""
        for kind, field in (('operation_start', 'operation_id'), ('route_revised', 'route_version')):
            with self.subTest(field=field):
                folder = self.fixture('checkpoint_4b')
                events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
                original = next(event[field] for event in events if event['event_type'] == kind)
                changed = self.change_event(folder, kind, field, original + 1e-10)
                report = verify_run(folder)
                self.assertEqual(report['exit_code'], 1)
                finding = next(item for item in report['findings'] if item['rule'] == f'{kind}.{field}')
                self.assertEqual(finding['event_sequence'], changed['sequence'])
                self.assertEqual((finding['expected'], finding['observed']), (original, original + 1e-10))

    def test_candidate_score_integer_positions_and_payload_are_exact(self) -> None:
        """Nested candidate tie indices, aware priority flags and observed capacity require exact integers."""
        for fixture, field in (('checkpoint_4a', 'score'), ('checkpoint_4b', 'score'),
                               ('checkpoint_4c', 'score'), ('checkpoint_4b', 'payload_capacity')):
            with self.subTest(fixture=fixture, field=field):
                folder = self.fixture(fixture)
                path = folder / 'dispatch_decisions.jsonl'
                records = [json.loads(line) for line in path.read_text().splitlines()]
                record = records[0]
                if field == 'payload_capacity':
                    record['policy_input']['vehicles'][0][field] += 1e-10
                else:
                    score = record['candidates'][0]['score'] if fixture == 'checkpoint_4a' else record['requests'][0]['candidates'][0]['score']
                    score[3 if fixture == 'checkpoint_4b' else 0] += 1e-10
                path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
                report = verify_run(folder)
                self.assertEqual(report['exit_code'], 1)
                self.assertTrue(any(field in item['ids'].get('field', '') for item in report['findings']))

    def test_format_versions_are_json_integers(self) -> None:
        """Bool/float versions must not slip through Python equality with supported integer versions."""
        for value in (True, 1.0):
            folder = self.fixture()
            path = folder / 'run_manifest.json'
            manifest = json.loads(path.read_bytes())
            manifest['input_format_version'] = value
            self.save_json(path, manifest)
            report = verify_run(folder)
            self.assertEqual((report['status'], report['exit_code']), ('INCOMPLETE', 2))
            self.assertTrue(any(item['rule'] == 'format.version' for item in report['findings']))

    def test_large_integer_master_seed_keeps_full_precision(self) -> None:
        """A valid 401-digit master seed must never pass through float during parsing or comparison."""
        folder = self.root / 'large-seed'
        master = 10 ** 400 + 123
        run_once(ROOT / 'tests/inputs/checkpoint_5.json', folder, seed=master)
        report = verify_run(folder)
        self.assertEqual((report['status'], report['exit_code']), ('PASS', 0))
        self.assertEqual(json.loads((folder / 'scenario.json').read_bytes())['seed_provenance']['master_seed'], master)

    def test_physical_distance_integer_literals_and_harmless_differences_still_pass(self) -> None:
        """A distance's schema stays continuous even when its JSON literal is an integer."""
        for offset in (0, 1e-10):
            folder = self.fixture()
            events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
            distance = int(next(event['distance'] for event in events if event['event_type'] == 'leg_departure'))
            self.change_event(folder, 'leg_departure', 'distance', distance + offset)
            self.assertEqual(verify_run(folder)['exit_code'], 0)

    def test_physical_difference_outside_tolerance_still_fails(self) -> None:
        """Keep the existing physical leg diagnostic and tolerance threshold."""
        folder = self.fixture()
        self.change_event(folder, 'leg_departure', 'distance', 2.01)
        report = verify_run(folder)
        self.assertEqual(report['exit_code'], 1)
        finding = next(item for item in report['findings'] if item['rule'] == 'leg_departure.distance')
        self.assertEqual((finding['expected'], finding['observed']), (2, 2.01))

    def test_identifier_dictionary_keys_do_not_turn_physical_times_into_counts(self) -> None:
        """Product/AV IDs named like discrete fields remain opaque keys in time/container maps."""
        expected = {'inventory': {'sequence': 1}, 'waiting_vehicles': {'generated': 2},
                    'products': {'operation_index': {'inventory_wait': 3, 'next_operation': 1}}}
        observed = copy.deepcopy(expected)
        observed['inventory']['sequence'] += 1e-10
        observed['waiting_vehicles']['generated'] += 1e-10
        observed['products']['operation_index']['inventory_wait'] += 1e-10
        findings: list[dict] = []
        self.assertTrue(check_value(findings, 'results.final_state.json', expected, observed))
        observed['products']['operation_index']['next_operation'] += 1e-10
        self.assertFalse(check_value(findings, 'results.final_state.json', expected, observed))


if __name__ == '__main__':
    unittest.main()
