"""Construct one explicit format-two example for retained single-product safeguards.

Tests call single_product_scenario and own its returned dictionaries. This is not
a runtime converter for retired inputs. Historical fixture bytes are never edited.
"""


def single_product_scenario() -> dict:
    """Return fresh inventory inputs with the hand-calculated 4/6/8/11/12/13 job times.

    Test helpers write this scenario to temporary files before calling the real
    runner or CLI. A speed change affects travel only. No files or state change here.
    """
    return {
        'format_version': 2, 'policy': 'static_quality_unaware', 'horizon': 14,
        'seed_provenance': {'source': 'Explicit single-product inventory example', 'seed': None},
        'plant': {'id': 'RF1', 'coordinates': [0, 0], 'inventory_capacity': None,
            'workstations': [
                {'id': 'WS1', 'operation_id': 1, 'duration': 2, 'windows': [[4, 6]]},
                {'id': 'WS3', 'operation_id': 3, 'duration': 3, 'windows': [[8, 11]]},
                {'id': 'WS2', 'operation_id': 2, 'duration': 1, 'windows': [[12, 13]]},
            ]},
        'collection_point': {'id': 'CP1', 'coordinates': [2, 0]},
        'vehicles': [{'id': 'AV1', 'initial_site': 'RF1', 'speed': 1,
                      'payload_capacity': 1, 'maximum_range': None}],
        'products': [{'id': 'P1', 'origin': 'CP1', 'release_time': 0,
                      'quality_class': 'Q132', 'disposition': 'remanufacture', 'operations': [1, 3, 2]}],
        'trips': [{'vehicle_id': 'AV1', 'departure_time': 0, 'stops': ['CP1', 'RF1']}],
        'recycling_facilities': [],
    }
