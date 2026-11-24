"""Check exact serialization, copy ownership and conservation-check error order.

unittest discovery (or direct module invocation) calls each test_ method.
Where present, setUp creates fresh temporary data before each case and helpers
prepare runs or inspect results. Deliberate state/evidence changes stay in owned
copies or temporary folders; historical fixtures are read only.
"""
import copy
from collections.abc import Callable
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from simulation import generator
from simulation.config import as_dynamic_scenario
from simulation.engine import build_dynamic_state
from simulation.plant import check_inventory_state, check_plant_network
from simulation.state import (
    InventoryRunState, PhysicalStateError, ScenarioDefinitions,
    initialize_plant_states, physical_snapshot,
)
from tools import verify
from tests.verifier_cli import verify_run_via_cli

ROOT = Path(__file__).resolve().parents[1]


class CanonicalScenarioTests(unittest.TestCase):
    """Pin byte/order/ownership guarantees rather than regenerate audited fixtures."""

    # Check the expected model or evidence behavior

    def test_explicit_bytes_order_and_owned_decoded_graph(self) -> None:
        """Canonicalization escapes Unicode, preserves floats and detaches sorted nested records."""
        scenario = {'z': {'b': 2, 'a': 1}, 'value': 1.0, 'negative': -0.0, 'a': 'é'}
        before = copy.deepcopy(scenario)
        resolved, encoded = generator._canonical_resolved_scenario(scenario)
        self.assertEqual(encoded, b'{\n  "a": "\\u00e9",\n  "negative": -0.0,\n'
                         b'  "value": 1.0,\n  "z": {\n    "a": 1,\n    "b": 2\n  }\n}\n')
        self.assertEqual(list(resolved), ['a', 'negative', 'value', 'z'])
        self.assertEqual(list(resolved['z']), ['a', 'b'])
        self.assertIsInstance(resolved['value'], float)
        resolved['z']['a'] = 999
        self.assertEqual(scenario, before)

    def test_nonfinite_values_still_fail_and_large_seeds_stay_exact(self) -> None:
        """Reject nonstandard physical JSON numbers while retaining full integer metadata precision."""
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                generator._canonical_resolved_scenario({'value': value})
        seed = 10 ** 400 + 123
        resolved, encoded = generator._canonical_resolved_scenario({'seed': seed})
        self.assertEqual(resolved['seed'], seed)
        self.assertEqual(encoded, ('{\n  "seed": ' + str(seed) + '\n}\n').encode('utf-8'))

    def test_both_preparation_exits_validate_then_canonicalize_once(self) -> None:
        """Keep both formats' weighted/fixed/empty generation and validation on the real orchestration path."""
        sources = ('tests/inputs/checkpoint_3b_recycling.json', 'configs/three_site_pilot.json')
        with tempfile.TemporaryDirectory() as temporary:
            for source in sources:
                for variant in ('weighted', 'fixed', 'empty', 'large-seed', 'invalid'):
                    with self.subTest(source=source, variant=variant):
                        config = json.loads((ROOT / source).read_bytes())
                        settings = ([config['generation']] if config['format_version'] == 2 else
                                    [point['generation'] for point in config['collection_points']])
                        for distribution in settings:
                            if variant in ('fixed', 'invalid'):
                                distribution['batch_size'] = 2 if variant == 'fixed' else 0
                            elif variant == 'empty':
                                distribution['arrival_rate'] = 0
                        path = Path(temporary) / 'configuration.json'
                        path.write_text(json.dumps(config), encoding='utf-8')
                        original_bytes = path.read_bytes()
                        seed = 10 ** 400 + 123 if variant == 'large-seed' else 42
                        with patch.object(generator, '_canonical_resolved_scenario',
                                          wraps=generator._canonical_resolved_scenario) as canonicalize:
                            if variant == 'invalid':
                                with self.assertRaises(ValueError):
                                    generator.prepare_scenario(path, seed)
                                canonicalize.assert_not_called()
                            else:
                                scenario, encoded = generator.prepare_scenario(path, seed)
                                canonicalize.assert_called_once()
                                self.assertEqual(scenario, json.loads(encoded))
                                self.assertEqual(scenario['seed_provenance']['master_seed'], seed)
                                if variant == 'empty':
                                    self.assertEqual(scenario['products'], [])
                                    self.assertEqual(scenario['arrival_batches'], [])
                        self.assertEqual(path.read_bytes(), original_bytes)


class VerifierCopyScopeTests(unittest.TestCase):
    """Check independent per-point evidence ownership without changing saved fixtures."""

    # Prepare separate test data and inspect results

    def scenario(self) -> dict:
        """Return owned generated pilot evidence with an extra retained nested declaration."""
        scenario, _ = generator.prepare_scenario(ROOT / 'configs/three_site_pilot.json', 42)
        scenario['metadata'] = {'products': [{'kept': 'original'}]}
        return scenario

    # Check the expected model or evidence behavior

    def test_source_evidence_is_unchanged_for_valid_and_invalid_generation(self) -> None:
        """Audit valid samples, altered products and invalid settings without changing source data."""
        for variant in ('valid', 'product', 'quality'):
            with self.subTest(variant=variant):
                scenario = self.scenario()
                if variant == 'product':
                    scenario['products'][0]['quality_class'] = 'forged'
                elif variant == 'quality':
                    scenario['quality_classes'][0]['weight'] = -1
                original = copy.deepcopy(scenario)
                findings = []
                if variant == 'quality':
                    with self.assertRaisesRegex(verify.EvidenceProblem, 'quality weights'):
                        verify.audit_network_generation(scenario, findings)
                else:
                    verify.audit_network_generation(scenario, findings)
                    self.assertEqual(bool(findings), variant == 'product')
                self.assertEqual(scenario, original)

    def test_nested_expected_and_observed_diagnostic_ownership(self) -> None:
        """Expected quality routes stay detached; observed replacement routes retain their original borrowing."""
        scenario = self.scenario()
        changed_product = scenario['products'][0]
        changed_product['quality_class'] = 'forged'
        original = copy.deepcopy(scenario)
        findings = []
        verify.audit_network_generation(scenario, findings)
        finding = next(item for item in findings if item['rule'] == 'generation.products')
        index = next(index for index, product in enumerate(finding['observed'])
                     if product['quality_class'] == 'forged')
        expected = finding['expected'][index]
        quality = next(quality for quality in scenario['quality_classes']
                       if quality['id'] == expected['quality_class'])
        self.assertIsNot(expected['operations'], quality['operations'])
        self.assertIs(finding['observed'][index]['operations'], changed_product['operations'])
        expected['operations'].append('expected-only')
        self.assertEqual(scenario, original)

    def test_replaced_lists_are_skipped_but_retained_declarations_are_deep(self) -> None:
        """Skip only top-level replaced lists while preserving nested retained values and source immutability."""
        scenario = self.scenario()
        original = copy.deepcopy(scenario)
        original_copy = copy.deepcopy

        def copy_retained_declarations(value: object, memo: dict | None = None) -> object:
            """Observe the actual copy boundary while leaving all evidence as ordinary JSON types."""
            if isinstance(value, dict) and any(value.get(key) is scenario[key]
                                              for key in ('products', 'arrival_batches')):
                raise AssertionError('Copied a replaced evidence collection')
            return original_copy(value, memo)

        findings = []
        with patch.object(verify, 'audit_seeded_generation', wraps=verify.audit_seeded_generation) as audit, patch(
                'tools.verify.copy.deepcopy', side_effect=copy_retained_declarations):
            verify.audit_network_generation(scenario, findings)
        self.assertEqual(findings, [])
        self.assertEqual(audit.call_count, 3)
        point_evidence = audit.call_args_list[0].args[0]
        self.assertIsNot(point_evidence['products'], scenario['products'])
        self.assertIsNot(point_evidence['arrival_batches'], scenario['arrival_batches'])
        self.assertIsNot(point_evidence['quality_classes'], scenario['quality_classes'])
        self.assertIsNot(point_evidence['metadata']['products'], scenario['metadata']['products'])
        point_evidence['metadata']['products'][0]['kept'] = 'private'
        self.assertEqual(scenario, original)


class ConservationCheckingTests(unittest.TestCase):
    """Pin local/network guard scope, error ordering and borrowed-container ownership."""

    # Prepare separate test data and inspect results

    def state_for_scope(self, scope: str) -> tuple[dict, InventoryRunState, Callable[[dict, InventoryRunState], None]]:
        """Build owned four-product state and return its scenario, state and appropriate checker.

        Tests receive local, authoritative-network or absent-map fallback state.
        Products occupy inventory; the network uses duplicate local WS1 IDs.
        Only this test data changes; no execution events or files are created.
        """
        scenario = as_dynamic_scenario(json.loads((ROOT / 'tests/fixtures/checkpoint_4c/scenario.json').read_bytes()))
        template = scenario['products'][0]
        scenario['products'] = [{**template, 'id': identifier, 'release_time': 0}
                                for identifier in ('P1', 'P2', 'P3', 'P4')]
        vehicle = scenario['vehicles'][0]
        scenario['vehicles'] = [{**vehicle, 'id': identifier, 'payload_capacity': 1}
                                for identifier in ('AV2', 'AV1')]
        for plant in scenario['plants']:
            plant['inventory_capacity'] = 4
        scenario['plant'] = copy.deepcopy(scenario['plants'][0])
        state = build_dynamic_state(scenario, ScenarioDefinitions.from_scenario(scenario))
        for identifier, product in state.products.items():
            plant_id = 'RF2' if scope == 'network' and identifier in ('P2', 'P4') else 'RF1'
            product.update(state='in_plant_inventory', location=plant_id)
            state.plant_states[plant_id]['inventory'][identifier] = 0
        if scope == 'fallback':
            state.plant_states = {}
        return scenario, state, check_inventory_state if scope == 'local' else check_plant_network

    def assert_unchanged_failure(self, scenario: dict, state: InventoryRunState,
                                 checker: Callable[[dict, InventoryRunState], None], expected: str) -> None:
        """Run the selected physical checker and compare its exact first-error details.

        Conservation tests supply deliberately invalid separate state. Check both
        exception context and unchanged input/state; no files are written.
        """
        original_scenario, original_state = copy.deepcopy(scenario), copy.deepcopy(state)
        with self.assertRaises(PhysicalStateError) as raised:
            checker(scenario, state)
        self.assertEqual(str(raised.exception), expected)
        self.assertEqual(scenario, original_scenario)
        self.assertEqual(state, original_state)

    # Check the expected model or evidence behavior

    def test_capacity_wins_over_simultaneous_membership_and_payload_failures(self) -> None:
        """Local, network and fallback checks retain capacity-first rule and exact context."""
        for scope, occupancy in (('local', 4), ('network', 2), ('fallback', 4)):
            with self.subTest(scope=scope):
                scenario, state, checker = self.state_for_scope(scope)
                scenario['plant']['inventory_capacity'] = 1
                scenario['plants'][0]['inventory_capacity'] = 1
                state.vehicles['AV2']['load'] = ['P1', 'P2']
                self.assert_unchanged_failure(scenario, state, checker,
                    "Physical state violation: time=0.0, rule=inventory.capacity; "
                    f"plant_id='RF1', expected=1, observed={occupancy}")

    def test_product_iteration_missing_membership_and_duplicate_occurrences(self) -> None:
        """Check product-order before payload and count duplicate load entries rather than membership booleans."""
        for scope in ('local', 'network', 'fallback'):
            for variant in ('ordered', 'reversed', 'duplicate', 'missing'):
                with self.subTest(scope=scope, variant=variant):
                    scenario, state, checker = self.state_for_scope(scope)
                    if variant in ('ordered', 'reversed'):
                        state.vehicles['AV2']['load'] = ['P1', 'P2', 'P3', 'P4']
                        if variant == 'reversed':
                            state.products = dict(reversed(state.products.items()))
                    else:
                        state.inventory.pop('P1')
                        if variant == 'duplicate':
                            state.products['P1'].update(state='onboard_av', location='AV2')
                            state.vehicles['AV2']['load'] = ['P1', 'P1']
                    product = 'P4' if variant == 'reversed' else 'P1'
                    count = 0 if variant == 'missing' else 2
                    self.assert_unchanged_failure(scenario, state, checker,
                        "Physical state violation: time=0.0, rule=product.conservation; "
                        f"product_id='{product}', expected=1, observed={count}")

    def test_payloads_follow_definition_order_after_all_products_conserve(self) -> None:
        """Two overfull vehicles fail in declaration order, after valid global membership checks."""
        for scope in ('local', 'network', 'fallback'):
            for reverse in (False, True):
                with self.subTest(scope=scope, reverse=reverse):
                    scenario, state, checker = self.state_for_scope(scope)
                    state.inventory.clear()
                    for plant in state.plant_states.values():
                        plant['inventory'].clear()
                    for identifier, product in state.products.items():
                        vehicle_id = 'AV2' if identifier in ('P1', 'P2') else 'AV1'
                        state.vehicles[vehicle_id]['load'].append(identifier)
                        product.update(state='onboard_av', location=vehicle_id)
                    if reverse:
                        scenario['vehicles'].reverse()
                    vehicle_id = 'AV1' if reverse else 'AV2'
                    self.assert_unchanged_failure(scenario, state, checker,
                        "Physical state violation: time=0.0, rule=vehicle.capacity; "
                        f"vehicle_id='{vehicle_id}', expected=1, observed=2")

    def test_held_slots_aliases_and_repeated_station_ids_do_not_double_count(self) -> None:
        """All four products conserve across repeated container scans; holds reserve space without extra stock."""
        for scope in ('local', 'network', 'fallback'):
            with self.subTest(scope=scope):
                scenario, state, checker = self.state_for_scope(scope)
                state.inventory.pop('P1')
                plant_id = 'RF2' if scope == 'network' else 'RF1'
                containers = state.plant_states[plant_id] if scope == 'network' else {
                    'held_slots': state.held_slots, 'workstations': state.workstations}
                scenario['products'][0]['operations'] = [1, 1]
                state.products['P1'].update(state='processing', location='WS1')
                containers['held_slots'].append('P1')
                containers['workstations']['WS1'] = {'product_id': 'P1', 'plant_id': plant_id,
                    'workstation_id': 'WS1', 'operation_id': 1, 'operation_index': 0,
                    'start_time': 0, 'end_time': 1, 'window': [0, 10]}
                before = copy.deepcopy(state)
                checker(scenario, state)
                self.assertEqual(state, before)
                if state.plant_states:
                    self.assertIs(state.inventory, state.plant_states['RF1']['inventory'])

    def test_checking_private_projection_preserves_live_and_candidate_state(self) -> None:
        """A copied candidate's invalid membership fails locally without changing either physical graph."""
        scenario, live, _ = self.state_for_scope('network')
        original_live = copy.deepcopy(live)
        candidate = InventoryRunState(**physical_snapshot(live, released_only=True))
        initialize_plant_states(scenario, candidate)
        candidate.workstations['WS1'] = {'product_id': 'P1'}
        self.assert_unchanged_failure(scenario, candidate, check_plant_network,
            "Physical state violation: time=0.0, rule=product.conservation; "
            "product_id='P1', expected=1, observed=2")
        self.assertEqual(live, original_live)
        self.assertIs(candidate.inventory, candidate.plant_states['RF1']['inventory'])
        self.assertIsNot(candidate.inventory, live.inventory)


class VerifierCliHelperTests(unittest.TestCase):
    """Keep the consolidated test boundary's process command, report agreement and diagnostics."""

    # Check the expected model or evidence behavior

    def test_compatibility_command_options_and_returned_report(self) -> None:
        """Use the same interpreter/root wrapper and inherited cwd, then return the parsed report."""
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            report = {'status': 'FAIL', 'exit_code': 1, 'findings': []}
            (folder / 'verification.json').write_text(json.dumps(report), encoding='utf-8')
            result = subprocess.CompletedProcess([], 1, stdout='output', stderr='diagnostic')
            with patch('tests.verifier_cli.subprocess.run', return_value=result) as child:
                self.assertEqual(verify_run_via_cli(self, folder, 1), report)
            child.assert_called_once_with([sys.executable, str(ROOT / 'verify_simulation.py'), str(folder)],
                                          capture_output=True, text=True)

    def test_process_and_report_exit_mismatches_remain_assertion_failures(self) -> None:
        """Reject a wrong child exit before reading reports, and reject a disagreeing saved exit afterward."""
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for mismatch in ('process', 'report'):
                with self.subTest(mismatch=mismatch):
                    path = folder / 'verification.json'
                    path.unlink(missing_ok=True)
                    if mismatch == 'report':
                        path.write_text(json.dumps({'exit_code': 0}), encoding='utf-8')
                    result = subprocess.CompletedProcess([], 0 if mismatch == 'process' else 1,
                                                         stdout='child stdout', stderr='child stderr')
                    with patch('tests.verifier_cli.subprocess.run', return_value=result):
                        with self.assertRaises(AssertionError) as raised:
                            verify_run_via_cli(self, folder, 1)
                    self.assertIn('0 != 1', str(raised.exception))
                    if mismatch == 'process':
                        self.assertIn('child stdoutchild stderr', str(raised.exception))
