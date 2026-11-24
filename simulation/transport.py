"""Move vehicles and products, admit plant stock and accept recycling deliveries.

engine calls travel and pickup functions. plant settlement calls admission
retries; forecasts use the same mechanics on separate state. Inventory helpers
serve both fixed and dynamic runs. Dynamic unloading requires delivery IDs at
the current stop; later-stop load stays onboard. Range resets only at actual facility arrival.
These functions change supplied state and record events, never write run files.
"""
from __future__ import annotations

from .numerics import physical_distance, travel_arrival, finite_physical, nonnegative_physical, travel_prefix
from .state import (
    require_physical_state,
    ScenarioDefinitions,
    FixedLeg,
    SiteDefinition,
    Record,
    InventoryRunState,
    record_event,
    inventory_occupancy,
)


# Travel and collect on fixed inventory trips

def depart_inventory_leg(
    definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record,
    origin: SiteDefinition, target: SiteDefinition, stop_index: int = 0,
) -> float:
    """Start a fixed inventory trip leg and return its checked arrival time.

    engine.start_fixed_leg supplies definitions, state, trip, sites and zero-based
    stop_index. For finite range, recheck the remaining trip and save the departure
    allowance. Set travelling/active_leg and record leg_departure.
    An infeasible suffix raises ValueError; invalid geometry/time raises
    PhysicalArithmeticError before departure. Only supplied state changes.
    """
    vehicle_id = trip['vehicle_id']
    vehicle = state.vehicles[vehicle_id]
    definition = definitions.vehicles[vehicle_id]
    distance = physical_distance(origin['coordinates'], target['coordinates'], time=state.time, vehicle_id=vehicle_id)
    leg: FixedLeg = {'vehicle_id': vehicle_id, 'origin': origin['id'], 'target': target['id'],
        'origin_coordinates': origin['coordinates'], 'target_coordinates': target['coordinates'],
        'departure_time': state.time, 'arrival_time': travel_arrival(state.time, distance, definition['speed'], vehicle_id=definition['id']),
        'distance': distance, 'load': list(vehicle['load'])}
    if definition['maximum_range'] is not None:
        suffix_check = evaluate_fixed_trip_range(definitions, state, trip, stop_index)
        if not suffix_check['feasible']:
            raise ValueError(f"Vehicle {vehicle_id}: {suffix_check['rejection']}")
        leg['remaining_range_at_departure'] = vehicle['remaining_range']
        vehicle['leg_distance_accounted'] = 0.0
    vehicle.update(active_leg=leg, status='travelling')
    record_event(state, 'leg_departure', **leg)
    return leg['arrival_time']


def arrive_inventory_leg(definitions: ScenarioDefinitions, state: 'InventoryRunState', vehicle_id: str) -> None:
    """Apply a fixed inventory arrival, then reset finite range before service.

    engine.service_fixed_arrival passes definitions, state and vehicle ID.
    Require an active leg ending now; clear it, set target/idle and record leg_arrival.
    Tracked distance was already added. Unlimited fixed legs add their distance here.
    reset_vehicle_range records a reset at a facility. Return None; plant capacity
    blocking is handled later by settlement. No files change.
    """
    vehicle = state.vehicles[vehicle_id]
    leg = vehicle['active_leg']
    require_physical_state(leg is not None and leg['arrival_time'] == state.time, state, 'arrival.active_leg',
                           vehicle_id=vehicle_id, observed=leg)
    limited = 'remaining_range' in vehicle
    if not limited:
        vehicle['distance'] = nonnegative_physical(vehicle['distance'] + (leg['distance']), 'vehicle.distance', time=state.time)
    vehicle.update(location=leg['target'], status='idle', active_leg=None)
    details = dict(leg)
    if limited:
        details['remaining_range_on_arrival'] = vehicle['remaining_range']
        vehicle['leg_distance_accounted'] = 0.0
        vehicle['position'] = list(leg['target_coordinates'])
    record_event(state, 'leg_arrival', **details)
    reset_vehicle_range(definitions, state, vehicle_id)


def collect_inventory_products(scenario: Record, state: 'InventoryRunState', vehicle_id: str) -> None:
    """Pick up a fixed trip's assigned waiting products in product-ID order.

    engine.service_fixed_arrival calls at collection. Check location and available
    payload, move products onboard and record pickup with its milestone.
    Return None; change only supplied state. Dispatch/engine already checked the
    trip and assignment; this does not repeat full-route feasibility.
    """
    vehicle = state.vehicles[vehicle_id]
    for product_id in sorted(state.products):
        product = state.products[product_id]
        if product['assigned_vehicle'] == vehicle_id and product['state'] == 'at_collection_point':
            require_physical_state(vehicle['location'] == product['location'] == scenario['collection_point']['id'],
                                   state, 'pickup.location', product_id=product_id, vehicle_id=vehicle_id,
                                   location=vehicle['location'], expected=scenario['collection_point']['id'])
            capacity = next(av['payload_capacity'] for av in scenario['vehicles'] if av['id'] == vehicle_id)
            require_physical_state(len(vehicle['load']) < capacity, state, 'vehicle.capacity',
                                   vehicle_id=vehicle_id, product_id=product_id, expected=capacity, observed=len(vehicle['load']) + 1)
            product.update(state='onboard_av', location=vehicle_id)
            product['milestones']['pickup'] = state.time
            vehicle['load'].append(product_id)
            record_event(state, 'pickup', product_id=product_id, vehicle_id=vehicle_id,
                         collection_point=scenario['collection_point']['id'])


# Serve only the products authorized at this facility visit

def facility_delivery_products(state: InventoryRunState, vehicle_id: str, facility_id: str) -> list[str]:
    """Return onboard product IDs authorized at this facility visit, in load order.

    Admission retries and recycler service read live state or a separate forecast.
    A dynamic vehicle uses its current stop's delivery IDs, copied into a local
    fixed set. A fixed trip uses planned destination at final service.
    Return a new list without state changes. A missing/mismatched dynamic head stop
    raises PhysicalStateError. Sharing the destination alone never authorizes a
    later-stop product; waiting vehicles keep that head stop until service finishes.
    """
    vehicle = state.vehicles[vehicle_id]
    authorized: frozenset[str] | None = None
    if 'remaining_stops' in vehicle:
        stops = vehicle['remaining_stops']
        require_physical_state(bool(stops) and stops[0]['site_id'] == facility_id and vehicle['location'] == facility_id,
                               state, 'delivery.current_stop', vehicle_id=vehicle_id, facility_id=facility_id,
                               location=vehicle['location'], current_stop=stops[0] if stops else None)
        authorized = frozenset(stops[0]['deliveries'])
    return [product_id for product_id in vehicle['load']
            if state.products[product_id]['planned_destination'] == facility_id
            and (authorized is None or product_id in authorized)]


def retry_inventory_admissions(scenario: Record, definitions: ScenarioDefinitions, state: 'InventoryRunState') -> bool:
    """Try waiting vehicles at one plant in arrival-time, then vehicle-ID order.

    Plant settlement and static forecasts supply local plant state and definitions.
    Return whether any product entered inventory. facility_delivery_products limits
    dynamic service to this stop, including each retry; fixed service stays
    based on destination. Move authorized products into free slots, counting holds.
    Record plant_admission and, after blocking, unload_retry. If current work remains,
    keep it onboard and record unload_wait once. When it finishes, add elapsed wait
    and remove the queue entry. Later-stop load stays onboard. Check ownership and
    remanufacture disposition. No range reset or file write occurs here.
    """
    changed = False
    products = definitions.products
    capacity = scenario['plant']['inventory_capacity']
    for vehicle_id in sorted(state.waiting_vehicles, key=lambda vehicle_id: (state.waiting_vehicles[vehicle_id], vehicle_id)):
        vehicle = state.vehicles[vehicle_id]
        eligible = facility_delivery_products(state, vehicle_id, scenario['plant']['id'])
        while eligible and (capacity is None or inventory_occupancy(state) < capacity):
            if vehicle['wait_logged']:
                record_event(state, 'unload_retry', vehicle_id=vehicle_id,
                             arrival_time=state.waiting_vehicles[vehicle_id])
            product_id = eligible.pop(0)
            product = state.products[product_id]
            require_physical_state(product['state'] == 'onboard_av' and product['location'] == vehicle_id,
                                   state, 'admission.ownership', product_id=product_id, vehicle_id=vehicle_id,
                                   plant_id=scenario['plant']['id'], observed=product['state'], location=product['location'])
            require_physical_state(products[product_id]['disposition'] == 'remanufacture' and product['planned_destination'] == scenario['plant']['id'], state, 'admission.disposition_destination',
                                   product_id=product_id, vehicle_id=vehicle_id, plant_id=scenario['plant']['id'], disposition=products[product_id]['disposition'])
            vehicle['load'].remove(product_id)
            product.update(state='in_plant_inventory', location=scenario['plant']['id'],
                           actual_destination=scenario['plant']['id'])
            product['milestones']['admission'] = state.time
            state.inventory[product_id] = state.time
            record_event(state, 'plant_admission', product_id=product_id, vehicle_id=vehicle_id,
                plant_id=scenario['plant']['id'], waiting_count=len(state.inventory), held_count=len(state.held_slots))
            changed = True
        if eligible:
            vehicle['status'] = 'waiting_to_unload'
            if not vehicle['wait_logged']:
                vehicle.update(wait_logged=True, waiting_since=state.time)
                record_event(state, 'unload_wait', vehicle_id=vehicle_id,
                    arrival_time=state.waiting_vehicles[vehicle_id], load=list(vehicle['load']),
                    waiting_count=len(state.inventory), held_count=len(state.held_slots))
        else:
            if vehicle['waiting_since'] is not None:
                vehicle['waiting_to_unload'] = nonnegative_physical(vehicle['waiting_to_unload'] + (state.time - vehicle['waiting_since']), 'vehicle.waiting_to_unload', time=state.time)
            vehicle.update(status='idle', waiting_since=None, wait_logged=False)
            del state.waiting_vehicles[vehicle_id]
    return changed


def accept_recycling_products(definitions: ScenarioDefinitions, state: InventoryRunState, vehicle_id: str) -> None:
    """Accept only this visit's eligible recycling load at an arrived recycler.

    Engine facility service passes definitions, state and vehicle ID.
    facility_delivery_products chooses the authorized IDs. Check recycler location,
    ownership, recycle disposition and empty operation sequence. Remove those parts,
    mark them recycled, save destination/time and record recycler_acceptance.
    Return None. Other load stays onboard; no processing history or files are written.
    """
    vehicle = state.vehicles[vehicle_id]
    require_physical_state(vehicle['location'] in definitions.facility_ids and vehicle['location'] not in definitions.plants, state, 'recycling.facility',
                           vehicle_id=vehicle_id, location=vehicle['location'])
    products = definitions.products
    for product_id in facility_delivery_products(state, vehicle_id, vehicle['location']):
        product = state.products[product_id]
        require_physical_state(product['state'] == 'onboard_av' and product['location'] == vehicle_id,
                               state, 'recycling.ownership', product_id=product_id, vehicle_id=vehicle_id,
                               observed=product['state'], location=product['location'])
        require_physical_state(products[product_id]['disposition'] == 'recycle' and not products[product_id]['operations'], state, 'recycling.disposition',
                               product_id=product_id, vehicle_id=vehicle_id, recycler_id=vehicle['location'], disposition=products[product_id]['disposition'], operations=products[product_id]['operations'])
        require_physical_state(product['planned_destination'] == vehicle['location'], state, 'recycling.destination',
                               product_id=product_id, vehicle_id=vehicle_id, expected=vehicle['location'], observed=product['planned_destination'])
        vehicle['load'].remove(product_id)
        product.update(state='recycled', location=None, actual_destination=vehicle['location'])
        product['milestones']['recycling'] = state.time
        record_event(state, 'recycler_acceptance', product_id=product_id, vehicle_id=vehicle_id,
                     recycler_id=vehicle['location'])


RANGE_TOLERANCE = 1e-9


# Check range and account for actual travel

def evaluate_fixed_trip_range(definitions: ScenarioDefinitions, state: InventoryRunState, trip: Record,
                              start_index: int = 0) -> Record:
    """Return required/available range for each remaining fixed facility segment.

    Fixed dispatch/departure passes definitions, state, trip and zero-based next
    stop index. Use actual position/allowance. Only plants and recyclers refill range;
    the validated trip ends at one. Unlimited range returns feasible with no segments.
    A failing segment returns rejection details, not an exception. Read only;
    invalid references/arithmetic may raise. No events or files change.
    """
    vehicle_id = trip['vehicle_id']
    definition = definitions.vehicles[vehicle_id]
    maximum = definition['maximum_range']
    if maximum is None:
        return {'feasible': True, 'segments': []}
    vehicle = state.vehicles[vehicle_id]
    position = vehicle['position']
    allowance = vehicle['remaining_range']
    segment_start = vehicle['location']
    facilities = definitions.facility_ids
    required = 0.0
    segments = []
    for site_id in trip['stops'][start_index:]:
        site = definitions.sites[site_id]
        required = nonnegative_physical(required + (physical_distance(position, site['coordinates'])), 'required', time=state.time)
        position = site['coordinates']
        if site_id in facilities:
            segments.append({'start': segment_start, 'end': site_id,
                             'required_distance': required, 'available_range': allowance})
            required = 0.0
            allowance = maximum
            segment_start = site_id
    failing = next((segment for segment in segments
                    if segment['required_distance'] > segment['available_range'] + RANGE_TOLERANCE), None)
    result = {'feasible': failing is None, 'segments': segments}
    if failing is not None:
        result['rejection'] = {'reason': 'range_infeasible', **failing}
    return result


def advance_vehicle_travel(scenario: Record, state: InventoryRunState, time: float) -> None:
    """Add actual travel through absolute time before the engine advances its clock.

    execute_inventory and horizon accounting call with scenario and state. Update
    tracked vehicle distance, position, accounted leg distance and remaining range
    once per newly covered part. Dynamic unlimited vehicles are tracked too;
    fixed unlimited vehicles use their existing separate accounting.
    Return None. Do not change the clock, record events or reset range here.
    Reject backward/excess travel with ValueError and unrepresentable calculations
    with PhysicalArithmeticError. Supplied state alone changes, including forecasts.
    """
    for definition in scenario['vehicles']:
        vehicle = state.vehicles[definition['id']]
        if definition['maximum_range'] is None and 'position' not in vehicle:
            continue
        leg = vehicle['active_leg']
        if leg is None:
            continue
        travelled = travel_prefix(time - leg['departure_time'], definition['speed'], leg['distance'], time=time, vehicle_id=definition['id'])
        increment = travelled - vehicle['leg_distance_accounted']
        if increment < -RANGE_TOLERANCE or (vehicle['remaining_range'] is not None and increment > vehicle['remaining_range'] + RANGE_TOLERANCE):
            raise ValueError(f"Vehicle {definition['id']} cannot travel beyond its remaining allowance")
        vehicle['distance'] = nonnegative_physical(vehicle['distance'] + (increment), 'vehicle.distance', time=state.time)
        if vehicle['remaining_range'] is not None:
            vehicle['remaining_range'] = max(0.0, vehicle['remaining_range'] - increment)
        vehicle['leg_distance_accounted'] = travelled
        fraction = travelled / leg['distance'] if leg['distance'] else 1.0
        vehicle['position'] = [finite_physical(origin + fraction * (target - origin), 'travel.position', time=time, vehicle_id=definition['id'])
                               for origin, target in zip(leg['origin_coordinates'], leg['target_coordinates'])]


def reset_vehicle_range(definitions: ScenarioDefinitions, state: InventoryRunState, vehicle_id: str) -> None:
    """Refill finite range after an actual plant or recycler arrival.

    Engine arrival service passes definitions, state and vehicle ID. Record
    range_reset with old/new allowance even for an empty visit or blocked unloading.
    Collection points and unlimited vehicles do nothing. Return None.
    The caller must already have recorded arrival and set location; this helper
    alone cannot establish that travel occurred. No files change.
    """
    definition = definitions.vehicles[vehicle_id]
    if definition['maximum_range'] is None:
        return
    vehicle = state.vehicles[vehicle_id]
    facilities = definitions.facility_ids
    if vehicle['location'] not in facilities:
        return
    before = vehicle['remaining_range']
    vehicle['remaining_range'] = definition['maximum_range']
    record_event(state, 'range_reset', vehicle_id=vehicle_id, facility_id=vehicle['location'],
                 range_before=before, range_after=vehicle['remaining_range'], load=list(vehicle['load']))
