"""Prepare repeatable inputs before a simulation starts.

runner.run_once and tools.experiments call prepare_scenario. The functions below
derive separate random streams (sequences of random draws), generate coordinates,
returns, qualities and workstation windows, then return resolved data and its
exact JSON bytes.
config checks declarations; state supplies record types. No run files are written
and no physical events execute here. Generated future returns stay hidden from
routing until the engine records their release.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from pathlib import Path

from .config import (
    station_schedule_settings, validate_coordinate_generation, validate_dynamic_scenario,
    validate_generation_config, validate_inventory_scenario, validate_network_generation,
)
from .state import Record, ProductDefinition, ReturnBatch


COORDINATE_SITE_GROUPS = (('collection_point', 'collection_points'),
                          ('plant', 'plants'), ('recycler', 'recycling_facilities'))


# Name random streams and draw weighted choices

def derive_stream_seed(seed: int, stream_name: str) -> int:
    """Return a repeatable integer seed for a named random stream using SHA-256.

    Preparation helpers call this so changing quality sampling does not shift arrivals.
    The master seed and stream name are read only; no random values are drawn.
    """
    encoded = f'thesis-return-v1|{seed}|{stream_name}'.encode('utf-8')
    return int.from_bytes(hashlib.sha256(encoded).digest(), 'big')


def network_stream_name(kind: str, *identifiers: str) -> str:
    """Name a network stream using its purpose and site/workstation IDs.

    prepare_network_scenario uses this text with derive_stream_seed. JSON encoding
    keeps IDs with separators distinct; input list order does not define the name.
    """
    return json.dumps(['network-v2', kind, *identifiers], separators=(',', ':'), ensure_ascii=True)


def derive_coordinate_seed(master_seed: int, site_type: str, site_id: str) -> int:
    """Return one site's coordinate seed from the master seed, site type and ID.

    generate_site_coordinates calls this. A site's layout stream is independent of
    returns, qualities, workstation schedules and other sites.
    """
    encoded = json.dumps(['coordinates-v1', site_type, site_id], separators=(',', ':'), ensure_ascii=True)
    return derive_stream_seed(master_seed, encoded)


def weighted_index(generator: random.Random, weights: list[float]) -> int:
    """Return a zero-based choice using one draw from the supplied random stream.

    Batch and quality generation pass nonnegative weights with a positive sum.
    Read weights without changing them; only the random stream advances.
    """
    target = generator.random() * sum(weights)
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += weight
        if target < cumulative:
            return index
    return max(index for index, weight in enumerate(weights) if weight > 0)


# Generate site coordinates

def generate_site_coordinates(scenario: Record, master_seed: int) -> Record:
    """Draw each site's x then y and return the seeds used for those positions.

    prepare_network_scenario passes its separate scenario copy and master seed.
    Replace coordinates in that copy. Each site gets its own random stream, so
    other generators do not advance. Invalid bounds raise ValueError.
    Fixed-coordinate inputs skip this function. No events or files are written.
    """
    settings = scenario['coordinate_generation']
    validate_coordinate_generation(settings)
    seeds = {}
    for site_type, group in COORDINATE_SITE_GROUPS:
        seeds[site_type] = {}
        for site in scenario[group]:
            seed = derive_coordinate_seed(master_seed, site_type, site['id'])
            seeds[site_type][site['id']] = seed
            generator = random.Random(seed)
            site['coordinates'] = [generator.uniform(*settings['x_range']), generator.uniform(*settings['y_range'])]
    return seeds


# Generate return arrivals and batches

def generate_return_batches(settings: Record, generator: random.Random) -> list[ReturnBatch]:
    """Draw return times and batch sizes for one collection point.

    Scenario preparation passes validated arrival/batch settings and their random
    stream. Return batches in arrival order with B/P identifiers. Each arrival uses
    one draw; a weighted size uses another, but a fixed size does not.
    Include arrivals at end_time. Stop at the first arrival beyond it; rate zero
    returns no batches. Raise ValueError if time cannot advance. Settings stay
    unchanged; only the supplied stream advances. No physical events are recorded.
    """
    time = settings['start_time']
    batches: list[ReturnBatch] = []
    product_count = 0
    if settings['arrival_rate'] == 0:
        return batches
    while True:
        interval = -math.log1p(-generator.random()) / settings['arrival_rate']
        next_time = time + interval
        if next_time <= time:
            raise ValueError('Arrival interval does not advance the floating-point clock')
        time = next_time
        if time > settings['end_time']:
            return batches
        distribution = settings['batch_size']
        size = distribution if type(distribution) is int else distribution['values'][weighted_index(generator, distribution['weights'])]
        product_ids = [f'P{index:06d}' for index in range(product_count + 1, product_count + size + 1)]
        batches.append({'id': f'B{len(batches) + 1:06d}', 'release_time': time,
                        'batch_size': size, 'product_ids': product_ids})
        product_count += size


# Sample product quality and assign required operations

def generate_products(
    batches: list[ReturnBatch], origin: str, quality_classes: list[Record],
    overrides: dict[str, float], has_recycler: bool, generator: random.Random,
) -> list[ProductDefinition]:
    """Draw a quality for each batched product and copy its required operations.

    Scenario preparation supplies batches, origin, quality classes, point weight
    overrides, recycler availability and a separate quality stream. Return product
    records in batch/product order, including release time, quality and disposition.
    First remove recycling-only classes when no recycler exists, sort by class ID,
    apply overrides and divide weights by their total. Use one draw per product.
    Inputs stay unchanged; only this stream advances. Validation must establish
    positive eligible weight when products can exist. No events or files are written.
    """
    qualities = sorted((q for q in quality_classes
        if has_recycler or q['disposition'] == 'remanufacture'), key=lambda quality: quality['id'])
    weights = [overrides.get(quality['id'], quality['weight']) for quality in qualities]
    total = sum(weights)
    probabilities = [weight / total for weight in weights] if total else []
    products: list[ProductDefinition] = []
    for batch in batches:
        for product_id in batch['product_ids']:
            quality = qualities[weighted_index(generator, probabilities)]
            products.append({'id': product_id, 'batch_id': batch['id'], 'origin': origin,
                'release_time': batch['release_time'], 'quality_class': quality['id'],
                'disposition': quality['disposition'], 'operations': list(quality['operations'])})
    return products


# Generate workstation availability windows

def generate_windows(settings: Record, generator: random.Random) -> list[list[float]]:
    """Draw one workstation's opening and closing windows.

    prepare_network_scenario supplies validated settings and its private stream.
    Draw an initial delay, then alternating open/closed durations; return ordered
    [opening, closing] pairs clipped at end_time. Equal bounds still use a draw.
    Do not draw another closure after reaching end_time. Only the stream changes.
    Raise ValueError when a sampled duration cannot advance time; write no files.
    """
    opening = settings['start_time'] + generator.uniform(*settings['initial_delay'])
    windows = []
    while opening < settings['end_time']:
        closing = min(settings['end_time'], opening + generator.uniform(*settings['open_duration']))
        if closing <= opening:
            raise ValueError('Sampled opening duration cannot advance the floating-point clock')
        windows.append([opening, closing])
        if closing == settings['end_time']:
            break
        opening = closing + generator.uniform(*settings['closed_duration'])
        if opening <= closing:
            raise ValueError('Sampled closure duration cannot advance the floating-point clock')
    return windows


# Prepare complete resolved scenarios and their exact bytes

def _canonical_resolved_scenario(scenario: Record) -> tuple[Record, bytes]:
    """Return resolved data in sorted-key order and the exact bytes to save.

    Both preparation paths call this after validation. Encode with the established
    JSON options, one final LF newline and UTF-8. Decode those same bytes into a
    separate dictionary; this sets key order used by later event records.
    Do not change the supplied data or draw random values. Nonfinite values raise
    ValueError. The caller saves the bytes and hashes them; this helper writes nothing.
    """
    scenario_bytes = (json.dumps(scenario, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')
    return json.loads(scenario_bytes), scenario_bytes


def prepare_network_scenario(configuration: Record, seed: int) -> tuple[Record, bytes]:
    """Generate all inputs for one network on a separate configuration copy.

    prepare_scenario supplies the parsed configuration and integer master seed.
    Validate first. Generate optional coordinates, workstation calendars, then each
    point's arrivals, batches and qualities using separate streams. Prefix local
    product/batch IDs with the point ID and sort the combined records.
    Return validated resolved data and exact UTF-8/LF JSON bytes, including seeds
    and algorithm names. Only the copy and private random streams change.
    Invalid settings or a non-integer seed raise; no run executes or files are written.
    """
    if type(seed) is not int:
        raise ValueError('Generation requires an explicit integer seed')
    validate_network_generation(configuration)
    scenario = copy.deepcopy(configuration)
    seeds: Record = {'returns': {}, 'qualities': {}, 'workstation_schedules': {}}
    if 'coordinate_generation' in scenario:
        seeds['coordinates'] = generate_site_coordinates(scenario, seed)
    point_seeds = {}
    # Resolve calendars first; their private streams cannot shift return samples.
    for plant in scenario['plants']:
        station_seeds = {}
        for station in plant['workstations']:
            settings = station_schedule_settings(configuration.get('schedule_generation', {}), station, configuration['horizon'])
            if settings is not None:
                station_seed = derive_stream_seed(seed, network_stream_name('schedule', plant['id'], station['id']))
                station_seeds[station['id']] = station_seed
                station['window_generation'] = copy.deepcopy(settings)
                station['windows'] = generate_windows(settings, random.Random(station_seed))
        if station_seeds:
            seeds['workstation_schedules'][plant['id']] = station_seeds
    # Generate each point separately, then give its local IDs a point prefix.
    products, batches = [], []
    for point in sorted(scenario['collection_points'], key=lambda item: item['id']):
        identifier = point['id']
        point_seed = derive_stream_seed(seed, network_stream_name('collection', identifier))
        point_seeds[identifier] = point_seed
        returns_seed = derive_stream_seed(point_seed, 'arrivals_batches')
        quality_seed = derive_stream_seed(point_seed, 'qualities')
        seeds['returns'][identifier], seeds['qualities'][identifier] = returns_seed, quality_seed
        local_batches = generate_return_batches(point['generation'], random.Random(returns_seed))
        local_products = generate_products(
            local_batches, identifier, configuration['quality_classes'], point.get('quality_weights', {}),
            bool(configuration['recycling_facilities']), random.Random(quality_seed),
        )
        for batch in local_batches:
            batch.update(id=f"{identifier}:{batch['id']}", origin=identifier,
                         product_ids=[f'{identifier}:{product_id}' for product_id in batch['product_ids']])
        for product in local_products:
            product.update(id=f"{identifier}:{product['id']}", batch_id=f"{identifier}:{product['batch_id']}")
        batches.extend(local_batches)
        products.extend(local_products)
    scenario.update(products=sorted(products, key=lambda item: (item['release_time'], item['origin'], item['id'])),
        arrival_batches=sorted(batches, key=lambda item: (item['release_time'], item['origin'], item['id'])),
        seed_provenance={'source': 'seeded_network_configuration', 'master_seed': seed, 'generation_version': 2,
            'algorithm': 'site-streams-v2/exponential-inverse-cdf/weighted-cdf/alternating-uniform-windows-v1',
            'point_seeds': point_seeds, 'stream_seeds': seeds})
    if 'coordinate_generation' in scenario:
        scenario['seed_provenance']['coordinate_algorithm'] = 'site-type-id/uniform-xy-v1'
    # Validate resolved inputs before fixing their saved bytes and key order.
    validate_dynamic_scenario(scenario)
    return _canonical_resolved_scenario(scenario)


def prepare_scenario(configuration_path: Path, seed: int) -> tuple[Record, bytes]:
    """Read a generation configuration and return resolved data and exact JSON bytes.

    runner.run_once and tools.experiments supply the path and integer master seed.
    Network inputs call prepare_network_scenario. Single-site inputs keep separate
    arrivals_batches and qualities streams. Validate before and after generation;
    work on copies and return sorted-key UTF-8 JSON ending in LF.
    File/JSON errors and invalid settings reach the caller. No inputs are written.
    Saved-input replay uses config.load_scenario instead and draws nothing.
    """
    if type(seed) is not int:
        raise ValueError('Generation requires an explicit integer seed')
    configuration = json.loads(configuration_path.read_text(encoding='utf-8-sig'))
    if configuration['format_version'] == 3:
        return prepare_network_scenario(configuration, seed)
    validate_generation_config(configuration)
    stream_seeds = {name: derive_stream_seed(seed, name) for name in ('arrivals_batches', 'qualities')}
    batches = generate_return_batches(configuration['generation'], random.Random(stream_seeds['arrivals_batches']))
    point = configuration['collection_point']
    products = generate_products(
        batches, point['id'], configuration['quality_classes'], point.get('quality_weights', {}),
        bool(configuration['recycling_facilities']), random.Random(stream_seeds['qualities']),
    )
    scenario = copy.deepcopy(configuration)
    scenario.update(products=products, arrival_batches=batches, seed_provenance={
        'source': 'seeded_configuration', 'master_seed': seed, 'generation_version': 1,
        'algorithm': 'Random.random/exponential-inverse-cdf/weighted-cdf-v1', 'stream_seeds': stream_seeds})
    validate_inventory_scenario(scenario)
    return _canonical_resolved_scenario(scenario)
