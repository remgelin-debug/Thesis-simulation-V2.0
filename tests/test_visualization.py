"""Check saved-event replay, exact stepping, timetable rows and processing bands.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
ReplayTests need no graphics. VisualizationTests use the optional Agg backend
for rendering and callbacks; they do not prove an interactive desktop works.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import TYPE_CHECKING
import unittest
from unittest.mock import patch

from tools.replay import ReplayController, RunReplay, apply_view_event, initial_view_state, normalize_view_scenario
from simulation.runner import run_once

from tests.single_product import single_product_scenario

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'checkpoint_4b'
HAS_MATPLOTLIB = importlib.util.find_spec('matplotlib') is not None

if TYPE_CHECKING:
    from tools.viewer import MatplotlibViewer


class ReplayTests(unittest.TestCase):
    """Exercise snapshots and playback with no plotting dependency or repository mutations."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate cleaned scratch space and load the immutable rerouting fixture."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.replay = RunReplay(FIXTURE)

    # Check the expected model or evidence behavior

    def test_time_five_reroute_and_cancelled_arrival(self) -> None:
        """Show actual position/load/allowance at five and no superseded RF1 arrival at eight."""
        state = self.replay.snapshot(5)
        vehicle = state['vehicles']['AV1']
        self.assertEqual(vehicle['position'], [3, 0])
        self.assertEqual(vehicle['load'], ['P1'])
        self.assertEqual(vehicle['remaining_range'], 5)
        self.assertEqual(vehicle['active_leg']['target'], 'CP2')
        self.assertEqual(state['last_event']['sequence'], 16)
        at_eight = self.replay.snapshot(8)
        self.assertEqual(at_eight['last_event']['sequence'], 20)
        self.assertNotEqual(at_eight['vehicles']['AV1']['position'], [0, 0])
        self.assertEqual(at_eight['vehicles']['AV1']['load'], ['P1', 'P2'])
        self.assertLess(at_eight['vehicles']['AV1']['remaining_range'], 5)

    def test_exact_pickup_arrival_and_first_completion_records(self) -> None:
        """Step through same-time log microstates instead of skipping pickup/admission/completion transitions."""
        controller = ReplayController(self.replay, 5)
        controller.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 17)
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1'])
        controller.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 18)
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1', 'P2'])
        self.assertAlmostEqual(controller.state['time'], 5 + math.sqrt(2))
        controller.seek_event(21)
        self.assertEqual(controller.state['vehicles']['AV1']['position'], [0, 0])
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1', 'P2'])
        controller.seek_event(25)
        self.assertEqual(controller.state['plants']['RF1']['inventory'], ['P2'])
        self.assertEqual(controller.state['plants']['RF1']['workstations']['WS1']['product_id'], 'P1')
        controller.seek_event(29)
        self.assertAlmostEqual(controller.state['time'], 6 + math.sqrt(2) + math.sqrt(5))
        self.assertIsNone(controller.state['plants']['RF1']['workstations']['WS1'])
        self.assertEqual(controller.state['products']['P1']['state'], 'operation_complete')
        controller.step_next_event()
        self.assertEqual(controller.state['products']['P1']['state'], 'remanufactured')
        controller.step_next_event()
        self.assertEqual(controller.state['plants']['RF1']['workstations']['WS1']['product_id'], 'P2')

    def test_interpolation_before_after_reroute_and_backward_seek(self) -> None:
        """Interpolate the selected recorded leg, including diagonal travel and backward seeks across cancellation."""
        before = self.replay.snapshot(4.5)['vehicles']['AV1']
        self.assertEqual(before['position'], [3.5, 0])
        midpoint = self.replay.snapshot(5 + math.sqrt(2) / 2)['vehicles']['AV1']
        self.assertAlmostEqual(midpoint['position'][0], 2.5)
        self.assertAlmostEqual(midpoint['position'][1], 0.5)
        self.assertAlmostEqual(midpoint['remaining_range'], 5 - math.sqrt(2) / 2)
        controller = ReplayController(self.replay, 8)
        controller.seek_time(4.5)
        self.assertEqual(controller.state['vehicles']['AV1']['active_leg']['leg_id'], 'AV1:L2')
        self.assertEqual(controller.state['vehicles']['AV1']['position'], [3.5, 0])

    def test_pause_resume_speed_and_horizon_stop(self) -> None:
        """Paused wall time changes nothing; resumed speed scales interpolation and horizon stops playback."""
        controller = ReplayController(self.replay, 5, speed=2)
        controller.advance(100)
        self.assertEqual(controller.state['time'], 5)
        controller.toggle_play()
        controller.advance(0.5)
        self.assertEqual(controller.state['time'], 6)
        controller.toggle_play()
        controller.advance(100)
        self.assertEqual(controller.state['time'], 6)
        controller.toggle_play()
        controller.advance(100)
        self.assertEqual(controller.state['time'], 12)
        self.assertTrue(controller.paused)
        controller.toggle_play()
        self.assertTrue(controller.paused)
        controller.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 34)

    def test_refresh_cadence_does_not_choose_events_or_motion(self) -> None:
        """Different frame intervals reaching time nine produce the same recorded state and actual position."""
        first, second = ReplayController(self.replay, 5), ReplayController(self.replay, 5)
        first.toggle_play()
        second.toggle_play()
        first.advance(4)
        for _ in range(8):
            second.advance(0.5)
        self.assertEqual(first.state, second.state)

    def test_held_slot_waiting_load_and_each_plant_jobs(self) -> None:
        """Replay finite shared stock and preserve separate RF1/RF2 station maps with identical station IDs."""
        finite = RunReplay(ROOT / 'tests' / 'fixtures' / 'checkpoint_3a').snapshot(5)
        self.assertEqual(finite['vehicles']['AV2']['load'], ['P2'])
        self.assertEqual(finite['vehicles']['AV2']['status'], 'waiting_to_unload')
        self.assertEqual(finite['plants']['RF1']['inventory'], [])
        self.assertEqual(finite['plants']['RF1']['held_slots'], ['P1'])
        self.assertEqual(finite['plants']['RF1']['waiting_vehicles'], ['AV2'])
        network = RunReplay(ROOT / 'tests' / 'fixtures' / 'checkpoint_4c').snapshot(4)
        self.assertIsNone(network['plants']['RF1']['workstations']['WS1'])
        self.assertEqual(network['plants']['RF2']['workstations']['WS1']['product_id'], 'P1')

    def test_partial_delivery_clears_local_unload_queue_with_other_load_onboard(self) -> None:
        """Finishing RF1 unloading clears its queue while a committed RF2 product stays on the AV."""
        scenario = normalize_view_scenario(json.loads((ROOT / 'tests' / 'fixtures' / 'checkpoint_4c' / 'scenario.json').read_text()))
        scenario['products'].append({**scenario['products'][0], 'id': 'P2'})
        state = initial_view_state(scenario)
        state['products']['P1']['planned_destination'] = 'RF1'
        state['products']['P2']['planned_destination'] = 'RF2'
        state['vehicles']['AV1'].update(load=['P1', 'P2'], location='RF1', status='waiting_to_unload')
        state['plants']['RF1']['waiting_vehicles'] = ['AV1']
        apply_view_event(state, {'sequence': 1, 'time': 4, 'event_type': 'plant_admission',
            'product_id': 'P1', 'vehicle_id': 'AV1', 'plant_id': 'RF1'})
        self.assertEqual(state['vehicles']['AV1']['load'], ['P2'])
        self.assertEqual(state['vehicles']['AV1']['status'], 'idle')
        self.assertEqual(state['plants']['RF1']['waiting_vehicles'], [])

    def test_all_saved_formats_recycling_and_terminal_counts(self) -> None:
        """Load retained saved formats and match terminal products, onboard loads and plant stock."""
        for directory in (ROOT / 'tests' / 'fixtures').iterdir():
            if not (directory / 'run_manifest.json').exists():
                continue
            if json.loads((directory / 'scenario.json').read_bytes())['format_version'] == 1:
                continue  # Retired evidence has separate rejection coverage.
            with self.subTest(fixture=directory.name):
                replay = RunReplay(directory)
                state = replay.snapshot(replay.horizon)
                final = json.loads((directory / 'final_state.json').read_text())
                for product_id, product in final['products'].items():
                    if product['state'] in ('remanufactured', 'recycled', 'unreleased'):
                        self.assertEqual(state['products'][product_id]['state'], product['state'])
                for vehicle_id, vehicle in final['vehicles'].items():
                    self.assertEqual(state['vehicles'][vehicle_id]['load'], vehicle['load'])
                for plant_id, plant in state['plants'].items():
                    actual = final.get('plant_states', {}).get(plant_id, final)
                    self.assertEqual(sorted(plant['inventory']), sorted(actual['inventory']))

    def test_horizon_mid_leg_uses_actual_travel(self) -> None:
        """Retain the active leg at a truncated horizon instead of snapping to its destination."""
        directory = self.root / 'mid-leg'
        run_once(ROOT / 'tests' / 'inputs' / 'checkpoint_3c_mid_leg.json', directory)
        replay = RunReplay(directory)
        state = replay.snapshot(replay.horizon)
        final = json.loads((directory / 'final_state.json').read_text())
        self.assertEqual(state['vehicles']['AV1']['position'], final['vehicles']['AV1']['position'])
        self.assertAlmostEqual(state['vehicles']['AV1']['remaining_range'], final['vehicles']['AV1']['remaining_range'])
        self.assertIsNotNone(state['vehicles']['AV1']['active_leg'])

    def test_sparse_checkpoints_and_snapshot_isolation(self) -> None:
        """Seek through more than one hundred release events and keep cached state independent of returned copies."""
        config = json.loads((ROOT / 'tests' / 'inputs' / 'checkpoint_5.json').read_text())
        config.update(vehicles=[], trips=[])
        config['generation']['arrival_rate'] = 100
        path = self.root / 'many.json'
        path.write_text(json.dumps(config))
        directory = self.root / 'many'
        run_once(path, directory, seed=42)
        replay = RunReplay(directory)
        self.assertGreater(len(replay.events), 100)
        expected = replay.snapshot(replay.horizon)
        altered = replay.snapshot(replay.horizon)
        altered['vehicles'].clear()
        altered['products'].clear()
        altered['plants'].clear()
        self.assertEqual(replay.snapshot(replay.horizon), expected)
        self.assertIn(100, replay.checkpoints)

    def test_replay_rejects_wrong_hash_order_and_unknown_records(self) -> None:
        """Reject source mismatch, reordered events and unsupported transitions rather than drawing invented state."""
        for mode in ('hash', 'order', 'unknown'):
            with self.subTest(mode=mode):
                directory = self.root / mode
                shutil.copytree(FIXTURE, directory)
                if mode == 'hash':
                    path = directory / 'scenario.json'
                    path.write_bytes(path.read_bytes() + b'\n')
                else:
                    path = directory / 'events.jsonl'
                    events = [json.loads(line) for line in path.read_text().splitlines()]
                    if mode == 'order':
                        events[0]['sequence'] = 2
                    else:
                        events[0]['event_type'] = 'unknown_transition'
                    path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                with self.assertRaises(ValueError):
                    RunReplay(directory)

    def test_processing_history_is_cursor_scoped_and_undoes_completion(self) -> None:
        """Keep completed jobs only after their completion records and restore active work when undoing them."""
        controller = ReplayController(self.replay)
        controller.seek_event(29)
        plant = controller.state['plants']['RF1']
        self.assertEqual(len(plant['processing_history']['WS1']), 1)
        self.assertEqual(plant['processing_history']['WS1'][0]['product_id'], 'P1')
        self.assertEqual(plant['processing_history']['WS1'][0]['end_time'], self.replay.events[28]['time'])
        self.assertIsNone(plant['workstations']['WS1'])
        controller.step_previous_event()
        plant = controller.state['plants']['RF1']
        self.assertEqual(plant['processing_history']['WS1'], [])
        self.assertEqual(plant['workstations']['WS1']['product_id'], 'P1')
        controller.step_next_event()
        self.assertEqual(len(controller.state['plants']['RF1']['processing_history']['WS1']), 1)
        altered = self.replay.snapshot(self.replay.horizon)
        altered['plants']['RF1']['processing_history']['WS1'][0]['end_time'] = 999
        self.assertNotEqual(self.replay.snapshot(self.replay.horizon)['plants']['RF1']['processing_history']['WS1'][0]['end_time'], 999)

    def test_invalid_seek_speed_and_elapsed_inputs(self) -> None:
        """Reject nonfinite/out-of-bounds navigation while keeping the current display state intact."""
        controller = ReplayController(self.replay, 5)
        expected = copy.deepcopy(controller.state)
        for value in (-1, 13, math.nan, math.inf):
            with self.assertRaises(ValueError):
                controller.seek_time(value)
        for value in (0, 35, True):
            with self.assertRaises(ValueError):
                controller.seek_event(value)
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                controller.advance(value)
        for speed in (0, -1, math.nan):
            with self.assertRaises(ValueError):
                ReplayController(self.replay, speed=speed)
        self.assertEqual(controller.state, expected)


    def test_previous_event_reaches_initial_state_and_stops_there(self) -> None:
        """Undo the first time-zero record, keep cursor zero stable, and advance one record again."""
        controller = ReplayController(self.replay)
        controller.seek_event(1)
        controller.step_previous_event()
        self.assertEqual(controller.state['events_applied'], 0)
        self.assertEqual(controller.state['time'], 0)
        self.assertIsNone(controller.state['last_event'])
        self.assertTrue(all(product['state'] == 'unreleased' for product in controller.state['products'].values()))
        self.assertEqual(controller.state['vehicles']['AV1']['position'], [0, 0])
        self.assertIsNone(controller.state['vehicles']['AV1']['active_leg'])
        expected = copy.deepcopy(controller.state)
        controller.step_previous_event()
        self.assertEqual(controller.state, expected)
        controller.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 1)

    def test_previous_event_uses_sequences_at_identical_times_and_pauses(self) -> None:
        """Undo only P2's pickup at the same arrival time and recover exactly the earlier microstate."""
        controller = ReplayController(self.replay)
        controller.seek_event(18)
        time = controller.state['time']
        controller.toggle_play()
        self.assertFalse(controller.paused)
        controller.step_previous_event()
        self.assertTrue(controller.paused)
        self.assertEqual(controller.state['last_event']['sequence'], 17)
        self.assertEqual(controller.state['time'], time)
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1'])
        controller.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 18)
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1', 'P2'])
        controller.seek_time(8)
        count = controller.state['events_applied']
        controller.step_previous_event()
        self.assertEqual(controller.state['events_applied'], count - 1)



@unittest.skipUnless(HAS_MATPLOTLIB, 'Install requirements-visualization.txt to run rendering tests')
class VisualizationTests(unittest.TestCase):
    """Exercise the actual viewer and child CLIs with a headless backend and isolated writable caches."""

    # Prepare separate test data and inspect results

    def setUp(self) -> None:
        """Allocate clean output/cache folders and select Agg before importing any plotting API."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = {**os.environ, 'MPLBACKEND': 'Agg', 'MPLCONFIGDIR': str(self.root / 'mpl-cache'),
                            'XDG_CACHE_HOME': str(self.root / 'cache')}
        self.environment_patch = patch.dict(os.environ, self.environment)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        import matplotlib
        matplotlib.use('Agg')

    def call_cli(self, arguments: list[str], expected_exit: int = 0) -> subprocess.CompletedProcess[str]:
        """Run a Python entry point in a separate Agg process and assert its expected exit status."""
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        return result

    def assert_processing_matches_cursor(self, viewer: MatplotlibViewer) -> None:
        """Independently pair applied start/completion records and compare every row's displayed interval endpoints."""
        state, replay = viewer.controller.state, viewer.controller.replay
        rows = [(plant['id'], station['id']) for plant in replay.scenario['plants'] for station in plant['workstations']]
        expected = {row: [] for row in rows}
        active = {}
        for event in replay.events[:state['events_applied']]:
            if event['event_type'] not in ('operation_start', 'operation_complete'):
                continue
            key = (event['plant_id'], event['workstation_id'], event['product_id'], event['operation_index'])
            if event['event_type'] == 'operation_start':
                job = {'start': event['time'], 'end': None}
                active[key] = job
                expected[key[:2]].append(job)
            else:
                active.pop(key)['end'] = event['time']
        self.assertEqual(len(viewer.processing_bands), len(rows))
        for row, collection in zip(rows, viewer.processing_bands):
            paths = collection.get_paths()
            self.assertEqual(len(paths), len(expected[row]), row)
            for polygon, job in zip(paths, expected[row]):
                self.assertAlmostEqual(min(polygon.vertices[:, 0]), job['start'])
                self.assertAlmostEqual(max(polygon.vertices[:, 0]), state['time'] if job['end'] is None else job['end'])

    # Check the expected model or evidence behavior

    def test_visualized_and_headless_runs_have_identical_evidence_and_pass(self) -> None:
        """Run the same resolved inputs/policy through real CLI modes and independently verify both artifacts."""
        common = ['-m', 'simulation', '--resolved-scenario', str(ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json'),
                  '--policy', 'dynamic_quality_unaware']
        headless, visual = self.root / 'headless', self.root / 'visual'
        self.call_cli([*common, '--output', str(headless)])
        image = self.root / 'time-five.png'
        self.call_cli([*common, '--output', str(visual), '--visualize', '--view-time', '5', '--render', str(image)])
        self.assertTrue(image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        for filename in ('scenario.json', 'events.jsonl', 'dispatch_decisions.jsonl', 'summary.json', 'final_state.json'):
            self.assertEqual((headless / filename).read_bytes(), (visual / filename).read_bytes())
        for directory in (headless, visual):
            self.call_cli([str(ROOT / 'verify_simulation.py'), str(directory)])
            self.assertEqual(json.loads((directory / 'verification.json').read_text())['status'], 'PASS')

    def test_existing_replay_render_is_read_only_and_event_exact(self) -> None:
        """Render a saved completion microstate without modifying any evidence or needing another simulation."""
        directory = self.root / 'saved'
        shutil.copytree(FIXTURE, directory)
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}
        image = self.root / 'completion.png'
        self.call_cli(['-m', 'tools.viewer', str(directory), '--event', '29', '--render', str(image)])
        self.assertGreater(image.stat().st_size, 1000)
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()})
        self.assertEqual(RunReplay(directory).snapshot(9.650281539872886, 29)['last_event']['sequence'], 29)

    def test_buttons_seek_keyboard_and_timer_callbacks(self) -> None:
        """Exercise the same GUI callbacks under Agg, including pause, stepping, focus guards and invalid entry."""
        from tools.viewer import MatplotlibViewer
        controller = ReplayController(RunReplay(FIXTURE), 5)
        viewer = MatplotlibViewer(controller)
        self.addCleanup(viewer.close)
        viewer.toggle_play()
        self.assertFalse(controller.paused)
        with patch('tools.viewer.time.perf_counter', return_value=viewer.last_wall_time + 0.5):
            viewer.on_timer()
        self.assertEqual(controller.state['time'], 5.5)
        viewer.seek_time('5')
        self.assertTrue(controller.paused)
        viewer.step_next_event()
        viewer.step_next_event()
        self.assertEqual(controller.state['vehicles']['AV1']['load'], ['P1', 'P2'])
        viewer.seek_event('29')
        self.assertEqual(controller.state['last_event']['sequence'], 29)
        viewer.on_key(SimpleNamespace(key='right'))
        self.assertEqual(controller.state['last_event']['sequence'], 30)
        viewer.time_box.capturekeystrokes = True
        viewer.on_key(SimpleNamespace(key='n'))
        self.assertEqual(controller.state['last_event']['sequence'], 30)
        viewer.time_box.capturekeystrokes = False
        viewer.seek_time('not-a-time')
        self.assertIn('could not convert', viewer.message.get_text())
        viewer.seek_event('9999')
        self.assertIn('between', viewer.message.get_text())
        viewer.restart()
        self.assertEqual(controller.state['time'], 0)
        self.assertTrue(controller.paused)
        viewer.toggle_play()
        with patch('tools.viewer.time.perf_counter', return_value=viewer.last_wall_time + 20):
            viewer.on_timer()
        self.assertEqual(controller.state['time'], 12)
        self.assertTrue(controller.paused)
        self.assertEqual(viewer.play_button.label.get_text(), 'Resume')
        with patch.object(viewer, 'redraw') as redraw, patch(
                'tools.viewer.time.perf_counter', return_value=viewer.last_wall_time + 1):
            viewer.on_timer()
            redraw.assert_not_called()
        with self.assertRaises(ValueError):
            viewer.show()
        with self.assertRaises(ValueError):
            viewer.save_image(self.root / 'events.jsonl')

    def test_render_all_plants_and_jobs_in_details(self) -> None:
        """Draw distinct RF1/RF2 inventories and active jobs even when workstation names coincide."""
        from tools.viewer import MatplotlibViewer
        replay = RunReplay(ROOT / 'tests' / 'fixtures' / 'checkpoint_4c')
        viewer = MatplotlibViewer(ReplayController(replay, 4))
        self.addCleanup(viewer.close)
        text = viewer.detail_axes.texts[0].get_text()
        self.assertIn('RF1 | inventory', text)
        self.assertIn('RF2 | inventory', text)
        self.assertIn('P1 [WS1] op 1', text)
        viewer.save_image(self.root / 'network.png')
        self.assertTrue((self.root / 'network.png').exists())

    def test_repeated_workstation_label_advances_only_on_completion(self) -> None:
        """Keep both visits in order and restore the full label when seeking back into the first visit."""
        from tools.viewer import MatplotlibViewer
        scenario = single_product_scenario()
        scenario['products'][0]['operations'] = [1, 3, 1]
        scenario['plant']['workstations'][0]['windows'].append([12, 14])
        scenario_path = self.root / 'repeated.json'
        scenario_path.write_text(json.dumps(scenario))
        directory = self.root / 'repeated'
        run_once(scenario_path, directory)
        viewer = MatplotlibViewer(ReplayController(RunReplay(directory)))
        self.addCleanup(viewer.close)

        for time, released_product in (
            ('0', 'P1 [WS1 → WS3 → WS1]: at_collection_point'),
            ('4', 'P1 [WS1 → WS3 → WS1]: processing'),
            ('6', 'P1 [WS3 → WS1]: in_plant_inventory'),
            ('12', 'P1 [WS1]: processing'),
            ('14', 'P1: remanufactured'),
            ('4', 'P1 [WS1 → WS3 → WS1]: processing'),
        ):
            with self.subTest(time=time, label=released_product):
                viewer.seek_time(time)
                self.assertIn('  ' + released_product, viewer.detail_axes.texts[0].get_text().splitlines())

    def test_nine_saved_pilot_timetable_rows_windows_and_moving_marker(self) -> None:
        """Check readable saved-window rows for all nine stations and marker motion through playback/backward seeks."""
        from tools.viewer import MatplotlibViewer
        directory = self.root / 'pilot'
        run_once(ROOT / 'configs' / 'three_site_pilot.json', directory, seed=42, policy='dynamic_quality_aware')
        saved = json.loads((directory / 'scenario.json').read_text())
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        controller = ReplayController(RunReplay(directory), 10)
        viewer = MatplotlibViewer(controller)
        self.addCleanup(viewer.close)
        labels = [f"{plant['id']} / {station['id']}" for plant in saved['plants'] for station in plant['workstations']]
        self.assertEqual(len(labels), 9)
        self.assertEqual([label.get_text() for label in viewer.timetable_axes.get_yticklabels()], labels)
        viewer.figure.canvas.draw()
        renderer = viewer.figure.canvas.get_renderer()
        bounds = [label.get_window_extent(renderer) for label in viewer.timetable_axes.get_yticklabels()]
        for index, rectangle in enumerate(bounds):
            self.assertGreaterEqual(rectangle.x0, viewer.figure.bbox.x0)
            self.assertLessEqual(rectangle.x1, viewer.figure.bbox.x1)
            self.assertGreaterEqual(rectangle.y0, viewer.figure.bbox.y0)
            self.assertLessEqual(rectangle.y1, viewer.figure.bbox.y1)
            self.assertFalse(any(rectangle.overlaps(other) for other in bounds[index + 1:]))
        stations = [station for plant in saved['plants'] for station in plant['workstations']]
        collections = list(viewer.availability_bands)
        self.assertEqual(len(collections), 9)
        for collection, station in zip(collections, stations):
            self.assertEqual(len(collection.get_paths()), len(station['windows']))
            for polygon, (opening, closing) in zip(collection.get_paths(), station['windows']):
                self.assertAlmostEqual(min(polygon.vertices[:, 0]), opening)
                self.assertAlmostEqual(max(polygon.vertices[:, 0]), closing)
        self.assertEqual(list(viewer.timetable_marker.get_xdata()), [10, 10])
        viewer.toggle_play()
        with patch('tools.viewer.time.perf_counter', return_value=viewer.last_wall_time + 0.1):
            viewer.on_timer()
        self.assertAlmostEqual(viewer.timetable_marker.get_xdata()[0], 10.1)
        sequence = controller.state['events_applied']
        viewer.step_previous_event()
        self.assertTrue(controller.paused)
        self.assertEqual(controller.state['events_applied'], sequence - 1)
        self.assertLess(controller.state['time'], 10)
        self.assertEqual(list(viewer.timetable_marker.get_xdata()), [controller.state['time']] * 2)
        viewer.seek_time('20')
        self.assertEqual(list(viewer.timetable_marker.get_xdata()), [20, 20])
        viewer.seek_event('1')
        viewer.step_previous_event()
        self.assertEqual(list(viewer.timetable_marker.get_xdata()), [0, 0])
        self.assertEqual(viewer.availability_bands, collections)
        viewer.save_image(self.root / 'nine-stations.png')
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})
        self.assertLess(viewer.previous_button.ax.get_position().x1, viewer.step_button.ax.get_position().x0)

    def test_previous_completion_restores_repeated_operation_before_same_time_start(self) -> None:
        """Undo a same-time start/return/completion and restore the completed operation in its remaining sequence."""
        from tools.viewer import MatplotlibViewer
        scenario = single_product_scenario()
        scenario['products'][0]['operations'] = [1, 3, 1]
        for station in scenario['plant']['workstations']:
            station['windows'] = [[0, 14]]
        path = self.root / 'continuous.json'
        path.write_text(json.dumps(scenario))
        directory = self.root / 'continuous'
        run_once(path, directory)
        replay = RunReplay(directory)
        viewer = MatplotlibViewer(ReplayController(replay))
        self.addCleanup(viewer.close)
        next_start = next(event for event in replay.events if event['event_type'] == 'operation_start' and event['operation_index'] == 1)
        complete = next(event for event in replay.events if event['event_type'] == 'operation_complete' and event['operation_index'] == 0)
        self.assertEqual(next_start['time'], complete['time'])
        viewer.seek_event(str(next_start['sequence']))
        self.assertIn('P1 [WS3 → WS1]: processing', viewer.detail_axes.texts[0].get_text())
        viewer.step_previous_event()
        self.assertEqual(viewer.controller.state['time'], complete['time'])
        self.assertEqual(viewer.controller.state['last_event']['event_type'], 'inventory_return')
        viewer.step_previous_event()
        self.assertEqual(viewer.controller.state['last_event']['event_type'], 'operation_complete')
        self.assertEqual(viewer.controller.state['products']['P1']['next_operation'], 1)
        viewer.on_key(SimpleNamespace(key='left'))
        self.assertEqual(viewer.controller.state['products']['P1']['next_operation'], 0)
        self.assertIn('P1 [WS1 → WS3 → WS1]: processing', viewer.detail_axes.texts[0].get_text())
        self.assertEqual(viewer.controller.state['plants']['RF1']['workstations']['WS1']['product_id'], 'P1')
        self.assertEqual(list(viewer.timetable_marker.get_xdata()), [viewer.controller.state['time']] * 2)

    def test_processing_bands_grow_complete_and_follow_same_time_record_order(self) -> None:
        """Draw no future duration, preserve finished history, and undo the next job/start/completion one record at a time."""
        from tools.viewer import MatplotlibViewer
        replay = RunReplay(FIXTURE)
        controller = ReplayController(replay)
        viewer = MatplotlibViewer(controller)
        self.addCleanup(viewer.close)
        viewer.seek_event('24')
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 0)
        viewer.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 25)
        self.assert_processing_matches_cursor(viewer)
        start = replay.events[24]['time']
        self.assertEqual(max(viewer.processing_bands[0].get_paths()[0].vertices[:, 0]), start)
        viewer.seek_time(str(start + 0.25))
        viewer.toggle_play()
        with patch('tools.viewer.time.perf_counter', return_value=viewer.last_wall_time + 0.25):
            viewer.on_timer()
        self.assertAlmostEqual(max(viewer.processing_bands[0].get_paths()[0].vertices[:, 0]), start + 0.5)
        self.assertLess(controller.state['time'], replay.events[24]['end_time'])
        viewer.seek_event('29')
        self.assertIsNone(controller.state['plants']['RF1']['workstations']['WS1'])
        self.assert_processing_matches_cursor(viewer)
        viewer.step_next_event()
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 1)
        viewer.step_next_event()
        self.assertEqual(controller.state['last_event']['sequence'], 31)
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 2)
        self.assert_processing_matches_cursor(viewer)
        viewer.seek_time('10')
        self.assert_processing_matches_cursor(viewer)
        viewer.step_previous_event()
        self.assertEqual(controller.state['last_event']['sequence'], 30)
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 1)
        viewer.step_previous_event()
        viewer.step_previous_event()
        self.assertEqual(controller.state['last_event']['sequence'], 28)
        self.assertEqual(controller.state['plants']['RF1']['processing_history']['WS1'], [])
        self.assertEqual(controller.state['plants']['RF1']['workstations']['WS1']['product_id'], 'P1')
        self.assert_processing_matches_cursor(viewer)
        viewer.seek_time('12')
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 2)
        self.assertLess(max(viewer.processing_bands[0].get_paths()[-1].vertices[:, 0]), 12)
        self.assert_processing_matches_cursor(viewer)

    def test_processing_bands_keep_facility_rows_distinct_and_availability_visible(self) -> None:
        """RF2/WS1 processing must not color RF1/WS1, and transparent overlays must leave the calendar artists intact."""
        from tools.viewer import MatplotlibViewer
        replay = RunReplay(ROOT / 'tests' / 'fixtures' / 'checkpoint_4c')
        viewer = MatplotlibViewer(ReplayController(replay, 4.5))
        self.addCleanup(viewer.close)
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 0)
        self.assertEqual(len(viewer.processing_bands[1].get_paths()), 1)
        self.assert_processing_matches_cursor(viewer)
        for collection in viewer.processing_bands:
            self.assertGreater(collection.get_alpha(), 0)
            self.assertLess(collection.get_alpha(), 1)
            self.assertGreater(collection.get_zorder(), viewer.availability_bands[0].get_zorder())
            self.assertLess(collection.get_zorder(), viewer.timetable_marker.get_zorder())
        scheduled = list(viewer.availability_bands)
        self.assertEqual([text.get_text() for text in viewer.timetable_axes.get_legend().get_texts()],
                         ['Scheduled availability', 'Actual processing'])
        viewer.seek_time('10')
        self.assertEqual(viewer.availability_bands, scheduled)
        self.assertEqual(len(viewer.processing_bands[0].get_paths()), 0)
        self.assertEqual(len(viewer.processing_bands[1].get_paths()), 1)
        viewer.seek_time('0')
        self.assertTrue(all(len(collection.get_paths()) == 0 for collection in viewer.processing_bands))
        self.assertEqual(len(viewer.timetable_axes.collections), len(scheduled) + len(viewer.processing_bands))

    def test_recorded_processing_over_closed_window_is_not_clipped(self) -> None:
        """Show logged processing across a deliberately conflicting saved calendar, without hiding the recorded interval."""
        from tools.viewer import MatplotlibViewer
        directory = self.root / 'calendar-conflict'
        shutil.copytree(FIXTURE, directory)
        scenario_path = directory / 'scenario.json'
        scenario = json.loads(scenario_path.read_text())
        # This is a presentation-only conflicting-evidence case, not a valid simulation fixture.
        scenario['plant']['workstations'][0]['windows'] = [[0, 9], [9.5, 12]]
        scenario_path.write_text(json.dumps(scenario))
        manifest_path = directory / 'run_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['scenario_sha256'] = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        viewer = MatplotlibViewer(ReplayController(RunReplay(directory), 9.25))
        self.addCleanup(viewer.close)
        self.assert_processing_matches_cursor(viewer)
        processing = viewer.processing_bands[0].get_paths()[0].vertices[:, 0]
        self.assertLess(min(processing), 9)
        self.assertEqual(max(processing), 9.25)
        self.assertEqual([(min(path.vertices[:, 0]), max(path.vertices[:, 0]))
                          for path in viewer.availability_bands[0].get_paths()], [(0, 9), (9.5, 12)])
        viewer.seek_event('29')
        self.assertGreater(max(viewer.processing_bands[0].get_paths()[0].vertices[:, 0]), 9.5)
        self.assert_processing_matches_cursor(viewer)
        viewer.save_image(self.root / 'processing-through-closure.png')

    def test_saved_three_site_processing_history_matches_event_prefix_and_is_read_only(self) -> None:
        """Check all nine rows across cached prefixes/backward completion steps without modifying the saved pilot."""
        from tools.viewer import MatplotlibViewer
        directory = self.root / 'processing-pilot'
        run_once(ROOT / 'configs' / 'three_site_pilot.json', directory, seed=42, policy='dynamic_quality_aware')
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        replay = RunReplay(directory)
        viewer = MatplotlibViewer(ReplayController(replay, 10))
        self.addCleanup(viewer.close)
        self.assertEqual(len(viewer.processing_bands), 9)
        self.assert_processing_matches_cursor(viewer)
        completion = next(event for event in replay.events if event['event_type'] == 'operation_complete')
        viewer.seek_event(str(completion['sequence']))
        self.assert_processing_matches_cursor(viewer)
        viewer.step_previous_event()
        self.assert_processing_matches_cursor(viewer)
        viewer.step_next_event()
        self.assert_processing_matches_cursor(viewer)
        self.assertGreater(len(replay.events), 100)
        viewer.seek_event('100')
        self.assert_processing_matches_cursor(viewer)
        viewer.seek_time(str(replay.horizon))
        self.assert_processing_matches_cursor(viewer)
        viewer.seek_time('10')
        self.assert_processing_matches_cursor(viewer)
        viewer.save_image(self.root / 'nine-processing-rows.png')
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})

    def test_optional_dependency_boundary_and_cli_guards(self) -> None:
        """Keep headless imports plotting-free and provide actionable setup/backend/argument errors."""
        from tools.viewer import open_viewer
        original_import = __import__
        def block_matplotlib(name: str, *arguments: object, **keywords: object) -> object:
            """Simulate an absent optional plotting package while preserving normal standard-library imports."""
            if name == 'matplotlib' or name.startswith('matplotlib.'):
                raise ImportError('injected missing optional package')
            return original_import(name, *arguments, **keywords)
        with patch('builtins.__import__', side_effect=block_matplotlib):
            with self.assertRaisesRegex(ValueError, 'requirements-visualization'):
                open_viewer(FIXTURE, render_path=self.root / 'missing.png')
        script = '''import sys
from simulation.runner import run_once
if any(name.startswith('matplotlib') for name in sys.modules):
    raise SystemExit('Headless runner imported optional Matplotlib')
'''
        self.call_cli(['-c', script])
        bad = self.root / 'bad'
        self.call_cli(['-m', 'simulation', '--resolved-scenario', str(ROOT / 'tests' / 'fixtures' / 'checkpoint_4b' / 'scenario.json'),
                       '--output', str(bad), '--view-time', '5'], 2)
        self.assertFalse(bad.exists())
        result = self.call_cli(['-m', 'tools.viewer', str(FIXTURE)], 1)
        self.assertIn('No desktop Matplotlib backend', result.stderr)


if __name__ == '__main__':
    unittest.main()
