"""Reject physical calculations that Python cannot represent correctly.

Execution, routing and forecasting call these checks before using calculated
distances or times. A positive trip or operation must advance the clock; a real
zero-distance trip may arrive immediately. Checks also run under python -O.
This module changes no state and writes no events or files. Seeds and other
integer identifiers do not pass through these physical-number checks.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


class PhysicalArithmeticError(ValueError):
    """A required physical calculation cannot be represented at the supplied scale."""


# Check physical amounts

def finite_physical(value: float, quantity: str, **context: object) -> float:
    """Return a finite physical value unchanged, or raise PhysicalArithmeticError.

    Travel, plant and forecast code pass a quantity name and IDs/time for the error
    message. This reads the result only; it does not round it or check integer seeds.
    """
    if not math.isfinite(value):
        raise PhysicalArithmeticError(f'Physical arithmetic: {quantity} must be finite; observed={value!r}; {context}')
    return value


def nonnegative_physical(value: float, quantity: str, **context: object) -> float:
    """Return a finite, nonnegative physical amount unchanged, or raise.

    Physical calculations use this for distances and accumulated waits. quantity
    and context describe a failure; no state or evidence changes here.
    """
    finite_physical(value, quantity, **context)
    if value < 0:
        raise PhysicalArithmeticError(f'Physical arithmetic: {quantity} must be nonnegative; observed={value!r}; {context}')
    return value


def physical_distance(origin: Sequence[float], target: Sequence[float], **context: object) -> float:
    """Return straight-line distance between positions, or reject lost/overflowed distance.

    Transport, routing and forecasts call this. Distinct coordinates must not
    collapse to zero through rounding. Read the coordinate sequences only.
    """
    distance = nonnegative_physical(math.dist(origin, target), 'distance', origin=origin, target=target, **context)
    if distance == 0 and any(a != b for a, b in zip(origin, target)):
        raise PhysicalArithmeticError(f'Physical arithmetic: distance underflow; origin={origin}, target={target}; {context}')
    return distance


# Check travel and processing times

def physical_end(start: float, duration: float, quantity: str, *, allow_zero: bool = False,
                 **context: object) -> float:
    """Return start plus duration only if the required activity advances time.

    Travel and processing checks call this before a transition is applied. start is
    an absolute time; duration is elapsed time. A genuine zero-distance trip may use
    allow_zero. A finite end beyond the horizon is valid arithmetic, even if no event
    will execute there. Raise PhysicalArithmeticError for an unrepresentable result;
    never add an artificial delay. No state changes.
    """
    finite_physical(start, f'{quantity}.start', **context)
    nonnegative_physical(duration, f'{quantity}.duration', time=start, **context)
    end = finite_physical(start + duration, f'{quantity}.end', time=start, duration=duration, **context)
    if not (duration == 0 and allow_zero) and (duration <= 0 or end <= start):
        raise PhysicalArithmeticError(f'Physical arithmetic: {quantity} must advance time; time={start!r}, '
                                      f'duration={duration!r}, observed_end={end!r}; {context}')
    return end


def travel_arrival(start: float, distance: float, speed: float, **context: object) -> float:
    """Return a checked arrival time from absolute start, distance and speed.

    Travel execution and route/forecast calculations call this before scheduling.
    Positive travel must advance time; zero-distance travel may be immediate.
    Invalid division or addition raises PhysicalArithmeticError without mutation.
    """
    nonnegative_physical(distance, 'distance', time=start, **context)
    finite_physical(speed, 'travel.speed', time=start, **context)
    if speed <= 0:
        raise PhysicalArithmeticError(f'Physical arithmetic: travel.speed must be positive; time={start!r}, speed={speed!r}; {context}')
    duration = distance / speed
    return physical_end(start, duration, 'travel', allow_zero=distance == 0, **context)


def processing_completion(start: float, duration: float, **context: object) -> float:
    """Return a checked completion time for a positive operation duration.

    Plant starts and copied forecasts call this before work begins. Raise
    PhysicalArithmeticError if adding the duration cannot advance the clock.
    """
    return physical_end(start, duration, 'processing', **context)


# Account for the part of a leg actually travelled

def travel_prefix(elapsed: float, speed: float, distance: float, **context: object) -> float:
    """Return distance actually covered so far, capped at the leg's full distance.

    advance_vehicle_travel and forecasts pass elapsed time, speed and leg length.
    Check multiplication before the cap can hide overflow or underflow. Positive
    elapsed travel must have a representable distance. Return distance units;
    change no state and do not round results.
    """
    nonnegative_physical(elapsed, 'travel.elapsed', **context)
    covered = nonnegative_physical(max(0.0, elapsed) * speed, 'travel.prefix', **context)
    if elapsed > 0 and distance > 0 and covered == 0:
        raise PhysicalArithmeticError(f'Physical arithmetic: travel.prefix underflow; elapsed={elapsed}, speed={speed}; {context}')
    return min(distance, covered)
