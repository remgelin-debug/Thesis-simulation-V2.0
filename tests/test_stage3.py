"""Check the separation between routing choices and processing predictions.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
import copy
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import forecasting, routing
from simulation.config import as_dynamic_scenario
from simulation.engine import build_dynamic_state
from simulation.runner import run_once
from simulation.state import Record, ScenarioDefinitions, build_inventory_state
from tools.verify import verify_run

ROOT = Path(__file__).resolve().parents[1]
PROCESSING_ENTRY_POINTS = (
    'build_forecast_context', 'forecast_candidate',
    'build_dynamic_forecast_context', 'forecast_dynamic_insertion',
)


class StageThreeTests(unittest.TestCase):
    """Keep generated evidence temporary and call the same runner used by the CLI."""

    # Check the expected model or evidence behavior

    def test_unaware_execution_bypasses_processing_forecasts(self) -> None:
        """Make every processing entry point raise, then run unaware policies through both retained formats."""
        cases = [
            ('checkpoint_4a', 'static_quality_unaware'),
            ('checkpoint_3c', 'static_quality_unaware'),
            ('checkpoint_4a', 'dynamic_quality_unaware'),
            ('checkpoint_4b', 'dynamic_quality_unaware'),
        ]
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            guards = [
                stack.enter_context(patch.object(forecasting, name, side_effect=AssertionError(name)))
                for name in PROCESSING_ENTRY_POINTS
            ]
            for index, (fixture, policy) in enumerate(cases):
                with self.subTest(fixture=fixture, policy=policy):
                    folder = Path(temporary) / str(index)
                    run_once(ROOT / 'tests/fixtures' / fixture / 'scenario.json', folder, policy=policy)
                    self.assertEqual(verify_run(folder)['status'], 'PASS')
                    decisions = [json.loads(line) for line in (folder / 'dispatch_decisions.jsonl').read_text().splitlines()]
                    self.assertTrue(decisions)
                    for decision in decisions:
                        observation = json.dumps(decision['policy_input'])
                        for field in ('forecast_context', 'operations', 'workstations', 'windows', 'quality_class'):
                            self.assertNotIn('"' + field + '"', observation)
            for guard in guards:
                guard.assert_not_called()

    def test_aware_execution_calls_context_and_candidate_forecasts(self) -> None:
        """Spy on real static/dynamic predictions and check the selected product/destination."""
        cases = [
            ('checkpoint_4a', 'static_quality_aware', 'build_forecast_context', 'forecast_candidate'),
            ('checkpoint_4c', 'dynamic_quality_aware', 'build_dynamic_forecast_context', 'forecast_dynamic_insertion'),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for index, (fixture, policy, context_name, prediction_name) in enumerate(cases):
                with self.subTest(policy=policy), patch.object(
                    forecasting, context_name, wraps=getattr(forecasting, context_name),
                ) as context, patch.object(
                    forecasting, prediction_name, wraps=getattr(forecasting, prediction_name),
                ) as prediction:
                    folder = Path(temporary) / str(index)
                    run_once(ROOT / 'tests/fixtures' / fixture / 'scenario.json', folder, policy=policy)
                    self.assertGreater(context.call_count, 0)
                    self.assertGreater(prediction.call_count, 0)
                    decision = json.loads((folder / 'dispatch_decisions.jsonl').read_text().splitlines()[0])
                    if policy == 'static_quality_aware':
                        self.assertEqual(decision['decision']['assigned_products'], ['P2'])
                    else:
                        self.assertEqual(decision['proposals'][0]['destination'], 'RF2')
                    self.assertEqual(verify_run(folder)['status'], 'PASS')

    def test_static_selection_leaves_assignment_to_the_engine(self) -> None:
        """Evaluate fixed choices while preserving live state, observations and referenced definitions."""
        for policy in ('static_quality_unaware', 'static_quality_aware'):
            with self.subTest(policy=policy):
                scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4a/scenario.json').read_bytes())
                scenario['policy'] = policy
                definitions = ScenarioDefinitions.from_scenario(scenario)
                state = build_inventory_state(scenario, definitions)
                state.time = 1
                for part in state.products.values():
                    part.update(state='at_collection_point', location='CP1')
                original_definitions, original_state = copy.deepcopy(scenario), asdict(state)
                boundary = routing.build_static_policy_input(scenario, definitions, state, scenario['trips'][0])
                original_boundary = copy.deepcopy(boundary)
                selection = routing.choose_static_products(
                    boundary, payload_capacity=1, disposition='remanufacture', pickup_site='CP1',
                    range_feasible=True, quality_aware=policy == 'static_quality_aware',
                )
                self.assertEqual(selection['decision']['assigned_products'],
                                 ['P1'] if policy == 'static_quality_unaware' else ['P2'])
                selection['decision']['stops'].append('local-result-only')
                self.assertEqual(boundary, original_boundary)
                self.assertEqual(asdict(state), original_state)
                self.assertEqual(scenario, original_definitions)
                self.assertIs(definitions.products['P1'], scenario['products'][0])

    def test_dynamic_candidate_projections_are_independent_of_evaluation_order(self) -> None:
        """Project both plants in reverse order on the same context without changing any caller data."""
        scenario = as_dynamic_scenario(json.loads((ROOT / 'tests/fixtures/checkpoint_4c/scenario.json').read_bytes()))
        scenario['policy'] = 'dynamic_quality_aware'
        definitions = ScenarioDefinitions.from_scenario(scenario)
        state = build_dynamic_state(scenario, definitions)
        state.products['P1'].update(state='at_collection_point', location='CP1')
        original_scenario, original_state = copy.deepcopy(scenario), asdict(state)
        boundary = routing.build_dynamic_policy_input(scenario, definitions, state)
        candidates = routing.enumerate_dynamic_candidates(boundary, boundary['requests'][0])
        original_boundary, original_candidates = copy.deepcopy(boundary), copy.deepcopy(candidates)
        predictions: dict[str, Record] = {}
        for candidate in candidates:
            self.assertTrue(candidate['feasible'])
            predictions[candidate['candidate_id']] = forecasting.forecast_dynamic_insertion(boundary, candidate)
        for candidate in reversed(candidates):
            self.assertEqual(forecasting.forecast_dynamic_insertion(boundary, candidate),
                             predictions[candidate['candidate_id']])
        self.assertEqual(boundary, original_boundary)
        self.assertEqual(candidates, original_candidates)
        self.assertEqual(asdict(state), original_state)
        self.assertEqual(scenario, original_scenario)


if __name__ == '__main__':
    unittest.main()
