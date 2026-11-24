"""Predict plant admission and processing for quality-aware routing only.

routing supplies information available now; future unreleased returns are left
out. Context builders copy physical data. Candidate functions copy it again so
one possible route cannot change another candidate or the real run.
Static forecasts use plant/transport rules. Dynamic forecasts call engine with
dispatch disabled. Return predictions to routing; do not select routes or write
run files. Unaware policies do not call this processing pipeline.
"""
from __future__ import annotations

import copy
import heapq
from .numerics import physical_distance, travel_arrival, processing_completion
from .state import (
    ScenarioDefinitions,
    CalendarIndex,
    require_physical_state,
    physical_snapshot,
    ForecastResult,
    DynamicForecastResult,
    DynamicCandidate,
    ProjectedDelivery,
    Record,
    InventoryRunState,
    configured_plants,
    initialize_plant_states,
)
from .plant import finish_inventory_operation, start_inventory_operation
from .transport import retry_inventory_admissions


# Copy information available at the decision time

def build_forecast_context(scenario: Record, state: InventoryRunState) -> Record:
    """Copy information available at a static aware departure.

    routing.build_static_policy_input passes resolved definitions and live state.
    Return separate scenario/state data plus departed-leg counts for existing trips.
    physical_snapshot excludes unreleased products and event/decision logs.
    Keep current jobs, held slots, waiting vehicles and commitments. Read live data
    without changing it; write no files or events.
    """
    snapshot = physical_snapshot(state, released_only=True)
    physical = {key: copy.deepcopy(scenario[key]) for key in
                ('horizon', 'plant', 'collection_point', 'recycling_facilities', 'vehicles', 'trips')}
    physical['products'] = [{key: copy.deepcopy(product[key]) for key in
        ('id', 'origin', 'release_time', 'disposition', 'operations')} for product in scenario['products'] if product['id'] in snapshot['products']]
    progress = {vehicle['id']: sum(event['event_type'] == 'leg_departure' and event.get('vehicle_id') == vehicle['id']
                                 for event in state.events) for vehicle in scenario['vehicles']}
    return {'scenario': physical, 'state': snapshot, 'departed_legs': progress}


def build_dynamic_forecast_context(scenario: Record, state: InventoryRunState) -> Record:
    """Copy current physical data for a dynamic aware decision.

    routing.build_dynamic_policy_input passes definitions and live state. Return
    separate state and scenario dictionaries with released products only. Keep
    calendars, held slots, inventory, jobs and committed routes; leave out logs and
    future releases. No live data, events or files change.
    """
    snapshot = physical_snapshot(state, released_only=True)
    physical = {key: copy.deepcopy(scenario[key]) for key in ('horizon', 'plant', 'collection_points', 'recycling_facilities', 'vehicles')}
    if 'plants' in scenario:
        physical['plants'] = copy.deepcopy(configured_plants(scenario))
    physical.update(format_version=3, policy='dynamic_quality_aware', trips=[])
    physical['products'] = [{key: copy.deepcopy(product[key]) for key in
        ('id', 'origin', 'release_time', 'disposition', 'operations')} for product in scenario['products'] if product['id'] in snapshot['products']]
    return {'scenario': physical, 'state': snapshot}


# Predict fixed-trip arrival, admission and first processing start

def predicted_delivery_time(definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record, departed_legs: int = 0) -> float:
    """Return the final fixed-trip arrival time from the current physical situation.

    forecast_candidate supplies fixed definitions, state, trip and the number of
    already-departed legs. Continue after an active leg, or from the current site, through the
    remaining stops. Read only. Check copied active timing and all derived travel;
    invalid state/arithmetic raises rather than becoming a missing prediction.
    """
    vehicle = state.vehicles[trip['vehicle_id']]
    speed = definitions.vehicles[trip['vehicle_id']]['speed']
    if vehicle['active_leg'] is not None:
        leg = vehicle['active_leg']
        arrival = travel_arrival(leg['departure_time'], leg['distance'], speed, vehicle_id=trip['vehicle_id'])
        require_physical_state(leg['arrival_time'] == arrival, state, 'travel.active_arrival',
                               vehicle_id=trip['vehicle_id'], expected=arrival, observed=leg['arrival_time'])
        time = vehicle['active_leg']['arrival_time']
        position = vehicle['active_leg']['target_coordinates']
    else:
        time = state.time
        position = definitions.sites[vehicle['location']]['coordinates']
    for site_id in trip['stops'][departed_legs:]:
        target = definitions.sites[site_id]['coordinates']
        time = travel_arrival(time, physical_distance(position, target), speed, vehicle_id=trip['vehicle_id'])
        position = target
    return time


def forecast_candidate(context: Record, definitions: ScenarioDefinitions, trip: Record, product_id: str, selected: list[str]) -> ForecastResult:
    """Predict admission and first processing start for one fixed-trip product.

    routing.choose_quality_aware_products supplies context, fixed definitions,
    trip, candidate ID and IDs chosen for earlier payload slots. Make another
    state copy, include committed deliveries, and apply plant rules there.
    Return arrival, admission, first-start time and admission-to-start wait.
    None means the corresponding event was not observed by the horizon; arrival
    itself may be later. Full inventory can delay admission. Stop once the candidate
    starts. No live data or files change. Require this released product in context;
    invalid copied state or arithmetic raises, not a None prediction.
    """
    scenario = context['scenario']
    # Start with a separate copy before reconnecting shared plant fields inside it.
    state = InventoryRunState(**copy.deepcopy(context['state']))
    initialize_plant_states(scenario, state)
    for station in scenario['plant']['workstations']:
        operation = state.workstations[station['id']]
        if operation is not None:
            end = processing_completion(operation['start_time'], station['duration'],
                                        product_id=operation['product_id'], workstation_id=station['id'])
            require_physical_state(operation['end_time'] == end, state, 'processing.active_end',
                                   workstation_id=station['id'], expected=end, observed=operation['end_time'])
    arrival = predicted_delivery_time(definitions, state, trip)
    result: ForecastResult = {'predicted_arrival': arrival, 'predicted_admission': None, 'predicted_first_start': None,
              'predicted_inventory_wait': None}
    if trip['stops'][-1] != scenario['plant']['id']:
        if arrival <= scenario['horizon']:
            result.update(predicted_admission=arrival)
        return result
    deliveries: dict[str, tuple[float, list[str]]] = {}
    for committed in scenario['trips']:
        vehicle_id = committed['vehicle_id']
        if vehicle_id == trip['vehicle_id'] or committed['stops'][-1] != scenario['plant']['id'] or vehicle_id in state.waiting_vehicles:
            continue
        carried = sorted(candidate for candidate, product in state.products.items()
            if product['assigned_vehicle'] == vehicle_id and product['state'] in ('at_collection_point', 'onboard_av'))
        if carried:
            time = predicted_delivery_time(definitions, state, committed, context['departed_legs'][vehicle_id])
            deliveries[vehicle_id] = time, carried
    deliveries[trip['vehicle_id']] = arrival, sorted([*selected, product_id])
    pending = [(operation['end_time'], 0, station_id, 'complete') for station_id, operation in state.workstations.items() if operation is not None]
    pending.extend((opening, 1, station['id'], 'window') for station in scenario['plant']['workstations']
                   for opening, _ in station['windows'] if opening >= state.time)
    pending.extend((time, 2, vehicle_id, 'delivery') for vehicle_id, (time, _) in deliveries.items())
    pending.append((state.time, 1, '', 'settle'))
    heapq.heapify(pending)
    while pending and pending[0][0] <= scenario['horizon']:
        state.time = pending[0][0]
        batch = []
        while pending and pending[0][0] == state.time:
            batch.append(heapq.heappop(pending))
        for _, _, identifier, action in batch:
            if action == 'complete':
                finish_inventory_operation(scenario, definitions, state, identifier)
            elif action == 'delivery':
                load = deliveries[identifier][1]
                state.vehicles[identifier].update(location=scenario['plant']['id'], status='idle', load=load,
                    active_leg=None, waiting_since=None, wait_logged=False)
                state.waiting_vehicles[identifier] = state.time
                for carried in load:
                    state.products[carried].update(state='onboard_av', location=identifier,
                        assigned_vehicle=identifier, planned_destination=scenario['plant']['id'])
        while True:
            admitted = retry_inventory_admissions(scenario, definitions, state)
            started = False
            for station in sorted(scenario['plant']['workstations'], key=lambda station: station['id']):
                end = start_inventory_operation(scenario, definitions, state, station)
                if end is not None:
                    heapq.heappush(pending, (end, 0, station['id'], 'complete'))
                    started = True
                    operation = state.workstations[station['id']]
                    if operation['product_id'] == product_id:
                        admission = state.products[product_id]['milestones']['admission']
                        result.update(predicted_admission=admission, predicted_first_start=state.time,
                                      predicted_inventory_wait=state.time - admission)
                        return result
                    break
            if not admitted and not started:
                break
    result['predicted_admission'] = state.products[product_id]['milestones'].get('admission')
    return result


# Predict a dynamic insertion on its own running-state copy

def forecast_dynamic_insertion(policy_input: Record, candidate: DynamicCandidate, *, calendars: CalendarIndex | None = None) -> DynamicForecastResult:
    """Predict one feasible insertion on its own copy of physical state.

    routing.choose_dynamic_insertions supplies the permitted observation and a
    candidate. Copy state, assign the candidate, and replace that vehicle's route;
    keep other commitments. Cancel a changed active target only in this copy.
    run_dynamic_simulation executes it with dispatch disabled, so forecasting cannot
    call routing recursively. The local import avoids a circular module import.
    Return arrival/admission/start/wait and all projected delivery arrivals, including
    changed times for existing commitments. Unobserved events are None at the horizon.
    Optional calendars share fixed window tuples only; execution checks compatibility.
    Each candidate owns its changing state. No caller data or run files change;
    invalid physical state or arithmetic still raises.
    """
    from .engine import run_dynamic_simulation
    context = policy_input['forecast_context']
    scenario = context['scenario']
    # Start with a separate copy before reconnecting shared plant fields inside it.
    state = InventoryRunState(**copy.deepcopy(context['state']))
    initialize_plant_states(scenario, state)
    for request in policy_input['requests']:
        product = state.products[request['product_id']]
        product.update(assigned_vehicle=request['assigned_vehicle'], planned_destination=request['planned_destination'])
    product = state.products[candidate['product_id']]
    product.update(assigned_vehicle=candidate['vehicle_id'], planned_destination=candidate['destination'])
    for observed in policy_input['vehicles']:
        vehicle = state.vehicles[observed['id']]
        route = candidate['new_remaining_stops'] if observed['id'] == candidate['vehicle_id'] else observed['remaining_stops']
        vehicle['remaining_stops'] = copy.deepcopy(route)
        leg = vehicle['active_leg']
        if leg is not None and route and leg['target'] != route[0]['site_id']:
            vehicle.update(active_leg=None, status='idle', leg_distance_accounted=0.0)
    arrivals: list[ProjectedDelivery] = []
    # Keep every projected delivery; do not stop as soon as this product starts.
    projected = run_dynamic_simulation(scenario, initial_state=state, dispatch_enabled=False, projected_deliveries=arrivals,
                                       calendars=calendars)
    candidate_id = candidate['product_id']
    product = projected.products[candidate_id]
    arrival = next((record['arrival_time'] for record in arrivals if record['product_id'] == candidate_id), None)
    admission = product['milestones'].get('admission', product['milestones'].get('recycling'))
    first_start = next((event['time'] for event in projected.events
                        if event['event_type'] == 'operation_start' and event['product_id'] == candidate_id), None)
    return {'predicted_arrival': arrival, 'predicted_admission': admission, 'predicted_first_start': first_start,
        'predicted_inventory_wait': first_start - admission if first_start is not None else None,
        'projected_deliveries': arrivals}
