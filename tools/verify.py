"""Independently reconstruct supported saved runs, generation and dispatch using only the standard library."""
from __future__ import annotations

# main calls verify_run, which reads artifacts, reconstructs expected behavior and
# replaces its reports. No simulation, routing, forecasting or replay module is
# imported: similar calculations below are intentionally independent checks.
# FAIL means a rule was disproved; INCOMPLETE means required evidence is unusable.
# PASS covers the supported checks, not proof that the whole model is correct.

import argparse
import copy
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

Record = dict[str, Any]
TOLERANCE = 1e-9
REQUIRED_FILES = ("scenario.json", "run_manifest.json", "events.jsonl",
                  "dispatch_decisions.jsonl", "summary.json", "final_state.json")

# Field roles come from the saved record contracts, not from whether a physical
# value happened to be serialized as 2 or 2.0. ID-keyed maps suppress field-name
# interpretation for their keys (a product may legitimately be called 'sequence').
INTEGER_FIELDS = frozenset({
    'sequence', 'last_event_sequence', 'decision_index', 'decision_count',
    'format_version', 'input_format_version', 'output_format_version',
    'generation_version', 'seed', 'master_seed', 'batch_size',
    'payload_capacity', 'inventory_capacity', 'waiting_count', 'held_count',
    'generated', 'remanufactured', 'recycled', 'unfinished',
    'operation_id', 'operation_index', 'next_operation', 'next_operation_index',
    'selection_round', 'pickup_index', 'delivery_index', 'pickup_stop_index',
    'delivery_stop_index', 'extra_visits', 'route_version', 'leg_serial',
})
EXACT_SUBTREES = frozenset({'seed_provenance', 'stream_seeds', 'point_seeds',
                           'departed_legs', 'operations', 'batch_values', 'candidate_coverage'})
KEYED_RECORDS = frozenset({'products', 'vehicles', 'workstations', 'plant_states'})
KEYED_PHYSICAL_VALUES = frozenset({'inventory', 'waiting_vehicles', 'quality_weights'})
EXACT_RULES = frozenset({'product.conservation', 'product.final_operation'})
# Static aware, dynamic unaware and dynamic aware score layouts respectively.
SCORE_INTEGER_POSITIONS = {6: {0, 4, 5}, 7: {3, 4, 5}, 9: {0, 5, 6, 7}}


class EvidenceProblem(Exception):
    """Stop an unsafe check when evidence is missing, malformed or unsupported."""


class ReplayViolation(Exception):
    """Stop replay after recording a proved transition violation."""


class PhysicalEvidenceError(Exception):
    """Independent arithmetic proves a physical contradiction, rather than unusable JSON."""

    def __init__(self, rule: str, expected: Any, observed: Any, **context: Any) -> None:
        """Store a proved physical contradiction for verify_run to report.

        Independent arithmetic checks pass rule, expected/observed values and IDs.
        Keep these values and optional event context on the exception; change no evidence.
        """
        super().__init__(rule)
        self.rule, self.expected, self.observed = rule, expected, observed
        self.context = context
        self.event: Record | None = None


# Independently check physical arithmetic

def audit_finite(value: float, quantity: str) -> float:
    """Return a finite calculated physical value or raise PhysicalEvidenceError.

    Independent travel, plant and total checks use this. Read the value only;
    integer seeds are checked separately without conversion to float.
    """
    if not math.isfinite(value):
        raise PhysicalEvidenceError('physical.finite_calculation', f'Finite {quantity}', repr(value), quantity=quantity)
    return value


def audit_distance(origin: list[float], target: list[float]) -> float:
    """Return independently calculated straight-line distance between two positions.

    Verifier travel checks call this without simulation imports. Reject overflow
    or distinct positions rounded to zero; read coordinates without changing them.
    """
    distance = audit_finite(math.hypot(target[0] - origin[0], target[1] - origin[1]), 'distance')
    if distance == 0 and any(origin[axis] != target[axis] for axis in (0, 1)):
        raise PhysicalEvidenceError('physical.distance', 'Positive distance for distinct positions', distance)
    return distance


def audit_end(start: float, duration: float, quantity: str, *, allow_zero: bool = False) -> float:
    """Return start plus duration only when the required physical clock can advance.

    Independent travel/processing predictions call this. Only genuine zero-distance
    travel may allow zero duration. Invalid arithmetic raises PhysicalEvidenceError;
    normal physical comparisons elsewhere still use TOLERANCE. No evidence changes.
    """
    audit_finite(start, f'{quantity}.start')
    audit_finite(duration, f'{quantity}.duration')
    end = audit_finite(start + duration, f'{quantity}.end')
    if duration < 0 or (not (allow_zero and duration == 0) and (duration <= 0 or end <= start)):
        raise PhysicalEvidenceError(f'physical.{quantity}_progress',
            {'start': start, 'duration': duration, 'requirement': 'Positive duration must advance time'}, {'end': end})
    return end


def audit_arrival(start: float, distance: float, speed: float) -> float:
    """Return an independently checked travel arrival from start, distance and speed.

    Verifier route/leg calculations call this. Zero-distance service may be immediate;
    positive travel must advance time. Invalid arithmetic raises; change nothing.
    """
    audit_finite(distance, 'distance')
    return audit_end(start, distance / speed, 'travel', allow_zero=distance == 0)


def audit_prefix(elapsed: float, speed: float, distance: float) -> float:
    """Return actual distance covered so far, capped at full leg length.

    Independent motion reconstruction calls this. Check multiplication before the
    cap can hide overflow or underflow. Read values only; invalid arithmetic raises.
    """
    covered = audit_finite(max(0, elapsed) * speed, 'travel.prefix')
    if elapsed > 0 and distance > 0 and covered == 0:
        raise PhysicalEvidenceError('physical.travel_progress', 'Representable positive traveled prefix', covered)
    return min(distance, covered)


def audit_observed_progress(start: float, end: float, quantity: str, event: Record,
                            *, positive: bool = True) -> None:
    """Require an actual recorded end after the independently reconstructed start.

    Replay supplies its active job/leg start and the observed event, not an altered
    start field on that event. Zero-distance travel may end at departure.
    Return None or raise PhysicalEvidenceError with event context. A tolerance must
    not turn a positive operation into zero-time work. Evidence stays unchanged.
    """
    if end < start or (positive and end == start):
        error = PhysicalEvidenceError(f'physical.{quantity}_progress',
            'End after start' if positive else 'End at or after start', {'start': start, 'end': end})
        error.event = event
        raise error


def audit_physical_event_arithmetic(scenario: Record, events: list[Record]) -> None:
    """Check that recorded positive travel and processing can advance time.

    verify_run calls after supported-input checks and before replay. Read events
    and declarations independently. Invalid calculations or actual zero-time
    positive work raise PhysicalEvidenceError with event/ID context.
    Leave missing transition fields for existing replay diagnostics. Do not trust
    repeating rounded start-plus-duration arithmetic as proof of valid work.
    """
    vehicles = {vehicle['id']: vehicle for vehicle in scenario['vehicles']}
    plants = {plant['id']: plant for plant in audit_configured_plants(scenario)}
    for event in events:
        try:
            if event['event_type'] == 'leg_departure':
                if not all(key in event for key in ('origin_coordinates', 'target_coordinates', 'departure_time', 'arrival_time', 'vehicle_id')):
                    continue  # Existing replay diagnoses missing/unknown transition fields.
                if event['vehicle_id'] not in vehicles or len(event['origin_coordinates']) != 2 or len(event['target_coordinates']) != 2:
                    continue
                distance = audit_distance(event['origin_coordinates'], event['target_coordinates'])
                audit_arrival(event['departure_time'], distance, vehicles[event['vehicle_id']]['speed'])
                audit_observed_progress(event['departure_time'], event['arrival_time'], 'travel', event,
                                        positive=distance > 0)
            elif event['event_type'] == 'operation_start':
                if not all(key in event for key in ('plant_id', 'workstation_id', 'start_time', 'end_time')):
                    continue
                plant = plants.get(event['plant_id'])
                station = next((station for station in plant['workstations'] if station['id'] == event['workstation_id']), None) if plant else None
                if station is None:
                    continue
                audit_end(event['start_time'], station['duration'], 'processing')
                audit_observed_progress(event['start_time'], event['end_time'], 'processing', event)
        except PhysicalEvidenceError as error:
            error.event = event
            raise


# Compare discrete identities exactly and physical values with tolerance

def comparison_difference(expected: Any, observed: Any, *, field: str = '',
                          path: str = '', exact: bool = False) -> tuple[str, Any, Any, bool] | None:
    """Return the first differing field, values and exact-comparison flag, or None.

    check_value and values_match pass independently expected and observed data.
    Read both without changes. Seeds, integer identities and score index positions
    compare exactly; times/distances use TOLERANCE even if written as integer literals.
    Dictionary keys that are product/vehicle IDs are not treated as field names.
    Keep full seed precision; unusably large physical numbers raise EvidenceProblem.
    """
    exact = exact or field in EXACT_SUBTREES
    numeric = isinstance(expected, (int, float)) or isinstance(observed, (int, float))
    discrete = exact or (field in INTEGER_FIELDS and numeric)
    if isinstance(expected, bool) or isinstance(observed, bool):
        matched = type(expected) is type(observed) and expected == observed
        return None if matched else (path, expected, observed, discrete)
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        if discrete:
            matched = (type(expected) is type(observed) and expected == observed
                       and (type(expected) is not float or math.isfinite(expected)))
            if field in INTEGER_FIELDS:
                matched = matched and type(expected) is int
        else:
            try:
                matched = math.isfinite(expected) and math.isfinite(observed) and math.isclose(
                    expected, observed, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
            except OverflowError as error:
                raise EvidenceProblem(f'{path}: physical number exceeds finite checking range') from error
        return None if matched else (path, expected, observed, discrete)
    if isinstance(expected, dict) and isinstance(observed, dict):
        if expected.keys() != observed.keys():
            return path, expected, observed, False
        for key, value in expected.items():
            child_field = '' if field in KEYED_RECORDS or field in KEYED_PHYSICAL_VALUES else key
            if field == 'batch_size' and key == 'values':
                child_field = 'batch_values'
            difference = comparison_difference(value, observed[key], field=child_field,
                path=f'{path}.{key}' if path else key, exact=exact)
            if difference is not None:
                return difference
        return None
    if isinstance(expected, (list, tuple)) and type(expected) is type(observed):
        if len(expected) != len(observed):
            return path, expected, observed, False
        integer_positions = SCORE_INTEGER_POSITIONS.get(len(expected), set()) if field == 'score' else set()
        for index, (left, right) in enumerate(zip(expected, observed)):
            difference = comparison_difference(left, right, path=f'{path}[{index}]',
                exact=exact or index in integer_positions)
            if difference is not None:
                return difference
        return None
    return None if type(expected) is type(observed) and expected == observed else (path, expected, observed, discrete)


def values_match(expected: Any, observed: Any, *, field: str = '', exact: bool = False) -> bool:
    """Return whether two read-only values agree under their field's comparison rule.

    Verifier calculations use this without adding findings. Bare numbers are physical
    values with tolerance; known discrete fields and exact=True require exact agreement.
    """
    return comparison_difference(expected, observed, field=field, exact=exact) is None


def add_finding(findings: list[Record], category: str, rule: str, expected: Any,
                observed: Any, event: Record | None = None, **identifiers: Any) -> None:
    """Append one finding with rule, time/sequence, IDs and expected/observed values.

    Verification checks pass the shared findings list. Make a new diagnostic record,
    but retain supplied expected/observed values without deep-copying them.
    No run evidence or files change; write_reports saves the findings later.
    """
    event_identifiers = {key: event[key] for key in
                         ("product_id", "vehicle_id", "plant_id", "workstation_id", "facility_id", "recycler_id")
                         if event is not None and key in event}
    event_identifiers.update(identifiers)
    findings.append({"category": category, "rule": rule,
        "time": event.get("time") if event else None,
        "event_sequence": event.get("sequence") if event else None,
        "ids": event_identifiers, "expected": expected, "observed": observed})


def check_value(findings: list[Record], rule: str, expected: Any, observed: Any,
                event: Record | None = None, **identifiers: Any) -> bool:
    """Compare expected and observed evidence and append a violation if they differ.

    Audit functions pass a rule and optional event/IDs. Return True on agreement,
    False on mismatch. Discrete differences identify the exact field; physical
    quantities keep tolerance. Only findings changes; this does not stop replay itself.
    """
    difference = comparison_difference(expected, observed, field=rule.rsplit('.', 1)[-1],
                                       path=rule, exact=rule in EXACT_RULES)
    if difference is not None:
        field_path, left, right, discrete = difference
        if discrete:
            expected, observed = left, right
            identifiers['field'] = field_path
        add_finding(findings, "violation", rule, expected, observed, event, **identifiers)
    return difference is None


# Read evidence safely before auditing its meaning

def reject_nonfinite_json(token: str) -> None:
    """Reject JSON NaN/Infinity tokens during parsing; return no replacement value.

    parse_json_evidence gives this callback to json.loads so unusable numbers
    cannot enter later physical calculations. Raise ValueError; change nothing.
    """
    raise ValueError(f"Nonfinite JSON constant: {token}")


def parse_json_evidence(contents: str) -> Any:
    """Parse JSON, retaining full integer precision and rejecting nonfinite floats.

    read_evidence calls with file or record text. Return parsed data or raise
    ValueError. Read only; do not convert large seeds to floating-point values.
    """
    value = json.loads(contents, parse_constant=reject_nonfinite_json)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON number exceeds finite checking range")
    return value


def evidence_object(value: Any, path: str, required: tuple[str, ...] = ()) -> Record:
    """Return the supplied dictionary after checking its type and required keys.

    Evidence validators call this without copying or changing it. Raise
    EvidenceProblem with the path for malformed or missing data; never fill defaults.
    """
    if not isinstance(value, dict):
        raise EvidenceProblem(f'{path}: expected object, observed {type(value).__name__}')
    missing = [key for key in required if key not in value]
    if missing:
        raise EvidenceProblem(f'{path}: missing required fields {missing}')
    return value


def evidence_list(value: Any, path: str) -> list[Any]:
    """Return the supplied JSON list, or raise EvidenceProblem with its path.

    Nested evidence checks use this without copying, changing records or filling defaults.
    """
    if not isinstance(value, list):
        raise EvidenceProblem(f'{path}: expected array, observed {type(value).__name__}')
    return value


def evidence_records(value: Any, path: str, required: tuple[str, ...] = ()) -> list[Record]:
    """Check a JSON list of dictionaries and return that same read-only list.

    Scenario/dispatch validators supply a path and required keys. Check each item
    with evidence_object; malformed data raises EvidenceProblem. No copies or writes.
    """
    records = evidence_list(value, path)
    for index, record in enumerate(records):
        evidence_object(record, f'{path}[{index}]', required)
    return records


def evidence_physical_number(value: Any, path: str, *, nullable: bool = False) -> None:
    """Check that evidence can be used as a finite physical number; change nothing.

    Structure validators supply a field path and whether null is allowed. Reject
    wrong types or values too large for checking with EvidenceProblem.
    Do not use this for integer seeds; their full precision must be retained.
    """
    if nullable and value is None:
        return
    if type(value) not in (int, float):
        raise EvidenceProblem(f'{path}: expected finite physical number')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise EvidenceProblem(f'{path}: physical number exceeds finite checking range')


def validate_record_structure(record: Record, path: str) -> None:
    """Check nested record shapes before audit code reads their contents.

    Scenario, state, event and dispatch validators pass an already checked dictionary.
    Check arrays, IDs, routes, jobs and physical-number fields without changes.
    Leave integer value/type contradictions for exact comparison and unknown fields
    for existing scope checks. Malformed structure raises EvidenceProblem.
    """
    for key in ('id', 'product_id', 'vehicle_id', 'plant_id', 'recycler_id', 'workstation_id',
                'candidate_id', 'batch_id', 'quality_class', 'origin', 'destination', 'target', 'site_id', 'state', 'status'):
        if key in record and not isinstance(record[key], str):
            raise EvidenceProblem(f'{path}.{key}: expected identity/status string')
    for key in ('assigned_vehicle', 'planned_destination', 'actual_destination', 'location', 'selected_candidate_id'):
        if key in record and record[key] is not None and not isinstance(record[key], str):
            raise EvidenceProblem(f'{path}.{key}: expected string or null')
    list_fields = ('operations', 'load', 'pickups', 'deliveries', 'stops', 'product_ids',
                   'held_slots', 'score', 'coordinates', 'position', 'origin_coordinates',
                   'target_coordinates', 'window', 'x_range', 'y_range', 'values', 'weights',
                   'initial_delay', 'open_duration', 'closed_duration')
    for key in list_fields:
        if key in record:
            items = evidence_list(record[key], f'{path}.{key}')
            if any(isinstance(item, (dict, list)) for item in items):
                raise EvidenceProblem(f'{path}.{key}: expected scalar array elements')
            if key in ('load', 'pickups', 'deliveries', 'stops', 'product_ids', 'held_slots') and any(
                    not isinstance(item, str) for item in items):
                raise EvidenceProblem(f'{path}.{key}: expected identity strings')
            if key in ('coordinates', 'position', 'origin_coordinates', 'target_coordinates',
                       'window', 'x_range', 'y_range', 'weights', 'initial_delay', 'open_duration', 'closed_duration'):
                for item in items:
                    evidence_physical_number(item, f'{path}.{key}')
    for key in ('remaining_stops', 'old_remaining_stops', 'new_remaining_stops', 'history', 'operation_history', 'projected_deliveries', 'segments'):
        if key in record:
            for index, item in enumerate(evidence_records(record[key], f'{path}.{key}')):
                validate_record_structure(item, f'{path}.{key}[{index}]')
    for key in ('active_leg', 'active_operation'):
        if key in record and record[key] is not None:
            validate_record_structure(evidence_object(record[key], f'{path}.{key}'), f'{path}.{key}')
    for key in ('milestones', 'quality_weights'):
        if key in record:
            for identifier, number in evidence_object(record[key], f'{path}.{key}').items():
                evidence_physical_number(number, f'{path}.{key}.{identifier}')
    for key in ('generation', 'window_generation'):
        if key in record:
            validate_record_structure(evidence_object(record[key], f'{path}.{key}'), f'{path}.{key}')
    if 'batch_size' in record and isinstance(record['batch_size'], dict):
        validate_record_structure(record['batch_size'], f'{path}.batch_size')
    if 'windows' in record:
        for index, window in enumerate(evidence_list(record['windows'], f'{path}.windows')):
            bounds = evidence_list(window, f'{path}.windows[{index}]')
            if len(bounds) != 2:
                raise EvidenceProblem(f'{path}.windows[{index}]: expected two bounds')
            for number in bounds:
                evidence_physical_number(number, f'{path}.windows[{index}]')
    physical_fields = ('time', 'horizon', 'release_time', 'departure_time', 'arrival_time',
                       'start_time', 'end_time', 'duration', 'speed', 'distance', 'vehicle_distance',
                       'inventory_wait', 'plant_inventory_wait', 'waiting_to_unload', 'arrival_rate',
                       'leg_distance_accounted', 'added_distance', 'remaining_distance', 'old_arrival_time',
                       'distance_travelled', 'required_distance', 'available_range', 'range_before', 'range_after')
    for key in physical_fields:
        if key in record:
            evidence_physical_number(record[key], f'{path}.{key}')
    for key in ('remaining_range', 'maximum_range', 'waiting_since', 'inventory_entered_at',
                'remaining_range_at_departure', 'remaining_range_on_arrival',
                'predicted_arrival', 'predicted_admission', 'predicted_first_start', 'predicted_inventory_wait'):
        if key in record:
            evidence_physical_number(record[key], f'{path}.{key}', nullable=True)


def validate_scenario_structure(scenario: Record, path: str) -> None:
    """Check scenario/container shapes before independent scope or generation checks.

    read_evidence and forecast-context validation call this. Provenance is checked
    separately so unusable seed metadata does not hide usable physical evidence.
    Read only; return None or raise EvidenceProblem. Format validators still own
    accepted numerical bounds and scope.
    """
    validate_record_structure(scenario, path)
    # Keep known singular-record shape checks for malformed retained evidence.
    # Recognizing a container does not authorize retired-format execution.
    for key in ('plant', 'collection_point', 'vehicle', 'product', 'trip'):
        if key in scenario:
            record = evidence_object(scenario[key], f'{path}.{key}')
            validate_record_structure(record, f'{path}.{key}')
            if key == 'plant':
                evidence_object(record, f'{path}.plant', ('workstations',))
                for index, station in enumerate(evidence_records(record['workstations'], f'{path}.plant.workstations')):
                    validate_record_structure(station, f'{path}.plant.workstations[{index}]')
    for key in ('plants', 'collection_points', 'vehicles', 'products', 'recycling_facilities', 'trips', 'quality_classes', 'arrival_batches'):
        if key not in scenario:
            continue
        for index, record in enumerate(evidence_records(scenario[key], f'{path}.{key}')):
            validate_record_structure(record, f'{path}.{key}[{index}]')
            if key == 'plants':
                evidence_object(record, f'{path}.{key}[{index}]', ('workstations',))
                for station_index, station in enumerate(evidence_records(record['workstations'], f'{path}.{key}[{index}].workstations')):
                    validate_record_structure(station, f'{path}.{key}[{index}].workstations[{station_index}]')
    for key in ('coordinate_generation', 'schedule_generation'):
        if key in scenario:
            validate_record_structure(evidence_object(scenario[key], f'{path}.{key}'), f'{path}.{key}')


def validate_inventory_containers(record: Record, path: str) -> None:
    """Check plant inventory/queue/job shapes without copying their contents.

    State validation passes primary or plant-ID containers and a path. ID keys name
    products or vehicles, not schema fields. Return None or raise EvidenceProblem.
    """
    for key in ('inventory', 'waiting_vehicles'):
        if key in record:
            for identifier, number in evidence_object(record[key], f'{path}.{key}').items():
                evidence_physical_number(number, f'{path}.{key}.{identifier}')
    if 'held_slots' in record:
        evidence_list(record['held_slots'], f'{path}.held_slots')
    if 'workstations' in record:
        for identifier, job in evidence_object(record['workstations'], f'{path}.workstations').items():
            if job is not None:
                validate_record_structure(evidence_object(job, f'{path}.workstations.{identifier}'), f'{path}.workstations.{identifier}')


def validate_state_structure(state: Record, path: str) -> None:
    """Check saved or forecast state containers before any comparison uses them.

    read_evidence and dispatch-context checks call this. Read dictionaries/lists
    without changing source or reconstructed state. Return None; malformed shapes
    raise EvidenceProblem. Check any embedded summary separately.
    """
    evidence_object(state, path, ('time',))
    validate_record_structure(state, path)
    if 'products' in state:
        evidence_object(state, path, ('products', 'vehicles', 'workstations', 'inventory', 'held_slots', 'waiting_vehicles'))
        for key in ('products', 'vehicles'):
            for identifier, record in evidence_object(state[key], f'{path}.{key}').items():
                validate_record_structure(evidence_object(record, f'{path}.{key}.{identifier}'), f'{path}.{key}.{identifier}')
        validate_inventory_containers(state, path)
        if 'plant_states' in state:
            for identifier, plant in evidence_object(state['plant_states'], f'{path}.plant_states').items():
                validate_inventory_containers(evidence_object(plant, f'{path}.plant_states.{identifier}'), f'{path}.plant_states.{identifier}')
    elif 'product_state' in state:
        # Validate this recognizable container shape without reconstructing it.
        # Supported-scope checks still reject format one independently.
        evidence_object(state, path, ('inventory',))
        evidence_list(state['inventory'], f'{path}.inventory')
    else:
        raise EvidenceProblem(f'{path}: missing physical product state')
    if 'summary' in state:
        validate_summary_structure(evidence_object(state['summary'], f'{path}.summary'), f'{path}.summary')


def validate_summary_structure(summary: Record, path: str) -> None:
    """Check that summary values have usable shapes before comparing totals.

    Evidence/state validation calls this. Count exactness is checked later against
    recalculated totals. Read only; malformed operands raise EvidenceProblem.
    """
    validate_record_structure(summary, path)
    for key in ('generated', 'remanufactured', 'recycled', 'unfinished'):
        if key in summary and not isinstance(summary[key], (int, float)):
            raise EvidenceProblem(f'{path}.{key}: expected count number')


def validate_dispatch_structure(records: list[Record], path: str, policy: Any = None) -> None:
    """Check nested observations, candidates and proposals before dispatch auditing.

    read_evidence calls for the entire log. Use manifest policy or record markers
    to check static/dynamic and aware shapes. Read borrowed records only.
    Malformed structure makes the artifact unavailable with an INCOMPLETE finding;
    usable events/exports can still be checked. Never drop individual bad records
    and pretend the rest is a complete valid log.
    """
    static_fields = ('selection_round', 'product_id', 'destination', 'pickup_stop_index',
                     'delivery_stop_index', 'added_distance', 'score', 'selected')
    dynamic_fields = ('candidate_id', 'product_id', 'vehicle_id', 'destination', 'pickup_index',
                      'delivery_index', 'extra_visits', 'old_remaining_stops', 'new_remaining_stops',
                      'position', 'remaining_range', 'feasible', 'reason', 'remaining_distance', 'added_distance', 'score')
    forecasts = ('predicted_arrival', 'predicted_admission', 'predicted_first_start', 'predicted_inventory_wait')
    for index, record in enumerate(records):
        location = f'{path}[{index}]'
        evidence_object(record, location, ('time', 'trigger', 'last_event_sequence', 'policy_input'))
        validate_record_structure(record, location)
        boundary = evidence_object(record['policy_input'], f'{location}.policy_input')
        dynamic = policy in ('dynamic_quality_unaware', 'dynamic_quality_aware') or (
            policy is None and isinstance(record['trigger'], dict))
        aware = policy in ('static_quality_aware', 'dynamic_quality_aware') or 'forecast_context' in boundary
        evidence_object(boundary, f'{location}.policy_input', ('requests', 'vehicles', 'sites', 'time')
                        if dynamic else ('requests', 'vehicle_id', 'stops'))
        if aware:
            evidence_object(boundary, f'{location}.policy_input', ('forecast_context',))
        if 'trigger' in record:
            if dynamic:
                evidence_object(record, location, ('requests', 'proposals'))
                evidence_object(record['trigger'], f'{location}.trigger')
            elif not isinstance(record['trigger'], str):
                raise EvidenceProblem(f'{location}.trigger: expected static trigger string')
            else:
                evidence_object(record, location, ('decision', 'tie_break'))
                if 'forecast_context' in boundary or 'selection_reason' in record:
                    evidence_object(record, location, ('candidates', 'selection_reason'))
                    evidence_object(boundary, f'{location}.policy_input', ('forecast_context',))
        if 'decision' in record:
            evidence_object(record['decision'], f'{location}.decision')
        validate_record_structure(boundary, f'{location}.policy_input')
        for key in ('requests', 'vehicles', 'sites'):
            if key in boundary:
                for item_index, item in enumerate(evidence_records(boundary[key], f'{location}.policy_input.{key}')):
                    validate_record_structure(item, f'{location}.policy_input.{key}[{item_index}]')
        if 'forecast_context' in boundary:
            context = evidence_object(boundary['forecast_context'], f'{location}.policy_input.forecast_context', ('scenario', 'state'))
            validate_scenario_structure(evidence_object(context['scenario'], f'{location}.forecast_context.scenario'), f'{location}.forecast_context.scenario')
            validate_state_structure(evidence_object(context['state'], f'{location}.forecast_context.state'), f'{location}.forecast_context.state')
            if 'departed_legs' in context:
                evidence_object(context['departed_legs'], f'{location}.forecast_context.departed_legs')
        for key in ('candidates', 'proposals', 'requests'):
            if key in record:
                for item_index, item in enumerate(evidence_records(record[key], f'{location}.{key}')):
                    item_path = f'{location}.{key}[{item_index}]'
                    if key == 'candidates':
                        evidence_object(item, item_path, static_fields + forecasts)
                    elif key == 'proposals':
                        evidence_object(item, item_path, dynamic_fields)
                        if aware:
                            evidence_object(item, item_path, forecasts + ('projected_deliveries',))
                    validate_record_structure(item, item_path)
                    if key == 'requests':
                        evidence_object(item, item_path, ('product_id', 'candidates', 'selected_candidate_id', 'reason'))
                        for candidate_index, candidate in enumerate(evidence_records(item['candidates'], f'{item_path}.candidates')):
                            candidate_path = f'{item_path}.candidates[{candidate_index}]'
                            evidence_object(candidate, candidate_path, dynamic_fields)
                            if aware and candidate['feasible'] is True:
                                evidence_object(candidate, candidate_path, forecasts + ('projected_deliveries',))
                            validate_record_structure(candidate, candidate_path)


def validate_evidence_structure(filename: str, value: Any, policy: Any = None) -> None:
    """Choose the read-only shape check for one parsed saved artifact.

    read_evidence supplies filename, value and optional policy. Return None;
    unsafe nested data raises EvidenceProblem before reconstruction uses it.
    """
    if filename == 'scenario.json':
        validate_scenario_structure(value, filename)
    elif filename == 'dispatch_decisions.jsonl':
        validate_dispatch_structure(value, filename, policy)
    elif filename == 'events.jsonl':
        for index, event in enumerate(value):
            path = f'{filename}[{index}]'
            evidence_object(event, path, ('sequence', 'time', 'event_type'))
            if not isinstance(event['event_type'], str):
                raise EvidenceProblem(f'{path}.event_type: expected event type string')
            if not isinstance(event['sequence'], (int, float)):
                raise EvidenceProblem(f'{path}.sequence: expected sequence number')
            validate_record_structure(event, path)
            if event['event_type'] == 'dispatch' and 'trigger' in event:
                evidence_object(event['trigger'], f'{path}.trigger')
            if 'product' in event:
                validate_record_structure(evidence_object(event['product'], f'{path}.product'), f'{path}.product')
            if 'stop' in event:
                validate_record_structure(evidence_object(event['stop'], f'{path}.stop'), f'{path}.stop')
    elif filename == 'summary.json':
        validate_summary_structure(value, filename)
    elif filename == 'final_state.json':
        validate_state_structure(value, filename)


def validate_seed_mapping(value: Any, path: str, nested_maps: int = 0) -> None:
    """Check seed dictionary nesting without converting large integers to float.

    Provenance validation supplies a path and remaining map depth. Numeric leaves,
    including wrong numeric types, stay for later exact auditing. Wrong container
    or nonnumeric leaf shapes raise EvidenceProblem; change nothing.
    """
    for identifier, seed in evidence_object(value, path).items():
        location = f'{path}.{identifier}'
        if nested_maps:
            validate_seed_mapping(seed, location, nested_maps - 1)
        elif not isinstance(seed, (int, float)):
            raise EvidenceProblem(f'{location}: expected seed number')


def validate_expected_seed_structure(expected: Record, observed: Record, path: str) -> None:
    """Require the independently expected seed keys before comparing their values.

    Generation audits pass reconstructed and saved dictionaries. Missing required
    keys raise EvidenceProblem (INCOMPLETE); present but wrong numeric seeds stay
    for exact comparison (FAIL). Read only; no random draws or file writes.
    """
    pending = [(expected, observed, path)]
    while pending:
        wanted, seen, location = pending.pop()
        evidence_object(seen, location, tuple(wanted))
        for key, value in wanted.items():
            if isinstance(value, dict):
                pending.append((value, evidence_object(seen[key], f'{location}.{key}'), f'{location}.{key}'))


# Check seed-information structure separately from physics

def validate_provenance_structure(value: Any, path: str) -> None:
    """Check seed-information shapes without discarding usable physical evidence.

    verify_run passes scenario or manifest provenance. Return None or raise
    EvidenceProblem with its path; do not substitute an empty dictionary.
    Optional fixture seed=null is permitted. Numeric stream leaves are compared
    exactly later, so bool/float impostors cannot pass as integer seeds.
    """
    provenance = evidence_object(value, path)
    for key in ('source', 'algorithm', 'coordinate_algorithm'):
        if key in provenance and not isinstance(provenance[key], str):
            raise EvidenceProblem(f'{path}.{key}: expected provenance string')
    for key in ('seed', 'master_seed'):
        if key in provenance and provenance[key] is not None and type(provenance[key]) is not int:
            raise EvidenceProblem(f'{path}.{key}: expected JSON integer or permitted null seed')
    source = provenance.get('source')
    if source in ('seeded_configuration', 'seeded_network_configuration'):
        evidence_object(provenance, path, ('master_seed', 'generation_version', 'algorithm', 'stream_seeds'))
        streams = evidence_object(provenance['stream_seeds'], f'{path}.stream_seeds')
        required = ('arrivals_batches', 'qualities') if source == 'seeded_configuration' else ('returns', 'qualities', 'workstation_schedules')
        evidence_object(streams, f'{path}.stream_seeds', required)
        if source == 'seeded_configuration':
            validate_seed_mapping(streams, f'{path}.stream_seeds')
        else:
            evidence_object(provenance, path, ('point_seeds',))
            validate_seed_mapping(provenance['point_seeds'], f'{path}.point_seeds')
            for group in ('returns', 'qualities', 'workstation_schedules', 'coordinates'):
                if group in streams:
                    validate_seed_mapping(streams[group], f'{path}.stream_seeds.{group}',
                                          int(group in ('workstation_schedules', 'coordinates')))
    for key in ('stream_seeds', 'point_seeds'):
        if key not in provenance:
            continue
        pending = [(evidence_object(provenance[key], f'{path}.{key}'), f'{path}.{key}')]
        while pending:
            mapping, location = pending.pop()
            for identifier, seed in mapping.items():
                if isinstance(seed, dict):
                    pending.append((seed, f'{location}.{identifier}'))
                elif not isinstance(seed, (int, float)):
                    raise EvidenceProblem(f'{location}.{identifier}: expected seed number or nested stream object')


# Load required artifacts and recognize supported scope

def read_evidence(run_directory: Path, findings: list[Record]) -> dict[str, Any]:
    """Read the six required artifacts separately and collect evidence problems.

    verify_run supplies folder and findings. Return successfully parsed and
    structurally usable artifacts. Record missing, unreadable or malformed artifacts
    without inventing replacements. Raw scenario hashing remains independently
    checkable by the caller. Read source only; reports are written later.
    """
    evidence: dict[str, Any] = {}
    for filename in REQUIRED_FILES:
        path = run_directory / filename
        try:
            contents = path.read_text(encoding="utf-8-sig")
            if filename.endswith(".jsonl"):
                value = [parse_json_evidence(line) for line in contents.splitlines() if line.strip()]
                if not all(isinstance(record, dict) for record in value):
                    raise EvidenceProblem("Each JSONL record must be an object")
            else:
                value = parse_json_evidence(contents)
                if not isinstance(value, dict):
                    raise EvidenceProblem("Expected a JSON object")
            try:
                manifest = evidence.get('run_manifest.json')
                policy = manifest.get('policy') if manifest is not None else None
                validate_evidence_structure(filename, value, policy)
            except EvidenceProblem as error:
                add_finding(findings, 'incomplete', 'evidence.structure',
                            f'Supported nested structure in {filename}', str(error), file=filename)
                continue
            evidence[filename] = value
        except (OSError, UnicodeError, ValueError, EvidenceProblem) as error:
            add_finding(findings, "incomplete", "evidence.read", f"Readable {filename}",
                        str(error), file=filename)
    return evidence


def validate_supported_scenario(scenario: Record) -> None:
    """Independently recognize supported format-two/three declarations.

    verify_run calls before replay. Format one is retired, so historical scalar
    runs receive INCOMPLETE rather than being reconstructed or converted.
    Return None or raise EvidenceProblem. Read only; do not import simulator checks.
    """
    if type(scenario['format_version']) is not int:
        raise EvidenceProblem('Format version must be a JSON integer')
    if scenario['format_version'] == 3:
        validate_dynamic_inputs(scenario)
    elif scenario['format_version'] == 2:
        validate_inventory_inputs(scenario)
    elif scenario['format_version'] == 1:
        raise EvidenceProblem('Scenario format 1 is retired; supported formats are 2 and 3')
    else:
        raise EvidenceProblem(f"Unsupported scenario format {scenario['format_version']!r}; supported formats are 2 and 3")


# Replace only the verification reports

def write_reports(run_directory: Path, report: Record) -> None:
    """Replace verification.json and verification.txt with the current result.

    verify_run calls even for classified FAIL or INCOMPLETE evidence. Read report
    and write only these two files; do not change scenario, events or decisions.
    I/O failures propagate, so callers cannot claim a fresh report was saved.
    """
    (run_directory / "verification.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [f"{report['status']} (exit {report['exit_code']})", report["scope"],
             f"Tolerance: absolute and relative {TOLERANCE}",
             f"Recalculated summary: {json.dumps(report['recalculated_summary'])}"]
    for finding in report["findings"]:
        lines.append(f"{finding['category'].upper()} {finding['rule']} "
            f"time={finding['time']} sequence={finding['event_sequence']} ids={finding['ids']} "
            f"expected={finding['expected']!r} observed={finding['observed']!r}")
    lines.append("Unchecked scope: " + "; ".join(report["unchecked_scope"]))
    (run_directory / "verification.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# Recognize fixed inventory inputs

def validate_inventory_inputs(scenario: Record) -> None:
    """Independently check supported fixed-trip inventory declarations.

    validate_supported_scenario and the dynamic validator's local input copies call
    this. Return None or raise EvidenceProblem for unusable/unsupported declarations.
    Read only. Keep these checks separate from simulation.config so a shared mistake
    does not become the verifier's expected answer.
    """
    if scenario['format_version'] != 2 or scenario['policy'] not in ('static_quality_unaware', 'static_quality_aware'):
        raise EvidenceProblem('Expected inventory format 2 and supported static policy')
    plant, point = scenario['plant'], scenario['collection_point']
    capacity = plant['inventory_capacity']
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise EvidenceProblem('Capacity must be a positive integer or null')
    ids = [plant['id'], point['id']]
    numbers = [scenario['horizon']]
    ids.extend(recycler['id'] for recycler in scenario['recycling_facilities'])
    for site in (plant, point, *scenario['recycling_facilities']):
        if len(site['coordinates']) != 2:
            raise EvidenceProblem('Expected two-dimensional coordinates')
        numbers.extend(site['coordinates'])
    operations = set()
    station_ids = set()
    for station in plant['workstations']:
        if type(station['operation_id']) is not int and not isinstance(station['operation_id'], str):
            raise EvidenceProblem('Operation identities must be integers or strings')
        if station['id'] in station_ids or station['operation_id'] in operations:
            raise EvidenceProblem('Duplicate workstation or operation')
        station_ids.add(station['id'])
        operations.add(station['operation_id'])
        if not isinstance(station['id'], str) or not station['id'] or station['duration'] <= 0:
            raise EvidenceProblem('Invalid workstation ID/duration')
        numbers.append(station['duration'])
        previous = -math.inf
        for start, end in station['windows']:
            if not 0 <= start < end or start < previous:
                raise EvidenceProblem('Invalid inventory calendar')
            numbers.extend((start, end))
            previous = end
    vehicle_ids = set()
    for vehicle in scenario['vehicles']:
        ids.append(vehicle['id'])
        vehicle_ids.add(vehicle['id'])
        if vehicle['initial_site'] not in {plant['id'], *(site['id'] for site in scenario['recycling_facilities'])}:
            raise EvidenceProblem('Expected a configured facility start')
        maximum = vehicle['maximum_range']
        if maximum is not None:
            if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0:
                raise EvidenceProblem('Expected positive finite range or null')
            numbers.append(maximum)
        if vehicle['speed'] <= 0 or type(vehicle['payload_capacity']) is not int or vehicle['payload_capacity'] <= 0:
            raise EvidenceProblem('Invalid vehicle speed/payload')
        numbers.append(vehicle['speed'])
    for product in scenario['products']:
        if any(type(operation) is not int and not isinstance(operation, str) for operation in product['operations']):
            raise EvidenceProblem('Operation identities must be integers or strings')
        ids.append(product['id'])
        if product['origin'] != point['id']:
            raise EvidenceProblem('Invalid origin')
        if product['disposition'] == 'remanufacture':
            if not product['operations'] or any(operation not in operations for operation in product['operations']):
                raise EvidenceProblem('Incompatible operation route')
        elif product['disposition'] != 'recycle' or product['operations'] or not scenario['recycling_facilities']:
            raise EvidenceProblem('Recycling needs a facility and an empty operation route')
        if not 0 <= product['release_time'] <= scenario['horizon']:
            raise EvidenceProblem('Release outside horizon')
        numbers.append(product['release_time'])
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        raise EvidenceProblem('Expected unique entity IDs')
    used = set()
    for trip in scenario['trips']:
        if trip['vehicle_id'] not in vehicle_ids or trip['vehicle_id'] in used:
            raise EvidenceProblem('Expected at most one fixed trip per AV')
        used.add(trip['vehicle_id'])
        facilities = {plant['id'], *(site['id'] for site in scenario['recycling_facilities'])}
        if not trip['stops'] or trip['stops'][-1] not in facilities or trip['stops'].count(point['id']) > 1 or any(stop not in facilities | {point['id']} for stop in trip['stops']) or not 0 <= trip['departure_time'] <= scenario['horizon']:
            raise EvidenceProblem('Invalid fixed trip')
        numbers.append(trip['departure_time'])
    if scenario['horizon'] < 0 or not all(type(n) in (int, float) and math.isfinite(n) for n in numbers):
        raise EvidenceProblem('Expected finite inputs')
    scenario['seed_provenance']


# Independently reconstruct seeded inputs

def audit_weighted_index(generator: random.Random, weights: list[float]) -> int:
    """Return a weighted zero-based index using one draw from the supplied stream.

    audit_seeded_generation uses this independent calculation. Read weights only;
    the local random stream advances, but no simulation generator is called.
    """
    threshold = generator.random() * sum(weights)
    for index, weight in enumerate(weights):
        if threshold < weight:
            return index
        threshold -= weight
    return max(index for index, weight in enumerate(weights) if weight > 0)


def audit_seeded_generation(scenario: Record, findings: list[Record]) -> None:
    """Rebuild the recorded seed streams, batches and product qualities independently.

    verify_run supplies scenario and findings. Network provenance delegates to
    audit_network_generation; single-site generation is rebuilt here. Compare
    reconstructed products/batches and exact seed information with saved values.
    Append findings; return None. Require supported algorithm/settings evidence.
    Source data stays unchanged; no simulator imports or files are used for answers.
    """
    if scenario.get('seed_provenance', {}).get('source') == 'seeded_network_configuration':
        audit_network_generation(scenario, findings)
        return
    if not any(key in scenario for key in ('generation', 'quality_classes', 'arrival_batches')) and scenario.get('seed_provenance', {}).get('source') != 'seeded_configuration':
        return
    if not all(key in scenario for key in ('generation', 'quality_classes', 'arrival_batches', 'seed_provenance', 'products')):
        raise EvidenceProblem('Missing generation settings, quality classes, batches, provenance or products')
    provenance = scenario['seed_provenance']
    seed = provenance['master_seed']
    if type(seed) is not int or type(provenance['generation_version']) is not int or provenance['generation_version'] != 1:
        raise EvidenceProblem('Unsupported generation seed/version')
    expected_seeds = {name: int(hashlib.sha256(f'thesis-return-v1|{seed}|{name}'.encode('utf-8')).hexdigest(), 16)
                      for name in ('arrivals_batches', 'qualities')}
    expected_provenance = {'source': 'seeded_configuration', 'master_seed': seed,
        'generation_version': 1, 'algorithm': 'Random.random/exponential-inverse-cdf/weighted-cdf-v1',
        'stream_seeds': expected_seeds}
    validate_expected_seed_structure(expected_provenance, provenance, 'scenario.seed_provenance')
    check_value(findings, 'generation.seed_provenance', expected_provenance, provenance)
    settings = scenario['generation']
    rate, beginning, end = (settings[key] for key in ('arrival_rate', 'start_time', 'end_time'))
    if any(type(value) not in (int, float) for value in (rate, beginning, end)) or rate < 0 or not 0 <= beginning <= end <= scenario['horizon']:
        raise EvidenceProblem('Invalid arrival generation settings')
    distribution = settings['batch_size']
    if type(distribution) is int:
        if distribution <= 0:
            raise EvidenceProblem('Invalid fixed batch size')
    elif (not isinstance(distribution, dict) or not distribution['values']
            or len(distribution['values']) != len(distribution['weights'])
            or any(type(value) is not int or value <= 0 for value in distribution['values'])
            or any(type(weight) not in (int, float) or weight < 0 for weight in distribution['weights'])
            or not 0 < sum(distribution['weights']) < math.inf):
        raise EvidenceProblem('Invalid batch size distribution')
    qualities = scenario['quality_classes']
    identifiers = [quality['id'] for quality in qualities]
    if len(set(identifiers)) != len(identifiers) or not all(isinstance(identifier, str) and identifier for identifier in identifiers):
        raise EvidenceProblem('Invalid quality identifiers')
    overrides = scenario['collection_point'].get('quality_weights', {})
    if set(overrides) - set(identifiers):
        raise EvidenceProblem('Unknown quality weight override')
    operation_ids = {station['operation_id'] for station in scenario['plant']['workstations']}
    for quality in qualities:
        weights = [quality['weight'], overrides.get(quality['id'], quality['weight'])]
        if any(type(weight) not in (int, float) or weight < 0 for weight in weights):
            raise EvidenceProblem('Invalid quality weights')
        if quality['disposition'] == 'recycle':
            if quality['operations']:
                raise EvidenceProblem('Recycling class contains processing operations')
        elif quality['disposition'] != 'remanufacture' or not quality['operations'] or (
                weights[-1] > 0 and any(operation not in operation_ids for operation in quality['operations'])):
            raise EvidenceProblem('Unsupported remanufacturing quality route')
    effective = sorted((quality for quality in qualities
        if scenario['recycling_facilities'] or quality['disposition'] == 'remanufacture'), key=lambda quality: quality['id'])
    weights = [overrides.get(quality['id'], quality['weight']) for quality in effective]
    total = sum(weights)
    if not math.isfinite(total) or (rate > 0 and end > beginning and total <= 0):
        raise EvidenceProblem('No positive effective quality mass')
    probabilities = [weight / total for weight in weights] if total else []
    arrivals = random.Random(expected_seeds['arrivals_batches'])
    quality_stream = random.Random(expected_seeds['qualities'])
    batches, products = [], []
    time = beginning
    if rate > 0:
        while True:
            next_time = time - math.log1p(-arrivals.random()) / rate
            if next_time <= time:
                raise EvidenceProblem('Generated interval cannot advance the clock')
            time = next_time
            if time > end:
                break
            size = distribution if type(distribution) is int else distribution['values'][audit_weighted_index(arrivals, distribution['weights'])]
            product_ids = [f'P{index:06d}' for index in range(len(products) + 1, len(products) + size + 1)]
            batch_id = f'B{len(batches) + 1:06d}'
            batches.append({'id': batch_id, 'release_time': time, 'batch_size': size, 'product_ids': product_ids})
            for product_id in product_ids:
                quality = effective[audit_weighted_index(quality_stream, probabilities)]
                products.append({'id': product_id, 'batch_id': batch_id, 'origin': scenario['collection_point']['id'],
                    'release_time': time, 'quality_class': quality['id'], 'disposition': quality['disposition'],
                    'operations': quality['operations']})
    check_value(findings, 'generation.arrival_batches', batches, scenario['arrival_batches'])
    check_value(findings, 'generation.products', products, scenario['products'])


def audit_generated_coordinates(scenario: Record, findings: list[Record]) -> Record:
    """Rebuild each site's x/y coordinates and return the expected per-site seeds.

    audit_network_generation supplies saved bounds/master seed and findings.
    Use independently derived site-type/ID streams. Append position mismatches;
    read source coordinates unchanged. Later replay checks actual vehicle starts.
    Missing or invalid settings raise EvidenceProblem; no simulation helpers or writes.
    """
    settings = scenario['coordinate_generation']
    if not isinstance(settings, dict):
        raise EvidenceProblem('Missing coordinate bounds')
    for name in ('x_range', 'y_range'):
        bounds = settings.get(name)
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds) or
                bounds[0] > bounds[1] or not math.isfinite(bounds[1] - bounds[0])):
            raise EvidenceProblem(f'Invalid coordinate {name}')
    seed = scenario['seed_provenance']['master_seed']
    seeds = {}
    for site_type, group in (('collection_point', 'collection_points'), ('plant', 'plants'), ('recycler', 'recycling_facilities')):
        seeds[site_type] = {}
        for site in scenario[group]:
            encoded = json.dumps(['coordinates-v1', site_type, site['id']], separators=(',', ':'), ensure_ascii=True)
            site_seed = int(hashlib.sha256(f'thesis-return-v1|{seed}|{encoded}'.encode('utf-8')).hexdigest(), 16)
            seeds[site_type][site['id']] = site_seed
            generator = random.Random(site_seed)
            expected = [generator.uniform(*settings['x_range']), generator.uniform(*settings['y_range'])]
            check_value(findings, 'generation.site_coordinates', expected, site['coordinates'], site_type=site_type, site_id=site['id'])
    return seeds


def audit_network_generation(scenario: Record, findings: list[Record]) -> None:
    """Independently rebuild network coordinates, returns, qualities and calendars.

    audit_seeded_generation calls for seeded format three. Read saved declarations;
    append mismatches and return None. Each point reuses the verifier's single-site
    audit through a local input dictionary. Deep-copy retained declarations before
    replacing product/batch collections. Expected nested quality routes are separate;
    replacement observed records can still refer to the source's nested lists, which
    are read only. No simulation generators, source writes or new events are used.
    Complete supported seed/settings evidence is required.
    """
    provenance = scenario['seed_provenance']
    seed = provenance['master_seed']
    if scenario['format_version'] != 3 or type(seed) is not int or type(provenance['generation_version']) is not int or provenance['generation_version'] != 2:
        raise EvidenceProblem('Unsupported seeded network format, seed or generation version')
    point_seeds = {}
    seeds: Record = {'returns': {}, 'qualities': {}, 'workstation_schedules': {}}
    for point in sorted(scenario['collection_points'], key=lambda item: item['id']):
        identifier = point['id']
        encoded = json.dumps(['network-v2', 'collection', identifier], separators=(',', ':'), ensure_ascii=True)
        point_seed = int(hashlib.sha256(f'thesis-return-v1|{seed}|{encoded}'.encode('utf-8')).hexdigest(), 16)
        point_seeds[identifier] = point_seed
        local_seeds = {name: int(hashlib.sha256(f'thesis-return-v1|{point_seed}|{name}'.encode('utf-8')).hexdigest(), 16)
                       for name in ('arrivals_batches', 'qualities')}
        seeds['returns'][identifier], seeds['qualities'][identifier] = local_seeds['arrivals_batches'], local_seeds['qualities']
        prefix = identifier + ':'
        # These two collections are replaced below from the original evidence.
        # Keep deep ownership of every retained declaration, including quality
        # routes that may become nested expected values in generation findings.
        proxy = copy.deepcopy({key: value for key, value in scenario.items()
                               if key not in ('products', 'arrival_batches')})
        proxy.update(format_version=2, plant=scenario['plants'][0], collection_point=point, generation=point['generation'],
            seed_provenance={'source': 'seeded_configuration', 'master_seed': point_seed, 'generation_version': 1,
                'algorithm': 'Random.random/exponential-inverse-cdf/weighted-cdf-v1', 'stream_seeds': local_seeds})
        proxy['products'] = [{**product, 'id': product['id'].removeprefix(prefix),
            'batch_id': product['batch_id'].removeprefix(prefix)} for product in scenario['products'] if product['origin'] == identifier]
        proxy['arrival_batches'] = [{'id': batch['id'].removeprefix(prefix), 'release_time': batch['release_time'],
            'batch_size': batch['batch_size'], 'product_ids': [product_id.removeprefix(prefix) for product_id in batch['product_ids']]}
            for batch in scenario['arrival_batches'] if batch['origin'] == identifier]
        audit_seeded_generation(proxy, findings)
        check_value(findings, 'generation.product_namespace', True,
            all(product['id'].startswith(prefix) and product['batch_id'].startswith(prefix)
                for product in scenario['products'] if product['origin'] == identifier))
        check_value(findings, 'generation.batch_namespace', True,
            all(batch['id'].startswith(prefix) and all(product_id.startswith(prefix) for product_id in batch['product_ids'])
                for batch in scenario['arrival_batches'] if batch['origin'] == identifier))
    known_points = {point['id'] for point in scenario['collection_points']}
    check_value(findings, 'generation.batch_origins', True, all(batch['origin'] in known_points for batch in scenario['arrival_batches']))
    for field in ('products', 'arrival_batches'):
        check_value(findings, f'generation.{field}_order', sorted(scenario[field],
            key=lambda item: (item['release_time'], item['origin'], item['id'])), scenario[field])
    for plant in scenario['plants']:
        station_seeds = {}
        for station in plant['workstations']:
            if 'window_generation' not in station:
                continue
            settings = station['window_generation']
            beginning, end = settings['start_time'], settings['end_time']
            if not 0 <= beginning <= end <= scenario['horizon']:
                raise EvidenceProblem('Invalid generated schedule time bounds')
            for name in ('initial_delay', 'open_duration', 'closed_duration'):
                bounds = settings[name]
                if (not isinstance(bounds, list) or len(bounds) != 2 or
                        any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds) or
                        not 0 <= bounds[0] <= bounds[1] or (name != 'initial_delay' and bounds[0] <= 0)):
                    raise EvidenceProblem(f'Invalid generated schedule {name}')
            encoded = json.dumps(['network-v2', 'schedule', plant['id'], station['id']], separators=(',', ':'), ensure_ascii=True)
            station_seed = int(hashlib.sha256(f'thesis-return-v1|{seed}|{encoded}'.encode('utf-8')).hexdigest(), 16)
            station_seeds[station['id']] = station_seed
            generator = random.Random(station_seed)
            windows = []
            opening = beginning + generator.uniform(*settings['initial_delay'])
            while opening < end:
                closing = min(end, opening + generator.uniform(*settings['open_duration']))
                if closing <= opening:
                    raise EvidenceProblem('Generated opening does not advance the clock')
                windows.append([opening, closing])
                if closing == end:
                    break
                opening = closing + generator.uniform(*settings['closed_duration'])
                if opening <= closing:
                    raise EvidenceProblem('Generated closure does not advance the clock')
            check_value(findings, 'generation.workstation_windows', windows, station['windows'],
                        plant_id=plant['id'], workstation_id=station['id'])
        if station_seeds:
            seeds['workstation_schedules'][plant['id']] = station_seeds
    if 'coordinate_generation' in scenario:
        seeds['coordinates'] = audit_generated_coordinates(scenario, findings)
    expected_provenance = {'source': 'seeded_network_configuration', 'master_seed': seed, 'generation_version': 2,
        'algorithm': 'site-streams-v2/exponential-inverse-cdf/weighted-cdf/alternating-uniform-windows-v1',
        'point_seeds': point_seeds, 'stream_seeds': seeds}
    if 'coordinate_generation' in scenario:
        expected_provenance['coordinate_algorithm'] = 'site-type-id/uniform-xy-v1'
    validate_expected_seed_structure(expected_provenance, provenance, 'scenario.seed_provenance')
    # Seeds identify random streams exactly; physical-value tolerances must not hide integer changes.
    if not values_match(expected_provenance, provenance, field='seed_provenance', exact=True):
        add_finding(findings, 'violation', 'generation.network_seed_provenance', expected_provenance, provenance)


# Independently audit static aware forecasts and selections

def audit_forecast_context(scenario: Record, state: Record, departed_legs: dict[str, int]) -> Record:
    """Copy released definitions and current audit state for static aware checking.

    InventoryAudit.capture_dispatch supplies scenario/state and departed-leg counts.
    Return a separate context for independent forecasts, excluding unreleased
    products even though saved inputs contain them. Read inputs without changes;
    no files or recorded events are written.
    """
    released = {product_id for product_id, product in state['products'].items() if product['state'] != 'unreleased'}
    snapshot = copy.deepcopy(state)
    snapshot['products'] = {product_id: product for product_id, product in snapshot['products'].items() if product_id in released}
    physical = {key: copy.deepcopy(scenario[key]) for key in
                ('horizon', 'plant', 'collection_point', 'recycling_facilities', 'vehicles', 'trips')}
    physical['products'] = [{key: copy.deepcopy(product[key]) for key in
        ('id', 'origin', 'release_time', 'disposition', 'operations')} for product in scenario['products'] if product['id'] in released]
    return {'scenario': physical, 'state': snapshot, 'departed_legs': dict(departed_legs)}


def audit_delivery_time(scenario: Record, state: Record, trip: Record, departed_legs: int = 0) -> float:
    """Return the final arrival time through the fixed trip's remaining stops.

    audit_candidate_forecast passes independently reconstructed state and departed
    leg count. Continue from an active leg or current site. Read only; invalid
    physical timing raises PhysicalEvidenceError, not a missing forecast.
    """
    vehicle = state['vehicles'][trip['vehicle_id']]
    speed = next(vehicle['speed'] for vehicle in scenario['vehicles'] if vehicle['id'] == trip['vehicle_id'])
    sites = {site['id']: site for site in [scenario['plant'], scenario['collection_point'], *scenario['recycling_facilities']]}
    leg = vehicle['active_leg']
    time = leg['arrival_time'] if leg is not None else state['time']
    position = leg['target_coordinates'] if leg is not None else sites[vehicle['location']]['coordinates']
    for stop in trip['stops'][departed_legs:]:
        target = sites[stop]['coordinates']
        time = audit_arrival(time, audit_distance(position, target), speed)
        position = target
    return time


def audit_candidate_forecast(context: Record, trip: Record, candidate_id: str, selected: list[str]) -> Record:
    """Predict fixed-trip admission and first processing start without simulation code.

    audit_quality_selection passes a context, trip, candidate ID and earlier chosen
    IDs. Copy state, include committed deliveries and advance plant work in time order.
    Return arrival/admission/start/wait, with None for unobserved opportunities.
    Respect held slots and full-window fitting. Change only the separate forecast
    copy; write no events or files. Invalid evidence/arithmetic raises.
    """
    scenario, state = context['scenario'], copy.deepcopy(context['state'])
    arrival = audit_delivery_time(scenario, state, trip)
    result = {'predicted_arrival': arrival, 'predicted_admission': None,
              'predicted_first_start': None, 'predicted_inventory_wait': None}
    if trip['stops'][-1] != scenario['plant']['id']:
        result['predicted_admission'] = arrival if arrival <= scenario['horizon'] else None
        return result
    definitions = {product['id']: product for product in scenario['products']}
    deliveries = []
    for committed in scenario['trips']:
        vehicle_id = committed['vehicle_id']
        if vehicle_id == trip['vehicle_id'] or committed['stops'][-1] != scenario['plant']['id'] or vehicle_id in state['waiting_vehicles']:
            continue
        carried = sorted(product_id for product_id, product in state['products'].items()
            if product['assigned_vehicle'] == vehicle_id and product['state'] in ('at_collection_point', 'onboard_av'))
        if carried:
            deliveries.append((audit_delivery_time(scenario, state, committed, context['departed_legs'][vehicle_id]), vehicle_id, carried))
    deliveries.append((arrival, trip['vehicle_id'], sorted([*selected, candidate_id])))
    calendar = []
    for station in scenario['plant']['workstations']:
        intervals = []
        for opening, closing in station['windows']:
            if intervals and intervals[-1][1] == opening:
                intervals[-1][1] = closing
            else:
                intervals.append([opening, closing])
        calendar.append((station, intervals))
    calendar.sort(key=lambda item: item[0]['id'])
    while state['time'] <= scenario['horizon']:
        time = state['time']
        # Complete all due active work before arrivals and instantaneous settling.
        for station_id in sorted(state['workstations']):
            operation = state['workstations'][station_id]
            if operation is None or operation['end_time'] > time:
                continue
            product_id = operation['product_id']
            product = state['products'][product_id]
            product['next_operation'] += 1
            state['workstations'][station_id] = None
            if product['next_operation'] < len(definitions[product_id]['operations']):
                if product_id in state['held_slots']:
                    state['held_slots'].remove(product_id)
                state['inventory'][product_id] = time
        due = sorted((delivery for delivery in deliveries if delivery[0] <= time), key=lambda delivery: delivery[1])
        for _, vehicle_id, load in due:
            state['vehicles'][vehicle_id]['load'] = list(load)
            state['waiting_vehicles'][vehicle_id] = time
        deliveries = [delivery for delivery in deliveries if delivery[0] > time]
        while True:
            changed = False
            for vehicle_id in sorted(state['waiting_vehicles'], key=lambda vehicle_id: (state['waiting_vehicles'][vehicle_id], vehicle_id)):
                load = state['vehicles'][vehicle_id]['load']
                capacity = scenario['plant']['inventory_capacity']
                while load and (capacity is None or len(state['inventory']) + len(state['held_slots']) < capacity):
                    product_id = load.pop(0)
                    state['inventory'][product_id] = time
                    if product_id == candidate_id:
                        result['predicted_admission'] = time
                    changed = True
                if not load:
                    del state['waiting_vehicles'][vehicle_id]
            started = False
            for station, intervals in calendar:
                if state['workstations'][station['id']] is not None:
                    continue
                eligible = sorted((product_id for product_id in state['inventory']
                    if definitions[product_id]['operations'][state['products'][product_id]['next_operation']] == station['operation_id']),
                    key=lambda product_id: (state['inventory'][product_id], product_id))
                if not eligible:
                    continue
                window = next((interval for interval in intervals if interval[0] <= time < interval[1]
                               and audit_end(time, station['duration'], 'processing') <= interval[1]), None)
                if window is None:
                    continue
                product_id = eligible[0]
                state['inventory'].pop(product_id)
                operation_index = state['products'][product_id]['next_operation']
                state['workstations'][station['id']] = {'product_id': product_id,
                    'end_time': audit_end(time, station['duration'], 'processing')}
                if scenario['plant']['inventory_capacity'] is not None and operation_index + 1 < len(definitions[product_id]['operations']):
                    state['held_slots'].append(product_id)
                if product_id == candidate_id:
                    result.update(predicted_first_start=time, predicted_inventory_wait=time - result['predicted_admission'])
                    return result
                changed, started = True, True
                break
            if not changed and not started:
                break
        future = [delivery[0] for delivery in deliveries]
        future.extend(operation['end_time'] for operation in state['workstations'].values() if operation is not None)
        future.extend(opening for _, intervals in calendar for opening, _ in intervals if opening > time)
        if not future or min(future) > scenario['horizon']:
            return result
        state['time'] = min(future)
    return result


def audit_quality_selection(policy_input: Record) -> tuple[list[str], list[Record], str]:
    """Independently choose static aware products one payload slot at a time.

    InventoryAudit.capture_dispatch supplies the reconstructed permitted input.
    Use audit_candidate_forecast on separate state and apply the documented score.
    Return selected IDs, all candidate records and reason. Keep fixed destination,
    stops and departure; change local lists only. Saved scores are not used as answers.
    """
    context = policy_input['forecast_context']
    scenario = context['scenario']
    trip = next(trip for trip in scenario['trips'] if trip['vehicle_id'] == policy_input['vehicle_id'])
    capacity = next(vehicle['payload_capacity'] for vehicle in scenario['vehicles'] if vehicle['id'] == trip['vehicle_id'])
    disposition = 'remanufacture' if trip['stops'][-1] == scenario['plant']['id'] else 'recycle'
    remaining = [request['product_id'] for request in policy_input['requests'] if request['disposition'] == disposition]
    selected, audit = [], []
    if scenario['collection_point']['id'] not in trip['stops']:
        return selected, audit, 'Fixed trip has no pickup stop'
    for round_index in range(min(capacity, len(remaining))):
        candidates = []
        for product_id in remaining:
            prediction = audit_candidate_forecast(context, trip, product_id, selected)
            candidate = {'selection_round': round_index, 'product_id': product_id, 'destination': trip['stops'][-1],
                'pickup_stop_index': trip['stops'].index(scenario['collection_point']['id']),
                'delivery_stop_index': len(trip['stops']) - 1, 'added_distance': 0.0, **prediction}
            feasible_start = prediction['predicted_first_start'] is not None
            candidate['score'] = [int(not feasible_start), prediction['predicted_inventory_wait'] if feasible_start else 0.0,
                0.0, product_id, candidate['pickup_stop_index'], candidate['delivery_stop_index']]
            candidates.append(candidate)
        winner = min(candidates, key=lambda candidate: candidate['score'])['product_id']
        for candidate in candidates:
            candidate['selected'] = candidate['product_id'] == winner
        audit.extend(candidates)
        selected.append(winner)
        remaining.remove(winner)
    return selected, audit, 'Prefer start within horizon, then admission-to-first-start wait, added distance, product ID and stop indexes'


def audit_aware_dispatch_records(expected_records: list[Record], actual_records: list[Record], findings: list[Record]) -> None:
    """Compare rebuilt static aware candidates and selections against saved records.

    InventoryAudit.reconcile supplies expected/saved decisions and findings.
    Check observations, candidate coverage, forecasts, scores and selection in order;
    append differences with decision/event/product context. Return None.
    Missing required fields raise EvidenceProblem. Do not recalculate expectations
    from possibly forged saved scores or change source evidence.
    """
    check_value(findings, 'policy.decision_count', len(expected_records), len(actual_records))
    for expected, actual in zip(expected_records, actual_records):
        event = {'time': expected['time'], 'sequence': expected['last_event_sequence'] + 1,
                 'vehicle_id': expected['policy_input']['vehicle_id']}
        for field in ('policy_input', 'candidates', 'decision', 'selection_reason', 'tie_break'):
            if field not in actual:
                raise EvidenceProblem(f'Aware dispatch record lacks {field}')
        for field in ('policy_input', 'selection_reason', 'tie_break'):
            check_value(findings, f'policy.{field}', expected[field], actual[field], event)
        if 'assigned_products' not in actual['decision']:
            raise EvidenceProblem('Aware dispatch selection lacks assigned_products')
        check_value(findings, 'policy.selection', expected['decision']['assigned_products'], actual['decision']['assigned_products'], event)
        expected_keys = [(candidate['selection_round'], candidate['product_id']) for candidate in expected['candidates']]
        actual_keys = [(candidate['selection_round'], candidate['product_id']) for candidate in actual['candidates']]
        check_value(findings, 'policy.candidate_coverage', expected_keys, actual_keys, event)
        for wanted, observed in zip(expected['candidates'], actual['candidates']):
            for field, value in wanted.items():
                if field not in observed:
                    raise EvidenceProblem(f"Candidate {wanted['product_id']} lacks {field}")
                check_value(findings, f'policy.candidate.{field}', value, observed[field], event,
                            product_id=wanted['product_id'], selection_round=wanted['selection_round'])


class InventoryAudit:
    """Reconstruct fixed inventory events and expected decisions without simulation imports."""

    # Create independent audit state and shared comparison helpers

    def __init__(self, scenario: Record, findings: list[Record]) -> None:
        """Create empty independent state and lookups for fixed inventory auditing.

        verify_run calls with a validated execution scenario and shared findings list;
        DynamicAudit also calls through super for common fields. Definition lookups refer
        to the supplied scenario, while physical containers are new. Track required
        immediate follow-up events, departures and expected decisions. Return None.
        No saved files change and no simulation events execute.
        """
        self.scenario = scenario
        self.findings = findings
        self.products = {p['id']: p for p in scenario['products']}
        self.vehicles = {v['id']: v for v in scenario['vehicles']}
        self.stations = {s['id']: s for s in scenario['plant']['workstations']}
        self.trips = {t['vehicle_id']: t for t in scenario['trips']}
        self.delivery_sites = {site['id']: site for site in [scenario['plant'], *scenario['recycling_facilities']]}
        self.route_sites = {**self.delivery_sites, scenario['collection_point']['id']: scenario['collection_point']}
        self.range_plans: dict[str, Record] = {}
        self.state = {'time': 0, 'products': {product_id: {'state': 'unreleased', 'location': None,
            'next_operation': 0, 'assigned_vehicle': None, 'planned_destination': None,
            'actual_destination': None, 'milestones': {}, 'history': [], 'inventory_wait': 0.0} for product_id in self.products},
            'vehicles': {vehicle_id: {'status': 'idle', 'location': v['initial_site'], 'load': [],
                'active_leg': None, 'distance': 0.0, 'waiting_since': None,
                'waiting_to_unload': 0.0, 'wait_logged': False} for vehicle_id, v in self.vehicles.items()},
            'workstations': dict.fromkeys(self.stations), 'inventory': {}, 'held_slots': [], 'waiting_vehicles': {}}
        for vehicle_id, definition in self.vehicles.items():
            if definition['maximum_range'] is not None:
                self.state['vehicles'][vehicle_id].update(remaining_range=definition['maximum_range'],
                    position=list(self.route_sites[definition['initial_site']]['coordinates']), leg_distance_accounted=0.0)
        self.required: list[tuple[str, str, str]] = []
        self.dispatched: set[str] = set()
        self.leg_counts = dict.fromkeys(self.vehicles, 0)
        self.expected_decisions: list[Record] = []
        self.finished = False

    def require(self, rule: str, expected: Any, observed: Any, event: Record) -> None:
        """Compare one expected/observed value and stop unsafe replay on mismatch.

        Audit transition checks supply the rule and event context. check_value appends
        a violation; then raise ReplayViolation. Return None if valid; no evidence writes.
        """
        if not check_value(self.findings, rule, expected, observed, event):
            raise ReplayViolation()

    def match_fields(self, event: Record, details: Record) -> None:
        """Check a recorded event's full details against independently expected fields.

        Event consumers call this. Missing/extra unsupported fields raise EvidenceProblem;
        mismatches use require and may stop reconstruction. Read event only; findings
        can change, but source records and files do not.
        """
        for field, expected in details.items():
            if field not in event:
                raise EvidenceProblem(f"Event {event['sequence']} lacks {field}")
            self.require(f"{event['event_type']}.{field}", expected, event[field], event)
        if set(event) != set(details) | {'sequence', 'time', 'event_type'}:
            raise EvidenceProblem(f"Unsupported fields in event {event['sequence']}")

    # Find required inventory actions and deadlines

    def occupancy(self) -> int:
        """Return waiting inventory plus held return slots from this audit's local plant.

        Admission/capacity/deadline checks call this. Read only; a held slot reserves
        space for an existing processing product, not an additional product.
        """
        return len(self.state['inventory']) + len(self.state['held_slots'])

    def window_opportunity(self, station: Record, earliest: float) -> tuple[float, list[float]] | None:
        """Return the earliest full-fit start/window from earliest time, or None.

        Deadline and operation-start checks supply the station and absolute time.
        Independently join touching windows and require the whole positive duration
        to fit through the horizon. Read only; invalid arithmetic raises.
        """
        intervals = []
        for start, end in station['windows']:
            if intervals and intervals[-1][1] == start:
                intervals[-1] = (intervals[-1][0], end)
            else:
                intervals.append((start, end))
        for opening, closing in intervals:
            start = max(earliest, opening)
            if start <= self.scenario['horizon'] and start < closing and audit_end(start, station['duration'], 'processing') <= closing:
                return start, [opening, closing]
        return None

    def eligible_products(self, station_id: str) -> list[str]:
        """Return next-operation matches by inventory-entry time, then product ID.

        pending_deadlines and operation-start checks call this. An intermediate return
        sets a new inventory-entry time. Read reconstructed inventory only; change nothing.
        """
        return sorted((product_id for product_id in self.state['inventory'] if
            self.products[product_id]['operations'][self.state['products'][product_id]['next_operation']] == self.stations[station_id]['operation_id']),
            key=lambda product_id: (self.state['inventory'][product_id], product_id))

    def waiting_order(self) -> list[str]:
        """Return waiting vehicle IDs by original plant arrival time, then vehicle ID.

        Admission and deadline checks call this. Read audit queues only; do not
        reorder or change their stored records.
        """
        return sorted(self.state['waiting_vehicles'], key=lambda vehicle_id: (self.state['waiting_vehicles'][vehicle_id], vehicle_id))

    def pending_deadlines(self) -> list[float]:
        """Return times of required releases, trips, arrivals, completions and enabled service.

        check_clock_boundary and horizon checks use the earliest time to detect skipped
        actions. Read reconstructed state and calendars only; return a new list.
        This checks physical opportunities, not a new routing decision.
        """
        now = self.state['time']
        deadlines = [p['release_time'] for product_id, p in self.products.items() if self.state['products'][product_id]['state'] == 'unreleased']
        deadlines.extend(t['departure_time'] for vehicle_id, t in self.trips.items() if vehicle_id not in self.dispatched)
        deadlines.extend(v['active_leg']['arrival_time'] for v in self.state['vehicles'].values() if v['active_leg'] is not None)
        deadlines.extend(op['end_time'] for op in self.state['workstations'].values() if op is not None)
        for workstation_id, active in self.state['workstations'].items():
            if active is None and self.eligible_products(workstation_id):
                opportunity = self.window_opportunity(self.stations[workstation_id], now)
                if opportunity:
                    deadlines.append(opportunity[0])
        capacity = self.scenario['plant']['inventory_capacity']
        if self.waiting_order() and (capacity is None or self.occupancy() < capacity):
            deadlines.append(now)
        return deadlines

    def check_clock_boundary(self, event: Record) -> None:
        """Check that time does not advance past required work or unfinished same-time actions.

        replay calls before advancing audit state. Require immediate follow-ups to be
        finished, inspect pending_deadlines and check physical membership. Return None;
        only findings may change and a proved violation raises ReplayViolation.
        """
        now = self.state['time']
        if event['time'] <= now:
            return
        self.require('events.immediate_followup', [], self.required, event)
        deadlines = self.pending_deadlines()
        if deadlines:
            self.require('events.no_skipped_action', True, event['time'] <= min(deadlines) + TOLERANCE, event)
        self.check_physical_state(event)

    # Check that products and reserved slots are accounted for

    def check_physical_state(self, event: Record) -> None:
        """Check that reconstructed products are counted once and holds/waits are valid.

        Clock-boundary and horizon checks pass an event for diagnostics. Compare
        membership, expected held slots and logged waiting vehicles. Only findings may
        change; a mismatch stops unsafe reconstruction. A held slot is capacity reserved
        for a processing product, not another physical product. No source writes.
        """
        for product_id, product in self.state['products'].items():
            physical = int(product['state'] == 'at_collection_point') + int(product_id in self.state['inventory'])
            physical += sum(product_id in v['load'] for v in self.state['vehicles'].values())
            physical += sum(op is not None and op['product_id'] == product_id for op in self.state['workstations'].values())
            physical += int(product['state'] in ('remanufactured', 'recycled'))
            self.require('product.conservation', int(product['state'] != 'unreleased'), physical, event)

        expected_holds = sorted(op['product_id'] for op in self.state['workstations'].values()
            if op is not None and op['operation_index'] + 1 < len(self.products[op['product_id']]['operations']))
        if self.scenario['plant']['inventory_capacity'] is None:
            expected_holds = []
        self.require('inventory.hold_ownership', expected_holds, sorted(self.state['held_slots']), event)
        self.require('inventory.wait_evidence', [], sorted(vehicle_id for vehicle_id in self.state['waiting_vehicles']
            if not self.state['vehicles'][vehicle_id]['wait_logged']), event)

    # Reconstruct range plans and actual travel

    def evaluate_range_plan(self, vehicle_id: str, start_index: int = 0) -> Record:
        """Return independent range requirements for every remaining fixed facility segment.

        Range-event and departure checks pass vehicle ID and zero-based next-stop index.
        Use actual allowance/position, refilling only at configured facilities.
        Return feasibility/segment/rejection data without changing audit state or files.
        Invalid physical calculations raise.
        """
        maximum = self.vehicles[vehicle_id]['maximum_range']
        if maximum is None:
            return {'feasible': True, 'segments': []}
        vehicle = self.state['vehicles'][vehicle_id]
        coordinates = vehicle['position']
        allowance = vehicle['remaining_range']
        beginning = vehicle['location']
        total = 0.0
        segments = []
        for site_id in self.trips[vehicle_id]['stops'][start_index:]:
            site = self.route_sites[site_id]
            total = audit_finite(total + (audit_finite(math.hypot(site['coordinates'][0] - coordinates[0], site['coordinates'][1] - coordinates[1]), 'distance')), 'total')
            coordinates = site['coordinates']
            if site_id in self.delivery_sites:
                segments.append({'start': beginning, 'end': site_id, 'required_distance': total, 'available_range': allowance})
                beginning, total, allowance = site_id, 0.0, maximum
        rejection = next((segment for segment in segments if segment['required_distance'] > segment['available_range'] + TOLERANCE), None)
        result = {'feasible': rejection is None, 'segments': segments}
        if rejection is not None:
            result['rejection'] = {'reason': 'range_infeasible', **rejection}
        return result

    def advance_range_travel(self, time: float, event: Record) -> None:
        """Add finite vehicles' actual travel before checking an event at time.

        replay supplies absolute time and event context. Update only audit positions,
        distance, accounted leg distance and allowance for newly covered travel.
        Use the same accounting at horizon; do not count a part of a leg twice.
        Return None; violations append findings or raise. No source records change.
        """
        for vehicle_id, definition in self.vehicles.items():
            if definition['maximum_range'] is None:
                continue
            vehicle = self.state['vehicles'][vehicle_id]
            leg = vehicle['active_leg']
            if leg is None:
                continue
            covered = audit_prefix(time - leg['departure_time'], definition['speed'], leg['distance'])
            change = covered - vehicle['leg_distance_accounted']
            self.require('range.actual_distance', True, -TOLERANCE <= change <= vehicle['remaining_range'] + TOLERANCE, event)
            vehicle['distance'] = audit_finite(vehicle['distance'] + (change), "vehicle['distance']")
            vehicle['remaining_range'] = max(0.0, vehicle['remaining_range'] - change)
            vehicle['leg_distance_accounted'] = covered
            ratio = covered / leg['distance'] if leg['distance'] else 1.0
            vehicle['position'] = [audit_finite(leg['origin_coordinates'][axis] + ratio *
                (leg['target_coordinates'][axis] - leg['origin_coordinates'][axis]), 'travel.position') for axis in (0, 1)]

    def consume_range_event(self, event: Record) -> None:
        """Check a recorded range plan, rejected trip or facility arrival reset.

        consume_event supplies the saved event. Independently compute required/available
        range or reset, compare details, and update audit tracking. A collection-point
        reset is forbidden. Return None; invalid evidence/violations raise with context.
        Only audit data/findings change, not source evidence.
        """
        vehicle_id = event['vehicle_id']
        vehicle = self.state['vehicles'][vehicle_id]
        maximum = self.vehicles[vehicle_id]['maximum_range']
        self.require('range.limited_vehicle', True, maximum is not None, event)
        kind = event['event_type']
        if kind == 'range_plan_check':
            self.require('range.single_plan_check', False, vehicle_id in self.range_plans, event)
            self.require('range.plan_time', self.trips[vehicle_id]['departure_time'], event['time'], event)
            expected = self.evaluate_range_plan(vehicle_id)
            self.match_fields(event, {'vehicle_id': vehicle_id, **expected})
            self.range_plans[vehicle_id] = expected
        elif kind == 'trip_rejected':
            plan = self.range_plans[vehicle_id]
            self.require('range.justified_rejection', False, plan['feasible'], event)
            self.match_fields(event, {'vehicle_id': vehicle_id, **plan['rejection']})
            self.require('range.no_departure_on_rejection', ('idle', []), (vehicle['status'], vehicle['load']), event)
        else:
            self.require('range.reset_facility', True, event['facility_id'] in self.delivery_sites, event)
            self.require('range.reset_at_vehicle_site', vehicle['location'], event['facility_id'], event)
            self.match_fields(event, {'vehicle_id': vehicle_id, 'facility_id': vehicle['location'],
                'range_before': vehicle['remaining_range'], 'range_after': maximum, 'load': list(vehicle['load'])})
            vehicle['remaining_range'] = maximum

    # Rebuild decisions before applying recorded events

    def capture_dispatch(self, event: Record, vehicle_id: str) -> None:
        """Rebuild permitted requests and selection before consuming fixed-trip assignments.

        consume_event calls at the first assignment/departure/rejection for that trip.
        Use released inventory, scheduled departure/vehicle ties and independent range
        checks. For aware policy only, independently calculate processing predictions.
        Save the expected decision and required immediate events on this audit.
        Return None; no saved records change or physical events are emitted.
        """
        self.require('policy.configured_trip', True, vehicle_id in self.trips, event)
        trip = self.trips[vehicle_id]
        due_vehicles = sorted(vehicle_id for vehicle_id, plan in self.trips.items()
                              if vehicle_id not in self.dispatched and values_match(plan['departure_time'], event['time']))
        self.require('policy.vehicle_id_tie', due_vehicles[0] if due_vehicles else None, vehicle_id, event)
        self.require('policy.departure_time', trip['departure_time'], event['time'], event)
        available = sorted((product_id for product_id, p in self.state['products'].items()
            if p['state'] == 'at_collection_point' and p['assigned_vehicle'] is None),
            key=lambda product_id: (self.products[product_id]['release_time'], product_id))
        disposition = 'remanufacture' if trip['stops'][-1] == self.scenario['plant']['id'] else 'recycle'
        selected = [product_id for product_id in available if self.products[product_id]['disposition'] == disposition][:self.vehicles[vehicle_id]['payload_capacity']]
        plan = {'feasible': True, 'segments': []}
        if self.vehicles[vehicle_id]['maximum_range'] is not None:
            self.require('range.plan_evidence', True, vehicle_id in self.range_plans, event)
            plan = self.range_plans[vehicle_id]
        if not plan['feasible'] or self.scenario['collection_point']['id'] not in trip['stops']:
            selected = []
        self.expected_decisions.append({'time': event['time'], 'trigger': 'static_departure',
            'last_event_sequence': event['sequence'] - 1, 'policy_input': {'vehicle_id': vehicle_id,
                'stops': trip['stops'], 'requests': [{'product_id': product_id, 'collection_point': self.products[product_id]['origin'],
                'release_time': self.products[product_id]['release_time'], 'disposition': self.products[product_id]['disposition']} for product_id in available]},
            'decision': {'assigned_products': selected, 'stops': trip['stops']}, 'tie_break': 'release_time, product_id'})
        if self.scenario['policy'] == 'static_quality_aware':
            boundary = self.expected_decisions[-1]['policy_input']
            for request in boundary['requests']:
                request['operations'] = list(self.products[request['product_id']]['operations'])
            boundary['forecast_context'] = audit_forecast_context(self.scenario, self.state, self.leg_counts)
            if plan['feasible']:
                selected, candidates, reason = audit_quality_selection(boundary)
            else:
                selected, candidates, reason = [], [], 'Fixed trip rejected by range check'
            self.expected_decisions[-1].update(candidates=candidates, selection_reason=reason,
                tie_break='product_id, pickup_stop_index, delivery_stop_index')
            self.expected_decisions[-1]['decision']['assigned_products'] = selected
        self.dispatched.add(vehicle_id)
        if not plan['feasible']:
            self.expected_decisions[-1]['decision']['rejection'] = plan['rejection']
            self.required.append(('trip_rejected', 'vehicle_id', vehicle_id))
        else:
            self.required.extend(('assignment', 'product_id', product_id) for product_id in selected)
            self.required.append(('leg_departure', 'vehicle_id', vehicle_id))

    def consume_event(self, event: Record) -> None:
        """Check one fixed inventory event, then apply it to independent audit state.

        replay passes records in sequence order. Check ownership, capacity, priorities,
        range and mandatory follow-ups before updating local physical tracking.
        Return None. A proved mismatch records its rule and raises ReplayViolation;
        missing or unsupported evidence raises EvidenceProblem. verify_run classifies
        those results. No simulation calls or source files change.
        """
        kind, time = event['event_type'], event['time']
        plant = self.scenario['plant']
        product_id, vehicle_id, workstation_id = event.get('product_id'), event.get('vehicle_id'), event.get('workstation_id')
        if vehicle_id is not None:
            self.require('vehicle.known_identity', True, vehicle_id in self.vehicles, event)
        if product_id is not None:
            self.require('product.known_identity', True, product_id in self.products, event)
        if kind == 'recycler_acceptance':
            self.require('recycling.disposition', 'recycle', self.products[product_id]['disposition'], event)
        if kind == 'plant_admission':
            self.require('plant.disposition', 'remanufacture', self.products[product_id]['disposition'], event)
        if kind == 'range_reset' and event.get('facility_id') not in self.delivery_sites:
            self.require('range.reset_facility', sorted(self.delivery_sites), event.get('facility_id'), event)
        if kind in ('assignment', 'leg_departure', 'trip_rejected') and vehicle_id not in self.dispatched:
            self.capture_dispatch(event, vehicle_id)
        had_required = bool(self.required)
        if self.required:
            expected_kind, identity_field, identifier = self.required.pop(0)
            self.require('events.immediate_followup_type', expected_kind, kind, event)
            self.require('events.immediate_followup_identity', identifier, event.get(identity_field), event)
        # Capacity is checked against replayed stock BEFORE accepting an admission.
        if kind == 'plant_admission':
            capacity = plant['inventory_capacity']
            if capacity is not None and self.occupancy() + 1 > capacity:
                self.require('inventory.capacity', f'waiting + held <= {capacity}',
                    {'waiting_after_admission': len(self.state['inventory']) + 1,
                     'held_slots': len(self.state['held_slots']), 'attempted_occupancy': self.occupancy() + 1}, event)
            if self.state['vehicles'][vehicle_id]['wait_logged']:
                self.require('inventory.retry_required', True, had_required, event)
        if kind in ('inventory_hold', 'inventory_slot_release', 'inventory_return', 'remanufactured', 'pickup', 'recycler_acceptance', 'range_reset'):
            self.require('events.required_transition', True, had_required, event)
        if workstation_id is not None:
            self.require('workstation.known_identity', True, workstation_id in self.stations, event)
        if kind not in ('operation_complete', 'inventory_return', 'remanufactured'):
            self.require('events.completion_precedence', [],
                sorted(s for s, op in self.state['workstations'].items() if op is not None and op['end_time'] <= time), event)
        if kind in ('assignment', 'leg_departure', 'plant_admission', 'unload_wait', 'unload_retry', 'operation_start'):
            self.require('events.release_precedence', [], sorted(product_id for product_id, definition in self.products.items()
                if self.state['products'][product_id]['state'] == 'unreleased' and definition['release_time'] <= time), event)
        if kind in ('plant_admission', 'unload_wait', 'unload_retry', 'operation_start'):
            self.require('events.arrival_batch_precedence', [], sorted(vehicle_id for vehicle_id, vehicle in self.state['vehicles'].items()
                if vehicle['active_leg'] is not None and vehicle['active_leg']['arrival_time'] <= time
                and vehicle['active_leg']['departure_time'] < time), event)
        if kind in ('range_plan_check', 'trip_rejected', 'range_reset'):
            self.consume_range_event(event)
        elif kind == 'return_release':
            definition = event['product']
            product_id = definition['id']
            self.require('product.known_release', True, product_id in self.products, event)
            self.match_fields(event, {'product': self.products[product_id]})
            self.require('product.release_time', self.products[product_id]['release_time'], time, event)
            product = self.state['products'][product_id]
            self.require('product.unique_release', 'unreleased', product['state'], event)
            product.update(state='at_collection_point', location=self.products[product_id]['origin'])
            product['milestones']['release'] = time
        elif kind == 'assignment':
            product = self.state['products'][product_id]
            destination = self.trips[vehicle_id]['stops'][-1]
            self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'destination': destination})
            self.require('product.assignment_state', ('at_collection_point', None),
                         (product['state'], product['assigned_vehicle']), event)
            product.update(assigned_vehicle=vehicle_id, planned_destination=destination)
        elif kind == 'leg_departure':
            vehicle = self.state['vehicles'][vehicle_id]
            self.require('vehicle.not_busy', (None, 'idle'), (vehicle['active_leg'], vehicle['status']), event)
            count = self.leg_counts[vehicle_id]
            stops = self.trips[vehicle_id]['stops']
            self.require('vehicle.fixed_leg_count', True, count < len(stops), event)
            origin = self.route_sites[self.vehicles[vehicle_id]['initial_site'] if count == 0 else stops[count - 1]]
            target = self.route_sites[stops[count]]
            self.require('vehicle.departure_site', origin['id'], vehicle['location'], event)
            distance = audit_finite(math.hypot(target['coordinates'][0] - origin['coordinates'][0], target['coordinates'][1] - origin['coordinates'][1]), 'distance')
            leg = {'vehicle_id': vehicle_id, 'origin': origin['id'], 'target': target['id'],
                'origin_coordinates': origin['coordinates'], 'target_coordinates': target['coordinates'],
                'departure_time': time, 'arrival_time': audit_arrival(time, distance, self.vehicles[vehicle_id]['speed']),
                'distance': distance, 'load': list(vehicle['load'])}
            if self.vehicles[vehicle_id]['maximum_range'] is not None:
                self.require('range.remaining_plan', True, self.evaluate_range_plan(vehicle_id, count)['feasible'], event)
                leg['remaining_range_at_departure'] = vehicle['remaining_range']
                vehicle['leg_distance_accounted'] = 0.0
            self.match_fields(event, leg)
            vehicle.update(status='travelling', active_leg=leg)
            self.leg_counts[vehicle_id] += 1
        elif kind == 'leg_arrival':
            vehicle = self.state['vehicles'][vehicle_id]
            leg = vehicle['active_leg']
            self.require('vehicle.active_arrival', True, leg is not None, event)
            audit_observed_progress(leg['departure_time'], time, 'travel', event, positive=leg['distance'] > 0)
            details = dict(leg)
            limited = self.vehicles[vehicle_id]['maximum_range'] is not None
            if limited:
                details['remaining_range_on_arrival'] = vehicle['remaining_range']
                vehicle['leg_distance_accounted'] = 0.0
                vehicle['position'] = list(leg['target_coordinates'])
            else:
                vehicle['distance'] = audit_finite(vehicle['distance'] + (leg['distance']), "vehicle['distance']")
            self.match_fields(event, details)
            self.require('vehicle.arrival_time', leg['arrival_time'], time, event)
            vehicle.update(status='idle', location=leg['target'], active_leg=None)
            if limited and leg['target'] in self.delivery_sites:
                self.required.append(('range_reset', 'vehicle_id', vehicle_id))
            final_stop = self.leg_counts[vehicle_id] == len(self.trips[vehicle_id]['stops'])
            if leg['target'] == self.scenario['collection_point']['id']:
                assigned = sorted(product_id for product_id, product in self.state['products'].items()
                                  if product['assigned_vehicle'] == vehicle_id and product['state'] == 'at_collection_point')
                self.required.extend(('pickup', 'product_id', product_id) for product_id in assigned)
            if final_stop:
                if leg['target'] == plant['id'] and vehicle['load']:
                    self.state['waiting_vehicles'][vehicle_id] = time
                elif leg['target'] in {recycler['id'] for recycler in self.scenario['recycling_facilities']}:
                    self.required.extend(('recycler_acceptance', 'product_id', product_id) for product_id in vehicle['load'])
            else:
                self.required.append(('leg_departure', 'vehicle_id', vehicle_id))
        elif kind == 'pickup':
            product, vehicle = self.state['products'][product_id], self.state['vehicles'][vehicle_id]
            self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'collection_point': self.scenario['collection_point']['id']})
            self.require('product.pickup_ownership', ('at_collection_point', vehicle_id), (product['state'], product['assigned_vehicle']), event)
            self.require('vehicle.pickup_site', self.scenario['collection_point']['id'], vehicle['location'], event)
            self.require('vehicle.payload', True, len(vehicle['load']) < self.vehicles[vehicle_id]['payload_capacity'], event)
            product.update(state='onboard_av', location=vehicle_id)
            product['milestones']['pickup'] = time
            vehicle['load'].append(product_id)
        elif kind == 'recycler_acceptance':
            product, vehicle = self.state['products'][product_id], self.state['vehicles'][vehicle_id]
            recycler_id = event['recycler_id']
            self.require('recycling.configured_facility', True, recycler_id in {r['id'] for r in self.scenario['recycling_facilities']}, event)
            self.require('recycling.operation_route', [], self.products[product_id]['operations'], event)
            self.require('recycling.onboard_ownership', ('onboard_av', vehicle_id), (product['state'], product['location']), event)
            self.require('recycling.destination', recycler_id, product['planned_destination'], event)
            self.require('recycling.vehicle_location', recycler_id, vehicle['location'], event)
            self.require('recycling.payload', True, product_id in vehicle['load'], event)
            self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'recycler_id': recycler_id})
            vehicle['load'].remove(product_id)
            product.update(state='recycled', location=None, actual_destination=recycler_id)
            product['milestones']['recycling'] = time
        elif kind == 'unload_wait':
            self.require('inventory.registered_arrival', True, vehicle_id in self.state['waiting_vehicles'], event)
            vehicle = self.state['vehicles'][vehicle_id]
            capacity = plant['inventory_capacity']
            self.require('inventory.wait_only_when_full', True, capacity is not None and self.occupancy() == capacity, event)
            self.require('inventory.unique_wait', False, vehicle['wait_logged'], event)
            self.match_fields(event, {'vehicle_id': vehicle_id, 'arrival_time': self.state['waiting_vehicles'][vehicle_id],
                'load': vehicle['load'], 'waiting_count': len(self.state['inventory']), 'held_count': len(self.state['held_slots'])})
            vehicle.update(status='waiting_to_unload', waiting_since=time, wait_logged=True)
        elif kind in ('unload_retry', 'plant_admission'):
            self.require('inventory.registered_arrival', True, bool(self.waiting_order()), event)
            self.require('inventory.waiting_vehicle_priority', self.waiting_order()[0], vehicle_id, event)
            vehicle = self.state['vehicles'][vehicle_id]
            if kind == 'unload_retry':
                capacity = plant['inventory_capacity']
                self.require('inventory.retry_when_space', True, capacity is None or self.occupancy() < capacity, event)
                self.require('inventory.retry_after_wait', True, vehicle['wait_logged'], event)
                self.match_fields(event, {'vehicle_id': vehicle_id, 'arrival_time': self.state['waiting_vehicles'][vehicle_id]})
                self.required.append(('plant_admission', 'product_id', vehicle['load'][0]))
            else:
                self.require('inventory.onboard_before_admission', vehicle['load'][0], product_id, event)
                self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'plant_id': plant['id'],
                    'waiting_count': len(self.state['inventory']) + 1, 'held_count': len(self.state['held_slots'])})
                self.state['inventory'][product_id] = time
                product = self.state['products'][product_id]
                product.update(state='in_plant_inventory', location=plant['id'], actual_destination=plant['id'])
                product['milestones']['admission'] = time
                vehicle['load'].pop(0)
                if not vehicle['load']:
                    self.state['waiting_vehicles'].pop(vehicle_id)
                    if vehicle['waiting_since'] is not None:
                        vehicle['waiting_to_unload'] = audit_finite(vehicle['waiting_to_unload'] + (time - vehicle['waiting_since']), "vehicle['waiting_to_unload']")
                    vehicle.update(status='idle', waiting_since=None, wait_logged=False)
        elif kind == 'operation_start':
            self.require('workstation.idle', None, self.state['workstations'][workstation_id], event)
            capacity = plant['inventory_capacity']
            self.require('inventory.admission_before_next_start', False,
                         bool(self.waiting_order()) and (capacity is None or self.occupancy() < capacity), event)
            self.require('workstation.eligible_inventory', True, bool(self.eligible_products(workstation_id)), event)
            self.require('workstation.product_priority', self.eligible_products(workstation_id)[0], product_id, event)
            product = self.state['products'][product_id]
            opportunity = self.window_opportunity(self.stations[workstation_id], time)
            self.require('workstation.full_window_fit', True, opportunity is not None and values_match(opportunity[0], time), event)
            operation = {'product_id': product_id, 'plant_id': plant['id'], 'workstation_id': workstation_id,
                'operation_id': self.stations[workstation_id]['operation_id'], 'operation_index': product['next_operation'],
                'start_time': time, 'end_time': audit_end(time, self.stations[workstation_id]['duration'], 'processing'), 'window': opportunity[1]}
            self.match_fields(event, operation)
            product['inventory_wait'] = audit_finite(product['inventory_wait'] + (time - self.state['inventory'].pop(product_id)), "product['inventory_wait']")
            product.update(state='processing', location=workstation_id)
            self.state['workstations'][workstation_id] = operation
            if capacity is not None:
                intermediate = product['next_operation'] + 1 < len(self.products[product_id]['operations'])
                if intermediate:
                    self.state['held_slots'].append(product_id)
                self.required.append(('inventory_hold' if intermediate else 'inventory_slot_release', 'product_id', product_id))
        elif kind in ('inventory_hold', 'inventory_slot_release'):
            self.match_fields(event, {'product_id': product_id, 'plant_id': plant['id'],
                'waiting_count': len(self.state['inventory']), 'held_count': len(self.state['held_slots'])})
        elif kind == 'operation_complete':
            operation = self.state['workstations'][workstation_id]
            self.require('workstation.active_completion', True, operation is not None, event)
            audit_observed_progress(operation['start_time'], time, 'processing', event)
            self.match_fields(event, operation)
            self.require('workstation.fixed_completion_time', operation['end_time'], time, event)
            product = self.state['products'][product_id]
            product['history'].append(dict(operation))
            product['next_operation'] += 1
            self.state['workstations'][workstation_id] = None
            self.required.append(('remanufactured' if product['next_operation'] == len(self.products[product_id]['operations']) else 'inventory_return', 'product_id', product_id))
        elif kind == 'inventory_return':
            product = self.state['products'][product_id]
            replaced = product_id in self.state['held_slots']
            self.require('inventory.intermediate_hold', plant['inventory_capacity'] is not None, replaced, event)
            self.match_fields(event, {'product_id': product_id, 'plant_id': plant['id'], 'next_operation_index': product['next_operation'],
                'held_slot_replaced': replaced, 'waiting_count': len(self.state['inventory']) + 1,
                'held_count': len(self.state['held_slots']) - int(replaced)})
            if replaced:
                self.state['held_slots'].remove(product_id)
            self.state['inventory'][product_id] = time
            product.update(state='in_plant_inventory', location=plant['id'])
        elif kind == 'remanufactured':
            self.match_fields(event, {'product_id': product_id})
            product = self.state['products'][product_id]
            self.require('product.final_operation', len(self.products[product_id]['operations']), product['next_operation'], event)
            product.update(state='remanufactured', location=None)
            product['milestones']['completion'] = time
        elif kind == 'horizon':
            self.require('time.horizon', self.scenario['horizon'], time, event)
            self.require('events.settled_deadlines', True,
                         all(due > time + TOLERANCE for due in self.pending_deadlines()), event)
            self.check_physical_state(event)
            self.require('events.settled_horizon', [], self.required, event)
            self.match_fields(event, {'waiting_count': len(self.state['inventory']), 'held_count': len(self.state['held_slots'])})
            self.finished = True
        else:
            raise EvidenceProblem(f'Unsupported inventory event type {kind}')
        capacity = plant['inventory_capacity']
        self.require('inventory.capacity', True, capacity is None or self.occupancy() <= capacity, event)

    # Read the whole log and compare final exports

    def replay(self, events: list[Record]) -> Record:
        """Check static inventory events in order and return reconstructed horizon state.

        verify_run calls after input checks. Enforce sequences, deadlines and physical
        transitions; update only audit state/findings. Add unfinished waits and partial
        fixed travel at the horizon. Missing evidence raises EvidenceProblem; a proved
        violation records its context and raises ReplayViolation. No run files change.
        """
        for sequence, event in enumerate(events, 1):
            if not all(key in event for key in ('sequence', 'time', 'event_type')):
                raise EvidenceProblem(f'Incomplete event record {sequence}')
            if type(event['time']) not in (int, float):
                raise EvidenceProblem(f'Invalid time in event {sequence}')
            self.require('events.sequence', sequence, event['sequence'], event)
            self.require('events.after_horizon', False, self.finished, event)
            self.require('events.chronological', True, self.state['time'] <= event['time'] <= self.scenario['horizon'], event)
            try:
                self.check_clock_boundary(event)
                self.advance_range_travel(event['time'], event)
                self.state['time'] = event['time']
                self.consume_event(event)
            except PhysicalEvidenceError as error:
                error.event = event
                raise
        if not self.finished:
            raise EvidenceProblem('Missing horizon record')
        horizon = self.scenario['horizon']
        for product_id, entered in self.state['inventory'].items():
            self.state['products'][product_id]['inventory_wait'] = audit_finite(self.state['products'][product_id]['inventory_wait'] + (horizon - entered), "self.state['products'][product_id]['inventory_wait']")
        for vehicle_id, vehicle in self.state['vehicles'].items():
            if self.vehicles[vehicle_id]['maximum_range'] is None and vehicle['active_leg'] is not None:
                vehicle['distance'] = audit_finite(vehicle['distance'] + ((horizon - vehicle['active_leg']['departure_time']) * self.vehicles[vehicle_id]['speed']), "vehicle['distance']")
            if vehicle['waiting_since'] is not None:
                vehicle['waiting_to_unload'] = audit_finite(vehicle['waiting_to_unload'] + (horizon - vehicle['waiting_since']), "vehicle['waiting_to_unload']")
        return self.state

    def reconcile(self, evidence: dict[str, Any]) -> Record:
        """Calculate audit totals and compare complete state, summary and dispatch exports.

        verify_run calls after replay; DynamicAudit delegates the common comparisons here.
        Return the independently recalculated summary and add it to audit state.
        Append findings for mismatches; no source evidence changes. Saved summary values
        are compared with the calculation, never used as its inputs.
        """
        generated = sum(p['state'] != 'unreleased' for p in self.state['products'].values())
        completed = sum(p['state'] == 'remanufactured' for p in self.state['products'].values())
        recycled = sum(p['state'] == 'recycled' for p in self.state['products'].values())
        summary = {'generated': generated, 'remanufactured': completed, 'recycled': recycled, 'unfinished': generated - completed - recycled,
            'vehicle_distance': audit_finite(sum(v['distance'] for v in self.state['vehicles'].values()), 'vehicle_distance'),
            'plant_inventory_wait': audit_finite(sum(p['inventory_wait'] for p in self.state['products'].values()), 'plant_inventory_wait'),
            'waiting_to_unload': audit_finite(sum(v['waiting_to_unload'] for v in self.state['vehicles'].values()), 'waiting_to_unload')}
        self.state['summary'] = summary
        if self.scenario['policy'] == 'static_quality_aware' and 'dispatch_decisions.jsonl' in evidence:
            audit_aware_dispatch_records(self.expected_decisions, evidence['dispatch_decisions.jsonl'], self.findings)
        for filename, expected in (('summary.json', summary), ('final_state.json', self.state),
                                   ('dispatch_decisions.jsonl', self.expected_decisions)):
            if filename in evidence:
                check_value(self.findings, f'results.{filename}', expected, evidence[filename], file=filename)
        return summary


# Read plant containers and audit dynamic processing forecasts

def audit_configured_plants(scenario: Record) -> list[Record]:
    """Return explicit plant definitions or a list containing the older single plant.

    Independent network checks call this. The returned plant records refer to input
    definitions and must be read only. No simulation access helper is imported.
    """
    return scenario['plants'] if 'plants' in scenario else [scenario['plant']]


def audit_plant_containers(scenario: Record, state: Record) -> dict[str, Record]:
    """Return this audit state's plant containers without counting primary fields twice.

    Dynamic audit/forecast checks call this. Use plant_states when present, otherwise
    build a map referring to the older single-plant containers. Values are shared
    with the supplied audit state; this helper does not make an isolated copy.
    """
    if state.get('plant_states'):
        return state['plant_states']
    return {scenario['plant']['id']: {key: state[key] for key in ('inventory', 'held_slots', 'workstations', 'waiting_vehicles')}}


def audit_dynamic_forecast_context(scenario: Record, state: Record) -> Record:
    """Copy released definitions and current audit state for dynamic aware checking.

    DynamicAudit.policy_input calls at a decision. Return separate data including
    plant containers and committed routes, excluding future unreleased products.
    Read audit state without changing it; do not write files or execute simulation.
    """
    released = {product_id for product_id, product in state['products'].items() if product['state'] != 'unreleased'}
    snapshot = copy.deepcopy(state)
    snapshot['products'] = {product_id: product for product_id, product in snapshot['products'].items() if product_id in released}
    physical = {key: copy.deepcopy(scenario[key]) for key in ('horizon', 'plant', 'collection_points', 'recycling_facilities', 'vehicles')}
    if 'plants' in scenario:
        physical['plants'] = copy.deepcopy(scenario['plants'])
    physical.update(format_version=3, policy='dynamic_quality_aware', trips=[])
    physical['products'] = [{key: copy.deepcopy(product[key]) for key in ('id', 'origin', 'release_time', 'disposition', 'operations')}
                            for product in scenario['products'] if product['id'] in released]
    return {'scenario': physical, 'state': snapshot}


def audit_dynamic_forecast(boundary: Record, candidate: Record) -> Record:
    """Independently predict revised deliveries, admission and work in chronological order.

    audit_dynamic_selection supplies an aware observation and feasible insertion.
    Copy audit state and apply revised routes. Project unloading queues, held slots
    and workstation work, carrying upstream delays into later delivery times.
    Return arrival/admission/start/wait and all projected deliveries. No future
    returns or new dispatch choices are introduced. Only the separate forecast
    changes; no simulator imports, events or source files are used for answers.
    """
    context = boundary['forecast_context']
    scenario, state = context['scenario'], copy.deepcopy(context['state'])
    plants = {plant['id']: plant for plant in audit_configured_plants(scenario)}
    containers = audit_plant_containers(scenario, state)
    sites = {site['id']: site for site in [*plants.values(), *scenario['collection_points'], *scenario['recycling_facilities']]}
    definitions = {product['id']: product for product in scenario['products']}
    vehicle_definitions = {vehicle['id']: vehicle for vehicle in scenario['vehicles']}
    for request in boundary['requests']:
        state['products'][request['product_id']].update(assigned_vehicle=request['assigned_vehicle'], planned_destination=request['planned_destination'])
    state['products'][candidate['product_id']].update(assigned_vehicle=candidate['vehicle_id'], planned_destination=candidate['destination'])
    arrivals: dict[str, float] = {}
    for observed in boundary['vehicles']:
        vehicle = state['vehicles'][observed['id']]
        vehicle['remaining_stops'] = copy.deepcopy(candidate['new_remaining_stops'] if observed['id'] == candidate['vehicle_id'] else observed['remaining_stops'])
        if vehicle['remaining_stops'] and vehicle['status'] != 'waiting_to_unload':
            target = sites[vehicle['remaining_stops'][0]['site_id']]['coordinates']
            arrivals[observed['id']] = audit_arrival(state['time'], audit_distance(vehicle['position'], target), observed['speed'])
    calendars = {}
    for plant_id, plant in plants.items():
        calendars[plant_id] = []
        for station in sorted(plant['workstations'], key=lambda station: station['id']):
            intervals = []
            for opening, closing in station['windows']:
                if intervals and intervals[-1][1] == opening:
                    intervals[-1][1] = closing
                else:
                    intervals.append([opening, closing])
            calendars[plant_id].append((station, intervals))
    deliveries = []
    first_start = None
    time = state['time']
    while time <= scenario['horizon']:
        for plant_id in sorted(plants):
            plant_state = containers[plant_id]
            for station_id in sorted(plant_state['workstations']):
                operation = plant_state['workstations'][station_id]
                if operation is None or operation['end_time'] > time:
                    continue
                product_id = operation['product_id']
                product = state['products'][product_id]
                product['next_operation'] += 1
                plant_state['workstations'][station_id] = None
                if product['next_operation'] < len(definitions[product_id]['operations']):
                    if product_id in plant_state['held_slots']:
                        plant_state['held_slots'].remove(product_id)
                    plant_state['inventory'][product_id] = time
        for vehicle_id in sorted(identifier for identifier, arrival in arrivals.items() if arrival <= time):
            del arrivals[vehicle_id]
            vehicle = state['vehicles'][vehicle_id]
            stop = vehicle['remaining_stops'][0]
            vehicle.update(location=stop['site_id'], position=list(sites[stop['site_id']]['coordinates']), status='idle')
            for product_id in stop['pickups']:
                vehicle['load'].append(product_id)
                state['products'][product_id].update(state='onboard_av', location=vehicle_id)
            for product_id in stop['deliveries']:
                deliveries.append({'product_id': product_id, 'vehicle_id': vehicle_id, 'destination': stop['site_id'], 'arrival_time': time})
            if stop['deliveries']:
                if stop['site_id'] in plants:
                    containers[stop['site_id']]['waiting_vehicles'][vehicle_id] = time
                else:
                    for product_id in stop['deliveries']:
                        vehicle['load'].remove(product_id)
                        state['products'][product_id].update(actual_destination=stop['site_id'], state='recycled')
                        state['products'][product_id]['milestones']['recycling'] = time
        for plant_id in sorted(plants):
            plant_state, plant = containers[plant_id], plants[plant_id]
            while True:
                changed = False
                for vehicle_id in sorted(plant_state['waiting_vehicles'], key=lambda identifier: (plant_state['waiting_vehicles'][identifier], identifier)):
                    vehicle = state['vehicles'][vehicle_id]
                    # Only this visit's declared deliveries may enter inventory;
                    # later visits to the same plant retain their onboard load.
                    current_deliveries = set(vehicle['remaining_stops'][0]['deliveries'])
                    eligible = [product_id for product_id in vehicle['load']
                                if product_id in current_deliveries
                                and state['products'][product_id]['planned_destination'] == plant_id]
                    while eligible and (plant['inventory_capacity'] is None or len(plant_state['inventory']) + len(plant_state['held_slots']) < plant['inventory_capacity']):
                        product_id = eligible.pop(0)
                        vehicle['load'].remove(product_id)
                        plant_state['inventory'][product_id] = time
                        state['products'][product_id].update(actual_destination=plant_id, state='in_plant_inventory')
                        state['products'][product_id]['milestones']['admission'] = time
                        changed = True
                    if not eligible:
                        del plant_state['waiting_vehicles'][vehicle_id]
                        vehicle['status'] = 'idle'
                    else:
                        vehicle['status'] = 'waiting_to_unload'
                for station, intervals in calendars[plant_id]:
                    if plant_state['workstations'][station['id']] is not None:
                        continue
                    eligible = sorted((product_id for product_id in plant_state['inventory'] if
                        definitions[product_id]['operations'][state['products'][product_id]['next_operation']] == station['operation_id']),
                        key=lambda product_id: (plant_state['inventory'][product_id], product_id))
                    if not eligible:
                        continue
                    window = next((interval for interval in intervals if interval[0] <= time < interval[1] and audit_end(time, station['duration'], 'processing') <= interval[1]), None)
                    if window is None:
                        continue
                    product_id = eligible[0]
                    plant_state['inventory'].pop(product_id)
                    index = state['products'][product_id]['next_operation']
                    plant_state['workstations'][station['id']] = {'product_id': product_id, 'end_time': audit_end(time, station['duration'], 'processing')}
                    if plant['inventory_capacity'] is not None and index + 1 < len(definitions[product_id]['operations']):
                        plant_state['held_slots'].append(product_id)
                    if product_id == candidate['product_id'] and first_start is None:
                        first_start = time
                    changed = True
                    break
                if not changed:
                    break
        for vehicle_id, vehicle in sorted(state['vehicles'].items()):
            if vehicle_id in arrivals or vehicle['status'] == 'waiting_to_unload' or not vehicle['remaining_stops']:
                continue
            stop = vehicle['remaining_stops'][0]
            if vehicle['location'] != stop['site_id'] or any(product_id in vehicle['load'] for product_id in stop['deliveries']):
                continue
            vehicle['remaining_stops'].pop(0)
            if vehicle['remaining_stops']:
                target = sites[vehicle['remaining_stops'][0]['site_id']]['coordinates']
                arrivals[vehicle_id] = audit_arrival(time, audit_distance(vehicle['position'], target), vehicle_definitions[vehicle_id]['speed'])
        future = list(arrivals.values())
        future.extend(operation['end_time'] for plant_state in containers.values() for operation in plant_state['workstations'].values() if operation is not None)
        future.extend(opening for entries in calendars.values() for _, intervals in entries for opening, _ in intervals if opening > time)
        if not future or min(future) > scenario['horizon']:
            break
        time = min(future)
    product = state['products'][candidate['product_id']]
    admission = product['milestones'].get('admission', product['milestones'].get('recycling'))
    return {'predicted_arrival': next((delivery['arrival_time'] for delivery in deliveries if delivery['product_id'] == candidate['product_id']), None),
        'predicted_admission': admission, 'predicted_first_start': first_start,
        'predicted_inventory_wait': first_start - admission if first_start is not None else None,
        'projected_deliveries': deliveries}


def audit_dynamic_quality_records(expected: Record, observed: Record, findings: list[Record], event: Record) -> None:
    """Compare saved aware forecasts/scores with independent candidate predictions.

    DynamicAudit.reconcile supplies expected/observed decisions and event context.
    Append field-specific findings; missing fields raise EvidenceProblem.
    Read candidate data without changes; return None and write no files.
    """
    if 'requests' not in observed:
        raise EvidenceProblem('Missing dynamic quality candidate records')
    for wanted_request, actual_request in zip(expected['requests'], observed['requests']):
        for wanted, actual in zip(wanted_request['candidates'], actual_request['candidates']):
            for field in ('predicted_arrival', 'predicted_admission', 'predicted_first_start', 'predicted_inventory_wait', 'projected_deliveries', 'score'):
                if field not in wanted:
                    continue
                if field not in actual:
                    raise EvidenceProblem(f'Missing candidate forecast field {field}')
                check_value(findings, f'dynamic.forecast.{field}', wanted[field], actual[field], event,
                            product_id=wanted['product_id'], vehicle_id=wanted['vehicle_id'], destination=wanted['destination'])


# Recognize dynamic inputs and audit route selection

def audit_dynamic_shape(scenario: Record) -> Record:
    """Return a separate network-shaped copy for independent dynamic verification.

    verify_run passes saved inputs. Adapt supported singular collection-point
    fields, primary plant and dynamic policy without changing original bytes.
    No sampling, writes or events. validate_dynamic_inputs checks declarations;
    required fields must exist.
    """
    result = copy.deepcopy(scenario)
    if result['format_version'] == 2:
        result['collection_points'] = [result.pop('collection_point')]
    if 'plants' in result:
        result['plant'] = copy.deepcopy(result['plants'][0])
    policy = result.get('policy') if result.get('policy') in ('dynamic_quality_unaware', 'dynamic_quality_aware') else 'dynamic_quality_unaware'
    result.update(format_version=3, policy=policy, trips=[])
    return result


def validate_dynamic_inputs(scenario: Record) -> None:
    """Independently check supported network identities, origins and physical settings.

    validate_supported_scenario calls before replay. Return None or raise
    EvidenceProblem. Keep original inputs unchanged; use local copies to reuse this
    verifier's fixed inventory declaration checks. Never import simulation validation.
    """
    if scenario['format_version'] != 3 or scenario['policy'] not in ('dynamic_quality_unaware', 'dynamic_quality_aware') or scenario.get('trips', []):
        raise EvidenceProblem('Expected dynamic format 3 with no fixed trips')
    plants = audit_configured_plants(scenario)
    if not plants:
        raise EvidenceProblem('No remanufacturing plants')
    points = scenario['collection_points']
    identifiers = [site['id'] for site in [*plants, *points, *scenario['recycling_facilities']]]
    identifiers += [vehicle['id'] for vehicle in scenario['vehicles']] + [product['id'] for product in scenario['products']]
    if len(set(identifiers)) != len(identifiers) or not all(isinstance(identifier, str) and identifier for identifier in identifiers):
        raise EvidenceProblem('Dynamic entity IDs must be unique strings')
    for point in points:
        if len(point['coordinates']) != 2 or any(type(value) not in (int, float) or not math.isfinite(value) for value in point['coordinates']):
            raise EvidenceProblem('Invalid collection point coordinates')
    if any(product['origin'] not in {point['id'] for point in points} for product in scenario['products']):
        raise EvidenceProblem('Unknown dynamic product origin')
    proxy = copy.deepcopy(scenario)
    dummy = '__empty_collection__'
    while dummy in identifiers:
        dummy += '_'
    proxy['plant'] = plants[0]
    proxy.update(format_version=2, policy='static_quality_unaware', trips=[],
                 collection_point=points[0] if points else {'id': dummy, 'coordinates': [0, 0]})
    for product in proxy['products']:
        product['origin'] = proxy['collection_point']['id']
    facilities = {plant['id'] for plant in plants} | {site['id'] for site in scenario['recycling_facilities']}
    if any(vehicle['initial_site'] not in facilities for vehicle in scenario['vehicles']):
        raise EvidenceProblem('Unknown initial facility')
    for plant in plants:
        proxy['plant'] = plant
        for vehicle in proxy['vehicles']:
            vehicle['initial_site'] = plant['id']
        validate_inventory_inputs(proxy)


def audit_merge_stops(stops: list[Record]) -> list[Record]:
    """Return a copied route joining adjacent visits to the same site.

    audit_dynamic_candidates calls this independent helper. Combine service IDs in
    sorted order without changing the input route or sharing its changing lists.
    """
    result = []
    for item in stops:
        if result and result[-1]['site_id'] == item['site_id']:
            result[-1]['pickups'] = sorted(set(result[-1]['pickups'] + item['pickups']))
            result[-1]['deliveries'] = sorted(set(result[-1]['deliveries'] + item['deliveries']))
        else:
            result.append(copy.deepcopy(item))
    return result


def audit_route_distance(position: list[float], stops: list[Record], sites: dict[str, Record]) -> float:
    """Return remaining route distance from the independently reconstructed position.

    audit_dynamic_candidates passes stops and site definitions. Read only and reject
    nonfinite calculated distance; no source evidence or audit state changes.
    """
    result = 0.0
    for item in stops:
        target = sites[item['site_id']]['coordinates']
        result = audit_finite(result + (audit_finite(math.hypot(target[0] - position[0], target[1] - position[1]), 'distance')), 'result')
        position = target
    return result


def audit_dynamic_feasibility(vehicle: Record, stops: list[Record], requests: dict[str, Record],
                              sites: dict[str, Record], candidate_id: str, destination: str,
                              *, departure_time: float = 0.0) -> tuple[bool, str]:
    """Check a full possible route independently, including current load and commitments.

    audit_dynamic_candidates supplies vehicle, stops, request/site maps, new product,
    destination and absolute departure_time. Return (feasible, reason).
    Check pickup-before-delivery, ownership, capacity, destination and every range
    segment. Only facilities reset range. Read only; invalid physical arithmetic
    raises. Ordinary infeasibility is a result, not a skipped candidate.
    """
    if vehicle['status'] == 'waiting_to_unload':
        return False, 'vehicle_waiting_to_unload'
    contents = set(vehicle['load'])
    pickups = set(contents)
    delivered = set()
    owed = {product_id for product_id, request in requests.items() if request['assigned_vehicle'] == vehicle['id']} | {candidate_id}
    position, remaining = vehicle['position'], vehicle['remaining_range']
    arrival = departure_time
    for item in stops:
        site = sites[item['site_id']]
        distance = audit_finite(math.hypot(site['coordinates'][0] - position[0], site['coordinates'][1] - position[1]), 'distance')
        arrival = audit_arrival(arrival, distance, vehicle['speed'])
        if remaining is not None:
            if remaining + TOLERANCE < distance:
                return False, 'range_exceeded_before_facility'
            remaining -= distance
        position = site['coordinates']
        if site['kind'] != 'collection' and remaining is not None:
            remaining = vehicle['maximum_range']
        for product_id in item['pickups']:
            request = requests[product_id]
            if site['kind'] != 'collection' or request['origin'] != site['id'] or product_id in pickups:
                return False, 'invalid_or_duplicate_pickup'
            if request['assigned_vehicle'] not in (None, vehicle['id']):
                return False, 'pickup_owned_by_another_vehicle'
            contents.add(product_id)
            pickups.add(product_id)
            if len(contents) > vehicle['payload_capacity']:
                return False, 'payload_exceeded'
        for product_id in item['deliveries']:
            request = requests[product_id]
            target = destination if product_id == candidate_id else request['planned_destination']
            expected_kind = 'plant' if request['disposition'] == 'remanufacture' else 'recycler'
            if product_id not in contents or product_id in delivered or target != site['id'] or site['kind'] != expected_kind:
                return False, 'precedence_or_destination_violation'
            contents.remove(product_id)
            delivered.add(product_id)
    if contents or delivered != owed:
        return False, 'unserved_commitment'
    if not stops or sites[stops[-1]['site_id']]['kind'] == 'collection':
        return False, 'missing_facility_escape'
    return True, 'feasible'


def audit_dynamic_candidates(boundary: Record, request: Record) -> list[Record]:
    """Return independently rebuilt insertion alternatives, including rejected ones.

    audit_dynamic_selection supplies one released request and its observation.
    Recalculate routes, distances, feasibility, IDs and stable distance scores.
    This function does not forecast. The selection function adds forecasts only for
    feasible aware candidates. Change local lists only; no source data or files change.
    """
    sites = {site['id']: site for site in boundary['sites']}
    requests = {item['product_id']: item for item in boundary['requests']}
    destination_kind = 'plant' if request['disposition'] == 'remanufacture' else 'recycler'
    destinations = sorted(identifier for identifier, site in sites.items() if site['kind'] == destination_kind)
    facilities = sorted(identifier for identifier, site in sites.items() if site['kind'] != 'collection')
    result, seen = [], set()
    for vehicle in sorted(boundary['vehicles'], key=lambda item: item['id']):
        if vehicle['status'] == 'waiting_to_unload':
            continue
        old = vehicle['remaining_stops']
        original_distance = audit_route_distance(vehicle['position'], old, sites)
        for destination in destinations:
            for pickup_index in range(len(old) + 1):
                first = copy.deepcopy(old)
                first.insert(pickup_index, {'site_id': request['origin'], 'pickups': [request['product_id']], 'deliveries': []})
                for delivery_index in range(pickup_index + 1, len(first) + 1):
                    base = copy.deepcopy(first)
                    base.insert(delivery_index, {'site_id': destination, 'pickups': [], 'deliveries': [request['product_id']]})
                    variants = [(base, 0)]
                    if vehicle['maximum_range'] is not None:
                        level = [base]
                        for count in (1, 2):
                            next_level = []
                            for route in level:
                                for position in range(len(route) + 1):
                                    for facility in facilities:
                                        candidate = copy.deepcopy(route)
                                        candidate.insert(position, {'site_id': facility, 'pickups': [], 'deliveries': []})
                                        next_level.append(candidate)
                                        variants.append((candidate, count))
                            level = next_level
                    for variant, count in variants:
                        route = audit_merge_stops(variant)
                        canonical = json.dumps(route, sort_keys=True, separators=(',', ':'))
                        key = vehicle['id'], destination, canonical
                        if key in seen:
                            continue
                        seen.add(key)
                        feasible, reason = audit_dynamic_feasibility(vehicle, route, requests, sites, request['product_id'], destination, departure_time=boundary['time'])
                        distance = audit_route_distance(vehicle['position'], route, sites)
                        added = max(0.0, distance - original_distance)
                        result.append({'candidate_id': f'C{len(result) + 1:05d}', 'product_id': request['product_id'],
                            'vehicle_id': vehicle['id'], 'destination': destination, 'pickup_index': pickup_index,
                            'delivery_index': delivery_index, 'extra_visits': count, 'old_remaining_stops': copy.deepcopy(old),
                            'new_remaining_stops': route, 'position': list(vehicle['position']), 'remaining_range': vehicle['remaining_range'],
                            'feasible': feasible, 'reason': reason, 'remaining_distance': distance, 'added_distance': added,
                            'score': [round(added, 12), vehicle['id'], destination, pickup_index, delivery_index, count, canonical]})
    return result


def audit_dynamic_selection(boundary: Record) -> tuple[list[Record], list[Record]]:
    """Choose independently rebuilt candidates in release-time/product-ID order.

    DynamicAudit.prepare_dispatch passes the reconstructed observation. Make a
    separate working copy; each winner updates that copy's route/ownership for later
    choices. Only aware feasible candidates get processing forecasts.
    Return proposals and all request/candidate audit records, including rejections.
    Read caller data unchanged; do not trust saved selections or write files.
    """
    working = copy.deepcopy(boundary)
    proposals, audits = [], []
    outstanding = sorted((request for request in working['requests'] if request['state'] == 'at_collection_point'
        and request['assigned_vehicle'] is None), key=lambda item: (item['release_time'], item['product_id']))
    for request in outstanding:
        candidates = audit_dynamic_candidates(working, request)
        if working.get('policy') == 'dynamic_quality_aware':
            for candidate in candidates:
                if candidate['feasible']:
                    candidate.update(audit_dynamic_forecast(working, candidate))
                    start_flag = candidate['predicted_first_start'] is not None
                    candidate['score'] = [0 if start_flag else 1, candidate['predicted_inventory_wait'] if start_flag else 0.0,
                                          round(candidate['added_distance'], 12), *candidate['score'][1:]]
        feasible = [candidate for candidate in candidates if candidate['feasible']]
        selected = min(feasible, key=lambda item: item['score']) if feasible else None
        audits.append({'product_id': request['product_id'], 'candidates': candidates,
            'selected_candidate_id': selected['candidate_id'] if selected else None,
            'reason': 'minimum_added_distance_then_stable_ids_and_positions' if selected else 'no_feasible_insertion'})
        if working.get('policy') == 'dynamic_quality_aware' and selected is not None:
            audits[-1]['reason'] = 'start_within_horizon_then_admission_wait_then_added_distance_then_stable_ties'
        if selected is not None:
            proposals.append(copy.deepcopy(selected))
            for vehicle in working['vehicles']:
                if vehicle['id'] == selected['vehicle_id']:
                    vehicle['remaining_stops'] = copy.deepcopy(selected['new_remaining_stops'])
            request.update(assigned_vehicle=selected['vehicle_id'], planned_destination=selected['destination'])
    return proposals, audits


class DynamicAudit(InventoryAudit):
    """Audit dynamic routes and events; reuse only this verifier's independent plant checks."""

    # Create independent network and vehicle state

    def __init__(self, scenario: Record, findings: list[Record]) -> None:
        """Create independent network audit state, routes and trigger tracking.

        verify_run supplies a validated execution copy and shared findings list.
        Use InventoryAudit for common empty state, then add plant-ID containers and
        vehicle positions/leg counters. Primary fields refer to this audit's primary
        plant containers. Return None; do not change saved evidence or run simulation.
        """
        proxy = copy.deepcopy(scenario)
        proxy['collection_point'] = scenario['collection_points'][0] if scenario['collection_points'] else {'id': '__empty__', 'coordinates': [0, 0]}
        super().__init__(proxy, findings)
        self.plant_definitions = {plant['id']: plant for plant in audit_configured_plants(scenario)}
        self.delivery_sites = {site['id']: site for site in [*self.plant_definitions.values(), *scenario['recycling_facilities']]}
        self.route_sites = {site['id']: site for site in [*self.plant_definitions.values(), *scenario['collection_points'], *scenario['recycling_facilities']]}
        if len(self.plant_definitions) > 1:
            self.state['plant_states'] = {plant_id: {'inventory': {}, 'held_slots': [], 'waiting_vehicles': {},
                'workstations': dict.fromkeys(station['id'] for station in plant['workstations'])} for plant_id, plant in self.plant_definitions.items()}
            self.state.update(self.state['plant_states'][scenario['plant']['id']])
        self._plant_context_active = False
        for vehicle_id, definition in self.vehicles.items():
            self.state['vehicles'][vehicle_id].update(position=list(self.route_sites[definition['initial_site']]['coordinates']),
                remaining_range=definition['maximum_range'], leg_distance_accounted=0.0, remaining_stops=[], route_version=0, leg_serial=0)
        self.notices = {'released_products': set(), 'available_vehicles': set()}
        self.initial_available: set[str] = set()
        self.proposals: list[Record] = []

    # Enforce current-stop delivery authorization

    def current_delivery_products(self, vehicle_id: str) -> list[str]:
        """Return current-stop delivery IDs still onboard, in load order.

        Arrival and blocked-admission checks call this on independent audit state.
        A later visit to the same facility does not authorize unloading now.
        Read routes and products only; return a new list.
        """
        vehicle = self.state['vehicles'][vehicle_id]
        stop = vehicle['remaining_stops'][0]
        return [product_id for product_id in vehicle['load']
                if product_id in stop['deliveries']
                and self.state['products'][product_id]['planned_destination'] == stop['site_id']]

    def check_delivery_authorization(self, event: Record) -> None:
        """Require an acceptance to belong to the current route stop before applying it.

        consume_event supplies plant_admission or recycler_acceptance. On mismatch,
        append dynamic.stop_authorized_delivery with visit/product context and raise
        ReplayViolation. Read route state only; ownership/capacity checks still apply
        when authorization passes. No simulation helper or source write is used.
        """
        vehicle = self.state['vehicles'][event['vehicle_id']]
        stop = vehicle['remaining_stops'][0] if vehicle['remaining_stops'] else None
        facility = event.get('plant_id', event.get('recycler_id'))
        if stop is None or stop['site_id'] != facility or event['product_id'] not in stop['deliveries']:
            add_finding(self.findings, 'violation', 'dynamic.stop_authorized_delivery',
                        {'facility_id': stop['site_id'] if stop else None,
                         'product_ids': list(stop['deliveries']) if stop else []},
                        {'facility_id': facility, 'product_id': event['product_id']}, event)
            raise ReplayViolation()

    # Select the correct plant containers for local checks

    def consume_plant_event(self, event: Record, plant_id: str) -> None:
        """Use one plant's audit containers temporarily for local event checks.

        consume_event calls in a network. Save current references, select this plant's
        containers/declaration and call consume_event again with that local context.
        Restore references in finally, even on failure. These are the audit's separate
        containers, not live simulation state. Return None; findings may change but no
        saved evidence or physical events are written.
        """
        self.require('plant.known_facility', True, plant_id in self.plant_definitions, event)
        fields = ('inventory', 'held_slots', 'workstations', 'waiting_vehicles')
        previous = {key: self.state[key] for key in fields}
        previous_plant, previous_stations = self.scenario['plant'], self.stations
        self.state.update(self.state['plant_states'][plant_id])
        self.scenario['plant'] = self.plant_definitions[plant_id]
        self.stations = {station['id']: station for station in self.scenario['plant']['workstations']}
        self._plant_context_active = True
        try:
            self.consume_event(event)
        finally:
            self._plant_context_active = False
            self.scenario['plant'], self.stations = previous_plant, previous_stations
            self.state.update(previous)

    def check_physical_state(self, event: Record) -> None:
        """Check that network products are counted once and each plant's holds/capacity fit.

        Clock/horizon checks supply event context. Use authoritative plant containers,
        not primary references as extra stock. Fall back to InventoryAudit for one plant.
        Return None; only findings may change and a mismatch can stop replay.
        No source evidence or physical audit transitions change here.
        """
        if not self.state.get('plant_states'):
            super().check_physical_state(event)
            return
        for product_id, product in self.state['products'].items():
            count = int(product['state'] in ('at_collection_point', 'remanufactured', 'recycled'))
            count += sum(product_id in vehicle['load'] for vehicle in self.state['vehicles'].values())
            count += sum(product_id in plant['inventory'] for plant in self.state['plant_states'].values())
            count += sum(operation is not None and operation['product_id'] == product_id for plant in self.state['plant_states'].values() for operation in plant['workstations'].values())
            self.require('product.conservation', int(product['state'] != 'unreleased'), count, event)
        for plant_id, plant in self.state['plant_states'].items():
            capacity = self.plant_definitions[plant_id]['inventory_capacity']
            self.require('inventory.capacity', True, capacity is None or len(plant['inventory']) + len(plant['held_slots']) <= capacity, event)
            holds = sorted(operation['product_id'] for operation in plant['workstations'].values() if operation is not None and
                           operation['operation_index'] + 1 < len(self.products[operation['product_id']]['operations'])) if capacity is not None else []
            self.require('inventory.hold_ownership', holds, sorted(plant['held_slots']), event)

    # Rebuild permitted observations and physical deadlines

    def policy_input(self) -> Record:
        """Return the policy observation rebuilt from independent audit state.

        prepare_dispatch calls before assignments. Copy released transport requests,
        vehicle facts and sites. Only aware policy data adds operations and a separate
        forecast context. Future products stay excluded. Read audit state without changes.
        """
        sites = [{'id': site['id'], 'coordinates': copy.deepcopy(site['coordinates']), 'kind': kind}
            for kind, definitions in (('plant', list(self.plant_definitions.values())), ('recycler', self.scenario['recycling_facilities']),
                                     ('collection', self.scenario['collection_points'])) for site in definitions]
        requests = [{'product_id': product_id, 'origin': self.products[product_id]['origin'],
            'release_time': self.products[product_id]['release_time'], 'disposition': self.products[product_id]['disposition'],
            'state': product['state'], 'assigned_vehicle': product['assigned_vehicle'], 'planned_destination': product['planned_destination']}
            for product_id, product in self.state['products'].items() if product['state'] in ('at_collection_point', 'onboard_av')]
        vehicles = [{**copy.deepcopy(self.state['vehicles'][definition['id']]), 'id': definition['id'], 'speed': definition['speed'],
            'payload_capacity': definition['payload_capacity'], 'maximum_range': definition['maximum_range']} for definition in self.scenario['vehicles']]
        boundary = {'time': self.state['time'], 'sites': sites, 'requests': requests, 'vehicles': vehicles,
                    'insertion_rules': {'max_extra_facility_visits': 2, 'distance_score_decimals': 12}}
        if self.scenario['policy'] == 'dynamic_quality_aware':
            boundary['policy'] = self.scenario['policy']
            boundary['forecast_context'] = audit_dynamic_forecast_context(self.scenario, self.state)
            for request in requests:
                request['operations'] = list(self.products[request['product_id']]['operations'])
        return boundary

    def pending_deadlines(self) -> list[float]:
        """Return required dynamic dispatch/service times as well as physical deadlines.

        Inherited clock-boundary and dynamic horizon checks call this. Include all
        plants, pending notices and ready committed departures. Read state only;
        return a new time list without making dispatch choices.
        """
        deadlines = super().pending_deadlines()
        for plant_id, plant_state in audit_plant_containers(self.scenario, self.state).items():
            if plant_id == self.scenario['plant']['id']:
                continue
            plant = self.plant_definitions[plant_id]
            deadlines.extend(operation['end_time'] for operation in plant_state['workstations'].values() if operation is not None)
            for station in plant['workstations']:
                eligible = any(self.products[product_id]['operations'][self.state['products'][product_id]['next_operation']] == station['operation_id'] for product_id in plant_state['inventory'])
                if plant_state['workstations'][station['id']] is None and eligible:
                    opportunity = self.window_opportunity(station, self.state['time'])
                    if opportunity:
                        deadlines.append(opportunity[0])
            if plant_state['waiting_vehicles'] and (plant['inventory_capacity'] is None or len(plant_state['inventory']) + len(plant_state['held_slots']) < plant['inventory_capacity']):
                deadlines.append(self.state['time'])
        if any(self.notices.values()) or any(vehicle['active_leg'] is None and vehicle['status'] != 'waiting_to_unload'
            and vehicle['remaining_stops'] for vehicle in self.state['vehicles'].values()):
            deadlines.append(self.state['time'])
        return deadlines

    def advance_range_travel(self, time: float, event: Record) -> None:
        """Account for actual dynamic motion through time for finite and unlimited vehicles.

        replay calls before applying the next event. Update audit positions, distance,
        newly accounted leg distance and finite allowance. A cancelled leg stops
        contributing after cancellation; horizon uses only actual travelled distance.
        Return None; violations record findings or raise. Saved evidence stays unchanged.
        """
        for vehicle_id, definition in self.vehicles.items():
            vehicle = self.state['vehicles'][vehicle_id]
            leg = vehicle['active_leg']
            if leg is None:
                continue
            distance = audit_prefix(time - leg['departure_time'], definition['speed'], leg['distance'])
            increment = distance - vehicle['leg_distance_accounted']
            self.require('dynamic.actual_motion', True, increment >= -TOLERANCE, event)
            if vehicle['remaining_range'] is not None:
                self.require('dynamic.range_consumption', True, increment <= vehicle['remaining_range'] + TOLERANCE, event)
                vehicle['remaining_range'] = max(0.0, vehicle['remaining_range'] - increment)
            vehicle['distance'] = audit_finite(vehicle['distance'] + (increment), "vehicle['distance']")
            vehicle['leg_distance_accounted'] = distance
            ratio = distance / leg['distance'] if leg['distance'] else 1.0
            vehicle['position'] = [audit_finite(leg['origin_coordinates'][axis] + ratio * (leg['target_coordinates'][axis] - leg['origin_coordinates'][axis]), 'travel.position') for axis in (0, 1)]

    # Audit decisions, then apply recorded dynamic events

    def prepare_dispatch(self, event: Record) -> None:
        """Rebuild a dynamic decision and the immediate events it must produce.

        consume_event calls for dispatch after advancing audit physics. Check batched
        release/availability triggers and settled service. Build the permitted input,
        independently choose candidates, save the expected decision and required event
        order. Read only released information and current commitments. Change this
        audit's tracking, not saved evidence or simulation state; return None.
        """
        trigger = {key: sorted(values) for key, values in self.notices.items()}
        self.require('dynamic.valid_trigger', True, any(self.notices.values()), event)
        self.require('dynamic.settled_completions', [], [(plant_id, station_id) for plant_id, plant in audit_plant_containers(self.scenario, self.state).items()
            for station_id, operation in plant['workstations'].items() if operation is not None and operation['end_time'] <= event['time']], event)
        self.match_fields(event, {'trigger': trigger, 'decision_index': len(self.expected_decisions) + 1})
        self.require('dynamic.batched_releases', [], sorted(product_id for product_id, definition in self.products.items()
            if self.state['products'][product_id]['state'] == 'unreleased' and definition['release_time'] <= event['time']), event)
        self.require('dynamic.settled_arrivals', [], sorted(vehicle_id for vehicle_id, vehicle in self.state['vehicles'].items()
            if vehicle['active_leg'] is not None and vehicle['active_leg']['arrival_time'] <= event['time']), event)
        self.require('dynamic.settled_service', [], sorted(vehicle_id for vehicle_id, vehicle in self.state['vehicles'].items()
            if vehicle['active_leg'] is None and vehicle['status'] != 'waiting_to_unload' and vehicle['remaining_stops']), event)
        boundary = self.policy_input()
        proposals, audits = audit_dynamic_selection(boundary)
        self.expected_decisions.append({'time': event['time'], 'trigger': trigger, 'last_event_sequence': event['sequence'],
            'policy_input': boundary, 'requests': audits, 'proposals': proposals})
        self.proposals = copy.deepcopy(proposals)
        shadow = copy.deepcopy(self.state['vehicles'])
        for proposal in proposals:
            vehicle_id, product_id = proposal['vehicle_id'], proposal['product_id']
            vehicle = shadow[vehicle_id]
            self.required.append(('assignment', 'product_id', product_id))
            if vehicle['active_leg'] is not None and vehicle['active_leg']['target'] != proposal['new_remaining_stops'][0]['site_id']:
                self.required.append(('leg_cancelled', 'vehicle_id', vehicle_id))
                vehicle['active_leg'] = None
            self.required.append(('route_revised', 'vehicle_id', vehicle_id))
            vehicle['remaining_stops'] = proposal['new_remaining_stops']
        for vehicle_id, vehicle in sorted(shadow.items()):
            if vehicle['active_leg'] is None and vehicle['status'] != 'waiting_to_unload' and vehicle['remaining_stops']:
                self.required.append(('leg_departure', 'vehicle_id', vehicle_id))
        for values in self.notices.values():
            values.clear()

    def consume_event(self, event: Record) -> None:
        """Check dynamic events and apply them to independent reconstructed state.

        replay supplies each saved record. Check stop authorization before admission
        or recycler acceptance. Handle assignments, reroutes, travel, pickups and
        service; reuse only the verifier's local plant checks for workstation events.
        Return None. Append/raise precise violations or evidence problems as appropriate.
        No simulation helpers are used and source files stay unchanged.
        """
        kind, time = event['event_type'], event['time']
        # Keep missing-field/unknown-vehicle diagnostics with the existing
        # transition checks; this rule audits a fully identified delivery visit.
        if (kind in ('plant_admission', 'recycler_acceptance')
                and event.get('vehicle_id') in self.state['vehicles'] and 'product_id' in event
                and ('plant_id' if kind == 'plant_admission' else 'recycler_id') in event):
            self.check_delivery_authorization(event)
        if kind == 'horizon' and self.state.get('plant_states'):
            self.require('time.horizon', self.scenario['horizon'], time, event)
            self.require('events.settled_horizon', [], self.required, event)
            self.require('events.settled_deadlines', True, all(due > time + TOLERANCE for due in self.pending_deadlines()), event)
            self.check_physical_state(event)
            self.match_fields(event, {'waiting_count': sum(len(plant['inventory']) for plant in self.state['plant_states'].values()),
                'held_count': sum(len(plant['held_slots']) for plant in self.state['plant_states'].values())})
            self.finished = True
            return
        plant_kinds = {'operation_start', 'operation_complete', 'inventory_return', 'inventory_hold', 'inventory_slot_release', 'plant_admission', 'unload_wait', 'unload_retry'}
        if self.state.get('plant_states') and not self._plant_context_active and kind in plant_kinds:
            plant_id = event.get('plant_id') or self.state['vehicles'][event['vehicle_id']]['location']
            self.consume_plant_event(event, plant_id)
            return
        vehicle_id, product_id = event.get('vehicle_id'), event.get('product_id')
        custom = {'vehicle_available', 'dispatch', 'assignment', 'leg_cancelled', 'route_revised', 'leg_departure',
                  'leg_arrival', 'pickup', 'stop_completed', 'plant_admission', 'unload_retry', 'unload_wait'}
        if kind not in custom:
            super().consume_event(event)
            if kind == 'return_release':
                self.notices['released_products'].add(event['product']['id'])
            return
        had_required = bool(self.required)
        if self.required:
            expected_kind, field, identity = self.required.pop(0)
            self.require('dynamic.atomic_event', expected_kind, kind, event)
            self.require('dynamic.atomic_identity', identity, event.get(field), event)
        if vehicle_id is not None:
            self.require('vehicle.known_identity', True, vehicle_id in self.vehicles, event)
        vehicle = self.state['vehicles'].get(vehicle_id)
        if kind == 'dispatch':
            self.prepare_dispatch(event)
        elif kind == 'vehicle_available':
            reason = event['reason']
            if reason == 'initial':
                self.require('dynamic.initial_availability', True, time == 0 and vehicle_id not in self.initial_available, event)
                self.initial_available.add(vehicle_id)
            else:
                self.require('dynamic.availability_reason', 'facility_stop', reason, event)
                self.require('dynamic.availability_after_service', True, had_required, event)
                self.require('dynamic.facility_availability', True, vehicle['location'] in self.delivery_sites, event)
            self.require('dynamic.available_state', True, vehicle['status'] != 'waiting_to_unload', event)
            self.match_fields(event, {'vehicle_id': vehicle_id, 'reason': reason})
            self.notices['available_vehicles'].add(vehicle_id)
        elif kind == 'assignment':
            proposal = self.proposals[0]
            self.match_fields(event, {'product_id': proposal['product_id'], 'vehicle_id': proposal['vehicle_id'], 'destination': proposal['destination']})
            product = self.state['products'][product_id]
            self.require('dynamic.unassigned_request', ('at_collection_point', None), (product['state'], product['assigned_vehicle']), event)
            product.update(assigned_vehicle=vehicle_id, planned_destination=proposal['destination'])
        elif kind == 'leg_cancelled':
            leg = vehicle['active_leg']
            self.require('dynamic.active_cancellation', True, leg is not None, event)
            self.match_fields(event, {'vehicle_id': vehicle_id, 'leg_id': leg['leg_id'], 'old_arrival_time': leg['arrival_time'],
                'distance_travelled': vehicle['leg_distance_accounted'], 'position': vehicle['position'], 'remaining_range': vehicle['remaining_range']})
            vehicle.update(active_leg=None, status='idle', leg_distance_accounted=0.0)
        elif kind == 'route_revised':
            proposal = self.proposals.pop(0)
            self.require('dynamic.replan_position', vehicle['position'], event.get('position'), event)
            self.require('dynamic.replan_range', vehicle['remaining_range'], event.get('remaining_range'), event)
            self.match_fields(event, {'vehicle_id': proposal['vehicle_id'], 'product_id': proposal['product_id'],
                'route_version': vehicle['route_version'] + 1, 'position': vehicle['position'], 'remaining_range': vehicle['remaining_range'],
                'old_remaining_stops': vehicle['remaining_stops'], 'new_remaining_stops': proposal['new_remaining_stops']})
            vehicle['route_version'] += 1
            vehicle['remaining_stops'] = copy.deepcopy(proposal['new_remaining_stops'])
        elif kind == 'leg_departure':
            self.require('dynamic.departure_enabled', True, vehicle['active_leg'] is None and vehicle['remaining_stops'] != [] and vehicle['status'] != 'waiting_to_unload', event)
            target = self.route_sites[vehicle['remaining_stops'][0]['site_id']]
            distance = audit_finite(math.hypot(target['coordinates'][0] - vehicle['position'][0], target['coordinates'][1] - vehicle['position'][1]), 'distance')
            vehicle['leg_serial'] += 1
            leg = {'leg_id': f"{vehicle_id}:L{vehicle['leg_serial']}", 'vehicle_id': vehicle_id,
                'origin_coordinates': list(vehicle['position']), 'target': target['id'], 'target_coordinates': target['coordinates'],
                'departure_time': time, 'arrival_time': audit_arrival(time, distance, self.vehicles[vehicle_id]['speed']),
                'distance': distance, 'load': list(vehicle['load']), 'remaining_range_at_departure': vehicle['remaining_range']}
            self.match_fields(event, leg)
            vehicle.update(active_leg=leg, status='travelling', leg_distance_accounted=0.0)
        elif kind == 'leg_arrival':
            leg = vehicle['active_leg']
            self.require('dynamic.live_arrival', True, leg is not None, event)
            audit_observed_progress(leg['departure_time'], time, 'travel', event, positive=leg['distance'] > 0)
            self.match_fields(event, {**leg, 'remaining_range_on_arrival': vehicle['remaining_range']})
            self.require('dynamic.arrival_time', leg['arrival_time'], time, event)
            vehicle.update(location=leg['target'], position=list(leg['target_coordinates']), active_leg=None,
                           status='idle', leg_distance_accounted=0.0)
            stop = vehicle['remaining_stops'][0]
            if self.vehicles[vehicle_id]['maximum_range'] is not None and leg['target'] in self.delivery_sites:
                self.required.append(('range_reset', 'vehicle_id', vehicle_id))
            self.required.extend(('pickup', 'product_id', product_id) for product_id in stop['pickups'])
            if stop['deliveries']:
                if leg['target'] in self.plant_definitions:
                    audit_plant_containers(self.scenario, self.state)[leg['target']]['waiting_vehicles'][vehicle_id] = time
                else:
                    self.required.extend(('recycler_acceptance', 'product_id', product_id)
                                         for product_id in self.current_delivery_products(vehicle_id))
        elif kind == 'pickup':
            product = self.state['products'][product_id]
            self.require('dynamic.pickup_ownership', ('at_collection_point', vehicle_id), (product['state'], product['assigned_vehicle']), event)
            self.require('dynamic.pickup_location', self.products[product_id]['origin'], vehicle['location'], event)
            self.require('vehicle.payload', True, len(vehicle['load']) < self.vehicles[vehicle_id]['payload_capacity'], event)
            self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'collection_point': vehicle['location']})
            vehicle['load'].append(product_id)
            product.update(state='onboard_av', location=vehicle_id)
            product['milestones']['pickup'] = time
        elif kind == 'stop_completed':
            stop = vehicle['remaining_stops'][0]
            self.match_fields(event, {'vehicle_id': vehicle_id, 'stop': stop})
            self.require('dynamic.completed_site', stop['site_id'], vehicle['location'], event)
            self.require('dynamic.completed_deliveries', True, all(product_id not in vehicle['load'] and self.state['products'][product_id]['actual_destination'] == stop['site_id'] for product_id in stop['deliveries']), event)
            vehicle['remaining_stops'].pop(0)
            if stop['site_id'] in self.delivery_sites:
                self.required.append(('vehicle_available', 'vehicle_id', vehicle_id))
        elif kind in ('unload_wait', 'unload_retry', 'plant_admission'):
            capacity = self.scenario['plant']['inventory_capacity']
            if kind == 'plant_admission':
                self.require('inventory.capacity', True, capacity is None or self.occupancy() < capacity, event)
            order = self.waiting_order()
            self.require('inventory.registered_arrival', True, vehicle_id in order, event)
            if kind != 'unload_wait':
                self.require('inventory.waiting_vehicle_priority', order[0], vehicle_id, event)
            if kind == 'unload_wait':
                self.require('inventory.wait_only_when_full', True, capacity is not None and self.occupancy() == capacity, event)
                self.match_fields(event, {'vehicle_id': vehicle_id, 'arrival_time': self.state['waiting_vehicles'][vehicle_id],
                    'load': vehicle['load'], 'waiting_count': len(self.state['inventory']), 'held_count': len(self.state['held_slots'])})
                vehicle.update(status='waiting_to_unload', waiting_since=time, wait_logged=True)
            elif kind == 'unload_retry':
                self.require('inventory.retry_after_wait', True, vehicle['wait_logged'], event)
                self.match_fields(event, {'vehicle_id': vehicle_id, 'arrival_time': self.state['waiting_vehicles'][vehicle_id]})
                eligible = self.current_delivery_products(vehicle_id)
                self.required.append(('plant_admission', 'product_id', eligible[0]))
            else:
                eligible = self.current_delivery_products(vehicle_id)
                self.require('inventory.delivery_order', eligible[0], product_id, event)
                self.require('plant.disposition', 'remanufacture', self.products[product_id]['disposition'], event)
                self.match_fields(event, {'product_id': product_id, 'vehicle_id': vehicle_id, 'plant_id': self.scenario['plant']['id'],
                    'waiting_count': len(self.state['inventory']) + 1, 'held_count': len(self.state['held_slots'])})
                vehicle['load'].remove(product_id)
                self.state['inventory'][product_id] = time
                product = self.state['products'][product_id]
                product.update(state='in_plant_inventory', location=self.scenario['plant']['id'], actual_destination=self.scenario['plant']['id'])
                product['milestones']['admission'] = time
                if len(eligible) == 1:
                    del self.state['waiting_vehicles'][vehicle_id]
                    if vehicle['waiting_since'] is not None:
                        vehicle['waiting_to_unload'] = audit_finite(vehicle['waiting_to_unload'] + (time - vehicle['waiting_since']), "vehicle['waiting_to_unload']")
                    vehicle.update(status='idle', waiting_since=None, wait_logged=False)

    # Reconstruct the horizon and compare exported results

    def replay(self, events: list[Record]) -> Record:
        """Read dynamic events and return independently reconstructed horizon state.

        verify_run supplies ordered records. Check deadlines, positions, range, routes,
        plants and decisions; do not count partial travel again at horizon.
        Add unfinished inventory/unloading waits. Only audit state/findings change.
        Missing evidence raises EvidenceProblem; impossible transitions or stale arrivals
        record a finding and raise ReplayViolation. No future returns enter decisions.
        """
        for sequence, event in enumerate(events, 1):
            if not all(key in event for key in ('sequence', 'time', 'event_type')):
                raise EvidenceProblem(f'Incomplete dynamic event {sequence}')
            self.require('events.sequence', sequence, event['sequence'], event)
            self.require('events.after_horizon', False, self.finished, event)
            self.require('events.chronological', True, self.state['time'] <= event['time'] <= self.scenario['horizon'], event)
            try:
                self.check_clock_boundary(event)
                self.advance_range_travel(event['time'], event)
                self.state['time'] = event['time']
                self.consume_event(event)
            except PhysicalEvidenceError as error:
                error.event = event
                raise
        if not self.finished:
            raise EvidenceProblem('Missing dynamic horizon')
        for plant in audit_plant_containers(self.scenario, self.state).values():
            for product_id, entered in plant['inventory'].items():
                self.state['products'][product_id]['inventory_wait'] = audit_finite(self.state['products'][product_id]['inventory_wait'] + (self.scenario['horizon'] - entered), "self.state['products'][product_id]['inventory_wait']")
        for vehicle in self.state['vehicles'].values():
            if vehicle['waiting_since'] is not None:
                vehicle['waiting_to_unload'] = audit_finite(vehicle['waiting_to_unload'] + (self.scenario['horizon'] - vehicle['waiting_since']), "vehicle['waiting_to_unload']")
        return self.state

    def reconcile(self, evidence: dict[str, Any]) -> Record:
        """Compare dynamic observations and choices, then reconcile physical totals.

        verify_run calls after replay. Check actual replan position/range and all aware
        candidate predictions against independent expectations. InventoryAudit.reconcile
        then compares complete exports and returns recalculated summary.
        Only audit state/findings change; saved records are read only.
        """
        actual = evidence.get('dispatch_decisions.jsonl')
        if actual is not None:
            check_value(self.findings, 'dynamic.decision_count', len(self.expected_decisions), len(actual))
            for expected, observed in zip(self.expected_decisions, actual):
                event = {'time': expected['time'], 'sequence': expected['last_event_sequence']}
                if 'policy_input' not in observed:
                    raise EvidenceProblem('Missing dynamic policy input')
                for wanted, seen in zip(expected['policy_input']['vehicles'], observed['policy_input']['vehicles']):
                    check_value(self.findings, 'dynamic.replan_position', wanted['position'], seen.get('position'), event, vehicle_id=wanted['id'])
                    check_value(self.findings, 'dynamic.replan_range', wanted['remaining_range'], seen.get('remaining_range'), event, vehicle_id=wanted['id'])
                if self.scenario['policy'] == 'dynamic_quality_aware':
                    audit_dynamic_quality_records(expected, observed, self.findings, event)
                for field in ('trigger', 'policy_input', 'requests', 'proposals'):
                    if field not in observed:
                        raise EvidenceProblem(f'Dynamic decision lacks {field}')
                    check_value(self.findings, f'dynamic.{field}', expected[field], observed[field], event)
        return super().reconcile(evidence)


# Coordinate checks, classify findings and write fresh reports

def verify_run(run_directory: Path) -> Record:
    """Read, independently check and report one saved run; return its report.

    main and audit clients supply the folder. Read six artifacts and original
    scenario bytes. Reconstruct supported generation, physical events and dispatch
    without simulation imports; compare all saved exports. Previous reports are
    ignored as evidence. Write fresh verification.json and verification.txt.

    PASS/0 means available evidence passed these supported checks. A proved violation
    gives FAIL/1, even alongside missing/malformed evidence. Unusable evidence alone
    gives INCOMPLETE/2. Reconstructed state is separate; only findings are shared
    between checks. replay_complete becomes true before export comparison, so
    missing exports can leave a completed replay with an INCOMPLETE result.
    File-writing failures propagate. No source inputs, events or decisions change.
    """
    findings: list[Record] = []
    evidence = read_evidence(run_directory, findings)
    summary = None
    replay_complete = False
    scenario = evidence.get("scenario.json")
    manifest = evidence.get("run_manifest.json")
    # Bad seed metadata must not hide an independently checkable physical violation.
    provenance_valid = {}
    for name, artifact in (('scenario', scenario), ('manifest', manifest)):
        provenance_valid[name] = False
        if artifact is not None:
            try:
                evidence_object(artifact, name, ('seed_provenance',))
                validate_provenance_structure(artifact['seed_provenance'], f'{name}.seed_provenance')
                provenance_valid[name] = True
            except EvidenceProblem as error:
                add_finding(findings, 'incomplete', f'{name}.seed_provenance',
                            'Supported provenance object and stream mappings', str(error))
    if manifest is not None and not isinstance(manifest.get("run_id"), str):
        add_finding(findings, "incomplete", "manifest.run_id", "Run ID string", manifest.get("run_id"))
    if manifest is not None:
        # Byte identity remains checkable even when scenario declarations are
        # malformed. Keep that independent proof instead of discarding it with
        # the unsafe scenario object. Reading/writing I/O errors stay explicit.
        try:
            scenario_hash = hashlib.sha256((run_directory / 'scenario.json').read_bytes()).hexdigest()
        except OSError as error:
            if not any(item['ids'].get('file') == 'scenario.json' for item in findings):
                add_finding(findings, 'incomplete', 'evidence.read', 'Readable scenario.json', str(error), file='scenario.json')
        else:
            if 'scenario_sha256' not in manifest:
                add_finding(findings, 'incomplete', 'manifest.evidence', 'scenario_sha256', 'Missing field')
            else:
                check_value(findings, 'manifest.scenario_sha256', scenario_hash, manifest['scenario_sha256'])
    if scenario is not None and manifest is not None:
        if type(manifest.get('input_format_version')) is not int or type(manifest.get('output_format_version')) is not int or manifest.get("input_format_version") not in (2, 3) or manifest.get("output_format_version") != (3 if manifest.get("policy") in ("dynamic_quality_unaware", "dynamic_quality_aware") else manifest.get("input_format_version")) or manifest.get("input_format_version") != scenario.get("format_version"):
            add_finding(findings, "incomplete", "format.version", "Supported input/output formats matching the execution policy", manifest)
        if provenance_valid['scenario'] and provenance_valid['manifest']:
            check_value(findings, 'manifest.seed_provenance', scenario['seed_provenance'], manifest['seed_provenance'])
    execution_policy = scenario.get('policy') if scenario else None
    if scenario is not None and manifest is not None:
        if 'policy' not in manifest:
            add_finding(findings, 'incomplete', 'manifest.policy', 'Selected run policy', 'Missing field')
        elif not isinstance(manifest['policy'], str):
            add_finding(findings, 'incomplete', 'manifest.policy', 'Selected run policy string', manifest['policy'])
            execution_policy = None
        elif scenario.get('format_version') in (2, 3):
            check_value(findings, 'manifest.policy', True, manifest['policy'] in ('static_quality_unaware', 'static_quality_aware', 'dynamic_quality_unaware', 'dynamic_quality_aware'))
            execution_policy = manifest['policy']
        else:
            check_value(findings, 'manifest.policy', scenario.get('policy'), manifest['policy'])
    if manifest is not None and manifest.get("status") != "completed":
        add_finding(findings, "incomplete", "manifest.status", "completed", manifest.get("status"))
    if scenario is not None and provenance_valid['scenario'] and (scenario.get('format_version') == 2 or
            scenario['seed_provenance'].get('source') == 'seeded_network_configuration'):
        try:
            audit_seeded_generation(scenario, findings)
        except (EvidenceProblem, KeyError, TypeError, ValueError, IndexError) as error:
            add_finding(findings, 'incomplete', 'generation.evidence', 'Complete supported generation evidence', str(error))
    supported = False
    if scenario is not None:
        try:
            validate_supported_scenario(scenario)
            supported = manifest is None or (manifest.get("input_format_version") == scenario["format_version"] and manifest.get("output_format_version") == (3 if execution_policy in ("dynamic_quality_unaware", "dynamic_quality_aware") else scenario["format_version"]))
        except (EvidenceProblem, KeyError, TypeError, ValueError, IndexError) as error:
            # Keep the existing label for retained-format diagnostics. Unsupported
            # formats get a current scope description instead of a legacy label.
            expected_scope = ("Supported checkpoint-one scenario" if scenario.get('format_version') in (2, 3)
                              else "Supported scenario formats 2 and 3; format 1 is retired")
            add_finding(findings, "incomplete", "scenario.scope", expected_scope, str(error))
    if scenario is not None and scenario.get('format_version') in (2, 3) and (manifest is None or not isinstance(manifest.get('policy'), str)):
        supported = False  # A run override cannot be inferred from frozen inputs alone.
    # Reconstruct expected state locally, never by running the simulator.
    if supported and "events.jsonl" in evidence:
        try:
            audit_physical_event_arithmetic(scenario, evidence["events.jsonl"])
            dynamic = execution_policy in ("dynamic_quality_unaware", "dynamic_quality_aware")
            execution_scenario = audit_dynamic_shape(scenario) if dynamic else copy.deepcopy(scenario)
            execution_scenario['policy'] = execution_policy
            inventory_audit = DynamicAudit(execution_scenario, findings) if dynamic else InventoryAudit(execution_scenario, findings)
            state = inventory_audit.replay(evidence["events.jsonl"])
            replay_complete = True
            summary = inventory_audit.reconcile(evidence)
        except PhysicalEvidenceError as error:
            add_finding(findings, "violation", error.rule, error.expected, error.observed, error.event, **error.context)
        except ReplayViolation:
            pass
        except (EvidenceProblem, KeyError, TypeError, ValueError, IndexError) as error:
            add_finding(findings, "incomplete", "replay.evidence", "Complete replayable records", str(error))
    # Proved violations take precedence over incomplete evidence; refresh old reports.
    exit_code = 1 if any(f["category"] == "violation" for f in findings) else 2 if findings else 0
    # Preserve the historical scope placeholder when no scenario is usable. It
    # is report compatibility only; no scalar reconstruction remains.
    report = {"status": {0: "PASS", 1: "FAIL", 2: "INCOMPLETE"}[exit_code], "exit_code": exit_code,
        "scope": "Checkpoint 4c: dynamic quality-aware/unaware insertion and plant forecasts" if execution_policy in ("dynamic_quality_unaware", "dynamic_quality_aware") else "Checkpoint 4a: static quality-aware/unaware selection with fixed physical trips" if scenario and scenario.get("format_version") == 2 else "Checkpoint 1: one fixed quality-unaware remanufacturing journey" if scenario is None else "Unsupported scenario format; supported formats are 2 and 3",
        "numeric_tolerance": {"absolute": TOLERANCE, "relative": TOLERANCE},
        "replay_complete": replay_complete, "recalculated_summary": summary, "findings": findings,
        "unchecked_scope": (["Custom starts away from facilities, insertions with more than two extra reset visits, experiments, full-program metrics and statistical distribution validation"] if execution_policy in ("dynamic_quality_unaware", "dynamic_quality_aware") else ["Multiple plants/collection points, custom starts away from facilities, repeated trips per AV, dynamic policies/replanning, experiments, full-program metrics and statistical distribution validation remain outside this verifier scope"])}
    if not replay_complete:
        report["unchecked_scope"].append("Replay and final totals could not be fully checked")
    write_reports(run_directory, report)
    return report


# Read terminal arguments

def main() -> int:
    """Read a run folder, invoke verify_run and return the report's 0/1/2 exit code.

    python -m tools.verify, standalone verify.py and verify_simulation.py call this.
    Only reports are written by verify_run. A missing folder or report I/O failure
    returns 2 with a message; it does not claim that fresh reports were saved.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    arguments = parser.parse_args()
    if not arguments.run_directory.is_dir():
        print(f"INCOMPLETE: run directory does not exist: {arguments.run_directory}")
        return 2
    try:
        report = verify_run(arguments.run_directory)
    except OSError as error:
        print(f"INCOMPLETE: unable to read evidence or write reports: {error}")
        return 2
    print(f"{report['status']} (exit {report['exit_code']})")
    print(json.dumps(report["recalculated_summary"], indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
