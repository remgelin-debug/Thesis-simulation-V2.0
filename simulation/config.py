"""Read scenario files and check their declarations before execution.

runner.run_once calls load_scenario for saved inputs. generator calls the
generation validators before drawing random values. Shared checks cover sites,
vehicles, products and workstation windows; format-specific checks keep older
input rules. as_dynamic_scenario makes a separate execution copy for routing.
Nothing here generates returns, advances the clock or writes files.
"""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Collection
from pathlib import Path

from .state import OperationId, Record, configured_plants


# Check shared physical declarations

def finite_number(value: object) -> bool:
    """Return whether a format-two/three physical value is a finite number.

    Shared validators use this check. True and False are not accepted as numbers.
    """
    return type(value) in (int, float) and math.isfinite(value)


def validate_coordinates(site: Record, *, collection: bool = False) -> None:
    """Check that a resolved site has two finite coordinates; change nothing.

    Plant/network validators call this. collection selects the collection-point
    error wording. Invalid coordinates raise ValueError; return None on success.
    """
    coordinates = site['coordinates']
    if len(coordinates) != 2 or not all(finite_number(axis) for axis in coordinates):
        if collection:
            raise ValueError('Collection coordinates must be finite two-dimensional values')
        if len(coordinates) != 2:
            raise ValueError('Coordinates must have two components')
        raise ValueError('Coordinates must be finite')


def validate_windows(windows: list[list[float]]) -> None:
    """Check ordered, nonoverlapping workstation windows; touching is allowed.

    validate_plant passes [opening, closing] pairs in simulation-time units.
    Return None without changing them, or raise ValueError for invalid bounds.
    plant.fitting_window separately checks that a whole operation fits. For example,
    work may finish exactly at closing, but it cannot start at that closing time.
    """
    previous_end = -math.inf
    for opening, closing in windows:
        if (not 0 <= opening < closing or opening < previous_end
                or not finite_number(opening) or not finite_number(closing)):
            raise ValueError('Calendar windows must be ordered and not overlap')
        previous_end = closing


def validate_horizon(horizon: float) -> None:
    """Require a finite, nonnegative run end time; return None or raise ValueError.

    Fixed/network validators call this without changing the supplied value.
    """
    if not finite_number(horizon) or horizon < 0:
        raise ValueError('Times, coordinates, durations and speeds must be finite')


def validate_plant(plant: Record, *, horizon: float, generation_config: Record | None = None) -> set[OperationId]:
    """Check a plant's capacity, workstations, durations and availability.

    Fixed/network validators pass the declaration and run horizon. When generating
    inputs, generation_config allows schedule settings instead of saved windows.
    Return the supported operation IDs without changing declarations. Duplicate IDs,
    invalid capacity/durations or bad windows/settings raise ValueError.
    """
    capacity = plant['inventory_capacity']
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise ValueError('Inventory capacity must be a positive integer or null')
    stations = plant['workstations']
    station_ids = set()
    operations = set()
    for station in stations:
        if generation_config is None or station_schedule_settings(
            generation_config.get('schedule_generation', {}), station, horizon
        ) is None:
            validate_windows(station['windows'])
        if station['id'] in station_ids or station['operation_id'] in operations:
            raise ValueError('Workstation IDs and operation IDs must be unique within the plant')
        station_ids.add(station['id'])
        operations.add(station['operation_id'])
        if (not isinstance(station['id'], str) or not station['id'] or station['duration'] <= 0
                or not finite_number(station['duration'])):
            raise ValueError('Workstation IDs must be strings and durations positive')
    return operations


def validate_vehicles(vehicles: list[Record], facility_ids: set[str]) -> None:
    """Check vehicle speed, payload, range and allowed starting facilities.

    Fixed/network validators supply facility_ids. Return None and leave the fleet
    unchanged, or raise ValueError. A null maximum_range means unlimited travel.
    """
    for vehicle in vehicles:
        if vehicle['initial_site'] not in facility_ids:
            raise ValueError('Vehicles must start at a configured facility')
        maximum = vehicle['maximum_range']
        if maximum is not None and (not finite_number(maximum) or maximum <= 0):
            raise ValueError('Maximum range must be positive and finite, or null')
        if (vehicle['speed'] <= 0 or not finite_number(vehicle['speed'])
                or type(vehicle['payload_capacity']) is not int or vehicle['payload_capacity'] <= 0):
            raise ValueError('Speed and integer payload capacity must be positive')


def validate_products(products: list[Record], origins: Collection[str], operations: set[OperationId],
                      has_recycler: bool, horizon: float) -> None:
    """Check each product's release, collection point and processing requirements.

    Fixed/network validators supply allowed origins and the operations supported
    at every eligible plant. Recycling needs a recycler and no operations.
    Return None without changes; invalid declarations raise ValueError.
    """
    for product in products:
        if not 0 <= product['release_time'] <= horizon or not finite_number(product['release_time']):
            raise ValueError('Product releases must fall within the horizon')
        if product['origin'] not in origins:
            raise ValueError('Product origin must reference a configured collection point')
        if product['disposition'] == 'remanufacture':
            if not product['operations'] or any(op not in operations for op in product['operations']):
                raise ValueError('Every operation must have a compatible workstation')
        elif product['disposition'] != 'recycle' or product['operations'] or not has_recycler:
            raise ValueError('Recycling products require a recycler and an empty operation list')


# Check complete scenarios and keep format-specific rules

def validate_fixed_declarations(scenario: Record, products: list[Record]) -> None:
    """Check format-two declarations and at most one fixed trip per vehicle.

    validate_inventory_scenario supplies resolved products; generation validation
    supplies an empty list before products exist. Check sites, plant, fleet,
    products, trips and horizon without copying or changing them. Return None;
    invalid declarations raise ValueError. Missing fields still raise lookup errors.
    """
    if scenario['format_version'] != 2 or scenario['policy'] not in ('static_quality_unaware', 'static_quality_aware'):
        raise ValueError('Inventory runs require format 2 and a supported static policy')
    plant, point = scenario['plant'], scenario['collection_point']
    recyclers = scenario['recycling_facilities']
    for site in (plant, point, *recyclers):
        validate_coordinates(site)
    operations = validate_plant(plant, horizon=scenario['horizon'])
    vehicles = {vehicle['id'] for vehicle in scenario['vehicles']}
    facilities = {plant['id'], *(site['id'] for site in recyclers)}
    validate_vehicles(scenario['vehicles'], facilities)
    validate_products(products, (point['id'],), operations, bool(recyclers), scenario['horizon'])
    identifiers = [item['id'] for item in (plant, point, *recyclers, *scenario['vehicles'], *products)]
    if not all(isinstance(i, str) and i for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError('Entity IDs must be unique nonempty strings')
    used = set()
    for trip in scenario['trips']:
        if trip['vehicle_id'] not in vehicles or trip['vehicle_id'] in used:
            raise ValueError('Allow at most one fixed trip per configured vehicle')
        used.add(trip['vehicle_id'])
        stops = trip['stops']
        if (not stops or stops[-1] not in facilities or stops.count(point['id']) > 1
                or any(stop not in facilities | {point['id']} for stop in stops)
                or not 0 <= trip['departure_time'] <= scenario['horizon'] or not finite_number(trip['departure_time'])):
            raise ValueError('Fixed trips may pick up once, may visit facilities, and must end at a facility')
    validate_horizon(scenario['horizon'])


def validate_inventory_scenario(scenario: Record) -> None:
    """Check a resolved format-two scenario and require seed_provenance.

    validate_scenario and generator.prepare_scenario call this. It reads the
    scenario only and returns None; invalid or absent declarations raise.
    """
    validate_fixed_declarations(scenario, scenario['products'])
    scenario['seed_provenance']  # Required in resolved input, optional before generation.


def validate_network_declarations(
    scenario: Record, products: list[Record], *, generating: bool = False,
) -> set[OperationId]:
    """Check network declarations using their real collection points and starts.

    Resolved and generation validators call this with products, or an empty list
    before sampling. generating allows coordinate/calendar settings in place of
    resolved values. Return operation IDs common to all plants. Read only; invalid
    scope, references or physical declarations raise ValueError. No sampling occurs.
    """
    if scenario['format_version'] != 3 or scenario['policy'] not in ('dynamic_quality_unaware', 'dynamic_quality_aware') or scenario.get('trips', []):
        raise ValueError('Dynamic format 3 requires a dynamic policy and no fixed trips')
    plants = configured_plants(scenario)
    if not plants:
        raise ValueError('At least one remanufacturing plant is required')
    points, recyclers, vehicles = scenario['collection_points'], scenario['recycling_facilities'], scenario['vehicles']
    identifiers = [item['id'] for group in (plants, points, recyclers, vehicles, products) for item in group]
    if len(set(identifiers)) != len(identifiers) or not all(isinstance(i, str) and i for i in identifiers):
        raise ValueError('Dynamic entity IDs must be unique nonempty strings')
    if not (generating and 'coordinate_generation' in scenario):
        for point in points:
            validate_coordinates(point, collection=True)
        for site in (*plants, *recyclers):
            validate_coordinates(site)
    validate_vehicles(vehicles, {site['id'] for site in (*plants, *recyclers)})
    # Both policies can send a part to any plant, so its route must fit every plant.
    plant_operations = [validate_plant(plant, horizon=scenario['horizon'],
                                       generation_config=scenario if generating else None) for plant in plants]
    operations = set.intersection(*plant_operations)
    validate_products(products, {point['id'] for point in points}, operations, bool(recyclers), scenario['horizon'])
    validate_horizon(scenario['horizon'])
    return operations


def validate_dynamic_scenario(scenario: Record) -> None:
    """Check resolved network declarations and require seed_provenance.

    validate_scenario and network preparation call this. Return None without
    changes; invalid declarations or missing evidence raise.
    """
    validate_network_declarations(scenario, scenario['products'])
    scenario['seed_provenance']


def validate_scenario(scenario: Record) -> None:
    """Check resolved format-two inventory or format-three network inputs.

    load_scenario calls before runner creates an output folder. Keep each format's
    existing declaration checks. Format one is retired and is never converted.
    Return None without changes; unsupported/missing format declarations raise
    ValueError. Retained malformed fields keep their existing validation errors.
    """
    if not isinstance(scenario, dict):
        raise ValueError('Scenario must be a JSON object with format_version 2 or 3')
    version = scenario.get('format_version')
    if version == 3:
        validate_dynamic_scenario(scenario)
    elif version == 2:
        validate_inventory_scenario(scenario)
    elif version == 1:
        raise ValueError('Scenario format 1 is retired; supported formats are 2 and 3')
    else:
        raise ValueError(f'Unsupported scenario format {version!r}; supported formats are 2 and 3')


# Check generation settings without drawing random values

def validate_coordinate_generation(settings: Record) -> None:
    """Check x/y sampling bounds without drawing coordinates.

    Network validation and generate_site_coordinates call this. Equal bounds allow
    a fixed axis value. Return None or raise ValueError for invalid or infinite spans.
    """
    if not isinstance(settings, dict):
        raise ValueError('coordinate_generation must define x_range and y_range bounds')
    for name in ('x_range', 'y_range'):
        bounds = settings.get(name)
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds) or
                bounds[0] > bounds[1] or not math.isfinite(bounds[1] - bounds[0])):
            raise ValueError(f'Coordinate {name} must contain ordered finite bounds with a finite span')


def validate_schedule_generation(settings: Record, horizon: float) -> None:
    """Check calendar sampling bounds without drawing windows.

    station_schedule_settings passes the settings and horizon. Open/closed
    durations must be positive; initial delay may be zero. Return None without
    changes, or raise ValueError for missing settings or invalid intervals.
    """
    if not all(key in settings for key in ('start_time', 'end_time', 'initial_delay', 'open_duration', 'closed_duration')):
        raise ValueError('Schedule settings require start_time, end_time, initial_delay, open_duration and closed_duration')
    beginning, end = settings['start_time'], settings['end_time']
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (beginning, end)) or not 0 <= beginning <= end <= horizon:
        raise ValueError('Schedule generation requires 0 <= start_time <= end_time <= horizon')
    for name in ('initial_delay', 'open_duration', 'closed_duration'):
        interval = settings[name]
        if not isinstance(interval, list) or len(interval) != 2 or any(type(value) not in (int, float) or not math.isfinite(value) for value in interval):
            raise ValueError(f'Schedule {name} must contain two finite bounds')
        lower, upper = interval
        if not 0 <= lower <= upper or (name != 'initial_delay' and lower <= 0):
            raise ValueError(f'Schedule {name} bounds must be ordered and durations positive')


def station_schedule_settings(defaults: Record, station: Record, horizon: float) -> Record | None:
    """Choose fixed windows or validated generation settings for one workstation.

    validate_plant and prepare_network_scenario call this with defaults, the station
    and horizon. Return None for explicit windows. Otherwise return a new settings
    dictionary combining defaults and station overrides. Inputs stay unchanged.
    Windows combined with window_generation, or invalid bounds, raise ValueError.
    """
    if 'windows' in station:
        if 'window_generation' in station:
            raise ValueError('Choose fixed windows or window_generation for a workstation, not both')
        return None
    settings = {**defaults, **station.get('window_generation', {})}
    validate_schedule_generation(settings, horizon)
    return settings


def validate_weights(weights: list[float], require_positive: bool = True) -> None:
    """Check finite, nonnegative sampling weights and their sum.

    Arrival/quality validators call this. require_positive also requires a positive
    total. Return None without changes; invalid weights raise ValueError.
    """
    if not all(type(weight) in (int, float) and math.isfinite(weight) and weight >= 0 for weight in weights):
        raise ValueError('Weights must be finite and nonnegative')
    total = sum(weights)
    if not math.isfinite(total) or (require_positive and total <= 0):
        raise ValueError('Sampling requires positive finite effective weight')


def validate_arrival_distribution(generation: Record, horizon: float) -> None:
    """Check an arrival interval and its fixed or weighted batch sizes.

    validate_point_generation calls before sampling. Read rate, time bounds and
    batch settings; return None or raise ValueError. No random stream advances.
    """
    rate, start, end = (generation[key] for key in ('arrival_rate', 'start_time', 'end_time'))
    if not all(type(value) in (int, float) and math.isfinite(value) for value in (rate, start, end)):
        raise ValueError('Arrival settings must be finite numbers')
    if rate < 0 or not 0 <= start <= end <= horizon:
        raise ValueError('Require nonnegative rate and 0 <= start <= end <= horizon')
    batch = generation['batch_size']
    if type(batch) is int:
        if batch <= 0:
            raise ValueError('Fixed batch size must be positive')
    else:
        if not isinstance(batch, dict) or not batch['values'] or len(batch['values']) != len(batch['weights']):
            raise ValueError('Batch values and weights must be nonempty equal-length lists')
        if any(type(size) is not int or size <= 0 for size in batch['values']):
            raise ValueError('Batch sizes must be positive integers')
        validate_weights(batch['weights'])


def validate_quality_distribution(qualities: list[Record], overrides: dict[str, float], operations: set[OperationId],
                                  has_recycler: bool, can_generate: bool) -> None:
    """Check quality weights, operation sequences and point-specific overrides.

    validate_point_generation supplies operations common to the plants and whether
    recycling is available. can_generate determines whether eligible weights must
    have a positive sum. Return None without changes or sampling; invalid classes,
    overrides or processing routes raise ValueError.
    """
    quality_ids = [quality['id'] for quality in qualities]
    if len(set(quality_ids)) != len(quality_ids) or not all(isinstance(identifier, str) and identifier for identifier in quality_ids):
        raise ValueError('Quality class IDs must be unique nonempty strings')
    if set(overrides) - set(quality_ids):
        raise ValueError('Collection-point quality weights reference unknown classes')
    for quality in qualities:
        validate_weights([quality['weight']], require_positive=False)
        weight = overrides.get(quality['id'], quality['weight'])
        validate_weights([weight], require_positive=False)
        if quality['disposition'] == 'recycle':
            if quality['operations']:
                raise ValueError('Recycling classes must have no remanufacturing operations')
        elif quality['disposition'] == 'remanufacture':
            if not quality['operations'] or (weight > 0 and any(operation not in operations for operation in quality['operations'])):
                raise ValueError('Positive-weight remanufacturing routes must be supported by the plant')
        else:
            raise ValueError('Unknown quality disposition')
    effective = [overrides.get(q['id'], q['weight']) for q in qualities
                 if has_recycler or q['disposition'] == 'remanufacture']
    validate_weights(effective, require_positive=can_generate)


def validate_point_generation(
    generation: Record, point: Record, configuration: Record, operations: set[OperationId],
) -> None:
    """Check one collection point's arrivals and effective quality weights.

    Single-site/network generation validators call this. Use the point overrides
    and plant operations; return None, or propagate validation errors. Read only.
    """
    validate_arrival_distribution(generation, configuration['horizon'])
    can_generate = generation['arrival_rate'] > 0 and generation['end_time'] > generation['start_time']
    validate_quality_distribution(configuration['quality_classes'], point.get('quality_weights', {}),
                                  operations, bool(configuration['recycling_facilities']), can_generate)


def validate_generation_config(configuration: Record) -> None:
    """Check an unresolved single-site, format-two generation configuration.

    generator.prepare_scenario and the experiment manager call this. Products and
    arrival_batches must not exist yet. Check distributions and fixed declarations;
    return None without sampling or changes. Unsupported or invalid inputs raise.
    """
    if configuration.get('format_version') == 1:
        raise ValueError('Scenario format 1 is retired; supported formats are 2 and 3')
    if configuration['format_version'] != 2 or 'products' in configuration or 'arrival_batches' in configuration:
        raise ValueError('Use an unresolved format-two configuration, without products or arrival_batches')
    operations = {station['operation_id'] for station in configuration['plant']['workstations']}
    validate_point_generation(configuration['generation'], configuration['collection_point'], configuration, operations)
    validate_fixed_declarations(configuration, [])


def validate_network_generation(configuration: Record) -> None:
    """Check an unresolved network before coordinates, calendars or returns are drawn.

    prepare_network_scenario calls this. Check the network and every point's
    settings, allowing declared coordinate/calendar generation. Return None without
    changes or random draws; resolved fields or invalid declarations raise.
    """
    if configuration['format_version'] != 3 or any(key in configuration for key in ('products', 'arrival_batches', 'seed_provenance')):
        raise ValueError('Use an unresolved format-three configuration without products, batches or seed provenance')
    if not configuration['plants'] or not configuration['collection_points']:
        raise ValueError('Seeded networks require plants and collection points')
    if 'coordinate_generation' in configuration:
        validate_coordinate_generation(configuration['coordinate_generation'])
    operations = validate_network_declarations(configuration, [], generating=True)
    for point in configuration['collection_points']:
        validate_point_generation(point['generation'], point, configuration, operations)


# Load original evidence or make a dynamic execution copy

def load_scenario(path: Path) -> tuple[Record, bytes]:
    """Read a saved scenario, check it, and return its data and original bytes.

    runner.run_once calls this for --resolved-scenario. validate_scenario checks the
    parsed data; the original bytes are kept for the scenario hash. No sampling or
    file writing occurs. File, JSON and validation errors reach the caller.
    The saved seed information is read as supplied; this loader does not audit it.
    """
    scenario_bytes = path.read_bytes()
    scenario = json.loads(scenario_bytes)
    validate_scenario(scenario)
    return scenario, scenario_bytes


def as_dynamic_scenario(scenario: Record) -> Record:
    """Make a separate execution copy for a dynamic policy.

    runner.run_once passes validated format-two/three inputs. Copy the whole
    scenario, put a single collection point in a list when needed, choose a dynamic
    policy and clear fixed trips. For a network, copy its first plant declaration
    separately into plant. Return the copy; saved inputs and bytes stay unchanged.
    These definition copies differ from live plant fields that refer to the same
    containers in state.initialize_plant_states. This function does not validate
    scope itself; its caller must do that first.
    """
    dynamic = copy.deepcopy(scenario)
    if dynamic['format_version'] == 2:
        dynamic['collection_points'] = [dynamic.pop('collection_point')]
    policy = dynamic.get('policy') if dynamic.get('policy') in ('dynamic_quality_unaware', 'dynamic_quality_aware') else 'dynamic_quality_unaware'
    if 'plants' in dynamic:
        dynamic['plant'] = copy.deepcopy(dynamic['plants'][0])
    dynamic.update(format_version=3, policy=policy, trips=[])
    return dynamic
