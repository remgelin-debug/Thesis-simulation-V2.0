"""Describe fixed inputs, the current situation and saved physical snapshots.

engine creates and changes run state; runner reads it for output. Routing reads
ScenarioDefinitions and forecasting calls physical_snapshot for a separate copy.
A dictionary maps a name or ID to data, such as products['P1']. Record types
describe these dictionaries; they do not validate data at runtime.
The groups below cover definitions, progress, policy data, run state and calendars.
Plant views share supplied containers. A view alone is not a separate forecast.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from .numerics import nonnegative_physical


class PhysicalStateError(ValueError):
    """A required physical-state rule failed; the check also runs under python -O."""


# Keep required checks active under python -O

def require_physical_state(condition: bool, state: 'InventoryRunState',
                           rule: str, **context: object) -> None:
    """Raise PhysicalStateError when a required physical rule is broken.

    Transition functions and plant checks pass a condition, state clock, rule name
    and relevant IDs. Return None when valid. Read only; do not record an event or
    repair the state. Unlike an assert, this check remains active under python -O.
    """
    if not condition:
        details = ', '.join(f'{name}={value!r}' for name, value in context.items())
        raise PhysicalStateError(f'Physical state violation: time={state.time!r}, rule={rule}; {details}')


Record = dict[str, Any]
# Ordered heap fields are time, priority, identifier, event kind and optional leg/station token.
InventoryEvent = tuple[float, int, str, str, str]


# These describe existing JSON dictionaries; constructing them remains optional.
# total=False extensions express optional keys on Python 3.10 without dependencies.
OperationId = int | str
Disposition = Literal['remanufacture', 'recycle']
ProductStatus = Literal['unreleased', 'at_collection_point', 'onboard_av',
                        'in_plant_inventory', 'processing', 'remanufactured', 'recycled']
VehicleStatus = Literal['idle', 'travelling', 'waiting_to_unload']
CandidateScore = list[float | str]


# Resolved definitions and generated batches

class SiteDefinition(TypedDict):
    """Resolved site identity and [x, y] position, measured in distance units."""

    id: str
    coordinates: list[float]


class _WorkstationFields(TypedDict):
    """Workstation identity, operation it performs and fixed processing duration."""

    id: str
    operation_id: OperationId
    duration: float


class WorkstationDefinition(_WorkstationFields, total=False):
    """Input calendar settings or resolved [opening, closing] windows in time units."""

    windows: list[list[float]]
    window_generation: Record


class PlantDefinition(SiteDefinition):
    """Resolved remanufacturing facility; None capacity means unlimited inventory."""

    inventory_capacity: int | None
    workstations: list[WorkstationDefinition]


class VehicleDefinition(TypedDict):
    """Fixed fleet settings; speed is distance/time, None range is unlimited."""

    id: str
    initial_site: str
    speed: float
    payload_capacity: int
    maximum_range: float | None


class _ProductFields(TypedDict):
    """Physical product inputs required by execution, including ordered operations."""

    id: str
    origin: str
    release_time: float
    disposition: Disposition
    operations: list[OperationId]


class ProductDefinition(_ProductFields, total=False):
    """Resolved product; generated products also identify their batch and quality."""

    batch_id: str
    quality_class: str


class _BatchFields(TypedDict):
    """One sampled arrival and its consecutively assigned product identifiers."""

    id: str
    release_time: float
    batch_size: int
    product_ids: list[str]


class ReturnBatch(_BatchFields, total=False):
    """Single-site batch; network preparation adds origin and prefixes its IDs."""

    origin: str


# Physical progress and movement

class Operation(TypedDict):
    """Active/completed job; operation_index is zero-based, times are absolute."""

    product_id: str
    plant_id: str
    workstation_id: str
    operation_id: OperationId
    operation_index: int
    start_time: float
    end_time: float
    window: list[float]


class ProductMilestones(TypedDict, total=False):
    """Absolute times appear only once the corresponding physical event occurs."""

    release: float
    pickup: float
    admission: float
    recycling: float
    completion: float


class ProductState(TypedDict):
    """Track a product's physical place, assignment and next zero-based operation.

    inventory_wait adds only time spent waiting in plant inventory. Execution changes
    this dictionary; fixed ProductDefinition data describes the required work.
    """

    state: ProductStatus
    location: str | None
    next_operation: int
    assigned_vehicle: str | None
    planned_destination: str | None
    actual_destination: str | None
    milestones: ProductMilestones
    history: list[Operation]
    inventory_wait: float


class RouteStop(TypedDict):
    """Describe a dynamic visit's authorized pickups/deliveries; empty lists can mean a range-reset visit."""

    site_id: str
    pickups: list[str]
    deliveries: list[str]


class _LegFields(TypedDict):
    """Shared saved leg fields; load is the departure snapshot, distance is length."""

    vehicle_id: str
    target: str
    origin_coordinates: list[float]
    target_coordinates: list[float]
    departure_time: float
    arrival_time: float
    distance: float
    load: list[str]


class _FixedLegFields(_LegFields):
    """Fixed inventory legs identify their configured origin site."""

    origin: str


class FixedLeg(_FixedLegFields, total=False):
    """Fixed finite-range legs additionally record their departure allowance."""

    remaining_range_at_departure: float


class DynamicLeg(_LegFields):
    """Track a dynamic leg from an actual position; leg_id identifies which arrival is still valid."""

    leg_id: str
    remaining_range_at_departure: float | None


class _VehicleStateFields(TypedDict):
    """Describe vehicle fields present in every inventory execution state."""

    status: VehicleStatus
    location: str
    load: list[str]
    active_leg: FixedLeg | DynamicLeg | None
    distance: float
    waiting_since: float | None
    waiting_to_unload: float
    wait_logged: bool


class VehicleState(_VehicleStateFields, total=False):
    """Add tracking fields for finite range or dynamic travel, and remaining stops for dynamic routes."""

    position: list[float]
    remaining_range: float | None
    leg_distance_accounted: float
    remaining_stops: list[RouteStop]
    route_version: int
    leg_serial: int


class PlantContainers(TypedDict):
    """Keep one plant's inventory, held slots, waiting vehicles and active jobs.

    Inventory and queue values are absolute entry times. A held slot reserves return
    space for a product already processing; it is not another physical product.
    """

    inventory: dict[str, float]
    held_slots: list[str]
    waiting_vehicles: dict[str, float]
    workstations: dict[str, Operation | None]


# Copied policy inputs and forecast results

class PolicyRequest(TypedDict):
    """Describe released transport requests visible to both dynamic policies, without quality or operations."""

    product_id: str
    origin: str
    release_time: float
    disposition: Disposition
    state: ProductStatus
    assigned_vehicle: str | None
    planned_destination: str | None


class AwarePolicyRequest(PolicyRequest):
    """Add required operations only to the aware policy's request records."""

    operations: list[OperationId]


class PolicyVehicle(_VehicleStateFields):
    """Copy current vehicle facts, including actual position, range and remaining route, for routing."""

    id: str
    speed: float
    payload_capacity: int
    maximum_range: float | None
    position: list[float]
    remaining_range: float | None
    leg_distance_accounted: float
    remaining_stops: list[RouteStop]
    route_version: int
    leg_serial: int


class PolicySite(SiteDefinition):
    """Describe a site's position and kind for transport; omit plant calendars and product quality."""

    kind: Literal['plant', 'recycler', 'collection']


class ForecastResult(TypedDict):
    """Absolute predicted times and admission-to-first-start wait; None means not observed."""

    predicted_arrival: float | None
    predicted_admission: float | None
    predicted_first_start: float | None
    predicted_inventory_wait: float | None


class ProjectedDelivery(TypedDict):
    """Arrival evidence produced only on a private dynamic forecast state."""

    product_id: str
    vehicle_id: str
    destination: str
    arrival_time: float


class DynamicForecastResult(ForecastResult):
    """Add revised arrival times of committed deliveries to an insertion's processing predictions."""

    projected_deliveries: list[ProjectedDelivery]


class _DynamicCandidateFields(TypedDict):
    """Describe one distinct insertion alternative, whether feasible or rejected."""

    candidate_id: str
    product_id: str
    vehicle_id: str
    destination: str
    pickup_index: int
    delivery_index: int
    extra_visits: int
    old_remaining_stops: list[RouteStop]
    new_remaining_stops: list[RouteStop]
    position: list[float]
    remaining_range: float | None
    feasible: bool
    reason: str
    remaining_distance: float
    added_distance: float
    score: CandidateScore


class DynamicCandidate(_DynamicCandidateFields, total=False):
    """Add forecasts only to feasible aware candidates; rejected routes keep their distance scores."""

    predicted_arrival: float | None
    predicted_admission: float | None
    predicted_first_start: float | None
    predicted_inventory_wait: float | None
    projected_deliveries: list[ProjectedDelivery]


class _StaticCandidateFields(ForecastResult):
    """Describe one product forecast for a fixed destination and zero-based payload selection round."""

    selection_round: int
    product_id: str
    destination: str
    pickup_stop_index: int
    delivery_stop_index: int
    added_distance: float


class StaticCandidate(_StaticCandidateFields, total=False):
    """Add comparison scores and selected flags to a fixed-trip product forecast."""

    score: CandidateScore
    selected: bool


class RequestAudit(TypedDict):
    """Keep all candidates and the selected candidate ID, or None, for one considered request."""

    product_id: str
    candidates: list[DynamicCandidate]
    selected_candidate_id: str | None
    reason: str


# Existing runtime dataclasses and shared state helpers


# Record physical changes in sequence order

def record_event(state: 'InventoryRunState', event_type: str, **details: Any) -> None:
    """Append one event with the current time and next sequence number.

    Engine, plant and transport functions pass the event type and its details.
    Only state.events changes. Sequence numbers start at 1, including events that
    share the same time. runner.run_once later writes these records to events.jsonl.
    """
    state.events.append({"sequence": len(state.events) + 1, "time": state.time,
                         "event_type": event_type, **details})


@dataclass
class InventoryRunState:
    """Store inventory execution state and its authoritative plant-ID containers.

    Primary workstations/inventory/held_slots/waiting_vehicles refer to the same
    containers as that plant in plant_states. They are not additional stock.
    physical_snapshot chooses the unchanged saved layout for one or many plants.
    """

    time: float = 0.0
    products: dict[str, ProductState] = field(default_factory=dict)
    vehicles: dict[str, VehicleState] = field(default_factory=dict)
    workstations: dict[str, Operation | None] = field(default_factory=dict)
    inventory: dict[str, float] = field(default_factory=dict)
    held_slots: list[str] = field(default_factory=list)
    waiting_vehicles: dict[str, float] = field(default_factory=dict)
    plant_states: dict[str, PlantContainers] = field(default_factory=dict)
    events: list[Record] = field(default_factory=list)
    decisions: list[Record] = field(default_factory=list)


@dataclass(frozen=True)
class WorkstationCalendar:
    """Keep original and touching-window-merged bounds as fixed tuple data.

    One execution or aware decision owns these values. Candidate forecasts may
    reuse them without sharing changing physical state.
    """

    source_windows: tuple[tuple[float, float], ...]
    merged_windows: tuple[tuple[float, float], ...]


CalendarIndex = dict[tuple[str, str], WorkstationCalendar]


# Prepare fixed calendars for one execution or aware decision

def merge_adjacent_windows(windows: Iterable[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    """Return separate, immutable window pairs, joining only touching neighbors.

    prepare_calendars and plant.fitting_window pass ordered opening/closing pairs.
    For example, [0, 2] then [2, 5] becomes (0, 5). Overlaps remain separate.
    Read once without sorting, changing number types or using a tolerance.
    Empty input returns an empty tuple; malformed pairs fail when unpacked.
    Inputs, events and files stay unchanged.
    """
    merged: list[tuple[float, float]] = []
    for opening, closing in windows:
        if merged and opening == merged[-1][1]:
            merged[-1] = merged[-1][0], closing
        else:
            merged.append((opening, closing))
    return tuple(merged)


def prepare_calendars(scenario: Record, previous: CalendarIndex | None = None) -> CalendarIndex:
    """Prepare reusable windows under each (plant ID, workstation ID).

    ScenarioDefinitions.from_scenario and aware routing call this once per run or
    decision. Return a new lookup dictionary whose calendar values contain tuples.
    Reuse a supplied previous value only when original bounds and number types
    match. This keeps RF1 / WS1 separate from RF2 / WS1 and keeps event numbers exact.
    merge_adjacent_windows joins touching windows. No inputs or state change;
    there is no global cache. Separate forecasts may share these fixed values.
    """
    calendars: CalendarIndex = {}
    for plant in configured_plants(scenario):
        for station in plant['workstations']:
            key = plant['id'], station['id']
            windows = tuple(tuple(window) for window in station['windows'])
            existing = previous.get(key) if previous is not None else None
            if (existing is not None and existing.source_windows == windows
                    and all(type(left) is type(right) for old, new in zip(existing.source_windows, windows)
                            for left, right in zip(old, new))):
                calendars[key] = existing
                continue
            calendars[key] = WorkstationCalendar(windows, merge_adjacent_windows(windows))
    return calendars


@dataclass(frozen=True)
class ScenarioDefinitions:
    """ID lookups for fixed scenario records and reusable calendar values.

    The dictionaries refer to supplied definitions; treat them as read only. frozen
    prevents assigning dataclass fields, not changing the nested dictionaries.
    """

    products: dict[str, ProductDefinition]
    vehicles: dict[str, VehicleDefinition]
    plants: dict[str, PlantDefinition]
    sites: dict[str, SiteDefinition]
    facility_ids: frozenset[str]
    calendars: CalendarIndex = field(default_factory=dict)

    @classmethod
    def from_scenario(cls, scenario: Record, *, calendars: CalendarIndex | None = None) -> ScenarioDefinitions:
        """Build ID lookups for one execution and prepare its workstation calendars.

        The engine and static aware routing call this with resolved inputs. Return a
        ScenarioDefinitions whose product, vehicle, plant and site values refer to the
        supplied definition dictionaries; treat them as read only. Calendars may reuse
        compatible fixed values. The frozen dataclass does not freeze nested dictionaries.
        No physical state, events or files change.
        """
        plants = configured_plants(scenario)
        points = scenario['collection_points'] if scenario.get('format_version') == 3 else [scenario['collection_point']]
        return cls(
            products={product['id']: product for product in scenario['products']},
            vehicles={vehicle['id']: vehicle for vehicle in scenario['vehicles']},
            plants={plant['id']: plant for plant in plants},
            facility_ids=frozenset(site['id'] for site in (*plants, *scenario['recycling_facilities'])),
            sites={site['id']: site for site in (*points, *plants, *scenario['recycling_facilities'])},
            calendars=prepare_calendars(scenario, calendars),
        )


# Copy physical state without copying run logs

def physical_snapshot(state: InventoryRunState, *, released_only: bool) -> Record:
    """Return a separate copy of physical state in the saved field order.

    Forecast context builders set released_only=True to leave out unreleased
    products. runner uses False for final-state export. Copy included jobs, routes,
    queues and product histories without copying event/decision logs.
    Fields that refer to the same container still do so inside the copy. A candidate
    copies this snapshot again and restores its primary-plant references.
    Single-plant output keeps the older top-level layout; network output also has
    plant_states. Live state stays unchanged; no files or events are written.
    """
    products = state.products
    if released_only:
        products = {identifier: part for identifier, part in products.items() if part['state'] != 'unreleased'}
    physical: Record = {
        'time': state.time,
        'products': products,
        'vehicles': state.vehicles,
        'workstations': state.workstations,
        'inventory': state.inventory,
        'held_slots': state.held_slots,
        'waiting_vehicles': state.waiting_vehicles,
    }
    if len(state.plant_states) > 1:
        physical['plant_states'] = state.plant_states
    return copy.deepcopy(physical)


# Create state and share the correct plant containers

def build_inventory_state(scenario: Record, definitions: ScenarioDefinitions) -> InventoryRunState:
    """Create empty inventory execution state from validated definitions.

    execute_inventory uses this directly for fixed trips; build_dynamic_state adds
    routes and tracking for dynamic runs. Return new product/vehicle/plant containers:
    products unreleased, vehicles idle and empty, no jobs, and full finite range.
    The primary fields refer to the same containers as the primary plant in
    plant_states. Do not count them twice. Inputs stay unchanged; emit no events.
    """
    state = InventoryRunState()
    for product in scenario['products']:
        state.products[product['id']] = {'state': 'unreleased', 'location': None, 'next_operation': 0,
            'assigned_vehicle': None, 'planned_destination': None, 'actual_destination': None,
            'milestones': {}, 'history': [], 'inventory_wait': 0.0}
    for vehicle in scenario['vehicles']:
        state.vehicles[vehicle['id']] = {'status': 'idle', 'location': vehicle['initial_site'],
            'load': [], 'active_leg': None, 'distance': 0.0, 'waiting_since': None,
            'waiting_to_unload': 0.0, 'wait_logged': False}
    for definition in scenario['vehicles']:
        if definition['maximum_range'] is not None:
            site = definitions.sites[definition['initial_site']]
            state.vehicles[definition['id']].update(remaining_range=definition['maximum_range'],
                position=list(site['coordinates']), leg_distance_accounted=0.0)
    state.workstations = {s['id']: None for s in scenario['plant']['workstations']}
    initialize_plant_states(scenario, state)
    return state


def inventory_occupancy(state: 'InventoryRunState') -> int:
    """Return waiting product count plus slots held for intermediate operations.

    Local plant admission and capacity checks use this. A held slot uses capacity
    but is not another physical product. The supplied state is read only.
    """
    return len(state.inventory) + len(state.held_slots)


def configured_plants(scenario: Record) -> list[PlantDefinition]:
    """Return the plants list, or a one-item list for the older plant field.

    Execution, validation and forecasting use this common access rule. Plant
    records are shared with the scenario and must not be changed by readers.
    """
    return scenario['plants'] if 'plants' in scenario else [scenario['plant']]


def initialize_plant_states(scenario: Record, state: InventoryRunState) -> None:
    """Set up plant-ID containers and connect the primary fields to the same data.

    State builders and forecast reconstruction call this on live state or a
    separate forecast copy. Create missing containers; then bind inventory, holds,
    workstations and waiting vehicles to the primary plant's containers.
    Return None and emit no events. Existing containers must fit the scenario.
    These shared references are not extra inventory or extra plants.
    """
    plants = configured_plants(scenario)
    if not state.plant_states:
        if len(plants) == 1:
            # A single-plant saved snapshot carries its containers in the alias fields.
            state.plant_states = {plants[0]['id']: {
                'inventory': state.inventory, 'held_slots': state.held_slots,
                'waiting_vehicles': state.waiting_vehicles, 'workstations': state.workstations,
            }}
        else:
            state.plant_states = {plant['id']: {'inventory': {}, 'held_slots': [], 'waiting_vehicles': {},
                'workstations': {station['id']: None for station in plant['workstations']}} for plant in plants}
    if state.plant_states:
        primary = state.plant_states[scenario['plant']['id']]
        state.inventory, state.held_slots = primary['inventory'], primary['held_slots']
        state.workstations, state.waiting_vehicles = primary['workstations'], primary['waiting_vehicles']


def local_plant_view(scenario: Record, state: InventoryRunState, plant: PlantDefinition) -> tuple[Record, InventoryRunState]:
    """Give local plant rules access to one plant in the supplied state.

    Engine, settlement and forecasting callers supply a plant definition. Return
    (local scenario, state view); a single-plant call uses the original state.
    Products, vehicles, plant containers and logs refer to the same data as the
    supplied state. Only the numeric clock is copied when a network view is made.
    This does not isolate a forecast. Copy physical state first for that purpose,
    and make a new view after advancing time. No events or files are written.
    """
    if len(configured_plants(scenario)) == 1:
        return {**scenario, 'plant': plant}, state
    containers = state.plant_states[plant['id']]
    view = InventoryRunState(time=state.time, products=state.products, vehicles=state.vehicles,
        inventory=containers['inventory'], held_slots=containers['held_slots'], workstations=containers['workstations'],
        waiting_vehicles=containers['waiting_vehicles'], events=state.events, decisions=state.decisions)
    return {**scenario, 'plant': plant}, view


# Read final counts and totals

def inventory_summary(state: InventoryRunState) -> Record:
    """Return product counts and actual distance/wait totals at the horizon.

    runner.run_once calls after execution has accounted for unfinished waits and
    partial legs. Read state only; no events or files change. Sum each product and
    vehicle once. Raise PhysicalArithmeticError if accumulated totals overflow.
    """
    generated = sum(p['state'] != 'unreleased' for p in state.products.values())
    completed = sum(p['state'] == 'remanufactured' for p in state.products.values())
    recycled = sum(p['state'] == 'recycled' for p in state.products.values())
    return {'generated': generated, 'remanufactured': completed, 'recycled': recycled, 'unfinished': generated - completed - recycled,
        'vehicle_distance': nonnegative_physical(sum(v['distance'] for v in state.vehicles.values()), 'vehicle_distance', time=state.time),
        'plant_inventory_wait': nonnegative_physical(sum(p['inventory_wait'] for p in state.products.values()), 'plant_inventory_wait', time=state.time),
        'waiting_to_unload': nonnegative_physical(sum(v['waiting_to_unload'] for v in state.vehicles.values()), 'waiting_to_unload', time=state.time)}
