"""Advance the simulation clock and apply physical events and routing decisions.

runner calls run_inventory_simulation for fixed inventory trips, or
run_dynamic_simulation for dynamic routes. Both share execute_inventory.
Forecasting also runs the dynamic path on a separate state copy with dispatch
disabled. routing chooses; plant and transport apply
physical rules. This module changes supplied run state and records events, but
runner writes the files. Keep static and dynamic same-time ordering distinct.
"""
from __future__ import annotations

import copy
import heapq
from .numerics import physical_distance, travel_arrival, processing_completion, nonnegative_physical
from .state import (
    require_physical_state,
    ScenarioDefinitions,
    CalendarIndex,
    DynamicLeg,
    ProjectedDelivery,
    Record,
    InventoryRunState,
    InventoryEvent,
    build_inventory_state,
    record_event,
    configured_plants,
    initialize_plant_states,
    local_plant_view,
)
from .plant import (
    settle_plant_network, check_plant_network, finish_inventory_operation,
)
from .transport import (
    reset_vehicle_range, advance_vehicle_travel, accept_recycling_products,
    evaluate_fixed_trip_range,
    depart_inventory_leg, arrive_inventory_leg, collect_inventory_products,
)
from .routing import (
    build_dynamic_policy_input, choose_dynamic_insertions,
    build_static_policy_input, choose_static_products,
)


# Prepare inventory state and pending events

def build_dynamic_state(scenario: Record, definitions: ScenarioDefinitions) -> InventoryRunState:
    """Create initial inventory state and add dynamic vehicle positions and routes.

    execute_inventory calls for a new dynamic run. Return new state with vehicles
    at their initial sites, empty routes, full range and leg/revision counters.
    Fixed definitions stay unchanged; no events or files are written.
    """
    state = build_inventory_state(scenario, definitions)
    for definition in scenario['vehicles']:
        state.vehicles[definition['id']].update(position=list(definitions.sites[definition['initial_site']]['coordinates']),
            remaining_range=definition['maximum_range'], leg_distance_accounted=0.0,
            remaining_stops=[], route_version=0, leg_serial=0)
    return state


def fixed_inventory_events(scenario: Record) -> list[InventoryEvent]:
    """Build the initial pending events for a new fixed inventory run.

    execute_inventory supplies format-two inputs. Return release, window-opening
    and scheduled-departure tuples with their existing priorities and station-ID
    ties. The final token is empty. No state or recorded events change.
    """
    pending = [(part['release_time'], 1, part['id'], 'release', '') for part in scenario['products']]
    pending.extend((trip['departure_time'], 3, trip['vehicle_id'], 'dispatch', '') for trip in scenario['trips'])
    pending.extend((opening, 0, station['id'], 'window', '')
                   for station in scenario['plant']['workstations'] for opening, _ in station['windows'])
    return pending


def dynamic_inventory_events(
    scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState, *, continuing: bool,
) -> list[InventoryEvent]:
    """Build pending events for a new dynamic run or an already-running forecast.

    execute_inventory supplies state and continuing. A new run schedules releases
    and initial vehicle availability. A forecast resumes active legs/jobs, starts
    idle committed legs and requests immediate plant settlement. Return event tuples;
    starting a leg changes this supplied state and records leg_departure.
    Keep window-opening plant-ID ties. Check active timing before scheduling;
    invalid state/arithmetic raises. No live state is reached from a separate copy.
    """
    pending: list[InventoryEvent] = []
    if not continuing:
        pending.extend((part['release_time'], 1, part['id'], 'release', '') for part in scenario['products'])
        pending.extend((0, 2, vehicle['id'], 'available', '') for vehicle in scenario['vehicles'])
    else:
        for vehicle_id, vehicle in state.vehicles.items():
            if vehicle['active_leg'] is not None:
                leg = vehicle['active_leg']
                arrival = travel_arrival(leg['departure_time'], leg['distance'], definitions.vehicles[vehicle_id]['speed'],
                                         vehicle_id=vehicle_id)
                require_physical_state(leg['arrival_time'] == arrival, state, 'travel.active_arrival',
                                       vehicle_id=vehicle_id, expected=arrival, observed=leg['arrival_time'])
                pending.append((leg['arrival_time'], 2, vehicle_id, 'arrival', leg['leg_id']))
            else:
                arrival = start_dynamic_leg(definitions, state, vehicle_id)
                if arrival is not None:
                    pending.append(arrival)
        multiple_plants = len(configured_plants(scenario)) > 1
        for plant in configured_plants(scenario):
            _, view = local_plant_view(scenario, state, plant)
            stations = {station['id']: station for station in plant['workstations']}
            for station_id, operation in view.workstations.items():
                if operation is not None:
                    end = processing_completion(operation['start_time'], stations[station_id]['duration'],
                                                plant_id=plant['id'], workstation_id=station_id, product_id=operation['product_id'])
                    require_physical_state(operation['end_time'] == end, state, 'processing.active_end',
                                           plant_id=plant['id'], workstation_id=station_id, expected=end, observed=operation['end_time'])
                    pending.append((operation['end_time'], 0, plant['id'], 'complete', station_id) if multiple_plants else
                                   (operation['end_time'], 0, station_id, 'complete', ''))
        pending.append((state.time, 0, '', 'window', ''))
    pending.extend((opening, 0, plant['id'], 'window', '') for plant in configured_plants(scenario)
                   for station in plant['workstations'] for opening, _ in station['windows'] if opening >= state.time)
    return pending


# Apply fixed-trip decisions and arrivals

def dispatch_inventory_trip(
    scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record,
) -> bool:
    """Choose fixed-trip products at departure and apply their assignments.

    execute_inventory passes fixed definitions, state and the trip. Check the full
    range plan and call routing with the permitted observation. Save the decision,
    then assign selected products and record assignment events. Finite vehicles also
    record range_plan_check. An infeasible trip records trip_rejected and returns
    False before departure; feasible trips, including empty ones, return True.
    Only run state changes. Plant/transport helpers perform the later physical work.
    """
    vehicle_id = trip['vehicle_id']
    vehicle = definitions.vehicles[vehicle_id]
    destination = trip['stops'][-1]
    disposition = 'remanufacture' if destination == scenario['plant']['id'] else 'recycle'
    range_check = evaluate_fixed_trip_range(definitions, state, trip)
    if vehicle['maximum_range'] is not None:
        record_event(state, 'range_plan_check', vehicle_id=vehicle_id, **range_check)
    boundary = build_static_policy_input(scenario, definitions, state, trip)
    selection = choose_static_products(
        boundary, payload_capacity=vehicle['payload_capacity'], disposition=disposition,
        pickup_site=scenario['collection_point']['id'], range_feasible=range_check['feasible'],
        quality_aware=scenario['policy'] == 'static_quality_aware',
    )
    state.decisions.append({
        'time': state.time, 'trigger': 'static_departure',
        'last_event_sequence': len(state.events), 'policy_input': boundary, **selection,
    })
    if not range_check['feasible']:
        state.decisions[-1]['decision']['rejection'] = range_check['rejection']
        record_event(state, 'trip_rejected', vehicle_id=vehicle_id, **range_check['rejection'])
        return False
    for product_id in selection['decision']['assigned_products']:
        state.products[product_id].update(assigned_vehicle=vehicle_id, planned_destination=destination)
        record_event(state, 'assignment', product_id=product_id, vehicle_id=vehicle_id, destination=destination)
    return True


def start_fixed_leg(
    definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record, stop_index: int,
) -> InventoryEvent:
    """Start travel to a fixed trip's zero-based stop_index and return its arrival tuple.

    Fixed dispatch/arrival handling calls this. depart_inventory_leg checks range,
    changes the vehicle's active leg and records leg_departure. The vehicle must
    be at a configured site. Only supplied state changes; no files are written.
    """
    vehicle_id = trip['vehicle_id']
    origin = definitions.sites[state.vehicles[vehicle_id]['location']]
    target = definitions.sites[trip['stops'][stop_index]]
    arrival = depart_inventory_leg(definitions, state, trip, origin, target, stop_index)
    return arrival, 2, vehicle_id, 'arrival', ''


def service_fixed_arrival(
    scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState,
    trip: Record, stop_indices: dict[str, int],
) -> InventoryEvent | None:
    """Apply a fixed arrival and continue through its configured stops.

    execute_inventory passes the trip and its stop_indices dictionary. Record
    arrival/range reset and any pickup or recycling through transport helpers.
    At the final plant, queue a loaded vehicle for settlement. Otherwise start the
    next leg immediately and return its arrival tuple; return None at the final stop.
    Change supplied state and stop counters only; write no files.
    """
    vehicle_id = trip['vehicle_id']
    arrive_inventory_leg(definitions, state, vehicle_id)
    stop_indices[vehicle_id] += 1
    final_stop = stop_indices[vehicle_id] == len(trip['stops'])
    if state.vehicles[vehicle_id]['location'] == scenario['collection_point']['id']:
        collect_inventory_products(scenario, state, vehicle_id)
    if final_stop:
        if state.vehicles[vehicle_id]['location'] != scenario['plant']['id']:
            accept_recycling_products(definitions, state, vehicle_id)
        elif state.vehicles[vehicle_id]['load']:
            state.waiting_vehicles[vehicle_id] = state.time
        return None
    return start_fixed_leg(definitions, state, trip, stop_indices[vehicle_id])


# Apply dynamic routes, service and new decisions

def start_dynamic_leg(definitions: ScenarioDefinitions, state: InventoryRunState, vehicle_id: str) -> InventoryEvent | None:
    """Depart toward the first remaining stop and return a tagged arrival event.

    schedule_dynamic_legs and forecast initialization call with state and vehicle ID.
    Return None if travelling, waiting to unload or without stops. Otherwise use the
    actual position, check range/time, create a new leg ID and record leg_departure.
    Only the supplied state changes. The leg ID lets later code ignore cancelled
    arrivals. Invalid range or unrepresentable travel raises before departure.
    """
    vehicle = state.vehicles[vehicle_id]
    if vehicle['active_leg'] is not None or vehicle['status'] == 'waiting_to_unload' or not vehicle['remaining_stops']:
        return None
    target = definitions.sites[vehicle['remaining_stops'][0]['site_id']]
    definition = definitions.vehicles[vehicle_id]
    distance = physical_distance(vehicle['position'], target['coordinates'], time=state.time, vehicle_id=vehicle_id)
    if vehicle['remaining_range'] is not None and distance > vehicle['remaining_range'] + 1e-9:
        raise ValueError(f'Vehicle {vehicle_id} cannot depart on an infeasible leg')
    arrival = travel_arrival(state.time, distance, definition['speed'], vehicle_id=vehicle_id)
    vehicle['leg_serial'] += 1
    leg: DynamicLeg = {'leg_id': f"{vehicle_id}:L{vehicle['leg_serial']}", 'vehicle_id': vehicle_id,
        'origin_coordinates': list(vehicle['position']), 'target': target['id'], 'target_coordinates': target['coordinates'],
        'departure_time': state.time, 'arrival_time': arrival,
        'distance': distance, 'load': list(vehicle['load']), 'remaining_range_at_departure': vehicle['remaining_range']}
    vehicle.update(active_leg=leg, status='travelling', leg_distance_accounted=0.0)
    record_event(state, 'leg_departure', **leg)
    return leg['arrival_time'], 2, vehicle_id, 'arrival', leg['leg_id']


def service_dynamic_stop(scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState, vehicle_id: str) -> None:
    """Perform the pickups or authorized deliveries at the current arrived stop.

    service_dynamic_arrival calls with the supplied run/forecast state. Check pickup
    ownership, origin and payload before moving products onboard and recording pickup.
    For deliveries, queue plant admission or call recycler service; return None.
    Only this stop's delivery IDs authorize unloading. Full inventory keeps those
    products onboard until retries succeed. Load for a later visit stays onboard
    even when it has the same destination. No run files change.
    """
    vehicle = state.vehicles[vehicle_id]
    stop = vehicle['remaining_stops'][0]
    require_physical_state(vehicle['active_leg'] is None and vehicle['location'] == stop['site_id'],
                           state, 'service.arrived', vehicle_id=vehicle_id, expected=stop['site_id'],
                           observed=vehicle['location'], active_leg=vehicle['active_leg'])
    products = definitions.products
    for product_id in stop['pickups']:
        product = state.products[product_id]
        require_physical_state(product['state'] == 'at_collection_point' and product['assigned_vehicle'] == vehicle_id, state, 'pickup.ownership',
                               product_id=product_id, vehicle_id=vehicle_id, observed=product['state'], assigned_vehicle=product['assigned_vehicle'])
        require_physical_state(products[product_id]['origin'] == stop['site_id'], state, 'pickup.origin',
                               product_id=product_id, vehicle_id=vehicle_id, expected=stop['site_id'], observed=products[product_id]['origin'])
        require_physical_state(product['location'] == stop['site_id'], state, 'pickup.location',
                               product_id=product_id, vehicle_id=vehicle_id, expected=stop['site_id'], observed=product['location'])
        require_physical_state(len(vehicle['load']) < definitions.vehicles[vehicle_id]['payload_capacity'],
                               state, 'vehicle.capacity', vehicle_id=vehicle_id, product_id=product_id,
                               expected=definitions.vehicles[vehicle_id]['payload_capacity'], observed=len(vehicle['load']) + 1)
        product.update(state='onboard_av', location=vehicle_id)
        product['milestones']['pickup'] = state.time
        vehicle['load'].append(product_id)
        record_event(state, 'pickup', product_id=product_id, vehicle_id=vehicle_id, collection_point=stop['site_id'])
    if stop['deliveries']:
        if stop['site_id'] in definitions.plants:
            _, view = local_plant_view(scenario, state, definitions.plants[stop['site_id']])
            view.waiting_vehicles[vehicle_id] = state.time
        else:
            accept_recycling_products(definitions, state, vehicle_id)


def service_dynamic_arrival(
    scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState,
    vehicle_id: str, token: str, projected_deliveries: list[ProjectedDelivery] | None,
) -> None:
    """Apply an arrival only when its token still matches the vehicle's active leg.

    execute_inventory has already accounted for actual travel. A matching arrival
    sets the site/position, records leg_arrival and resets finite range before
    service_dynamic_stop. A forecast may also collect authorized delivery-arrival
    records in projected_deliveries. A stale token changes nothing.
    Return None; mutations and delegated events affect only supplied state.
    """
    vehicle = state.vehicles[vehicle_id]
    leg = vehicle['active_leg']
    if leg is None or leg['leg_id'] != token:
        return
    details = {**leg, 'remaining_range_on_arrival': vehicle['remaining_range']}
    vehicle.update(location=leg['target'], position=list(leg['target_coordinates']), active_leg=None,
                   status='idle', leg_distance_accounted=0.0)
    record_event(state, 'leg_arrival', **details)
    reset_vehicle_range(definitions, state, vehicle_id)
    if projected_deliveries is not None:
        for product_id in vehicle['remaining_stops'][0]['deliveries']:
            projected_deliveries.append({'product_id': product_id, 'vehicle_id': vehicle_id,
                                        'destination': leg['target'], 'arrival_time': state.time})
    service_dynamic_stop(scenario, definitions, state, vehicle_id)


def finish_dynamic_stops(definitions: ScenarioDefinitions, state: InventoryRunState) -> list[str]:
    """Remove arrived stops whose authorized work is finished.

    execute_inventory calls after plant settlement. Read and change supplied routes;
    return vehicle IDs available at facilities and record stop_completed and
    vehicle_available. Travelling or blocked vehicles keep their head stop.
    A stop can finish while load for a later visit stays onboard. Do not remove
    those future delivery obligations. No files change.
    """
    available = []
    facilities = definitions.facility_ids
    for vehicle_id, vehicle in sorted(state.vehicles.items()):
        if vehicle['active_leg'] is not None or vehicle['status'] == 'waiting_to_unload' or not vehicle['remaining_stops']:
            continue
        stop = vehicle['remaining_stops'][0]
        if vehicle['location'] != stop['site_id']:
            continue
        if any(product_id in vehicle['load'] for product_id in stop['deliveries']):
            continue
        vehicle['remaining_stops'].pop(0)
        record_event(state, 'stop_completed', vehicle_id=vehicle_id, stop=copy.deepcopy(stop))
        if stop['site_id'] in facilities:
            record_event(state, 'vehicle_available', vehicle_id=vehicle_id, reason='facility_stop')
            available.append(vehicle_id)
    return available


def dispatch_dynamic_requests(scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState, trigger: Record) -> None:
    """Ask routing for a decision, record it, then apply its assignments and routes.

    execute_inventory calls after returns release or vehicles become available.
    trigger names those products/vehicles. Build the permitted observation and call
    choose_dynamic_insertions; save all candidate evidence in state.decisions.
    Apply winners to the supplied state. A changed active target records leg_cancelled
    at the already-accounted actual position, then assignment/route_revised as needed.
    Record dispatch before selection. Return None; runner later writes the logs.
    """
    # Selection reads a permitted observation; only this engine applies winners.
    record_event(state, 'dispatch', trigger=trigger, decision_index=len(state.decisions) + 1)
    boundary = build_dynamic_policy_input(scenario, definitions, state)
    proposals, audits = choose_dynamic_insertions(boundary)
    state.decisions.append({'time': state.time, 'trigger': copy.deepcopy(trigger), 'last_event_sequence': len(state.events),
        'policy_input': boundary, 'requests': audits, 'proposals': proposals})
    # Keep future obligations; cancel only travel whose next target changed.
    for proposal in proposals:
        vehicle_id, product_id = proposal['vehicle_id'], proposal['product_id']
        vehicle = state.vehicles[vehicle_id]
        state.products[product_id].update(assigned_vehicle=vehicle_id, planned_destination=proposal['destination'])
        record_event(state, 'assignment', product_id=product_id, vehicle_id=vehicle_id, destination=proposal['destination'])
        leg = vehicle['active_leg']
        if leg is not None and proposal['new_remaining_stops'][0]['site_id'] != leg['target']:
            record_event(state, 'leg_cancelled', vehicle_id=vehicle_id, leg_id=leg['leg_id'],
                old_arrival_time=leg['arrival_time'], distance_travelled=vehicle['leg_distance_accounted'],
                position=list(vehicle['position']), remaining_range=vehicle['remaining_range'])
            vehicle.update(active_leg=None, status='idle', leg_distance_accounted=0.0)
        old = copy.deepcopy(vehicle['remaining_stops'])
        vehicle['remaining_stops'] = copy.deepcopy(proposal['new_remaining_stops'])
        vehicle['route_version'] += 1
        record_event(state, 'route_revised', vehicle_id=vehicle_id, product_id=product_id,
            route_version=vehicle['route_version'], position=list(vehicle['position']), remaining_range=vehicle['remaining_range'],
            old_remaining_stops=old, new_remaining_stops=copy.deepcopy(vehicle['remaining_stops']))


def schedule_dynamic_legs(
    definitions: ScenarioDefinitions, state: InventoryRunState, pending: list[InventoryEvent],
) -> None:
    """Start ready committed routes in vehicle-ID order and queue their arrivals.

    execute_inventory supplies state and its pending-event heap (earliest event
    first). start_dynamic_leg changes vehicles and records departures as needed.
    Return None. This does not choose new routes or write files.
    """
    for vehicle_id in sorted(state.vehicles):
        arrival = start_dynamic_leg(definitions, state, vehicle_id)
        if arrival is not None:
            heapq.heappush(pending, arrival)


# Run the shared inventory clock and finish at the horizon

def discard_stale_arrivals(state: InventoryRunState, pending: list[InventoryEvent]) -> None:
    """Remove cancelled arrivals at the queue head before advancing the clock.

    execute_inventory calls on the pending-event heap. Compare each arrival token
    with the active leg and discard obsolete entries. Change the queue only;
    record no arrival, delivery or range reset for a cancelled leg.
    """
    while pending and pending[0][3] == 'arrival':
        item = pending[0]
        leg = state.vehicles[item[2]]['active_leg']
        if leg is not None and leg['leg_id'] == item[4]:
            break
        heapq.heappop(pending)


def finish_inventory_horizon(scenario: Record, state: InventoryRunState) -> None:
    """Account for partial travel and unfinished waits at the run end.

    execute_inventory calls once after its event loop. Advance tracked travel,
    set the clock to the horizon and add inventory/unloading waits through that time.
    Fixed unlimited vehicles keep their existing separate travel accounting.
    Count authoritative plant containers once and record horizon. Later-completing
    jobs remain active. Invalid accumulated arithmetic raises; no files change.
    """
    advance_vehicle_travel(scenario, state, scenario['horizon'])
    state.time = scenario['horizon']
    for containers in state.plant_states.values():
        for product_id, entered in containers['inventory'].items():
            state.products[product_id]['inventory_wait'] = nonnegative_physical(state.products[product_id]['inventory_wait'] + (state.time - entered), 'product.inventory_wait', time=state.time)
    for definition in scenario['vehicles']:
        vehicle = state.vehicles[definition['id']]
        if 'position' not in vehicle and vehicle['active_leg'] is not None:
            vehicle['distance'] = nonnegative_physical(vehicle['distance'] + ((state.time - vehicle['active_leg']['departure_time']) * definition['speed']), 'vehicle.distance', time=state.time)
        if vehicle['waiting_since'] is not None:
            vehicle['waiting_to_unload'] = nonnegative_physical(vehicle['waiting_to_unload'] + (state.time - vehicle['waiting_since']), 'vehicle.waiting_to_unload', time=state.time)
    record_event(state, 'horizon',
                 waiting_count=sum(len(plant['inventory']) for plant in state.plant_states.values()),
                 held_count=sum(len(plant['held_slots']) for plant in state.plant_states.values()))


def execute_inventory(
    scenario: Record, *, dynamic: bool, initial_state: InventoryRunState | None = None,
    dispatch_enabled: bool = True, projected_deliveries: list[ProjectedDelivery] | None = None,
    calendars: CalendarIndex | None = None,
) -> InventoryRunState:
    """Run the shared event loop for fixed trips, dynamic routes or a private forecast.

    run_inventory_simulation and run_dynamic_simulation supply validated inputs.
    Create state and a pending-event heap, or continue the supplied initial_state.
    Return the changed final state. Fixed definitions are read only; optional
    prepared calendars are checked for compatibility and never exported.

    Advance actual travel before changing the clock. Apply due completions,
    releases and arrivals, then settle plants. Static batches settle before their
    scheduled departures. Dynamic batches drain new same-time events before routing,
    and repeat after zero-distance departures. Discard cancelled arrivals before
    they can advance time. Keep each mode's priorities and ID ties.

    A forecast supplies its own state copy and sets dispatch_enabled=False to avoid
    new routing decisions. projected_deliveries collects authorized arrivals there.
    Helpers record events in supplied state; runner writes files after a real run.
    Physical and arithmetic failures propagate rather than being skipped.
    """
    # A forecast supplies its separate running state; a new run starts empty.
    definitions = ScenarioDefinitions.from_scenario(scenario, calendars=calendars)
    if initial_state is not None:
        state = initial_state
        initialize_plant_states(scenario, state)
    else:
        state = build_dynamic_state(scenario, definitions) if dynamic else build_inventory_state(scenario, definitions)
    if dynamic:
        pending = dynamic_inventory_events(scenario, definitions, state, continuing=initial_state is not None)
        trips, stop_indices = {}, {}
    else:
        pending = fixed_inventory_events(scenario)
        trips = {trip['vehicle_id']: trip for trip in scenario['trips']}
        stop_indices = {vehicle['id']: 0 for vehicle in scenario['vehicles']}
    heapq.heapify(pending)
    multiple_plants = len(configured_plants(scenario)) > 1
    # Discard obsolete arrivals before choosing the next physical timestamp.
    while pending:
        if dynamic:
            discard_stale_arrivals(state, pending)
        if not pending or pending[0][0] > scenario['horizon']:
            break
        time = pending[0][0]
        advance_vehicle_travel(scenario, state, time)
        state.time = time
        releases, available = set(), set()
        while True:
            due = []
            while pending and pending[0][0] == time:
                due.append(heapq.heappop(pending))
            departures = []
            for _, _, identifier, kind, token in due:
                if kind == 'release':
                    definition = definitions.products[identifier]
                    state.products[identifier].update(state='at_collection_point', location=definition['origin'])
                    state.products[identifier]['milestones']['release'] = time
                    record_event(state, 'return_release', product=copy.deepcopy(definition))
                    releases.add(identifier)
                elif kind == 'complete':
                    plant = definitions.plants[identifier] if multiple_plants else scenario['plant']
                    local, view = local_plant_view(scenario, state, plant)
                    finish_inventory_operation(local, definitions, view, token if multiple_plants else identifier)
                elif kind == 'arrival':
                    if dynamic:
                        service_dynamic_arrival(scenario, definitions, state, identifier, token, projected_deliveries)
                    else:
                        arrival = service_fixed_arrival(scenario, definitions, state, trips[identifier], stop_indices)
                        if arrival is not None:
                            heapq.heappush(pending, arrival)
                elif kind == 'available':
                    record_event(state, 'vehicle_available', vehicle_id=identifier, reason='initial')
                    available.add(identifier)
                elif kind == 'dispatch':
                    departures.append(identifier)
            # A final-operation start can free a slot and admit waiting load now.
            for completion in settle_plant_network(scenario, definitions, state):
                heapq.heappush(pending, completion)
            if not dynamic:
                for vehicle_id in departures:
                    if dispatch_inventory_trip(scenario, definitions, state, trips[vehicle_id]):
                        heapq.heappush(pending, start_fixed_leg(definitions, state, trips[vehicle_id], 0))
                break  # Fixed execution checks conservation after each batch.
            available.update(finish_dynamic_stops(definitions, state))
            schedule_dynamic_legs(definitions, state, pending)
            if pending and pending[0][0] == time:
                continue
            # Routing runs only after current-time service has settled.
            if dispatch_enabled and (releases or available):
                trigger = {'released_products': sorted(releases), 'available_vehicles': sorted(available)}
                dispatch_dynamic_requests(scenario, definitions, state, trigger)
                releases.clear()
                available.clear()
                schedule_dynamic_legs(definitions, state, pending)
                if pending and pending[0][0] == time:
                    continue
            releases.clear()
            available.clear()
            break
        check_plant_network(scenario, state)
    # Include unfinished travel and waits; do not complete work beyond the horizon.
    finish_inventory_horizon(scenario, state)
    return state


def run_inventory_simulation(scenario: Record) -> InventoryRunState:
    """Return the horizon state of a new fixed inventory run via execute_inventory.

    runner.run_once calls this for static format two. Create and change new state;
    read definitions only. Helpers record events; no files are written here.
    """
    return execute_inventory(scenario, dynamic=False)


def run_dynamic_simulation(
    scenario: Record, *, initial_state: InventoryRunState | None = None,
    dispatch_enabled: bool = True, projected_deliveries: list[ProjectedDelivery] | None = None,
    calendars: CalendarIndex | None = None,
) -> InventoryRunState:
    """Return the horizon state of a dynamic run or a separate copied forecast.

    runner supplies normalized network inputs for a new run. Forecasting supplies
    initial_state, disables dispatch and may collect projected_deliveries.
    execute_inventory changes only that execution's state and records its events.
    Optional calendars share fixed tuples, with compatibility checked on entry.
    Definitions and files stay unchanged. Invalid state/arithmetic still raises.
    """
    return execute_inventory(scenario, dynamic=True, initial_state=initial_state,
                             dispatch_enabled=dispatch_enabled, projected_deliveries=projected_deliveries,
                             calendars=calendars)
