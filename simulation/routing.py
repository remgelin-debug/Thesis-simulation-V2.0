"""Build permitted observations, compare alternatives and choose routes/products.

engine calls the observation builders and selection functions at existing
static departures or dynamic release/vehicle-available triggers. Static policies
keep fixed trips and destinations. Dynamic policies insert pickups/deliveries
while preserving commitments. Only aware policies call forecasting for plant
predictions. Unaware policies see transport facts, not operations or calendars.
Selection changes local copies; engine applies winners to real state and logs.
"""
from __future__ import annotations

import copy
import json
from collections import deque
from collections.abc import Iterator
from .numerics import physical_distance, travel_arrival, nonnegative_physical
from .state import (
    ScenarioDefinitions,
    prepare_calendars,
    StaticCandidate,
    CandidateScore,
    RouteStop,
    PolicySite,
    PolicyVehicle,
    PolicyRequest,
    AwarePolicyRequest,
    DynamicCandidate,
    RequestAudit,
    Record,
    InventoryRunState,
    configured_plants,
)
from . import forecasting


# Build observations with only the information each policy may use

def build_static_policy_input(
    scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record,
) -> Record:
    """Return the information a policy may use at a scheduled fixed departure.

    engine.dispatch_inventory_trip passes definitions, live state and trip.
    Copy stops and unassigned collection requests in release-time/product-ID order.
    Only the aware policy receives required operations and a copied forecast context.
    Read live data without changes. Range checks and assignment stay in the engine;
    no events or files are written. Require validated format-two inputs.
    """
    products = definitions.products
    available = sorted(
        (product_id for product_id, part in state.products.items()
         if part['state'] == 'at_collection_point' and part['assigned_vehicle'] is None),
        key=lambda product_id: (products[product_id]['release_time'], product_id),
    )
    boundary: Record = {
        'vehicle_id': trip['vehicle_id'],
        'stops': list(trip['stops']),
        'requests': [
            {'product_id': product_id, 'collection_point': products[product_id]['origin'],
             'release_time': products[product_id]['release_time'], 'disposition': products[product_id]['disposition']}
            for product_id in available
        ],
    }
    if scenario['policy'] == 'static_quality_aware':
        for request in boundary['requests']:
            request['operations'] = list(products[request['product_id']]['operations'])
        boundary['forecast_context'] = forecasting.build_forecast_context(scenario, state)
    return boundary


def build_dynamic_policy_input(scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState) -> Record:
    """Copy current transport information and add plant details only for the aware policy.

    engine.dispatch_dynamic_requests supplies definitions and state. Return sites,
    actual vehicle position/load/range/routes, and requests waiting or onboard.
    Future unreleased returns and processing products are not transport requests.
    Unaware inputs contain neither quality classes, operations nor forecast context.
    Aware inputs add operations and build_dynamic_forecast_context. Read live data
    without changing it; record no events or files.
    """
    sites = [
        {'id': site['id'], 'coordinates': list(site['coordinates']), 'kind': kind}
        for kind, group in (('plant', configured_plants(scenario)), ('recycler', scenario['recycling_facilities']),
                            ('collection', scenario['collection_points']))
        for site in group
    ]
    products = definitions.products
    requests = [{'product_id': product_id, 'origin': products[product_id]['origin'],
        'release_time': products[product_id]['release_time'], 'disposition': products[product_id]['disposition'],
        'state': product['state'], 'assigned_vehicle': product['assigned_vehicle'],
        'planned_destination': product['planned_destination']} for product_id, product in state.products.items()
        if product['state'] in ('at_collection_point', 'onboard_av')]
    vehicles = [{**copy.deepcopy(state.vehicles[definition['id']]), 'id': definition['id'],
        'speed': definition['speed'], 'payload_capacity': definition['payload_capacity'], 'maximum_range': definition['maximum_range']}
        for definition in scenario['vehicles']]
    boundary = {'time': state.time, 'sites': sites, 'requests': requests, 'vehicles': vehicles,
                'insertion_rules': {'max_extra_facility_visits': 2, 'distance_score_decimals': 12}}
    if scenario['policy'] == 'dynamic_quality_aware':
        boundary['policy'] = scenario['policy']
        boundary['forecast_context'] = forecasting.build_dynamic_forecast_context(scenario, state)
        for request in requests:
            request['operations'] = list(products[request['product_id']]['operations'])
    return boundary


# Score and select products for fixed trips

def quality_candidate_score(candidate: StaticCandidate) -> CandidateScore:
    """Return the static aware comparison list used by choose_quality_aware_products.

    Prefer a start by the horizon, then admission-to-start wait, added distance,
    product ID and stop indexes. Read the candidate only; lower lists rank first.
    """
    starts = candidate['predicted_first_start'] is not None
    return [0 if starts else 1, candidate['predicted_inventory_wait'] if starts else 0.0,
            candidate['added_distance'], candidate['product_id'], candidate['pickup_stop_index'], candidate['delivery_stop_index']]


def choose_quality_aware_products(policy_input: Record) -> tuple[list[str], list[StaticCandidate], str]:
    """Choose fixed-trip products one payload slot at a time using plant forecasts.

    choose_static_products passes an aware observation with a feasible trip.
    For every remaining eligible product, forecast_candidate includes products
    already chosen for earlier slots. Select the lowest quality_candidate_score.
    Return (selected IDs, all candidate records, reason). Only local candidate lists
    change; no live assignments, events or files. Destination and departure stay fixed;
    future unreleased returns are absent from the supplied context.
    """
    context = policy_input['forecast_context']
    scenario = context['scenario']
    definitions = ScenarioDefinitions.from_scenario(scenario)
    trip = next(trip for trip in scenario['trips'] if trip['vehicle_id'] == policy_input['vehicle_id'])
    vehicle = definitions.vehicles[trip['vehicle_id']]
    disposition = 'remanufacture' if trip['stops'][-1] == scenario['plant']['id'] else 'recycle'
    remaining = [request['product_id'] for request in policy_input['requests'] if request['disposition'] == disposition]
    selected: list[str] = []
    audit: list[StaticCandidate] = []
    if scenario['collection_point']['id'] not in trip['stops']:
        return selected, audit, 'Fixed trip has no pickup stop'
    for selection_round in range(min(vehicle['payload_capacity'], len(remaining))):
        candidates = []
        for product_id in remaining:
            candidate: StaticCandidate = {'selection_round': selection_round, 'product_id': product_id, 'destination': trip['stops'][-1],
                'pickup_stop_index': trip['stops'].index(scenario['collection_point']['id']),
                'delivery_stop_index': len(trip['stops']) - 1, 'added_distance': 0.0,
                **forecasting.forecast_candidate(context, definitions, trip, product_id, selected)}
            candidate['score'] = quality_candidate_score(candidate)
            candidates.append(candidate)
        winner = min(candidates, key=lambda candidate: candidate['score'])
        for candidate in candidates:
            candidate['selected'] = candidate['product_id'] == winner['product_id']
        audit.extend(candidates)
        selected.append(winner['product_id'])
        remaining.remove(winner['product_id'])
    return selected, audit, 'Prefer start within horizon, then admission-to-first-start wait, added distance, product ID and stop indexes'


def choose_static_products(
    policy_input: Record, *, payload_capacity: int, disposition: str, pickup_site: str,
    range_feasible: bool, quality_aware: bool,
) -> Record:
    """Return a fixed-trip selection and its decision-log fields.

    engine.dispatch_inventory_trip supplies the observation, capacity, destination
    disposition, pickup site and full-trip range result. Keep its scheduled trip.
    Unaware selection takes eligible products in release-time/ID order; aware
    selection calls choose_quality_aware_products for each payload slot.
    Own returned stop lists; do not change caller data or physical state.
    No files/events are written. Aware input must contain forecast_context.
    """
    selected = [request['product_id'] for request in policy_input['requests']
                if request['disposition'] == disposition][:payload_capacity]
    if not range_feasible or pickup_site not in policy_input['stops']:
        selected = []
    result: Record = {
        'decision': {'assigned_products': selected, 'stops': list(policy_input['stops'])},
        'tie_break': 'release_time, product_id',
    }
    if quality_aware:
        if range_feasible:
            selected, candidates, reason = choose_quality_aware_products(policy_input)
        else:
            selected, candidates, reason = [], [], 'Fixed trip rejected by range check'
        result.update(candidates=candidates, selection_reason=reason,
                      tie_break='product_id, pickup_stop_index, delivery_stop_index')
        result['decision']['assigned_products'] = selected
    return result


# Construct and check dynamic route alternatives

def merge_route_stops(stops: list[RouteStop]) -> list[RouteStop]:
    """Return an owned route joining adjacent visits to the same site.

    Candidate generation calls this after inserting stops. Combine pickup/delivery
    IDs in sorted order. Separate result lists keep one candidate from changing
    another or the input route. No physical state changes.
    """
    merged: list[RouteStop] = []
    for stop in stops:
        if merged and merged[-1]['site_id'] == stop['site_id']:
            for field in ('pickups', 'deliveries'):
                merged[-1][field] = sorted(set(merged[-1][field]) | set(stop[field]))
        else:
            merged.append(copy.deepcopy(stop))
    return merged


def remaining_route_distance(
    position: list[float], stops: list[RouteStop], sites: dict[str, PolicySite],
) -> float:
    """Return total straight-line distance from position through the remaining stops.

    Candidate enumeration reads the supplied site map and route. No state changes.
    Reject unrepresentable geometry or accumulated distance instead of rounding it.
    """
    total = 0.0
    for stop in stops:
        target = sites[stop['site_id']]['coordinates']
        total = nonnegative_physical(total + (physical_distance(position, target)), 'total')
        position = target
    return total


def check_insertion_route(
    vehicle: PolicyVehicle, stops: list[RouteStop],
    requests: dict[str, PolicyRequest | AwarePolicyRequest],
    sites: dict[str, PolicySite], candidate_id: str, destination: str,
    *, departure_time: float = 0.0,
) -> tuple[bool, str]:
    """Check an entire proposed route, including every existing delivery commitment.

    enumerate_dynamic_candidates supplies vehicle facts, stops, released request/site
    maps, new product/destination and absolute departure_time. Return (feasible, reason).
    Check ownership, pickup-before-delivery, capacity and range from actual position.
    Only facility visits reset range. Travel-time arithmetic is needed by both
    policies and uses no plant forecast. Ordinary infeasibility returns False;
    invalid references/arithmetic raise. Read only; no files or events change.
    """
    if vehicle['status'] == 'waiting_to_unload':
        return False, 'vehicle_waiting_to_unload'
    load = set(vehicle['load'])
    picked = set(load)
    delivered: set[str] = set()
    expected = {product_id for product_id, request in requests.items()
                if request['assigned_vehicle'] == vehicle['id']} | {candidate_id}
    position, allowance = vehicle['position'], vehicle['remaining_range']
    arrival = departure_time
    for stop in stops:
        site = sites[stop['site_id']]
        distance = physical_distance(position, site['coordinates'])
        arrival = travel_arrival(arrival, distance, vehicle['speed'], vehicle_id=vehicle['id'], site_id=site['id'])
        if allowance is not None:
            if distance > allowance + 1e-9:
                return False, 'range_exceeded_before_facility'
            allowance -= distance
        position = site['coordinates']
        if site['kind'] != 'collection' and allowance is not None:
            allowance = vehicle['maximum_range']
        for product_id in stop['pickups']:
            request = requests[product_id]
            if request['origin'] != site['id'] or site['kind'] != 'collection' or product_id in picked:
                return False, 'invalid_or_duplicate_pickup'
            if request['assigned_vehicle'] not in (None, vehicle['id']):
                return False, 'pickup_owned_by_another_vehicle'
            load.add(product_id)
            picked.add(product_id)
            if len(load) > vehicle['payload_capacity']:
                return False, 'payload_exceeded'
        for product_id in stop['deliveries']:
            request = requests[product_id]
            required = destination if product_id == candidate_id else request['planned_destination']
            kind = 'plant' if request['disposition'] == 'remanufacture' else 'recycler'
            if product_id not in load or product_id in delivered or required != site['id'] or site['kind'] != kind:
                return False, 'precedence_or_destination_violation'
            load.remove(product_id)
            delivered.add(product_id)
    if load or delivered != expected:
        return False, 'unserved_commitment'
    if not stops or sites[stops[-1]['site_id']]['kind'] == 'collection':
        return False, 'missing_facility_escape'
    return True, 'feasible'


def _facility_insertions(route: list[RouteStop], facilities: list[str]) -> Iterator[list[RouteStop]]:
    """Yield separate routes with one empty facility visit, in position/facility order.

    _reset_route_variants consumes them for range-reset alternatives. Copy stop
    records and service lists so routes do not share changing data. Read the input
    only; do not check feasibility or choose a route here.
    """
    for index in range(len(route) + 1):
        for facility in facilities:
            visit: RouteStop = {'site_id': facility, 'pickups': [], 'deliveries': []}
            yield [*copy.deepcopy(route[:index]), visit, *copy.deepcopy(route[index:])]


def _reset_route_variants(base: list[RouteStop], facilities: list[str]) -> Iterator[tuple[list[RouteStop], int]]:
    """Yield the base route, then all one-reset routes, then all two-reset routes.

    enumerate_dynamic_candidates consumes this exact order. Keep the smaller
    one-reset queue, then generate two-reset routes one at a time to reduce temporary
    memory. merge_route_stops later owns each result. Do not remove duplicates or
    rejected routes here; enumeration does that at its existing decision boundary.
    """
    yield base, 0
    frontier: deque[list[RouteStop]] = deque()
    for variant in _facility_insertions(base, facilities):
        frontier.append(variant)
        yield variant, 1
    while frontier:
        parent = frontier.popleft()
        for variant in _facility_insertions(parent, facilities):
            yield variant, 2


def enumerate_dynamic_candidates(
    policy_input: Record, request: PolicyRequest | AwarePolicyRequest,
) -> list[DynamicCandidate]:
    """Return all distinct insertion alternatives, including rejected routes.

    choose_dynamic_insertions passes an observation and one released request.
    Try vehicles, destinations and pickup/delivery positions in the existing order.
    Finite-range vehicles may add up to two empty facility visits. Generate variants
    incrementally; merge_route_stops makes each candidate's own route. Keep the first
    of equivalent routes, preserving candidate IDs and ties. Skip blocked vehicles.
    Return feasibility/reason, distances, stops, position/range and distance scores.
    No forecasting or selection occurs here. Caller data and live state stay unchanged;
    valid request/site references and existing commitments are required.
    """
    sites = {site['id']: site for site in policy_input['sites']}
    requests = {item['product_id']: item for item in policy_input['requests']}
    destinations = sorted(site_id for site_id, site in sites.items()
        if site['kind'] == ('plant' if request['disposition'] == 'remanufacture' else 'recycler'))
    facilities = sorted(site_id for site_id, site in sites.items() if site['kind'] != 'collection')
    candidates: list[DynamicCandidate] = []
    seen = set()
    for vehicle in sorted(policy_input['vehicles'], key=lambda vehicle: vehicle['id']):
        if vehicle['status'] == 'waiting_to_unload':
            continue
        old = vehicle['remaining_stops']
        baseline = remaining_route_distance(vehicle['position'], old, sites)
        for destination in destinations:
            for pickup_index in range(len(old) + 1):
                pickup = {'site_id': request['origin'], 'pickups': [request['product_id']], 'deliveries': []}
                with_pickup = [*copy.deepcopy(old[:pickup_index]), pickup, *copy.deepcopy(old[pickup_index:])]
                for delivery_index in range(pickup_index + 1, len(with_pickup) + 1):
                    delivery = {'site_id': destination, 'pickups': [], 'deliveries': [request['product_id']]}
                    base = [*copy.deepcopy(with_pickup[:delivery_index]), delivery, *copy.deepcopy(with_pickup[delivery_index:])]
                    variants = (_reset_route_variants(base, facilities) if vehicle['maximum_range'] is not None
                                else ((base, 0),))
                    for variant, visit_count in variants:
                        route = merge_route_stops(variant)
                        key = json.dumps(route, sort_keys=True, separators=(',', ':'))
                        identity = vehicle['id'], destination, key
                        if identity in seen:
                            continue
                        seen.add(identity)
                        feasible, reason = check_insertion_route(vehicle, route, requests, sites, request['product_id'], destination,
                                                                departure_time=policy_input['time'])
                        distance = remaining_route_distance(vehicle['position'], route, sites)
                        added = max(0.0, distance - baseline)
                        candidates.append({'candidate_id': f'C{len(candidates) + 1:05d}',
                            'product_id': request['product_id'], 'vehicle_id': vehicle['id'], 'destination': destination,
                            'pickup_index': pickup_index, 'delivery_index': delivery_index, 'extra_visits': visit_count,
                            'old_remaining_stops': copy.deepcopy(old), 'new_remaining_stops': route,
                            'position': list(vehicle['position']), 'remaining_range': vehicle['remaining_range'],
                            'feasible': feasible, 'reason': reason, 'remaining_distance': distance, 'added_distance': added,
                            'score': [round(added, 12), vehicle['id'], destination, pickup_index, delivery_index, visit_count, key]})
    return candidates


# Score and select dynamic insertions

def dynamic_quality_score(candidate: DynamicCandidate) -> CandidateScore:
    """Return the dynamic aware comparison list after a candidate is forecast.

    choose_dynamic_insertions calls this. Prefer a first start by the horizon,
    shorter admission-to-start wait, then rounded added distance and stable insertion
    ties already in score. With no start, compare distance before those ties.
    Read only; None means no first start was observed by the horizon.
    """
    started = candidate['predicted_first_start'] is not None
    return [0 if started else 1, candidate['predicted_inventory_wait'] if started else 0.0,
            round(candidate['added_distance'], 12), *candidate['score'][1:]]


def choose_dynamic_insertions(policy_input: Record) -> tuple[list[DynamicCandidate], list[RequestAudit]]:
    """Choose insertions in release-time/product-ID order on a separate observation copy.

    engine.dispatch_dynamic_requests passes the permitted current information.
    Enumerate all candidates. Only the aware policy forecasts feasible ones and
    uses dynamic_quality_score; unaware selection compares added distance.
    After each winner, update the copied route/ownership so later choices keep
    commitments. Return (proposals, per-request audits), including rejected candidates.
    An aware decision shares one compatible fixed calendar index across its copies.
    No real assignments, events or files change; the engine applies returned winners.
    """
    # Earlier winners affect later insertions only inside this decision copy.
    working = copy.deepcopy(policy_input)
    calendars = (prepare_calendars(working['forecast_context']['scenario'])
                 if working.get('policy') == 'dynamic_quality_aware' else None)
    proposals, audits = [], []
    outstanding = sorted((request for request in working['requests'] if request['state'] == 'at_collection_point'
                          and request['assigned_vehicle'] is None), key=lambda request: (request['release_time'], request['product_id']))
    # Preserve release-time/ID order and retain rejected candidates as evidence.
    for request in outstanding:
        candidates = enumerate_dynamic_candidates(working, request)
        # Unaware routing keeps distance scores and never enters plant forecasting.
        if working.get('policy') == 'dynamic_quality_aware':
            for candidate in candidates:
                if candidate['feasible']:
                    candidate.update(forecasting.forecast_dynamic_insertion(working, candidate, calendars=calendars))
                    candidate['score'] = dynamic_quality_score(candidate)
        feasible = [candidate for candidate in candidates if candidate['feasible']]
        winner = min(feasible, key=lambda candidate: candidate['score']) if feasible else None
        audits.append({'product_id': request['product_id'], 'candidates': candidates,
            'selected_candidate_id': winner['candidate_id'] if winner else None,
            'reason': 'minimum_added_distance_then_stable_ids_and_positions' if winner else 'no_feasible_insertion'})
        if working.get('policy') == 'dynamic_quality_aware' and winner is not None:
            audits[-1]['reason'] = 'start_within_horizon_then_admission_wait_then_added_distance_then_stable_ties'
        if winner is not None:
            proposals.append(copy.deepcopy(winner))
            vehicle = next(vehicle for vehicle in working['vehicles'] if vehicle['id'] == winner['vehicle_id'])
            vehicle['remaining_stops'] = copy.deepcopy(winner['new_remaining_stops'])
            request['assigned_vehicle'], request['planned_destination'] = vehicle['id'], winner['destination']
    return proposals, audits
