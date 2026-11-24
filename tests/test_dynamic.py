"""Check pickup/delivery insertion, mid-leg rerouting and existing commitments.

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
from typing import Any

from tests.verifier_cli import verify_run_via_cli
from simulation.runner import run_once
from simulation.config import as_dynamic_scenario

Record = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_4b'


class DynamicTests(unittest.TestCase):
    """Keep dynamic runs and edited evidence in automatically cleaned private folders."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Create scratch storage, fresh hand-checkable inputs and unique run numbering."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenario = json.loads(CONFIG.read_text())
        self.number = 0

    def run_case(self, scenario: Record | None = None) -> tuple[Path, list[Record], list[Record], Record]:
        """Run one dynamic scenario and return folder, events, decisions and final state."""
        self.number += 1
        source = self.root / f'scenario-{self.number}.json'
        source.write_text(json.dumps(self.scenario if scenario is None else scenario))
        directory = self.root / f'run-{self.number}'
        run_once(source, directory)
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
        decisions = [json.loads(line) for line in (directory / 'dispatch_decisions.jsonl').read_text().splitlines()]
        return directory, events, decisions, json.loads((directory / 'final_state.json').read_text())

    def save_records(self, directory: Path, filename: str, records: list[Record]) -> None:
        """Replace one temporary JSONL file with LF records and renumber event sequences if needed."""
        if filename == 'events.jsonl':
            for sequence, record in enumerate(records, 1):
                record['sequence'] = sequence
        with (directory / filename).open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(record) for record in records) + '\n')

    # Check the expected model or evidence behavior

    def test_requested_mid_leg_reroute(self) -> None:
        """At time 5 check position (3, 0), P1 onboard and remaining range 5.

        Check the diverted CP2/RF1 arrivals and that the cancelled time-8 arrival
        causes no arrival, delivery or range reset.
        """
        directory, events, decisions, final = self.run_case()
        decision = next(item for item in decisions if item['time'] == 5)
        vehicle = decision['policy_input']['vehicles'][0]
        self.assertEqual((vehicle['position'], vehicle['remaining_range'], vehicle['load']), ([3, 0], 5, ['P1']))
        self.assertEqual(vehicle['active_leg']['arrival_time'], 8)
        self.assertEqual(decision['proposals'][0]['old_remaining_stops'], [{'site_id': 'RF1', 'pickups': [], 'deliveries': ['P1']}])
        self.assertAlmostEqual(decision['proposals'][0]['added_distance'], math.sqrt(2) + math.sqrt(5) - 3)
        self.assertEqual(final['products']['P1']['milestones']['pickup'], 4)
        self.assertAlmostEqual(final['products']['P2']['milestones']['pickup'], 5 + math.sqrt(2))
        arrival = 5 + math.sqrt(2) + math.sqrt(5)
        for product in final['products'].values():
            self.assertAlmostEqual(product['milestones']['admission'], arrival)
        self.assertAlmostEqual(final['summary']['vehicle_distance'], arrival)
        self.assertEqual((final['summary']['remanufactured'], final['summary']['unfinished']), (2, 0))
        self.assertFalse(any(event['time'] == 8 for event in events))
        cancellation = next(event for event in events if event['event_type'] == 'leg_cancelled')
        self.assertEqual((cancellation['old_arrival_time'], cancellation['distance_travelled']), (8, 1))
        self.assertEqual(sum(event['event_type'] == 'range_reset' for event in events), 1)
        for item in decisions:
            serialized = json.dumps(item['policy_input'])
            self.assertNotIn('operations', serialized)
            self.assertNotIn('quality_class', serialized)
        verify_run_via_cli(self, directory)

    def test_forged_replan_event_position_fails(self) -> None:
        """Forge the actual time-five revision position and require the precise independently reconstructed diagnostic."""
        directory, events, _, _ = self.run_case()
        revision = next(event for event in events if event['event_type'] == 'route_revised' and event['time'] == 5)
        revision['position'] = [4, 0]
        self.save_records(directory, 'events.jsonl', events)
        report = verify_run_via_cli(self, directory, 1)
        finding = next(item for item in report['findings'] if item['rule'] == 'dynamic.replan_position')
        self.assertEqual((finding['time'], finding['event_sequence']), (5, revision['sequence']))
        self.assertEqual((finding['expected'], finding['observed']), ([3, 0], [4, 0]))

    def test_forged_dispatch_position_or_candidates_fail(self) -> None:
        """Audit policy-boundary position and full candidate scores rather than trusting the saved decision."""
        for position in (True, False):
            directory, _, decisions, _ = self.run_case()
            decision = next(item for item in decisions if item['time'] == 5)
            if position:
                decision['policy_input']['vehicles'][0]['position'] = [4, 0]
            else:
                decision['requests'][0]['candidates'][0]['added_distance'] = -1
            self.save_records(directory, 'dispatch_decisions.jsonl', decisions)
            verify_run_via_cli(self, directory, 1)

    def test_capacity_forces_delivery_before_second_pickup(self) -> None:
        """With payload one, retain the current RF1 arrival and insert P2 only after unloading P1."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['payload_capacity'] = 1
        scenario['horizon'] = 16
        scenario['plant']['workstations'][0]['windows'] = [[0, 16]]
        directory, events, decisions, final = self.run_case(scenario)
        decision = next(item for item in decisions if item['time'] == 5)
        self.assertEqual(decision['proposals'][0]['new_remaining_stops'][0]['site_id'], 'RF1')
        self.assertTrue(any(candidate['reason'] == 'payload_exceeded' for candidate in decision['requests'][0]['candidates']))
        self.assertFalse(any(event['event_type'] == 'leg_cancelled' for event in events))
        self.assertEqual(final['products']['P1']['milestones']['admission'], 8)
        self.assertGreater(final['products']['P2']['milestones']['pickup'], 8)
        verify_run_via_cli(self, directory)

    def test_full_route_range_can_force_a_later_insertion(self) -> None:
        """Range eight requires reaching RF1 for a reset before the second pickup, despite a reachable detour leg."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 8
        scenario['horizon'] = 16
        scenario['plant']['workstations'][0]['windows'] = [[0, 16]]
        directory, _, decisions, final = self.run_case(scenario)
        decision = next(item for item in decisions if item['time'] == 5)
        self.assertEqual(decision['policy_input']['vehicles'][0]['remaining_range'], 3)
        self.assertTrue(any(candidate['reason'] == 'range_exceeded_before_facility' for candidate in decision['requests'][0]['candidates']))
        self.assertEqual(decision['proposals'][0]['new_remaining_stops'][0]['site_id'], 'RF1')
        self.assertGreater(final['products']['P2']['milestones']['pickup'], 8)
        verify_run_via_cli(self, directory)

    def test_infeasible_request_waits_and_is_reconsidered(self) -> None:
        """Keep unreachable P1 at CP1 and reconsider it when P2 releases and the AV later becomes available."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 7
        directory, _, decisions, final = self.run_case(scenario)
        self.assertEqual(final['products']['P1']['state'], 'at_collection_point')
        self.assertIsNone(final['products']['P1']['assigned_vehicle'])
        self.assertEqual(final['products']['P2']['state'], 'remanufactured')
        self.assertGreaterEqual(sum(any(request['product_id'] == 'P1' for request in decision['requests']) for decision in decisions), 3)
        verify_run_via_cli(self, directory)

    def test_unlimited_range_still_integrates_cancelled_motion(self) -> None:
        """Track actual positions/distance under unlimited range without adding reset events."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = None
        directory, events, _, final = self.run_case(scenario)
        self.assertAlmostEqual(final['summary']['vehicle_distance'], 5 + math.sqrt(2) + math.sqrt(5))
        self.assertIsNone(final['vehicles']['AV1']['remaining_range'])
        self.assertFalse(any(event['event_type'] == 'range_reset' for event in events))
        verify_run_via_cli(self, directory)

    def test_horizon_inside_replacement_leg_counts_only_actual_travel(self) -> None:
        """Stop at six on the new CP2 leg with total distance six and remaining allowance four."""
        scenario = copy.deepcopy(self.scenario)
        scenario['horizon'] = 6
        directory, _, _, final = self.run_case(scenario)
        vehicle = final['vehicles']['AV1']
        self.assertEqual((vehicle['distance'], vehicle['remaining_range']), (6, 4))
        self.assertAlmostEqual(vehicle['position'][0], 3 - 1 / math.sqrt(2))
        self.assertAlmostEqual(vehicle['position'][1], 1 / math.sqrt(2))
        self.assertEqual((vehicle['active_leg']['target'], vehicle['load']), ('CP2', ['P1']))
        verify_run_via_cli(self, directory)

    def test_simultaneous_releases_are_batched_before_one_departure(self) -> None:
        """Select both time-zero requests before starting a single final route leg."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'][1]['release_time'] = 0
        directory, events, decisions, _ = self.run_case(scenario)
        self.assertEqual(decisions[0]['trigger']['released_products'], ['P1', 'P2'])
        self.assertEqual(sum(event['event_type'] == 'leg_departure' and event['time'] == 0 for event in events), 1)
        self.assertEqual(len(decisions[0]['proposals']), 2)
        verify_run_via_cli(self, directory)

    def test_zero_distance_and_empty_fleet(self) -> None:
        """Settle same-time zero-distance stops finitely and leave stock accumulating with no AVs."""
        for empty in (True, False):
            scenario = copy.deepcopy(self.scenario)
            if empty:
                scenario['vehicles'] = []
            else:
                for point in scenario['collection_points']:
                    point['coordinates'] = [0, 0]
                scenario['products'][1]['release_time'] = 0
            directory, _, _, final = self.run_case(scenario)
            self.assertEqual(final['summary']['vehicle_distance'], 0)
            self.assertEqual(final['summary']['remanufactured'], 0 if empty else 2)
            verify_run_via_cli(self, directory)

    def test_waiting_vehicle_keeps_commitment_until_capacity_frees(self) -> None:
        """Do not insert P3 into a blocked AV; reconsider it when the held slot frees and unloading ends."""
        scenario = as_dynamic_scenario(json.loads((ROOT / 'tests' / 'fixtures' / 'checkpoint_3a' / 'scenario.json').read_text()))
        scenario['vehicles'] = [{**scenario['vehicles'][0], 'payload_capacity': 2}]
        scenario['products'].append({**scenario['products'][1], 'id': 'P3', 'release_time': 5})
        directory, _, decisions, final = self.run_case(scenario)
        blocked = next(decision for decision in decisions if decision['time'] == 5)
        self.assertEqual(blocked['policy_input']['vehicles'][0]['status'], 'waiting_to_unload')
        self.assertEqual(blocked['proposals'], [])
        self.assertEqual(final['products']['P2']['milestones']['admission'], 8)
        verify_run_via_cli(self, directory)

    def test_mixed_dispositions_preserve_different_destinations(self) -> None:
        """Carry a recyclable and remanufacturable part together, unloading only each destination's committed load."""
        scenario = copy.deepcopy(self.scenario)
        scenario['collection_points'] = [{'id': 'CP1', 'coordinates': [1, 0]}]
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [0, 1]}]
        scenario['vehicles'][0]['maximum_range'] = None
        scenario['products'][0].update(disposition='recycle', operations=[])
        scenario['products'][1].update(origin='CP1', release_time=0)
        directory, _, _, final = self.run_case(scenario)
        self.assertEqual((final['summary']['recycled'], final['summary']['remanufactured']), (1, 1))
        self.assertEqual(final['products']['P1']['actual_destination'], 'RC1')
        self.assertEqual(final['products']['P2']['actual_destination'], 'RF1')
        verify_run_via_cli(self, directory)

    def test_inserted_facility_visit_makes_range_feasible(self) -> None:
        """Use a recycler as a reset visit while retaining a remanufacturable RF1 delivery commitment."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'] = scenario['products'][:1]
        scenario['vehicles'][0]['maximum_range'] = 7
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [2, 0]}]
        directory, events, decisions, final = self.run_case(scenario)
        self.assertGreater(decisions[0]['proposals'][0]['extra_visits'], 0)
        self.assertTrue(any(event['event_type'] == 'range_reset' and event['facility_id'] == 'RC1' for event in events))
        self.assertFalse(any(event['event_type'] == 'recycler_acceptance' for event in events))
        self.assertEqual(final['products']['P1']['actual_destination'], 'RF1')
        verify_run_via_cli(self, directory)

    def test_resurrected_superseded_arrival_fails(self) -> None:
        """Insert the cancelled leg's old time-eight arrival and require an independent live-leg identity failure."""
        directory, events, decisions, _ = self.run_case()
        old = copy.deepcopy(next(decision for decision in decisions if decision['time'] == 5)['policy_input']['vehicles'][0]['active_leg'])
        ghost = {**old, 'time': 8, 'event_type': 'leg_arrival', 'remaining_range_on_arrival': 2}
        insertion = next(index for index, event in enumerate(events) if event['time'] > 8)
        events.insert(insertion, ghost)
        self.save_records(directory, 'events.jsonl', events)
        verify_run_via_cli(self, directory, 1)

    def test_seeded_static_inputs_can_run_and_replay_dynamically(self) -> None:
        """Use the existing single-point seeded config with a dynamic execution override and preserve frozen bytes on replay."""
        configuration = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_3b_recycling.json').read_text())
        configuration['generation'].update(arrival_rate=0.5, end_time=1, batch_size=1)
        path = self.root / 'seeded.json'
        path.write_text(json.dumps(configuration))
        first, replay = self.root / 'first', self.root / 'replay'
        run_once(path, first, seed=42, policy='dynamic_quality_unaware')
        run_once(first / 'scenario.json', replay, policy='dynamic_quality_unaware')
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((first / filename).read_bytes(), (replay / filename).read_bytes())
        manifest = json.loads((first / 'run_manifest.json').read_text())
        self.assertEqual((manifest['input_format_version'], manifest['output_format_version']), (2, 3))
        verify_run_via_cli(self, first)
        verify_run_via_cli(self, replay)

    def test_frozen_dynamic_fixture_lf_hash_and_standalone_verifier(self) -> None:
        """Verify new LF-pinned dynamic evidence using only an isolated copied verifier; preserve input bytes."""
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
