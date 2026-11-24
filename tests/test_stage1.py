"""Check that type descriptions match the dictionary records actually saved.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from typing import Any

from simulation import state

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class RecordDefinitionTests(unittest.TestCase):
    """Keep required/optional keys accurate without adding runtime engine validation."""

    # Prepare separate test data and inspect results

    def assert_record_fields(self, schema: type, record: dict[str, Any]) -> None:
        """Check a saved dictionary against its record type's required/optional field names.

        The schema test calls this for real records. Read both without conversion or
        mutation; unittest assertions report mismatches.
        """
        required = schema.__required_keys__
        allowed = required | schema.__optional_keys__
        self.assertLessEqual(required, record.keys(), schema.__name__)
        self.assertLessEqual(record.keys(), allowed, schema.__name__)

    # Check the expected model or evidence behavior

    def test_schemas_match_fixed_dynamic_and_forecast_records(self) -> None:
        """Read audited fixtures and check physical/decision shapes, including optional range/forecast fields."""
        for name in ('checkpoint_3a', 'checkpoint_3c', 'checkpoint_4a', 'checkpoint_4b', 'checkpoint_4c'):
            with self.subTest(fixture=name):
                folder = FIXTURES / name
                scenario = json.loads((folder / 'scenario.json').read_text())
                final = json.loads((folder / 'final_state.json').read_text())
                definitions = scenario['products']
                for product in definitions:
                    self.assert_record_fields(state.ProductDefinition, product)
                for event in map(json.loads, (folder / 'events.jsonl').read_text().splitlines()):
                    details = {key: value for key, value in event.items()
                               if key not in ('sequence', 'time', 'event_type')}
                    if event['event_type'] == 'leg_departure':
                        schema = state.DynamicLeg if 'leg_id' in details else state.FixedLeg
                        self.assert_record_fields(schema, details)
                    elif event['event_type'] == 'operation_start':
                        self.assert_record_fields(state.Operation, details)
                for product in final['products'].values():
                    self.assert_record_fields(state.ProductState, product)
                    self.assert_record_fields(state.ProductMilestones, product['milestones'])
                for vehicle in final['vehicles'].values():
                    self.assert_record_fields(state.VehicleState, vehicle)
                plants = final.get('plant_states') or {'primary': {key: final[key] for key in
                    ('inventory', 'held_slots', 'waiting_vehicles', 'workstations')}}
                for plant in plants.values():
                    self.assert_record_fields(state.PlantContainers, plant)
                for decision in map(json.loads, (folder / 'dispatch_decisions.jsonl').read_text().splitlines()):
                    if scenario['format_version'] == 2:
                        for candidate in decision.get('candidates', []):
                            self.assert_record_fields(state.StaticCandidate, candidate)
                        continue
                    boundary = decision['policy_input']
                    aware = boundary.get('policy') == 'dynamic_quality_aware'
                    for request in boundary['requests']:
                        schema = state.AwarePolicyRequest if aware else state.PolicyRequest
                        self.assert_record_fields(schema, request)
                    for vehicle in boundary['vehicles']:
                        self.assert_record_fields(state.PolicyVehicle, vehicle)
                        for stop in vehicle['remaining_stops']:
                            self.assert_record_fields(state.RouteStop, stop)
                    for audit in decision['requests']:
                        self.assert_record_fields(state.RequestAudit, audit)
                        for candidate in audit['candidates']:
                            self.assert_record_fields(state.DynamicCandidate, candidate)
                            if 'projected_deliveries' in candidate:
                                for delivery in candidate['projected_deliveries']:
                                    self.assert_record_fields(state.ProjectedDelivery, delivery)


if __name__ == '__main__':
    unittest.main()
