"""Check indexed processing history against replay from the initial state.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
The reference calculations protect order, ownership or retained-memory rules;
these are regression checks, not general runtime benchmarks.
"""
from bisect import bisect_right
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

from simulation.runner import run_once
from tools.replay import ReplayController, RunReplay, apply_view_event, initial_view_state, interpolate_vehicles

from tests.single_product import single_product_scenario

ROOT = Path(__file__).resolve().parents[1]


def full_history_reference(replay: RunReplay, time: float, count: int | None = None) -> dict:
    """Return a separate snapshot by applying saved records from initial display state.

    ReplayHistoryMemoryTests use this reference without checkpoints or the completed
    history index. Do not change the replay's records or files.
    """
    state = initial_view_state(replay.scenario)
    count = bisect_right(replay.times, time) if count is None else count
    for event in replay.events[:count]:
        apply_view_event(state, event)
    interpolate_vehicles(state, time)
    return state


class ReplayHistoryMemoryTests(unittest.TestCase):
    """Exercise long completed histories and nine-row network state using temporary saved evidence."""

    # Prepare separate test data and inspect results

    @classmethod
    def setUpClass(cls) -> None:
        """Save a deterministic 600-operation history and seed-42 pilot once for read-only replay tests."""
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        scenario = single_product_scenario()
        scenario['horizon'] = 601
        scenario['products'][0].update(release_time=0, operations=[1] * 600)
        scenario['trips'][0]['departure_time'] = 0
        scenario['collection_point']['coordinates'] = scenario['plant']['coordinates'][:]
        scenario['plant']['workstations'] = [{'id': 'WS1', 'operation_id': 1, 'duration': 1, 'windows': [[0, 601]]}]
        source = cls.root / 'history.json'
        source.write_text(json.dumps(scenario) + '\n')
        run_once(source, cls.root / 'history')
        run_once(ROOT / 'configs/three_site_pilot.json', cls.root / 'pilot', seed=42, policy='dynamic_quality_aware')

    # Check the expected model or evidence behavior

    def test_randomized_prefixes_and_checkpoint_boundaries(self) -> None:
        """Compare complete snapshot JSON against full replay at fixed and reproducibly shuffled prefixes."""
        for name in ('history', 'pilot'):
            replay = RunReplay(self.root / name)
            prefixes = [0, 1, 99, 100, 101, len(replay.events)]
            prefixes += random.Random(2026).choices(range(len(replay.events) + 1), k=25)
            for count in prefixes:
                time = replay.times[count - 1] if count else 0
                with self.subTest(run=name, count=count):
                    expected = full_history_reference(replay, time, count)
                    self.assertEqual(json.dumps(replay.snapshot(time, count)), json.dumps(expected))

    def test_alternating_distant_time_seeks_and_initial_state(self) -> None:
        """Time seeks retain all same-time events and remain stable across repeated forward/backward jumps."""
        replay = RunReplay(self.root / 'history')
        for time in (601, 0, 300, 0, 600, 1, 601, 99.5, 100, 100.5):
            self.assertEqual(replay.snapshot(time), full_history_reference(replay, time))
        self.assertEqual(replay.snapshot(0, 0), initial_view_state(replay.scenario))
        self.assertGreater(replay.snapshot(0)['events_applied'], 0)

    def test_completion_and_start_at_same_time_remain_distinct(self) -> None:
        """Previous/next restores the completed operation before a same-time return and new start."""
        replay = RunReplay(self.root / 'history')
        completion = next(event for event in replay.events if event['event_type'] == 'operation_complete')
        controller = ReplayController(replay)
        controller.seek_event(completion['sequence'])
        self.assertEqual(controller.state['products']['P1']['next_operation'], 1)
        self.assertEqual(len(controller.state['plants']['RF1']['processing_history']['WS1']), 1)
        controller.step_previous_event()
        self.assertEqual(controller.state['products']['P1']['next_operation'], 0)
        self.assertEqual(controller.state['plants']['RF1']['processing_history']['WS1'], [])
        self.assertIsNotNone(controller.state['plants']['RF1']['workstations']['WS1'])
        controller.step_next_event()
        controller.step_next_event()
        controller.step_next_event()
        self.assertEqual(controller.state['time'], completion['time'])
        self.assertEqual(controller.state['last_event']['event_type'], 'operation_start')
        self.assertEqual(controller.state, full_history_reference(replay, controller.state['time'], controller.state['events_applied']))

    def test_history_is_not_copied_into_checkpoints(self) -> None:
        """Keep one immutable event pair per completion; checkpoints retain only current physical state."""
        replay = RunReplay(self.root / 'history')
        self.assertGreater(len(replay.checkpoints), 10)
        for checkpoint in replay.checkpoints.values():
            for plant in checkpoint['plants'].values():
                self.assertTrue(all(not history for history in plant['processing_history'].values()))
        self.assertEqual(sum(len(pairs) for pairs in replay._completed_operations.values()), 600)
        self.assertTrue(all(isinstance(pairs, tuple) for pairs in replay._completed_operations.values()))
        self.assertEqual(len(replay.snapshot(replay.horizon)['plants']['RF1']['processing_history']['WS1']), 600)

    def test_snapshot_mutation_cannot_change_records_or_future_snapshots(self) -> None:
        """Detach history, active jobs, last events and physical containers from all replay-owned input/cache data."""
        replay = RunReplay(self.root / 'history')
        events = copy.deepcopy(replay.events)
        expected = full_history_reference(replay, 300)
        changed = replay.snapshot(300)
        changed['plants']['RF1']['processing_history']['WS1'][0]['window'][0] = -999
        changed['plants']['RF1']['workstations']['WS1']['window'][0] = -999
        changed['last_event']['window'][0] = -999
        changed['products'].clear()
        changed['vehicles'].clear()
        self.assertEqual(replay.snapshot(300), expected)
        self.assertEqual(replay.events, events)
        self.assertEqual(replay.snapshot(1), full_history_reference(replay, 1))

    def test_network_render_matches_full_history_reference(self) -> None:
        """Compare Agg RGBA pixels and controls against full-history state for all nine workstation rows."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            from tools.viewer import MatplotlibViewer
        except ImportError:
            self.skipTest('Optional Matplotlib is not installed')
        replay = RunReplay(self.root / 'pilot')
        completion = next(event for event in replay.events if event['event_type'] == 'operation_complete')
        indexed = ReplayController(replay)
        indexed.seek_event(completion['sequence'])
        reference = ReplayController(replay)
        reference.state = full_history_reference(replay, indexed.state['time'], indexed.state['events_applied'])
        first, second = MatplotlibViewer(indexed), MatplotlibViewer(reference)
        self.addCleanup(first.pyplot.close, first.figure)
        self.addCleanup(second.pyplot.close, second.figure)
        first.figure.canvas.draw()
        second.figure.canvas.draw()
        self.assertEqual(first.figure.canvas.buffer_rgba().tobytes(), second.figure.canvas.buffer_rgba().tobytes())
        self.assertEqual(len(first.processing_bands), 9)
        first.step_previous_event(None)
        self.assertEqual(first.controller.state, full_history_reference(replay, first.controller.state['time'], first.controller.state['events_applied']))
        first.step_next_event(None)
        self.assertEqual(first.controller.state, indexed.replay.snapshot(completion['time'], completion['sequence']))
