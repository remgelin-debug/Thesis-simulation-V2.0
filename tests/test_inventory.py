"""Check finite inventory, held return slots and waiting-vehicle priority.

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

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'tests' / 'fixtures' / 'checkpoint_3a' / 'scenario.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_3a'
Record = dict[str, Any]


class InventoryTests(unittest.TestCase):
    """Exercise physical inventory transitions and independent replay in private run folders."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate an automatically cleaned scratch root and load an independent scenario copy."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scenario = json.loads(CONFIG.read_text())
        self.case_number = 0

    def run_case(self, scenario: Record | None = None) -> tuple[Path, list[Record], Record]:
        """Run a resolved scenario and return its directory, saved events and final state."""
        self.case_number += 1
        config = self.root / f'config-{self.case_number}.json'
        config.write_text(json.dumps(self.scenario if scenario is None else scenario))
        directory = self.root / f'run-{self.case_number}'
        run_once(config, directory)
        return directory, [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()], json.loads((directory / 'final_state.json').read_text())

    def save_events(self, directory: Path, events: list[Record]) -> None:
        """Renumber edited events and replace only the temporary copy's event log in LF UTF-8."""
        for sequence, event in enumerate(events, 1):
            event['sequence'] = sequence
        with (directory / 'events.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(json.dumps(event) for event in events) + '\n')

    # Check the expected model or evidence behavior

    def test_requested_slot_timeline(self) -> None:
        """Check times 4/5/6/8 and exact release→retry→admission order at time eight."""
        directory, events, final = self.run_case()
        selected = [(e['sequence'], e['time'], e['event_type'], e.get('product_id')) for e in events
                    if e['event_type'] in ('inventory_hold', 'unload_wait', 'inventory_return', 'unload_retry', 'plant_admission', 'inventory_slot_release')]
        for expected in [(16, 4, 'inventory_hold', 'P1'), (18, 5, 'unload_wait', None),
                         (20, 6, 'inventory_return', 'P1'), (22, 8, 'inventory_slot_release', 'P1'),
                         (23, 8, 'unload_retry', None), (24, 8, 'plant_admission', 'P2')]:
            self.assertIn(expected, selected)
        self.assertEqual([e['event_type'] for e in events if e['time'] == 8],
                         ['operation_start', 'inventory_slot_release', 'unload_retry', 'plant_admission'])
        self.assertEqual(final['products']['P2']['milestones']['admission'], 8)
        self.assertEqual(final['summary'], {'generated': 2, 'remanufactured': 2, 'recycled': 0,
            'unfinished': 0, 'vehicle_distance': 8, 'plant_inventory_wait': 3, 'waiting_to_unload': 3})
        self.assertEqual((final['inventory'], final['held_slots'], final['waiting_vehicles']), ({}, [], {}))
        self.assertEqual(verify_run_via_cli(self, directory)['recalculated_summary'], final['summary'])

    def test_early_admission_exceeds_capacity(self) -> None:
        """Move P2 admission to time five and require a capacity finding with attempted occupancy two."""
        directory, events, _ = self.run_case()
        admission = next(e for e in events if e['event_type'] == 'plant_admission' and e['product_id'] == 'P2')
        events.remove(admission)
        events = [e for e in events if e['event_type'] != 'unload_retry']
        admission.update(time=5, waiting_count=1, held_count=1)
        insertion = next(i + 1 for i, e in enumerate(events) if e['event_type'] == 'unload_wait')
        events.insert(insertion, admission)
        self.save_events(directory, events)
        report = verify_run_via_cli(self, directory, 1)
        finding = next(f for f in report['findings'] if f['rule'] == 'inventory.capacity')
        self.assertEqual((finding['time'], finding['event_sequence']), (5, 19))
        self.assertEqual(finding['expected'], 'waiting + held <= 1')
        self.assertEqual(finding['observed']['attempted_occupancy'], 2)
        self.assertEqual(finding['observed']['held_slots'], 1)

    def test_waiting_vehicle_arrival_then_id_priority(self) -> None:
        """Test earlier-arrival precedence and same-arrival vehicle-ID tie-breaking with three loads."""
        for earlier in (True, False):
            with self.subTest(earlier=earlier):
                scenario = copy.deepcopy(self.scenario)
                scenario['horizon'] = 14
                scenario['plant']['workstations'][0]['windows'] = [[4, 6], [9, 15]]
                template = scenario['vehicles'][0]
                scenario['vehicles'] = [{**template, 'id': vehicle_id} for vehicle_id in ('AV1', 'AVA', 'AVZ')]
                scenario['products'].append({**scenario['products'][1], 'id': 'P3'})
                scenario['trips'] = [scenario['trips'][0],
                    {'vehicle_id': 'AVZ', 'departure_time': 0.5 if earlier else 1, 'stops': ['CP1', 'RF1']},
                    {'vehicle_id': 'AVA', 'departure_time': 1, 'stops': ['CP1', 'RF1']}]
                directory, events, _ = self.run_case(scenario)
                admissions = [e['vehicle_id'] for e in events if e['event_type'] == 'plant_admission']
                self.assertEqual(admissions, ['AV1', 'AVZ', 'AVA'] if earlier else ['AV1', 'AVA', 'AVZ'])
                verify_run_via_cli(self, directory)
                retry = next(e for e in events if e['event_type'] == 'unload_retry')
                retry['vehicle_id'] = 'AVA' if earlier else 'AVZ'
                self.save_events(directory, events)
                report = verify_run_via_cli(self, directory, 1)
                self.assertTrue(any(f['rule'] == 'inventory.waiting_vehicle_priority' for f in report['findings']))

    def test_workstation_inventory_fifo_and_id_tie(self) -> None:
        """Admit three loads together and check oldest-entry selection then ID for one workstation."""
        scenario = copy.deepcopy(self.scenario)
        scenario['horizon'] = 14
        scenario['plant']['inventory_capacity'] = 3
        scenario['plant']['workstations'][0]['windows'] = [[4, 6], [9, 15]]
        scenario['products'].append({**scenario['products'][1], 'id': 'P3'})
        scenario['vehicles'] = [{**scenario['vehicles'][0], 'payload_capacity': 3}]
        scenario['trips'] = [scenario['trips'][0]]
        directory, events, _ = self.run_case(scenario)
        starts = [e['product_id'] for e in events if e['event_type'] == 'operation_start' and e['workstation_id'] == 'WS1']
        self.assertEqual(starts, ['P1', 'P2', 'P3'])
        verify_run_via_cli(self, directory)
        next(e for e in events if e['event_type'] == 'operation_start' and e['product_id'] == 'P2')['product_id'] = 'P3'
        self.save_events(directory, events)
        report = verify_run_via_cli(self, directory, 1)
        self.assertTrue(any(f['rule'] == 'workstation.product_priority' for f in report['findings']))

    def test_partial_payload_stays_on_vehicle(self) -> None:
        """Unload P1 from a two-part AV at four while P2 remains onboard until eight."""
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'] = [{**scenario['vehicles'][0], 'payload_capacity': 2}]
        scenario['trips'] = [scenario['trips'][0]]
        directory, events, final = self.run_case(scenario)
        blocked = next(e for e in events if e['event_type'] == 'unload_wait')
        self.assertEqual((blocked['time'], blocked['load']), (4, ['P2']))
        self.assertEqual(final['vehicles']['AV1']['waiting_to_unload'], 4)
        verify_run_via_cli(self, directory)

    def test_horizon_retains_holds_and_onboard_stock(self) -> None:
        """Verify horizons during held processing, blocked unloading, inventory return and same-time admission."""
        for horizon in (0, 1, 4, 5, 6, 7, 8, 9, 11):
            with self.subTest(horizon=horizon):
                scenario = copy.deepcopy(self.scenario)
                scenario['horizon'] = horizon
                scenario['trips'] = [trip for trip in scenario['trips'] if trip['departure_time'] <= horizon]
                directory, _, final = self.run_case(scenario)
                if horizon == 5:
                    self.assertEqual(final['held_slots'], ['P1'])
                    self.assertEqual(final['vehicles']['AV2']['load'], ['P2'])
                    self.assertEqual(final['products']['P2']['state'], 'onboard_av')
                if horizon == 6:
                    self.assertEqual((final['inventory'], final['held_slots']), ({'P1': 6}, []))
                if horizon == 8:
                    self.assertEqual(final['inventory'], {'P2': 8})
                verify_run_via_cli(self, directory)

    def test_unlimited_capacity_and_adjacent_windows(self) -> None:
        """Keep unlimited inventory valid and allow a full operation across adjacent calendar intervals."""
        scenario = copy.deepcopy(self.scenario)
        scenario['plant']['inventory_capacity'] = None
        scenario['plant']['workstations'][0]['windows'] = [[4, 5], [5, 6], [9, 11]]
        directory, events, final = self.run_case(scenario)
        self.assertFalse(any(e['event_type'] in ('inventory_hold', 'inventory_slot_release', 'unload_wait') for e in events))
        self.assertEqual(final['summary']['waiting_to_unload'], 0)
        self.assertEqual(final['products']['P2']['milestones']['admission'], 5)
        verify_run_via_cli(self, directory)

    def test_missing_hold_or_falsified_replacement_fails(self) -> None:
        """Reject missing hold evidence and an intermediate return claiming it replaced no hold."""
        for missing in (True, False):
            directory, events, _ = self.run_case()
            if missing:
                events = [e for e in events if e['event_type'] != 'inventory_hold']
            else:
                next(e for e in events if e['event_type'] == 'inventory_return')['held_slot_replaced'] = False
            self.save_events(directory, events)
            verify_run_via_cli(self, directory, 1)

    def test_missing_retry_and_skipped_start_fail(self) -> None:
        """Reject skipped retry evidence and a log omitting all enabled time-eight transitions."""
        for skip_start in (True, False):
            directory, events, _ = self.run_case()
            events = [e for e in events if not (e['time'] == 8 if skip_start else e['event_type'] == 'unload_retry')]
            self.save_events(directory, events)
            verify_run_via_cli(self, directory, 1)

    def test_invalid_capacity_and_range_rejected(self) -> None:
        """Reject nonpositive/noninteger capacity and nonpositive range."""
        for capacity in (0, -1, 1.5, True):
            scenario = copy.deepcopy(self.scenario)
            scenario['plant']['inventory_capacity'] = capacity
            with self.assertRaises(ValueError):
                validate_scenario(scenario)
        scenario = copy.deepcopy(self.scenario)
        scenario['vehicles'][0]['maximum_range'] = 0
        with self.assertRaises(ValueError):
            validate_scenario(scenario)

    def test_exports_and_missing_evidence(self) -> None:
        """Reject a forged final hold and return incomplete for a missing required dispatch file."""
        directory, _, _ = self.run_case()
        path = directory / 'final_state.json'
        original = path.read_bytes()
        final = json.loads(original)
        final['held_slots'] = ['P1']
        path.write_text(json.dumps(final))
        verify_run_via_cli(self, directory, 1)
        path.write_bytes(original)
        (directory / 'dispatch_decisions.jsonl').unlink()
        verify_run_via_cli(self, directory, 2)

    def test_saved_fixture_hash_lf_and_standalone_verification(self) -> None:
        """Verify frozen LF evidence with an isolated copy of the verifier and unchanged input bytes."""
        directory = self.root / 'fixture'
        shutil.copytree(FIXTURE, directory)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        scenario_bytes = before['scenario.json']
        self.assertNotIn(b'\r', scenario_bytes)
        manifest = json.loads(before['run_manifest.json'])
        self.assertEqual(hashlib.sha256(scenario_bytes).hexdigest(), manifest['scenario_sha256'])
        script = self.root / 'verify_simulation.py'
        shutil.copyfile(ROOT / 'tools' / 'verify.py', script)
        result = subprocess.run([sys.executable, '-I', str(script), str(directory)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name, contents in before.items():
            self.assertEqual((directory / name).read_bytes(), contents)
        (directory / 'scenario.json').write_bytes(scenario_bytes + b'\n')
        report = verify_run_via_cli(self, directory, 1)
        self.assertTrue(any(f['rule'] == 'manifest.scenario_sha256' for f in report['findings']))

    def test_zero_distance_and_empty_fleet(self) -> None:
        """Verify zero-time legs and collection stock accumulating with no vehicles."""
        for empty in (True, False):
            scenario = copy.deepcopy(self.scenario)
            if empty:
                scenario['vehicles'] = []
                scenario['trips'] = []
            else:
                scenario['collection_point']['coordinates'] = [0, 0]
            directory, _, final = self.run_case(scenario)
            self.assertEqual(final['summary']['remanufactured'], 0 if empty else 2)
            self.assertEqual(final['summary']['vehicle_distance'], 0)
            verify_run_via_cli(self, directory)
