"""Compare fixed-trip policies and independently check their processing predictions.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
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
from simulation.forecasting import build_forecast_context
from simulation.routing import choose_quality_aware_products
from simulation.state import ScenarioDefinitions, build_inventory_state
from simulation.runner import run_once

Record = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'fixtures' / 'checkpoint_4a' / 'scenario.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_4a'


class StaticQualityTests(unittest.TestCase):
    """Keep every policy comparison and mutation in a private temporary run directory."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate automatically cleaned scratch space and fresh deterministic inputs."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenario = json.loads(CONFIG.read_text())
        self.case_number = 0

    def run_policy(self, scenario: Record | None = None, policy: str = 'static_quality_aware') -> tuple[Path, list[Record], Record]:
        """Execute a selected policy on unchanged source bytes; return folder, decisions and final state."""
        self.case_number += 1
        config = self.root / f'config-{self.case_number}.json'
        config.write_text(json.dumps(self.scenario if scenario is None else scenario))
        directory = self.root / f'run-{self.case_number}'
        run_once(config, directory, policy=policy)
        decisions = [json.loads(line) for line in (directory / 'dispatch_decisions.jsonl').read_text().splitlines()]
        return directory, decisions, json.loads((directory / 'final_state.json').read_text())

    def save_decisions(self, directory: Path, decisions: list[Record]) -> None:
        """Replace only a temporary copied dispatch trace with explicit UTF-8/LF JSONL."""
        with (directory / 'dispatch_decisions.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(decision) for decision in decisions) + '\n')

    # Check the expected model or evidence behavior

    def test_requested_paired_example_and_identical_schedule(self) -> None:
        """Check P1/5 versus P2/0 with the same saved scenario hash, departure/stops and distance two."""
        baseline, unaware, first = self.run_policy(policy='static_quality_unaware')
        aware_run, aware, second = self.run_policy()
        self.assertEqual(unaware[0]['decision']['assigned_products'], ['P1'])
        self.assertEqual(aware[0]['decision']['assigned_products'], ['P2'])
        self.assertEqual((first['summary']['plant_inventory_wait'], second['summary']['plant_inventory_wait']), (5, 0))
        self.assertEqual((first['summary']['vehicle_distance'], second['summary']['vehicle_distance']), (2, 2))
        self.assertEqual((baseline / 'scenario.json').read_bytes(), (aware_run / 'scenario.json').read_bytes())
        manifests = [json.loads((directory / 'run_manifest.json').read_text()) for directory in (baseline, aware_run)]
        self.assertEqual(manifests[0]['scenario_sha256'], manifests[1]['scenario_sha256'])
        self.assertNotEqual(manifests[0]['policy'], manifests[1]['policy'])
        movements = []
        for directory in (baseline, aware_run):
            events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
            movements.append([(event['event_type'], event['time'], event['origin'], event['target'], event['distance'])
                              for event in events if event['event_type'] in ('leg_departure', 'leg_arrival')])
            verify_run_via_cli(self, directory)
        self.assertEqual(movements[0], movements[1])
        self.assertEqual([entry[1] for entry in movements[0]], [1, 2, 2, 3])
        predictions = {candidate['product_id']: candidate for candidate in aware[0]['candidates']}
        self.assertEqual((predictions['P1']['predicted_admission'], predictions['P1']['predicted_first_start'], predictions['P1']['score']),
                         (3, 8, [0, 5, 0, 'P1', 0, 1]))
        self.assertEqual((predictions['P2']['predicted_admission'], predictions['P2']['predicted_first_start'], predictions['P2']['score']),
                         (3, 3, [0, 0, 0, 'P2', 0, 1]))

    def test_forged_candidate_score_is_rejected(self) -> None:
        """Forge P1's score and identify its independently recomputed score and product ID."""
        directory, decisions, _ = self.run_policy()
        decisions[0]['candidates'][0]['score'][1] = -1
        self.save_decisions(directory, decisions)
        report = verify_run_via_cli(self, directory, 1)
        finding = next(finding for finding in report['findings'] if finding['rule'] == 'policy.candidate.score')
        self.assertEqual((finding['time'], finding['event_sequence'], finding['ids']['product_id']), (1, 3, 'P1'))
        self.assertEqual(finding['expected'], [0, 5, 0, 'P1', 0, 1])
        self.assertEqual(finding['observed'], [0, -1, 0, 'P1', 0, 1])

    def test_forged_selection_and_missing_candidate_are_rejected(self) -> None:
        """Forge the chosen product or hide an eligible alternative and require a selection/coverage finding."""
        for selection in (True, False):
            directory, decisions, _ = self.run_policy()
            if selection:
                decisions[0]['decision']['assigned_products'] = ['P1']
            else:
                decisions[0]['candidates'].pop(0)
            self.save_decisions(directory, decisions)
            report = verify_run_via_cli(self, directory, 1)
            rule = 'policy.selection' if selection else 'policy.candidate_coverage'
            self.assertTrue(any(finding['rule'] == rule for finding in report['findings']))

    def test_committed_deliveries_holds_and_running_work_affect_forecasts(self) -> None:
        """Project a committed P0 through intermediate processing/hold/final release before admitting either candidate."""
        for departure in (1, 3):
            scenario = copy.deepcopy(self.scenario)
            scenario['horizon'] = 12
            scenario['plant']['inventory_capacity'] = 1
            scenario['plant']['workstations'] = [
                {'id': 'WS1', 'operation_id': 1, 'duration': 2, 'windows': [[2, 4], [8, 12]]},
                {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[6, 12]]}]
            scenario['products'][0]['release_time'] = 0.2
            scenario['products'].insert(0, {'id': 'P0', 'origin': 'CP1', 'release_time': 0,
                'quality_class': 'Q12', 'disposition': 'remanufacture', 'operations': [1, 2]})
            scenario['vehicles'].insert(0, {**scenario['vehicles'][0], 'id': 'AV0'})
            scenario['trips'][0]['departure_time'] = departure
            scenario['trips'].insert(0, {'vehicle_id': 'AV0', 'departure_time': 0, 'stops': ['CP1', 'RF1']})
            directory, decisions, final = self.run_policy(scenario)
            main = decisions[1]
            predictions = {candidate['product_id']: candidate for candidate in main['candidates']}
            self.assertEqual((predictions['P1']['predicted_admission'], predictions['P1']['predicted_first_start']), (6, 8))
            self.assertEqual((predictions['P2']['predicted_admission'], predictions['P2']['predicted_first_start']), (6, 7))
            self.assertEqual(main['decision']['assigned_products'], ['P2'])
            self.assertEqual(final['products']['P2']['milestones']['admission'], 6)
            self.assertEqual(final['products']['P2']['history'][0]['start_time'], 7)
            self.assertEqual([product['id'] for product in decisions[0]['policy_input']['forecast_context']['scenario']['products']], ['P0'])
            verify_run_via_cli(self, directory)

    def test_forecasting_does_not_change_live_state_or_context(self) -> None:
        """Call the aware selector on a released-only snapshot and preserve both the live state and its input."""
        state = build_inventory_state(self.scenario, ScenarioDefinitions.from_scenario(self.scenario))
        state.time = 1
        for product in state.products.values():
            product.update(state='at_collection_point', location='CP1')
        before = asdict(state)
        boundary = {'vehicle_id': 'AV1', 'stops': ['CP1', 'RF1'], 'requests': [
            {'product_id': product['id'], 'collection_point': 'CP1', 'release_time': product['release_time'],
             'disposition': product['disposition'], 'operations': product['operations']} for product in self.scenario['products']],
            'forecast_context': build_forecast_context(self.scenario, state)}
        original = copy.deepcopy(boundary)
        selected, _, _ = choose_quality_aware_products(boundary)
        self.assertEqual(selected, ['P2'])
        self.assertEqual(asdict(state), before)
        self.assertEqual(boundary, original)

    def test_future_returns_are_outside_the_policy_boundary(self) -> None:
        """Keep an unreleased fast product out of requests, projected products and candidate coverage."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'].append({**scenario['products'][1], 'id': 'P3', 'release_time': 1.5})
        directory, decisions, _ = self.run_policy(scenario)
        decision = decisions[0]
        for records in (decision['policy_input']['requests'], decision['policy_input']['forecast_context']['scenario']['products']):
            self.assertNotIn('P3', [record.get('id', record.get('product_id')) for record in records])
        self.assertNotIn('P3', decision['policy_input']['forecast_context']['state']['products'])
        self.assertEqual([candidate['product_id'] for candidate in decision['candidates']], ['P1', 'P2'])
        verify_run_via_cli(self, directory)

    def test_horizon_fallback_and_stable_product_id_ties(self) -> None:
        """Prefer a horizon-feasible start, then use ID rather than release time for tied/fallback aware candidates."""
        for mode in ('one_fallback', 'all_fallback', 'tied'):
            scenario = copy.deepcopy(self.scenario)
            scenario['products'][0]['id'] = 'Z'
            scenario['products'][1]['id'] = 'A'
            if mode == 'one_fallback':
                scenario['horizon'] = 5
            elif mode == 'all_fallback':
                scenario['plant']['workstations'][1]['windows'] = [[8, 10]]
                scenario['horizon'] = 5
            else:
                scenario['plant']['workstations'][0]['windows'] = [[3, 10]]
            directory, decisions, _ = self.run_policy(scenario)
            self.assertEqual(decisions[0]['decision']['assigned_products'], ['A'])
            if mode == 'all_fallback':
                self.assertTrue(all(candidate['score'][0] == 1 and candidate['predicted_first_start'] is None for candidate in decisions[0]['candidates']))
            verify_run_via_cli(self, directory)

    def test_payload_rounds_include_previously_selected_products(self) -> None:
        """Fill a two-item payload greedily and audit all conditional candidate rounds."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['payload_capacity'] = 2
        directory, decisions, final = self.run_policy(scenario)
        self.assertEqual(decisions[0]['decision']['assigned_products'], ['P2', 'P1'])
        self.assertEqual([candidate['selection_round'] for candidate in decisions[0]['candidates']], [0, 0, 1])
        self.assertEqual(final['summary']['remanufactured'], 2)
        verify_run_via_cli(self, directory)

    def test_unaware_boundary_does_not_receive_forecast_context(self) -> None:
        """Keep operations and projection state outside the baseline's policy input."""
        directory, decisions, _ = self.run_policy(policy='static_quality_unaware')
        self.assertNotIn('forecast_context', decisions[0]['policy_input'])
        self.assertTrue(all('operations' not in request for request in decisions[0]['policy_input']['requests']))
        verify_run_via_cli(self, directory)

    def test_range_rejection_and_empty_trip_remain_static(self) -> None:
        """Keep range rejection/no-pickup visits outside candidate allocation under the aware policy."""
        for no_pickup in (True, False):
            scenario = copy.deepcopy(self.scenario)
            if no_pickup:
                scenario['trips'][0]['stops'] = ['RF1']
            else:
                scenario['vehicles'][0]['maximum_range'] = 1
            directory, decisions, _ = self.run_policy(scenario)
            self.assertEqual(decisions[0]['decision']['assigned_products'], [])
            self.assertEqual(decisions[0]['candidates'], [])
            verify_run_via_cli(self, directory)

    def test_seeded_recycling_and_resolved_policy_replay(self) -> None:
        """Run the aware policy on seeded recycling inputs and replay unchanged scenario bytes with the same policy."""
        directory, replay = self.root / 'seeded', self.root / 'replay'
        run_once(ROOT / 'tests' / 'inputs' / 'checkpoint_3b_recycling.json', directory, seed=42, policy='static_quality_aware')
        run_once(directory / 'scenario.json', replay, policy='static_quality_aware')
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((directory / filename).read_bytes(), (replay / filename).read_bytes())
        verify_run_via_cli(self, directory)
        verify_run_via_cli(self, replay)

    def test_cli_policy_override_preserves_input_hash(self) -> None:
        """Exercise both CLI policies against the same resolved file and reject unsupported legacy aware runs."""
        for policy in ('static_quality_unaware', 'static_quality_aware'):
            directory = self.root / policy
            result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario', str(CONFIG),
                '--policy', policy, '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((directory / 'scenario.json').read_bytes(), CONFIG.read_bytes())
            verify_run_via_cli(self, directory)
        with self.assertRaises(ValueError):
            run_once(ROOT / 'tests' / 'fixtures' / 'checkpoint_1' / 'scenario.json', self.root / 'unsupported', policy='static_quality_aware')

    def test_frozen_aware_fixture_lf_hash_and_isolated_verifier(self) -> None:
        """Verify frozen aware evidence with an isolated standalone checker while preserving exact source bytes."""
        directory = self.root / 'fixture'
        shutil.copytree(FIXTURE, directory)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.assertNotIn(b'\r', before['scenario.json'])
        self.assertEqual(hashlib.sha256(before['scenario.json']).hexdigest(), json.loads(before['run_manifest.json'])['scenario_sha256'])
        script = self.root / 'verify_simulation.py'
        shutil.copyfile(ROOT / 'tools' / 'verify.py', script)
        result = subprocess.run([sys.executable, '-I', str(script), str(directory)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for filename, contents in before.items():
            self.assertEqual((directory / filename).read_bytes(), contents)

    def test_missing_manifest_or_candidate_evidence_is_incomplete(self) -> None:
        """Avoid guessing the execution policy when its manifest is missing, and require candidate evidence."""
        directory, decisions, _ = self.run_policy()
        path = directory / 'run_manifest.json'
        saved = path.read_bytes()
        path.unlink()
        verify_run_via_cli(self, directory, 2)
        path.write_bytes(saved)
        del decisions[0]['candidates']
        self.save_decisions(directory, decisions)
        verify_run_via_cli(self, directory, 2)

    def test_blocked_admission_uses_no_start_fallback(self) -> None:
        """Keep candidates onboard in a full held-slot projection rather than predicting an idle machine start."""
        scenario = copy.deepcopy(self.scenario)
        scenario['plant']['inventory_capacity'] = 1
        scenario['plant']['workstations'] = [
            {'id': 'WS1', 'operation_id': 1, 'duration': 20, 'windows': [[2, 22]]},
            {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[3, 10]]}]
        scenario['products'][0]['release_time'] = 0.2
        scenario['products'].insert(0, {'id': 'P0', 'origin': 'CP1', 'release_time': 0,
            'quality_class': 'Q12', 'disposition': 'remanufacture', 'operations': [1, 2]})
        scenario['vehicles'].insert(0, {**scenario['vehicles'][0], 'id': 'AV0'})
        scenario['trips'].insert(0, {'vehicle_id': 'AV0', 'departure_time': 0, 'stops': ['CP1', 'RF1']})
        directory, decisions, final = self.run_policy(scenario)
        self.assertEqual(decisions[1]['decision']['assigned_products'], ['P1'])
        for candidate in decisions[1]['candidates']:
            self.assertIsNone(candidate['predicted_admission'])
            self.assertIsNone(candidate['predicted_first_start'])
            self.assertEqual(candidate['score'][0], 1)
        self.assertEqual(final['vehicles']['AV1']['load'], ['P1'])
        self.assertEqual(final['held_slots'], ['P0'])
        verify_run_via_cli(self, directory)
