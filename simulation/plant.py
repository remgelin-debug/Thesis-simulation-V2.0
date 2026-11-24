"""Apply plant inventory and workstation rules during execution and forecasts.

engine calls operation completions and settle_plant_network at event times.
Settlement retries transport admission and starts eligible work until nothing
more can happen now. Processing must fit a whole availability window.
Intermediate work holds a return slot; final work frees it at start.
Checks ensure no product is lost or counted twice. Functions change the supplied
state, which may be live state or a separate forecast copy; they write no files.
"""
from __future__ import annotations

from .numerics import processing_completion, nonnegative_physical
from .state import (
    require_physical_state,
    ScenarioDefinitions,
    WorkstationDefinition,
    WorkstationCalendar,
    merge_adjacent_windows,
    PlantContainers,
    Record,
    InventoryRunState,
    InventoryEvent,
    record_event,
    inventory_occupancy,
    configured_plants,
    local_plant_view,
)
from .transport import retry_inventory_admissions


# Find fitting windows and start or finish inventory-mode work

def fitting_window(station: WorkstationDefinition, time: float, *, calendar: WorkstationCalendar | None = None) -> list[float] | None:
    """Return a fresh window list if the whole operation can start at time now.

    start_inventory_operation supplies a plant-qualified prepared calendar.
    Direct callers may omit it and merge touching windows on demand. Read only;
    choose the first fitting window and return None when none fits. Completion
    exactly at closing is allowed. The result cannot change cached tuples.
    Impossible completion arithmetic still raises PhysicalArithmeticError.
    """
    if calendar is None:
        windows = merge_adjacent_windows(station['windows'])
    else:
        windows = calendar.merged_windows
    return next((list(window) for window in windows if window[0] <= time < window[1]
                 and processing_completion(time, station['duration'], workstation_id=station['id']) <= window[1]), None)


def start_inventory_operation(scenario: Record, definitions: ScenarioDefinitions, state: 'InventoryRunState', station: WorkstationDefinition) -> float | None:
    """Start the oldest eligible waiting product at an idle workstation.

    settle_inventory and static forecasting pass a local plant state and station.
    Return the completion time, or None when busy, without eligible work or without
    a fitting window. Order eligible products by inventory-entry time, then ID.
    Remove waiting inventory, add its wait and record operation_start. Intermediate
    work keeps a held slot; final work frees that capacity. Record the corresponding
    finite-capacity hold/release event. Check positive completion time before removing
    stock. Changes affect only the supplied live state or separate forecast copy.
    """
    if state.workstations[station['id']] is not None:
        return None
    products = definitions.products
    eligible = [product_id for product_id in state.inventory
                if products[product_id]['operations'][state.products[product_id]['next_operation']] == station['operation_id']]
    if not eligible:
        return None
    window = fitting_window(station, state.time, calendar=definitions.calendars.get((scenario['plant']['id'], station['id'])))
    if window is None:
        return None
    product_id = min(eligible, key=lambda candidate: (state.inventory[candidate], candidate))
    product = state.products[product_id]
    require_physical_state(product['state'] == 'in_plant_inventory' and product['location'] == scenario['plant']['id'],
                           state, 'processing.inventory_membership', product_id=product_id,
                           plant_id=scenario['plant']['id'], workstation_id=station['id'], observed=product['state'])
    end = processing_completion(state.time, station['duration'], product_id=product_id,
                                workstation_id=station['id'], plant_id=scenario['plant']['id'])
    product['inventory_wait'] = nonnegative_physical(product['inventory_wait'] + (state.time - state.inventory.pop(product_id)), 'product.inventory_wait', time=state.time)
    operation = {'product_id': product_id, 'plant_id': scenario['plant']['id'], 'workstation_id': station['id'],
        'operation_id': station['operation_id'], 'operation_index': product['next_operation'],
        'start_time': state.time, 'end_time': end, 'window': window}
    product.update(state='processing', location=station['id'])
    state.workstations[station['id']] = operation
    record_event(state, 'operation_start', **operation)
    if scenario['plant']['inventory_capacity'] is not None:
        if product['next_operation'] + 1 < len(products[product_id]['operations']):
            state.held_slots.append(product_id)
            record_event(state, 'inventory_hold', product_id=product_id, plant_id=scenario['plant']['id'],
                         waiting_count=len(state.inventory), held_count=len(state.held_slots))
        else:
            record_event(state, 'inventory_slot_release', product_id=product_id, plant_id=scenario['plant']['id'],
                         waiting_count=len(state.inventory), held_count=len(state.held_slots))
    return operation['end_time']


def finish_inventory_operation(scenario: Record, definitions: ScenarioDefinitions, state: 'InventoryRunState', workstation_id: str) -> None:
    """Finish one inventory-mode job at its exact completion time.

    The engine and static forecast pass local plant state and workstation ID.
    Return None. Save history, advance the product's operation index and clear the
    job. Record operation_complete. Then record remanufactured, or replace its held
    slot with waiting inventory and record inventory_return. A missing/mismatched
    job raises PhysicalStateError. Only the supplied state changes.
    """
    operation = state.workstations[workstation_id]
    require_physical_state(operation is not None and operation['end_time'] == state.time, state, 'processing.completion_time',
                           plant_id=scenario['plant']['id'], workstation_id=workstation_id, observed=operation)
    product_id = operation['product_id']
    product = state.products[product_id]
    require_physical_state(product['state'] == 'processing' and product['location'] == workstation_id
                           and product['next_operation'] == operation['operation_index']
                           and operation['plant_id'] == scenario['plant']['id'], state, 'processing.active_product',
                           product_id=product_id, plant_id=scenario['plant']['id'], workstation_id=workstation_id,
                           observed=product['state'])
    product['history'].append(dict(operation))
    product['next_operation'] += 1
    state.workstations[workstation_id] = None
    record_event(state, 'operation_complete', **operation)
    definition = definitions.products[product_id]
    if product['next_operation'] == len(definition['operations']):
        product.update(state='remanufactured', location=None)
        product['milestones']['completion'] = state.time
        record_event(state, 'remanufactured', product_id=product_id)
    else:
        replaced = product_id in state.held_slots
        if replaced:
            state.held_slots.remove(product_id)
        state.inventory[product_id] = state.time
        product.update(state='in_plant_inventory', location=scenario['plant']['id'])
        record_event(state, 'inventory_return', product_id=product_id, plant_id=scenario['plant']['id'],
            next_operation_index=product['next_operation'], held_slot_replaced=replaced,
            waiting_count=len(state.inventory), held_count=len(state.held_slots))


# Settle admissions and starts without advancing time

def settle_inventory(scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState) -> list[tuple[float, int, str, str]]:
    """Retry unloading and start work until nothing else can happen at this time.

    settle_plant_network passes local plant containers; forecasts use the same rule.
    Return future completion records (time, priority, station ID, event kind).
    retry_inventory_admissions runs before each station start. After a final start
    frees a slot, retry waiting vehicles before starting another station. This keeps
    arrival-time/vehicle-ID admission priority. Helpers change supplied state and
    record admission/processing events; the clock and files do not change here.
    """
    completions = []
    while True:
        admitted = retry_inventory_admissions(scenario, definitions, state)
        started = False
        for station in sorted(scenario['plant']['workstations'], key=lambda station: station['id']):
            end = start_inventory_operation(scenario, definitions, state, station)
            if end is not None:
                completions.append((end, 0, station['id'], 'complete'))
                started = True
                break  # Newly freed capacity goes to waiting AVs before another start.
        if not admitted and not started:
            return completions


def settle_plant_network(scenario: Record, definitions: ScenarioDefinitions, state: InventoryRunState) -> list[InventoryEvent]:
    """Settle each plant in plant-ID order and return its future completions.

    execute_inventory calls this for live runs and private dynamic forecasts.
    local_plant_view shares the supplied state's plant containers with settle_inventory.
    Return five-field event records, including plant/workstation identity for a
    network. Single-plant records keep their existing station-ID layout.
    Admission and operation helpers change state and append events; no files change.
    """
    completions = []
    for plant in sorted(configured_plants(scenario), key=lambda plant: plant['id']):
        local, view = local_plant_view(scenario, state, plant)
        for time, priority, station_id, kind in settle_inventory(local, definitions, view):
            completions.append((time, priority, plant['id'], kind, station_id) if len(configured_plants(scenario)) > 1 else
                               (time, priority, station_id, kind, ''))
    return completions


# Check capacity and that no product is lost or counted twice

def _check_product_membership_and_payload(
    scenario: Record, state: InventoryRunState, plants: tuple[PlantContainers, ...],
) -> None:
    """Check that each product is counted once, then check vehicle loads.

    check_inventory_state and check_plant_network call after capacity checks.
    Read the repeatable tuple of plant containers and supplied state without copying
    or changes. Count duplicate load entries, but do not count held slots as extra
    products or count primary fields as another plant. Preserve product order and
    vehicle-definition order so the first error stays useful and repeatable.
    Return None, or raise PhysicalStateError with the failing rule and IDs.
    """
    for product_id, product in state.products.items():
        physical = int(product['state'] in ('at_collection_point', 'remanufactured', 'recycled'))
        physical += sum(vehicle['load'].count(product_id) for vehicle in state.vehicles.values())
        physical += sum(product_id in plant['inventory'] for plant in plants)
        physical += sum(operation is not None and operation['product_id'] == product_id
                        for plant in plants for operation in plant['workstations'].values())
        require_physical_state(physical == int(product['state'] != 'unreleased'), state, 'product.conservation',
                               product_id=product_id, expected=int(product['state'] != 'unreleased'), observed=physical)
    for definition in scenario['vehicles']:
        require_physical_state(len(state.vehicles[definition['id']]['load']) <= definition['payload_capacity'], state, 'vehicle.capacity',
                               vehicle_id=definition['id'], expected=definition['payload_capacity'], observed=len(state.vehicles[definition['id']]['load']))


def check_inventory_state(scenario: Record, state: InventoryRunState) -> None:
    """Check local capacity, product membership and payload without changing state.

    check_plant_network uses this when plant_states is absent, including manually
    constructed local states. Normal single-plant runs also have authoritative
    plant-ID containers and use the network check. Capacity is checked first.
    Then require one physical location per released product and valid payloads.
    Holds use slots but are not additional products. Return None or raise
    PhysicalStateError; no events or files change.
    """
    capacity = scenario['plant']['inventory_capacity']
    require_physical_state(capacity is None or inventory_occupancy(state) <= capacity, state, 'inventory.capacity',
                           plant_id=scenario['plant']['id'], expected=capacity, observed=inventory_occupancy(state))
    local_containers: PlantContainers = {
        'inventory': state.inventory, 'held_slots': state.held_slots,
        'waiting_vehicles': state.waiting_vehicles, 'workstations': state.workstations,
    }
    _check_product_membership_and_payload(scenario, state, (local_containers,))


def check_plant_network(scenario: Record, state: InventoryRunState) -> None:
    """Check all plant capacities, then products and payloads once across the network.

    execute_inventory calls after settling a timestamp, including on forecast copies.
    Use check_inventory_state as the absent-container fallback. Primary fields refer
    to the same plant containers, so they are not counted again. Held slots consume
    capacity without adding physical products. Read only; return None or raise
    PhysicalStateError with the first failing rule. No events or files change.
    """
    if not state.plant_states:
        check_inventory_state(scenario, state)
        return
    for plant in configured_plants(scenario):
        containers = state.plant_states[plant['id']]
        require_physical_state(plant['inventory_capacity'] is None or len(containers['inventory']) + len(containers['held_slots']) <= plant['inventory_capacity'], state, 'inventory.capacity',
                               plant_id=plant['id'], expected=plant['inventory_capacity'], observed=len(containers['inventory']) + len(containers['held_slots']))
    _check_product_membership_and_payload(scenario, state, tuple(state.plant_states.values()))
