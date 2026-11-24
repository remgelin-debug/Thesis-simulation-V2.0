"""Rebuild display state from saved events without executing simulation rules.

tools.viewer creates RunReplay, then ReplayController drives seeking and playback.
Helpers make initial display state, apply records and interpolate recorded legs.
A time seek includes all records through that timestamp; an event seek stops at
an exact record, even when several events have the same time. This is a display,
not independent verification. No simulation or plotting imports are needed and
no saved evidence is changed.
"""
from __future__ import annotations

from bisect import bisect_right
import copy
import hashlib
import json
import math
from pathlib import Path

from typing import Any

Record = dict[str, Any]
CHECKPOINT_INTERVAL = 100


# Build display data from saved inputs and events

def normalize_view_scenario(scenario: Record) -> Record:
    """Return an owned display copy of saved inputs with sites/fleet/products in lists.

    RunReplay calls after checking the raw scenario hash. Read formats two and
    three and add display list aliases inside the copy. No generation, validation
    of physical decisions or file writes occur. Required saved fields must exist.
    """
    if not isinstance(scenario, dict) or scenario.get('format_version') not in (2, 3):
        raise ValueError('Replay supports scenario formats 2 and 3; format 1 is retired')
    view = copy.deepcopy(scenario)
    view['plants'] = view['plants'] if 'plants' in view else [view['plant']]
    view['collection_points'] = view['collection_points'] if 'collection_points' in view else [view['collection_point']]
    return view


def initial_view_state(scenario: Record) -> Record:
    """Return initial display containers before any event is applied.

    RunReplay uses the normalized scenario to place empty vehicles at their initial
    sites and mark products unreleased. Input data stays unchanged. Actual releases,
    inventory and jobs come from apply_view_event, never from rerunning routing.
    """
    sites = {site['id']: site for site in [*scenario['plants'], *scenario['collection_points'], *scenario['recycling_facilities']]}
    return {'time': 0.0, 'events_applied': 0, 'last_event': None,
        'products': {product['id']: {'state': 'unreleased', 'location': None, 'next_operation': 0,
            'assigned_vehicle': None, 'planned_destination': None, 'actual_destination': None} for product in scenario['products']},
        'vehicles': {vehicle['id']: {'position': list(sites[vehicle['initial_site']]['coordinates']),
            'location': vehicle['initial_site'], 'status': 'idle', 'load': [], 'active_leg': None,
            'remaining_stops': [], 'remaining_range': vehicle['maximum_range']} for vehicle in scenario['vehicles']},
        'plants': {plant['id']: {'inventory': [], 'held_slots': [], 'waiting_vehicles': [],
            'workstations': {station['id']: None for station in plant['workstations']},
            'processing_history': {station['id']: [] for station in plant['workstations']}} for plant in scenario['plants']}}


def apply_view_event(state: Record, event: Record, *, record_history: bool = True) -> None:
    """Apply one recorded event to the supplied display state in sequence order.

    RunReplay calls when building checkpoints or reconstructing a snapshot.
    Update product progress, vehicle/load/route and plant display containers;
    return None. Do not execute plant rules, choose routes or validate the model.
    Direct callers retain completed processing history by default. RunReplay sets
    record_history=False because it indexes start/completion pairs separately.
    No saved record, run file or simulation event is changed. Unknown event types raise.
    """
    kind = event['event_type']
    product = state['products'].get(event.get('product_id'))
    vehicle = state['vehicles'].get(event.get('vehicle_id'))
    if kind == 'return_release':
        definition = event['product']
        state['products'][definition['id']].update(state='at_collection_point', location=definition['origin'])
    elif kind == 'assignment':
        product.update(assigned_vehicle=event['vehicle_id'], planned_destination=event['destination'])
    elif kind == 'leg_departure':
        vehicle.update(active_leg=copy.deepcopy(event), position=list(event['origin_coordinates']),
            location=None, status='travelling', remaining_range=event.get('remaining_range_at_departure'))
    elif kind == 'leg_arrival':
        vehicle.update(active_leg=None, position=list(event['target_coordinates']), location=event['target'], status='idle')
        if 'remaining_range_on_arrival' in event:
            vehicle['remaining_range'] = event['remaining_range_on_arrival']
    elif kind == 'leg_cancelled':
        vehicle.update(active_leg=None, position=list(event['position']), remaining_range=event['remaining_range'], status='idle')
    elif kind == 'route_revised':
        vehicle['remaining_stops'] = copy.deepcopy(event['new_remaining_stops'])
    elif kind == 'stop_completed':
        vehicle['remaining_stops'].pop(0)
    elif kind == 'pickup':
        vehicle['load'].append(event['product_id'])
        product.update(state='on_vehicle', location=event['vehicle_id'])
    elif kind in ('plant_admission', 'recycler_acceptance'):
        vehicle['load'].remove(event['product_id'])
        destination = event['plant_id'] if kind == 'plant_admission' else event['recycler_id']
        product.update(state='in_plant_inventory' if kind == 'plant_admission' else 'recycled',
            location=destination if kind == 'plant_admission' else None, actual_destination=destination)
        if kind == 'plant_admission':
            state['plants'][destination]['inventory'].append(event['product_id'])
        # A dynamic visit ends its unloading wait once its own deliveries are
        # served, even if same-destination load is reserved for a later visit.
        stops = vehicle['remaining_stops']
        if stops:
            pending = any(identifier in stops[0]['deliveries'] for identifier in vehicle['load'])
        else:
            pending = any(state['products'][identifier]['planned_destination'] == destination
                          for identifier in vehicle['load'])
        if not pending:
            vehicle['status'] = 'idle'
            for plant in state['plants'].values():
                if event['vehicle_id'] in plant['waiting_vehicles']:
                    plant['waiting_vehicles'].remove(event['vehicle_id'])
    elif kind == 'operation_start':
        plant = state['plants'][event['plant_id']]
        plant['inventory'].remove(event['product_id'])
        plant['workstations'][event['workstation_id']] = copy.deepcopy(event)
        product.update(state='processing', location=event['workstation_id'])
    elif kind == 'operation_complete':
        plant = state['plants'][event['plant_id']]
        if record_history:
            completed = copy.deepcopy(plant['workstations'][event['workstation_id']])
            completed.update(end_time=event['time'], completion_sequence=event['sequence'])
            plant['processing_history'][event['workstation_id']].append(completed)
        plant['workstations'][event['workstation_id']] = None
        product.update(state='operation_complete', next_operation=event['operation_index'] + 1)
    elif kind == 'inventory_return':
        plant = state['plants'][event['plant_id']]
        plant['inventory'].append(event['product_id'])
        if event.get('held_slot_replaced'):
            plant['held_slots'].remove(event['product_id'])
        product.update(state='in_plant_inventory', location=event['plant_id'])
    elif kind == 'inventory_hold':
        state['plants'][event['plant_id']]['held_slots'].append(event['product_id'])
    elif kind == 'inventory_slot_release':
        # A one-operation product need not have owned a held slot.
        held = state['plants'][event['plant_id']]['held_slots']
        if event['product_id'] in held:
            held.remove(event['product_id'])
    elif kind == 'remanufactured':
        product.update(state='remanufactured', location=None)
    elif kind in ('unload_wait', 'unload_retry'):
        vehicle['status'] = 'waiting_to_unload'
        waiting = state['plants'][vehicle['location']]['waiting_vehicles']
        if event['vehicle_id'] not in waiting:
            waiting.append(event['vehicle_id'])
    elif kind == 'range_reset':
        vehicle['remaining_range'] = event['range_after']
    elif kind == 'vehicle_available':
        vehicle['status'] = 'idle'
    elif kind not in ('dispatch', 'range_plan_check', 'trip_rejected', 'horizon'):
        raise ValueError(f'Unsupported replay event type: {kind}')
    state.update(time=event['time'], events_applied=event['sequence'], last_event=copy.deepcopy(event))


# Place displayed vehicles between recorded events

def interpolate_vehicles(state: Record, time: float) -> None:
    """Move displayed vehicles along recorded active legs to the requested time.

    RunReplay.snapshot calls after applying the selected records. Change display
    positions, remaining range and clock only; return None. Cap motion to the logged
    leg interval. Cancelled legs have already been replaced by preceding records.
    This uses saved times, not new routing decisions; no files or events change.
    """
    for vehicle in state['vehicles'].values():
        leg = vehicle['active_leg']
        if leg is None:
            continue
        duration = leg['arrival_time'] - leg['departure_time']
        fraction = min(1.0, max(0.0, (time - leg['departure_time']) / duration)) if duration else 1.0
        vehicle['position'] = [origin + fraction * (target - origin)
            for origin, target in zip(leg['origin_coordinates'], leg['target_coordinates'])]
        allowance = leg.get('remaining_range_at_departure')
        if allowance is not None:
            vehicle['remaining_range'] = max(0.0, allowance - fraction * leg['distance'])
    state['time'] = time


class RunReplay:
    """Read saved events once; keep physical checkpoints and index completed jobs once."""

    # Load once and reconstruct separate snapshots

    def __init__(self, run_folder: Path) -> None:
        """Read scenario, manifest and events and prepare saved display checkpoints.

        open_viewer and replay clients pass a completed run folder. Check the original
        scenario hash and event order. Keep each completed job as a start/completion
        sequence pair, stored once. Save physical display state every 100 records without
        copying completed history into each checkpoint. No evidence is written.
        Return None. Bad hashes, incomplete runs or unordered sequences raise ValueError.
        Snapshots later copy changing values so callers cannot alter stored state.
        """
        self.run_folder = run_folder.resolve()
        scenario_bytes = (self.run_folder / 'scenario.json').read_bytes()
        self.manifest = json.loads((self.run_folder / 'run_manifest.json').read_text(encoding='utf-8'))
        if hashlib.sha256(scenario_bytes).hexdigest() != self.manifest['scenario_sha256']:
            raise ValueError('Replay scenario bytes do not match the saved manifest hash')
        if self.manifest['status'] != 'completed':
            raise ValueError('Replay requires a completed single run')
        self.scenario = normalize_view_scenario(json.loads(scenario_bytes))
        self.horizon = self.scenario['horizon']
        self.events = [json.loads(line) for line in (self.run_folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.times = []
        history: dict[tuple[str, str], list[tuple[int, int]]] = {
            (plant['id'], station['id']): [] for plant in self.scenario['plants'] for station in plant['workstations']}
        self.checkpoints: dict[int, Record] = {0: initial_view_state(self.scenario)}
        state = copy.deepcopy(self.checkpoints[0])
        previous = 0.0
        for index, event in enumerate(self.events, 1):
            time = event['time']
            if event['sequence'] != index or not math.isfinite(time) or not previous <= time <= self.horizon:
                raise ValueError('Replay requires contiguous event sequences and ordered finite times inside the horizon')
            self.times.append(time)
            previous = time
            if event['event_type'] == 'operation_complete':
                job = state['plants'][event['plant_id']]['workstations'][event['workstation_id']]
                history[(event['plant_id'], event['workstation_id'])].append((index, job['sequence']))
            apply_view_event(state, event, record_history=False)
            if index % CHECKPOINT_INTERVAL == 0:
                self.checkpoints[index] = copy.deepcopy(state)
        # Pairs contain only completion/start sequence numbers. Saved records
        # remain owned read-only input, rather than copied into every checkpoint.
        self._completed_operations = {key: tuple(pairs) for key, pairs in history.items()}

    def snapshot(self, time: float, events_applied: int | None = None) -> Record:
        """Return a separate display snapshot at a time or after an exact record count.

        ReplayController supplies absolute time and optionally events_applied.
        With no count, include every record through that time. With a count, preserve
        the exact record state, including between same-time completion and new start.
        Copy the nearest checkpoint, apply at most 99 more records, reconstruct visible
        completed jobs and interpolate vehicles. Caller changes cannot alter other
        snapshots or stored replay data. Copying visible history costs time proportional
        to completed jobs. Invalid times/counts raise ValueError. No files/events change.
        """
        if not math.isfinite(time) or not 0 <= time <= self.horizon:
            raise ValueError(f'Viewer time must be between 0 and {self.horizon}')
        count = bisect_right(self.times, time) if events_applied is None else events_applied
        if type(count) is not int or not 0 <= count <= len(self.events) or (count and self.times[count - 1] > time):
            raise ValueError('Invalid replay event cursor')
        checkpoint = count // CHECKPOINT_INTERVAL * CHECKPOINT_INTERVAL
        state = copy.deepcopy(self.checkpoints[checkpoint])
        for event in self.events[checkpoint:count]:
            apply_view_event(state, event, record_history=False)
        # Copy only history visible after this exact number of records.
        for (plant_id, station_id), pairs in self._completed_operations.items():
            visible = bisect_right(pairs, (count, len(self.events)))
            completed_jobs = state['plants'][plant_id]['processing_history'][station_id]
            for index in range(visible):
                completion_sequence, start_sequence = pairs[index]
                completed = copy.deepcopy(self.events[start_sequence - 1])
                completed.update(end_time=self.events[completion_sequence - 1]['time'], completion_sequence=completion_sequence)
                completed_jobs.append(completed)
        interpolate_vehicles(state, time)
        return state


class ReplayController:
    """Own display time, pause state and an exact event position; never change evidence."""

    # Create the paused display

    def __init__(self, replay: RunReplay, time: float = 0.0, speed: float = 1.0) -> None:
        """Start paused with a separate snapshot through time and a playback speed.

        open_viewer passes an existing RunReplay. Retain it for reading and own the
        display snapshot. Return None. Nonpositive/nonfinite speed or invalid time raises;
        no saved records change.
        """
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('Playback speed must be positive and finite')
        self.replay, self.speed, self.paused = replay, speed, True
        self.state = replay.snapshot(time)

    # Seek by time or by exact event record

    def seek_time(self, time: float) -> None:
        """Pause at time, including every event with that timestamp.

        Viewer time/restart controls call this. Replace only controller state with a
        separate RunReplay snapshot. Invalid time raises; saved evidence stays unchanged.
        """
        self.state = self.replay.snapshot(time)
        self.paused = True

    def seek_event(self, sequence: int) -> None:
        """Pause after the exact numbered event, including an intermediate same-time state.

        Viewer event controls and next/previous stepping call this. Replace only the
        display snapshot. Sequence numbers start at 1; zero is handled by previous-event
        stepping instead. Invalid sequences raise ValueError; no evidence changes.
        """
        if type(sequence) is not int or not 1 <= sequence <= len(self.replay.events):
            raise ValueError(f'Event sequence must be between 1 and {len(self.replay.events)}')
        self.state = self.replay.snapshot(self.replay.times[sequence - 1], sequence)
        self.paused = True

    # Step or advance the displayed replay

    def step_next_event(self) -> None:
        """Pause and apply exactly one more record, even at the same time.

        Viewer Next/keyboard controls call this. Change only display state; at the last
        record do nothing further. Saved events are not executed again.
        """
        sequence = self.state['events_applied'] + 1
        self.paused = True
        if sequence <= len(self.replay.events):
            self.seek_event(sequence)

    def step_previous_event(self) -> None:
        """Pause and step back one record; before the first record show initial state.

        Viewer Previous/keyboard controls call this. At initial state it stays there.
        Replacing the snapshot restores product operations and processing history too;
        saved evidence remains unchanged.
        """
        sequence = self.state['events_applied'] - 1
        self.paused = True
        if sequence <= 0:
            self.state = self.replay.snapshot(0.0, 0)
        else:
            self.seek_event(sequence)

    def toggle_play(self) -> None:
        """Switch display playback between paused and running.

        Viewer Resume/Pause calls this. At the horizon remain paused until seeking
        earlier. Change the pause flag only; do not advance time or change evidence.
        """
        if self.state['time'] < self.replay.horizon:
            self.paused = not self.paused

    def advance(self, elapsed_seconds: float) -> None:
        """Advance display time by elapsed real seconds times playback speed.

        The viewer timer supplies nonnegative finite seconds. While running, replace
        the snapshot through the new time and pause at the horizon. While paused, keep
        the exact event position. Invalid elapsed time raises even when paused.
        Only controller state/paused change; no simulation runs and no files are written.
        """
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError('Elapsed playback time must be nonnegative and finite')
        if self.paused:
            return
        time = min(self.replay.horizon, self.state['time'] + elapsed_seconds * self.speed)
        self.state = self.replay.snapshot(time)
        if time == self.replay.horizon:
            self.paused = True
