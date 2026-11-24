"""Check finite allowance, actual travel and facility-only range resets.

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
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from tests.verifier_cli import verify_run_via_cli
from simulation.config import validate_scenario
from simulation.runner import run_once

Record = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'fixtures' / 'checkpoint_3c' / 'scenario.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_3c'


class VehicleRangeTests(unittest.TestCase):
    """Generate private runs and independently audit actual distance and remaining allowance."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Create automatically cleaned scratch space and a fresh deterministic scenario."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenario = json.loads(CONFIG.read_text())
        self.case_number = 0

    def run_case(self, scenario: Record | None = None) -> tuple[Path, list[Record], Record]:
        """Execute a fresh resolved case and return output path, events and final state."""
        self.case_number += 1
        config = self.root / f'config-{self.case_number}.json'
        config.write_text(json.dumps(self.scenario if scenario is None else scenario))
        directory = self.root / f'run-{self.case_number}'
        run_once(config, directory)
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
        return directory, events, json.loads((directory / 'final_state.json').read_text())

    def save_events(self, directory: Path, events: list[Record]) -> None:
        """Renumber edited copied events and save them using explicit UTF-8/LF."""
        for sequence, event in enumerate(events, 1):
            event['sequence'] = sequence
        with (directory / 'events.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(event) for event in events) + '\n')

    # Check the expected model or evidence behavior

    def test_range_six_journey_and_reset_order(self) -> None:
        """Check the complete 6→3→0→6 journey and reset before plant admission."""
        directory, events, final = self.run_case()
        arrivals = [event for event in events if event['event_type'] == 'leg_arrival']
        self.assertEqual([(event['time'], event['target'], event['remaining_range_on_arrival']) for event in arrivals],
                         [(3, 'CP1', 3), (6, 'RF1', 0)])
        resets = [event for event in events if event['event_type'] == 'range_reset']
        self.assertEqual([(event['sequence'], event['time'], event['facility_id'], event['range_before'], event['range_after'])
                          for event in resets], [(9, 6, 'RF1', 0, 6)])
        self.assertEqual([event['event_type'] for event in events if event['time'] == 6],
                         ['leg_arrival', 'range_reset', 'plant_admission', 'operation_start'])
        vehicle = final['vehicles']['AV1']
        self.assertEqual((vehicle['distance'], vehicle['remaining_range'], vehicle['position']), (6, 6, [0, 0]))
        self.assertEqual(final['summary']['remanufactured'], 1)
        verify_run_via_cli(self, directory)

    def test_range_five_rejected_before_assignment_or_departure(self) -> None:
        """Reject the whole six-distance trip with only five available; keep P1 unassigned at CP1."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 5
        directory, events, final = self.run_case(scenario)
        rejection = next(event for event in events if event['event_type'] == 'trip_rejected')
        self.assertEqual((rejection['required_distance'], rejection['available_range']), (6, 5))
        self.assertFalse(any(event['event_type'] in ('assignment', 'leg_departure', 'pickup') for event in events))
        self.assertEqual((final['products']['P1']['state'], final['products']['P1']['assigned_vehicle']), ('at_collection_point', None))
        self.assertEqual((final['vehicles']['AV1']['distance'], final['vehicles']['AV1']['remaining_range']), (0, 5))
        verify_run_via_cli(self, directory)

    def test_unlimited_range_skips_checks_and_completes(self) -> None:
        """Complete the same trip without finite range checks or resets."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = None
        directory, events, final = self.run_case(scenario)
        self.assertFalse(any(event['event_type'] in ('range_plan_check', 'range_reset', 'trip_rejected') for event in events))
        self.assertNotIn('remaining_range', final['vehicles']['AV1'])
        self.assertEqual((final['summary']['remanufactured'], final['summary']['vehicle_distance']), (1, 6))
        verify_run_via_cli(self, directory)

    def test_empty_recycler_visit_resets_before_next_leg(self) -> None:
        """Visit RC1 empty, reset from two to six, then complete the remaining four-distance segment."""
        scenario = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_3c_empty_recycler.json').read_text())
        directory, events, final = self.run_case(scenario)
        reset = next(event for event in events if event['event_type'] == 'range_reset' and event['facility_id'] == 'RC1')
        self.assertEqual((reset['time'], reset['range_before'], reset['range_after'], reset['load']), (4, 2, 6, []))
        self.assertFalse(any(event['event_type'] == 'recycler_acceptance' for event in events))
        following = events[reset['sequence']]
        self.assertEqual((following['event_type'], following['remaining_range_at_departure']), ('leg_departure', 6))
        self.assertEqual(final['vehicles']['AV1']['distance'], 8)
        verify_run_via_cli(self, directory)

    def test_false_collection_point_reset_fails(self) -> None:
        """Insert a false CP1 reset at sequence six and require a facility-only reset finding."""
        directory, events, _ = self.run_case()
        events.insert(5, {'time': 3, 'event_type': 'range_reset', 'vehicle_id': 'AV1',
                         'facility_id': 'CP1', 'range_before': 3, 'range_after': 6, 'load': []})
        self.save_events(directory, events)
        report = verify_run_via_cli(self, directory, 1)
        finding = next(finding for finding in report['findings'] if finding['rule'] == 'range.reset_facility')
        self.assertEqual((finding['event_sequence'], finding['time'], finding['expected'], finding['observed']),
                         (6, 3, ['RF1'], 'CP1'))

    def test_mid_leg_range_and_position_use_only_actual_motion(self) -> None:
        """Check partial outbound/inbound motion and extra event-clock updates without double counting."""
        for horizon, distance, remaining, position in [(1.5, 1.5, 4.5, [1.5, 0]), (4, 4, 2, [2, 0])]:
            with self.subTest(horizon=horizon):
                scenario = copy.deepcopy(self.scenario)
                scenario['horizon'] = horizon
                if horizon == 4:
                    scenario['products'].append({**scenario['products'][0], 'id': 'P2', 'release_time': 2})
                directory, _, final = self.run_case(scenario)
                vehicle = final['vehicles']['AV1']
                self.assertEqual((vehicle['distance'], vehicle['remaining_range'], vehicle['position']), (distance, remaining, position))
                self.assertEqual(vehicle['status'], 'travelling')
                verify_run_via_cli(self, directory)

    def test_facility_reset_precedes_blocked_unloading(self) -> None:
        """Reset to four on the AV2 arrival at a full plant, not later when it unloads at eight."""
        scenario = json.loads((ROOT / 'tests' / 'fixtures' / 'checkpoint_3a' / 'scenario.json').read_text())
        for vehicle in scenario['vehicles']:
            vehicle['maximum_range'] = 4
        directory, events, _ = self.run_case(scenario)
        reset = next(event for event in events if event['event_type'] == 'range_reset' and event['vehicle_id'] == 'AV2')
        waiting = next(event for event in events if event['event_type'] == 'unload_wait')
        self.assertEqual((reset['time'], reset['range_before'], reset['range_after']), (5, 0, 4))
        self.assertLess(reset['sequence'], waiting['sequence'])
        self.assertEqual(sum(event['event_type'] == 'range_reset' and event['vehicle_id'] == 'AV2' for event in events), 1)
        verify_run_via_cli(self, directory)

    def test_intermediate_recycler_resets_carried_remanufacturable_product(self) -> None:
        """Use RC1 to reset while carrying P1, then deliver only at its committed RF1 destination."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 4
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [2, 0]}]
        scenario['trips'][0]['stops'] = ['CP1', 'RC1', 'RF1']
        directory, events, final = self.run_case(scenario)
        plan = next(event for event in events if event['event_type'] == 'range_plan_check')
        self.assertEqual([segment['required_distance'] for segment in plan['segments']], [4, 2])
        reset = next(event for event in events if event['event_type'] == 'range_reset' and event['facility_id'] == 'RC1')
        self.assertEqual((reset['time'], reset['range_before'], reset['range_after'], reset['load']), (4, 0, 4, ['P1']))
        self.assertFalse(any(event['event_type'] == 'recycler_acceptance' for event in events))
        self.assertEqual(final['products']['P1']['actual_destination'], 'RF1')
        verify_run_via_cli(self, directory)

    def test_invalid_range_and_plan_structure_rejected(self) -> None:
        """Reject invalid finite maxima, unconfigured starts and fixed plans without a final facility."""
        for maximum in (0, -1, True, float('inf'), float('nan')):
            scenario = copy.deepcopy(self.scenario)
            scenario['vehicles'][0]['maximum_range'] = maximum
            with self.assertRaises(ValueError):
                validate_scenario(scenario)
        for stops in ([], ['CP1'], ['CP1', 'CP1', 'RF1'], ['UNKNOWN', 'RF1']):
            scenario = copy.deepcopy(self.scenario)
            scenario['trips'][0]['stops'] = stops
            with self.assertRaises(ValueError):
                validate_scenario(scenario)
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['initial_site'] = 'CP1'
        with self.assertRaises(ValueError):
            validate_scenario(scenario)

    def test_heterogeneous_finite_and_unlimited_vehicles(self) -> None:
        """Run one finite and one unlimited AV together without sharing or double-counting allowances."""
        scenario = copy.deepcopy(self.scenario)
        scenario['products'].append({**scenario['products'][0], 'id': 'P2'})
        scenario['vehicles'].append({**scenario['vehicles'][0], 'id': 'AV2', 'maximum_range': None})
        scenario['trips'].append({**scenario['trips'][0], 'vehicle_id': 'AV2'})
        directory, events, final = self.run_case(scenario)
        self.assertEqual(final['summary']['vehicle_distance'], 12)
        self.assertEqual(final['vehicles']['AV1']['remaining_range'], 6)
        self.assertNotIn('remaining_range', final['vehicles']['AV2'])
        self.assertTrue(all(event['vehicle_id'] == 'AV1' for event in events if event['event_type'] == 'range_reset'))
        verify_run_via_cli(self, directory)

    def test_missing_reset_and_forged_allowances_fail(self) -> None:
        """Reject a missing facility reset, forged CP allowance and forged exported final allowance."""
        for mutation in ('missing', 'arrival', 'final'):
            directory, events, _ = self.run_case()
            if mutation == 'missing':
                events = [event for event in events if event['event_type'] != 'range_reset']
                self.save_events(directory, events)
            elif mutation == 'arrival':
                next(event for event in events if event['event_type'] == 'leg_arrival')['remaining_range_on_arrival'] = 6
                self.save_events(directory, events)
            else:
                path = directory / 'final_state.json'
                final = json.loads(path.read_text())
                final['vehicles']['AV1']['remaining_range'] = 5
                path.write_text(json.dumps(final))
            verify_run_via_cli(self, directory, 1)

    def test_later_infeasible_segment_blocks_the_first_departure(self) -> None:
        """Reject before travelling to a reachable first reset if a later segment requires five but range is four."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 4
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [1, 0]}]
        scenario['trips'][0]['stops'] = ['RC1', 'CP1', 'RF1']
        directory, events, final = self.run_case(scenario)
        rejection = next(event for event in events if event['event_type'] == 'trip_rejected')
        self.assertEqual((rejection['start'], rejection['required_distance'], rejection['available_range']), ('RC1', 5, 4))
        self.assertEqual(final['summary']['vehicle_distance'], 0)
        verify_run_via_cli(self, directory)

    def test_initial_recycler_start_has_full_allowance(self) -> None:
        """Start at a configured recycler with full allowance and use its actual coordinates for feasibility."""
        scenario = copy.deepcopy(self.scenario)
        scenario['recycling_facilities'] = [{'id': 'RC1', 'coordinates': [4, 0]}]
        scenario['vehicles'][0].update(initial_site='RC1', maximum_range=4)
        directory, events, final = self.run_case(scenario)
        departure = next(event for event in events if event['event_type'] == 'leg_departure')
        self.assertEqual((departure['origin'], departure['remaining_range_at_departure']), ('RC1', 4))
        self.assertEqual(final['summary']['vehicle_distance'], 4)
        verify_run_via_cli(self, directory)

    def test_seeded_finite_range_replay_matches_events(self) -> None:
        """Apply finite range to seeded recycling inputs and replay their saved scenario identically."""
        configuration = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_3b_recycling.json').read_text())
        for vehicle in configuration['vehicles']:
            vehicle['maximum_range'] = 3
        path = self.root / 'seeded.json'
        path.write_text(json.dumps(configuration))
        directory = self.root / 'seeded-run'
        replay = self.root / 'seeded-replay'
        run_once(path, directory, seed=42)
        run_once(directory / 'scenario.json', replay)
        for filename in ('scenario.json', 'events.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((directory / filename).read_bytes(), (replay / filename).read_bytes())
        verify_run_via_cli(self, directory)
        verify_run_via_cli(self, replay)

    def test_frozen_fixture_hash_lf_and_isolated_verifier(self) -> None:
        """Verify new frozen LF evidence using only a copied standalone verifier and preserve all bytes."""
        directory = self.root / 'fixture'
        shutil.copytree(FIXTURE, directory)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.assertNotIn(b'\r', before['scenario.json'])
        manifest = json.loads(before['run_manifest.json'])
        self.assertEqual(hashlib.sha256(before['scenario.json']).hexdigest(), manifest['scenario_sha256'])
        script = self.root / 'verify_simulation.py'
        shutil.copyfile(ROOT / 'tools' / 'verify.py', script)
        result = subprocess.run([sys.executable, '-I', str(script), str(directory)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for filename, contents in before.items():
            self.assertEqual((directory / filename).read_bytes(), contents)
