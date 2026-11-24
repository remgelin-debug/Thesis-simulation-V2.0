"""Check definition lookups, selective copying and forecast ownership.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
import json
from pathlib import Path
import unittest

from simulation.config import as_dynamic_scenario, validate_network_generation, validate_scenario
from simulation.engine import build_dynamic_state
from simulation.forecasting import build_dynamic_forecast_context, build_forecast_context
from simulation.state import ScenarioDefinitions, physical_snapshot

ROOT = Path(__file__).resolve().parents[1]


class CopyGuard:
    """Fail if code traverses a discarded field while preparing a forecast."""

    def __deepcopy__(self, memo: dict) -> object:
        """Fail if copy.deepcopy visits data that the caller should have left out.

        The stage-two tests place this object in discarded logs/history or metadata.
        Raise AssertionError deliberately; return no copy and change no files.
        """
        raise AssertionError('Copied discarded data')


class StageTwoTests(unittest.TestCase):
    # Check the expected model or evidence behavior

    def test_forecasts_skip_future_products_and_logs_before_copying(self) -> None:
        """Exercise both context builders with data that would fail a whole-state copy."""
        for checkpoint, builder in [('checkpoint_4a', build_forecast_context),
                                    ('checkpoint_4c', build_dynamic_forecast_context)]:
            with self.subTest(checkpoint=checkpoint):
                scenario = json.loads((ROOT / 'tests/fixtures' / checkpoint / 'scenario.json').read_bytes())
                execution = as_dynamic_scenario(scenario)
                state = build_dynamic_state(execution, ScenarioDefinitions.from_scenario(execution))
                state.events.append({'event_type': 'dispatch', 'discarded': CopyGuard()})
                state.decisions.append({'discarded': CopyGuard()})
                for product in state.products.values():
                    product['history'].append(CopyGuard())
                context = builder(scenario if checkpoint == 'checkpoint_4a' else execution, state)
                self.assertEqual(context['state']['products'], {})
                self.assertEqual(context['scenario']['products'], [])
                self.assertNotIn('events', context['state'])
                self.assertNotIn('decisions', context['state'])
                # Final snapshots include unreleased products but still skip both logs.
                for product in state.products.values():
                    product['history'].clear()
                exported = physical_snapshot(state, released_only=False)
                self.assertEqual(exported['products'], state.products)
                self.assertIsNot(exported['products'], state.products)

    def test_validation_reads_declarations_without_copying_or_rewriting(self) -> None:
        """Check real network starts/origins, required fields and generated-calendar boundaries."""
        scenario = json.loads((ROOT / 'tests/fixtures/checkpoint_4c/scenario.json').read_bytes())
        scenario['collection_points'].append({'id': 'CP2', 'coordinates': [2, 3]})
        scenario['products'][0]['origin'] = 'CP2'
        scenario['vehicles'][0]['initial_site'] = 'RF2'
        scenario['metadata'] = CopyGuard()
        validate_scenario(scenario)
        self.assertEqual(scenario['products'][0]['origin'], 'CP2')
        self.assertEqual(scenario['vehicles'][0]['initial_site'], 'RF2')
        del scenario['seed_provenance']
        with self.assertRaises(KeyError):
            validate_scenario(scenario)
        pilot = json.loads((ROOT / 'configs/three_site_pilot.json').read_bytes())
        pilot['metadata'] = CopyGuard()
        del pilot['plants'][0]['coordinates']
        validate_network_generation(pilot)
        self.assertNotIn('coordinates', pilot['plants'][0])
        self.assertNotIn('windows', pilot['plants'][0]['workstations'][0])

    def test_lookup_identity_and_projected_plant_ownership(self) -> None:
        """Check that lookups refer to fixed definitions and snapshots own separate state.

        Primary plant fields must still share containers inside the snapshot; changing
        that snapshot must not change the live plant.
        """
        scenario = as_dynamic_scenario(json.loads((ROOT / 'tests/fixtures/checkpoint_4c/scenario.json').read_bytes()))
        definitions = ScenarioDefinitions.from_scenario(scenario)
        self.assertIs(definitions.sites['RF2'], scenario['plants'][1])
        self.assertIs(definitions.plants['RF2'], definitions.sites['RF2'])
        self.assertIs(definitions.vehicles['AV1'], scenario['vehicles'][0])
        self.assertIs(definitions.products['P1'], scenario['products'][0])
        state = build_dynamic_state(scenario, definitions)
        snapshot = physical_snapshot(state, released_only=True)
        snapshot['held_slots'].append('reservation')
        self.assertEqual(snapshot['plant_states']['RF1']['held_slots'], ['reservation'])
        self.assertEqual(state.plant_states['RF1']['held_slots'], [])
