"""Compare dynamic policies and check processing forecasts on separate state.

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
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from tests.verifier_cli import verify_run_via_cli
from simulation.runner import run_once
from simulation.state import ScenarioDefinitions
from simulation.config import as_dynamic_scenario
from simulation.engine import build_dynamic_state
from simulation.routing import build_dynamic_policy_input, choose_dynamic_insertions
from tools.verify import audit_dynamic_forecast

Record = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'fixtures' / 'checkpoint_4c' / 'scenario.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_4c'


class DynamicQualityTests(unittest.TestCase):
    """Keep paired runs and forged evidence in automatically cleaned temporary folders."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate private scratch space, scenario copy and unique case numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenario = json.loads(CONFIG.read_text())
        self.number = 0

    def run_policy(self, scenario: Record | None = None, policy: str = 'dynamic_quality_aware') -> tuple[Path, list[Record], Record]:
        """Run an execution policy without rewriting source inputs; return folder, decisions and final state."""
        self.number += 1
        path = self.root / f'config-{self.number}.json'
        path.write_text(json.dumps(self.scenario if scenario is None else scenario))
        directory = self.root / f'run-{self.number}'
        run_once(path, directory, policy=policy)
        decisions = [json.loads(line) for line in (directory / 'dispatch_decisions.jsonl').read_text().splitlines()]
        return directory, decisions, json.loads((directory / 'final_state.json').read_text())

    def save_decisions(self, directory: Path, decisions: list[Record]) -> None:
        """Rewrite only a temporary copied dispatch log with explicit UTF-8/LF."""
        with (directory / 'dispatch_decisions.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(decision) for decision in decisions) + '\n')

    # Check the expected model or evidence behavior

    def test_requested_two_plant_comparison_same_inputs(self) -> None:
        """Use identical inputs: unaware chooses RF1, travels 2, starts at 8 and waits 6.

        Aware chooses RF2, travels 4, starts at 4 and waits 0. Check saved input bytes
        and hashes too, so the policy comparison uses the same products and calendars.
        """
        first, baseline, unaware = self.run_policy(policy='dynamic_quality_unaware')
        second, decisions, aware = self.run_policy()
        self.assertEqual((first / 'scenario.json').read_bytes(), (second / 'scenario.json').read_bytes())
        self.assertEqual(json.loads((first / 'run_manifest.json').read_text())['scenario_sha256'],
                         json.loads((second / 'run_manifest.json').read_text())['scenario_sha256'])
        self.assertEqual(baseline[0]['proposals'][0]['destination'], 'RF1')
        self.assertEqual(decisions[0]['proposals'][0]['destination'], 'RF2')
        self.assertEqual((unaware['summary']['vehicle_distance'], unaware['summary']['plant_inventory_wait']), (2, 6))
        self.assertEqual((aware['summary']['vehicle_distance'], aware['summary']['plant_inventory_wait']), (4, 0))
        self.assertEqual((unaware['products']['P1']['milestones']['admission'], unaware['products']['P1']['history'][0]['start_time']), (2, 8))
        self.assertEqual((aware['products']['P1']['milestones']['admission'], aware['products']['P1']['history'][0]['start_time']), (4, 4))
        candidates = {candidate['destination']: candidate for candidate in decisions[0]['requests'][0]['candidates']}
        self.assertEqual((candidates['RF1']['predicted_admission'], candidates['RF1']['predicted_first_start'], candidates['RF1']['predicted_inventory_wait']), (2, 8, 6))
        self.assertEqual((candidates['RF2']['predicted_admission'], candidates['RF2']['predicted_first_start'], candidates['RF2']['predicted_inventory_wait']), (4, 4, 0))
        self.assertLess(candidates['RF2']['score'], candidates['RF1']['score'])
        verify_run_via_cli(self, first)
        verify_run_via_cli(self, second)

    def test_forged_predicted_start_is_rejected(self) -> None:
        """Forge the RF2 forecast start and require an event/product/destination-specific independent finding."""
        directory, decisions, _ = self.run_policy()
        candidate = next(candidate for candidate in decisions[0]['requests'][0]['candidates'] if candidate['destination'] == 'RF2')
        candidate['predicted_first_start'] = 9
        self.save_decisions(directory, decisions)
        report = verify_run_via_cli(self, directory, 1)
        finding = next(finding for finding in report['findings'] if finding['rule'] == 'dynamic.forecast.predicted_first_start')
        self.assertEqual((finding['time'], finding['event_sequence'], finding['expected'], finding['observed']), (0, 3, 4, 9))
        self.assertEqual(finding['ids']['destination'], 'RF2')

    def test_revised_existing_delivery_eta_changes_forecast(self) -> None:
        """Project P1 at the revised 8.650 arrival rather than its superseded ETA eight when forecasting P2's start."""
        scenario = json.loads((ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json').read_text())
        directory, decisions, final = self.run_policy(scenario)
        decision = next(decision for decision in decisions if decision['time'] == 5)
        self.assertEqual(decision['policy_input']['vehicles'][0]['active_leg']['arrival_time'], 8)
        candidate = decision['proposals'][0]
        eta = 5 + math.sqrt(2) + math.sqrt(5)
        revised = next(delivery for delivery in candidate['projected_deliveries'] if delivery['product_id'] == 'P1')
        self.assertAlmostEqual(revised['arrival_time'], eta)
        self.assertAlmostEqual(candidate['predicted_admission'], eta)
        self.assertAlmostEqual(candidate['predicted_first_start'], eta + 1)
        self.assertAlmostEqual(final['products']['P2']['history'][0]['start_time'], eta + 1)
        verify_run_via_cli(self, directory)

    def test_held_slot_delays_admission_and_projection_is_immutable(self) -> None:
        """Forecast RF1 admission eight/start nine while a known intermediate part holds the only slot."""
        scenario = as_dynamic_scenario(self.scenario)
        scenario['policy'] = 'dynamic_quality_aware'
        scenario['horizon'] = 12
        scenario['plants'][0].update(inventory_capacity=1, workstations=[
            {'id': 'WS1', 'operation_id': 1, 'duration': 4, 'windows': [[2, 6]]},
            {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[8, 12]]}])
        scenario['plants'][1]['workstations'].append({'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[0, 12]]})
        scenario['plant'] = copy.deepcopy(scenario['plants'][0])
        scenario['products'] = [
            {'id': 'P0', 'origin': 'CP1', 'release_time': 0, 'quality_class': 'Q12', 'disposition': 'remanufacture', 'operations': [1, 2]},
            {'id': 'P1', 'origin': 'CP1', 'release_time': 3, 'quality_class': 'Q2', 'disposition': 'remanufacture', 'operations': [2]}]
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        state.time = 3
        state.products['P0'].update(state='processing', location='WS1', actual_destination='RF1', planned_destination='RF1', assigned_vehicle='AV1')
        state.products['P0']['milestones'].update(release=0, pickup=1, admission=2)
        state.products['P1'].update(state='at_collection_point', location='CP1')
        state.products['P1']['milestones']['release'] = 3
        state.plant_states['RF1']['held_slots'].append('P0')
        state.plant_states['RF1']['workstations']['WS1'] = {'product_id': 'P0', 'plant_id': 'RF1', 'workstation_id': 'WS1',
            'operation_id': 1, 'operation_index': 0, 'start_time': 2, 'end_time': 6, 'window': [2, 6]}
        before = asdict(state)
        boundary = build_dynamic_policy_input(scenario, definitions, state)
        original = copy.deepcopy(boundary)
        _, audits = choose_dynamic_insertions(boundary)
        candidate = next(candidate for candidate in audits[0]['candidates'] if candidate['destination'] == 'RF1')
        self.assertEqual((candidate['predicted_arrival'], candidate['predicted_admission'], candidate['predicted_first_start']), (5, 8, 9))
        independent = audit_dynamic_forecast(boundary, candidate)
        self.assertEqual((independent['predicted_admission'], independent['predicted_first_start']), (8, 9))
        self.assertEqual(asdict(state), before)
        self.assertEqual(boundary, original)

    def test_horizon_fallback_distance_and_stable_destination_ties(self) -> None:
        """Prefer in-horizon starts, then fallback distance, and stable destination IDs for equal aware scores."""
        for mode in ('one_fallback', 'all_fallback', 'zero_wait_tie', 'destination_tie'):
            scenario = copy.deepcopy(self.scenario)
            if mode == 'one_fallback':
                scenario['plants'][1]['workstations'][0]['windows'] = [[11, 12]]
            elif mode == 'all_fallback':
                for plant in scenario['plants']:
                    plant['workstations'][0]['windows'] = [[11, 12]]
            elif mode == 'zero_wait_tie':
                scenario['plants'][0]['workstations'][0]['windows'] = [[0, 10]]
            else:
                scenario['plants'].append({**copy.deepcopy(scenario['plants'][1]), 'id': 'RF3'})
            directory, decisions, _ = self.run_policy(scenario)
            self.assertEqual(decisions[0]['proposals'][0]['destination'], 'RF2' if mode == 'destination_tie' else 'RF1')
            verify_run_via_cli(self, directory)

    def test_future_returns_and_unaware_visibility(self) -> None:
        """Exclude unreleased products from aware context and keep all operations/processing context out of unaware inputs."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'].append({**scenario['products'][0], 'id': 'P2', 'release_time': 5})
        directory, decisions, _ = self.run_policy(scenario)
        initial = decisions[0]['policy_input']
        self.assertEqual([product['id'] for product in initial['forecast_context']['scenario']['products']], ['P1'])
        self.assertNotIn('P2', initial['forecast_context']['state']['products'])
        verify_run_via_cli(self, directory)
        directory, decisions, _ = self.run_policy(scenario, policy='dynamic_quality_unaware')
        self.assertNotIn('forecast_context', decisions[0]['policy_input'])
        self.assertNotIn('operations', json.dumps(decisions[0]['policy_input']))
        verify_run_via_cli(self, directory)

    def test_nonprimary_stock_and_processing_at_horizon(self) -> None:
        """Retain RF2 processing/stock and aggregate network horizon counts without counting RF1 aliases twice."""
        scenario = copy.deepcopy(self.scenario)
        scenario['horizon'] = 4
        directory, _, final = self.run_policy(scenario)
        self.assertIsNotNone(final['plant_states']['RF2']['workstations']['WS1'])
        self.assertIsNone(final['workstations']['WS1'])
        verify_run_via_cli(self, directory)
        scenario = copy.deepcopy(self.scenario)
        scenario['collection_points'][0]['coordinates'] = [3, 0]
        scenario['plants'][1]['workstations'][0]['windows'] = [[8, 10]]
        scenario['horizon'] = 5
        directory, _, final = self.run_policy(scenario, policy='dynamic_quality_unaware')
        self.assertEqual(final['plant_states']['RF2']['inventory'], {'P1': 4})
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['waiting_count'], 1)
        verify_run_via_cli(self, directory)

    def test_all_operations_stay_at_the_chosen_plant_with_held_capacity(self) -> None:
        """Execute a two-step route wholly at RF2 with its own held slot and calendars."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'][0]['operations'] = [1, 2]
        for plant in scenario['plants']:
            plant['inventory_capacity'] = 1
            plant['workstations'].append({'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[6, 10]]})
        directory, _, final = self.run_policy(scenario)
        product = final['products']['P1']
        self.assertEqual(product['actual_destination'], 'RF2')
        self.assertEqual([operation['plant_id'] for operation in product['history']], ['RF2', 'RF2'])
        self.assertEqual([operation['start_time'] for operation in product['history']], [4, 6])
        verify_run_via_cli(self, directory)

    def test_mixed_recycling_keeps_prior_destination_commitments(self) -> None:
        """Insert a recyclable load without changing P1's selected RF2 destination."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['payload_capacity'] = 2
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [0, 1]}]
        scenario['products'].append({'id': 'P2', 'origin': 'CP1', 'release_time': 0, 'quality_class': 'QR', 'disposition': 'recycle', 'operations': []})
        directory, _, final = self.run_policy(scenario)
        self.assertEqual(final['products']['P1']['actual_destination'], 'RF2')
        self.assertEqual(final['products']['P2']['actual_destination'], 'RC1')
        verify_run_via_cli(self, directory)

    def test_range_infeasible_candidates_are_not_projected(self) -> None:
        """Reject a transport-infeasible pickup/delivery before plant forecasting can rank it."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 1
        directory, decisions, final = self.run_policy(scenario)
        self.assertEqual(decisions[0]['proposals'], [])
        self.assertTrue(all(not candidate['feasible'] and 'predicted_first_start' not in candidate for candidate in decisions[0]['requests'][0]['candidates']))
        self.assertEqual(final['summary']['vehicle_distance'], 0)
        verify_run_via_cli(self, directory)

    def test_seeded_single_plant_aware_replay(self) -> None:
        """Run the new aware policy on existing seeded inputs and replay identical frozen bytes/events."""
        configuration = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_3b_recycling.json').read_text())
        configuration['generation'].update(arrival_rate=0.5, end_time=1, batch_size=1)
        path = self.root / 'seeded.json'
        path.write_text(json.dumps(configuration))
        first, replay = self.root / 'first', self.root / 'replay'
        run_once(path, first, seed=42, policy='dynamic_quality_aware')
        run_once(first / 'scenario.json', replay, policy='dynamic_quality_aware')
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((first / filename).read_bytes(), (replay / filename).read_bytes())
        verify_run_via_cli(self, first)
        verify_run_via_cli(self, replay)

    def test_cli_policy_pair_and_default_aware_policy(self) -> None:
        """Run both policies through the actual CLI and honor an aware default without an override."""
        for policy in ('dynamic_quality_unaware', 'dynamic_quality_aware'):
            directory = self.root / policy
            result = subprocess.run([sys.executable, '-m', 'simulation', '--resolved-scenario', str(CONFIG),
                '--policy', policy, '--output', str(directory)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            verify_run_via_cli(self, directory)
        scenario = copy.deepcopy(self.scenario)
        scenario['policy'] = 'dynamic_quality_aware'
        path = self.root / 'default.json'
        path.write_text(json.dumps(scenario))
        directory = self.root / 'default'
        run_once(path, directory)
        verify_run_via_cli(self, directory)

    def test_frozen_aware_fixture_hash_lf_and_isolated_verifier(self) -> None:
        """Verify LF-pinned two-plant evidence using only an isolated copied checker and preserve its source bytes."""
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

    def test_wait_at_earlier_plant_propagates_to_later_delivery_eta(self) -> None:
        """Propagate RF1 unloading blocked until eight into the later RF2 arrival/start at twelve."""
        scenario = as_dynamic_scenario(self.scenario)
        scenario.update(policy='dynamic_quality_aware', horizon=16)
        scenario['vehicles'][0]['payload_capacity'] = 2
        scenario['plants'][0].update(inventory_capacity=1, workstations=[
            {'id': 'WS1', 'operation_id': 1, 'duration': 4, 'windows': [[2, 6], [8, 16]]},
            {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[8, 16]]}])
        scenario['plants'][1]['workstations'] = [
            {'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': [[4, 16]]},
            {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[0, 16]]}]
        scenario['plant'] = copy.deepcopy(scenario['plants'][0])
        scenario['products'] = [{'id': product_id, 'origin': 'CP1', 'release_time': release,
            'quality_class': 'Q', 'disposition': 'remanufacture', 'operations': operations}
            for product_id, release, operations in [('B', 0, [1, 2]), ('P0', 0, [1]), ('P1', 3, [1])]]
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        state.time = 3
        state.products['B'].update(state='processing', location='WS1', actual_destination='RF1', planned_destination='RF1')
        state.products['B']['milestones'].update(release=0, pickup=1, admission=2)
        state.products['P0'].update(state='onboard_av', location='AV1', assigned_vehicle='AV1', planned_destination='RF1')
        state.products['P0']['milestones'].update(release=0, pickup=2)
        state.products['P1'].update(state='at_collection_point', location='CP1')
        state.products['P1']['milestones']['release'] = 3
        state.held_slots.append('B')
        state.workstations['WS1'] = {'product_id': 'B', 'plant_id': 'RF1', 'workstation_id': 'WS1', 'operation_id': 1,
            'operation_index': 0, 'start_time': 2, 'end_time': 6, 'window': [2, 6]}
        state.vehicles['AV1'].update(position=[1, 0], location='CP1', status='travelling', load=['P0'], leg_serial=1,
            remaining_stops=[{'site_id': 'RF1', 'pickups': [], 'deliveries': ['P0']}],
            active_leg={'leg_id': 'AV1:L1', 'vehicle_id': 'AV1', 'origin_coordinates': [1, 0], 'target': 'RF1',
                'target_coordinates': [0, 0], 'departure_time': 3, 'arrival_time': 4, 'distance': 1,
                'load': ['P0'], 'remaining_range_at_departure': None})
        boundary = build_dynamic_policy_input(scenario, definitions, state)
        _, audits = choose_dynamic_insertions(boundary)
        route = [{'site_id': 'CP1', 'pickups': ['P1'], 'deliveries': []},
                 {'site_id': 'RF1', 'pickups': [], 'deliveries': ['P0']},
                 {'site_id': 'RF2', 'pickups': [], 'deliveries': ['P1']}]
        candidate = next(candidate for candidate in audits[0]['candidates'] if candidate['new_remaining_stops'] == route)
        self.assertEqual((candidate['predicted_arrival'], candidate['predicted_admission'], candidate['predicted_first_start']), (12, 12, 12))
        self.assertEqual(audit_dynamic_forecast(boundary, candidate)['predicted_arrival'], 12)
