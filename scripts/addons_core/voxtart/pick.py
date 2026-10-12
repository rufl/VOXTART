# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic finite-segment traversal for voxel picking."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .voxel import Cell, VoxelAsset


@dataclass(frozen=True, slots=True)
class RayHit:
    cell: Cell
    palette_index: int
    distance: float
    normal: Cell


@dataclass(frozen=True, slots=True)
class SegmentCell:
    """One cell crossed by a finite segment in normalized segment time."""

    cell: Cell
    t_enter: float
    t_exit: float
    normal: Cell


def _vector(value: Iterable[float], name: str) -> tuple[float, float, float]:
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(math.isfinite(component) for component in result):
        raise ValueError(f"{name} must contain three finite values")
    return result  # type: ignore[return-value]


def _segment_interval(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> tuple[float, float] | None:
    direction = tuple(end[axis] - start[axis] for axis in range(3))
    entry = 0.0
    exit = 1.0
    for axis in range(3):
        if abs(direction[axis]) < 1.0e-12:
            if start[axis] < minimum[axis] or start[axis] > maximum[axis]:
                return None
            continue
        near = (minimum[axis] - start[axis]) / direction[axis]
        far = (maximum[axis] - start[axis]) / direction[axis]
        if near > far:
            near, far = far, near
        entry = max(entry, near)
        exit = min(exit, far)
        if entry > exit:
            return None
    return entry, exit


def finite_segment(
    start: Iterable[float],
    end: Iterable[float],
    bounds_min: Iterable[float],
    bounds_max: Iterable[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Clip a finite segment to an axis-aligned box.

    Coordinates and segment length are bounded to prevent pathological
    traversal work from untrusted picking input.
    """

    source_start = _vector(start, "start")
    source_end = _vector(end, "end")
    minimum = _vector(bounds_min, "bounds_min")
    maximum = _vector(bounds_max, "bounds_max")
    if any(maximum[axis] <= minimum[axis] for axis in range(3)):
        raise ValueError("bounds_max must be greater than bounds_min")
    if any(abs(value) > 1.0e9 for value in (*source_start, *source_end)):
        raise ValueError("segment coordinates exceed the finite traversal limit")
    if math.sqrt(sum((source_end[axis] - source_start[axis]) ** 2 for axis in range(3))) > 1.0e9:
        raise ValueError("segment length exceeds the finite traversal limit")
    interval = _segment_interval(source_start, source_end, minimum, maximum)
    if interval is None:
        return None
    entry, exit = interval
    return (
        tuple(source_start[axis] + (source_end[axis] - source_start[axis]) * entry for axis in range(3)),
        tuple(source_start[axis] + (source_end[axis] - source_start[axis]) * exit for axis in range(3)),
    )  # type: ignore[return-value]


def _initial_cell(
    point: tuple[float, float, float],
    direction: tuple[float, float, float],
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> Cell:
    epsilon = 1.0e-10
    sample = tuple(
        point[axis] + (epsilon if direction[axis] > 0.0 else -epsilon if direction[axis] < 0.0 else 0.0)
        for axis in range(3)
    )
    result = []
    for axis in range(3):
        coordinate = min(max(sample[axis], minimum[axis]), math.nextafter(maximum[axis], minimum[axis]))
        result.append(math.floor(coordinate))
    return tuple(result)  # type: ignore[return-value]


def traverse_segment(
    start: Iterable[float],
    end: Iterable[float],
    bounds_min: Iterable[float],
    bounds_max: Iterable[float],
    *,
    include_endpoint: bool = False,
) -> tuple[SegmentCell, ...]:
    """Traverse every grid cell touched by a finite segment.

    All axes crossed at the same parametric time advance together.  This is
    the corner-safe Amanatides--Woo variant used by the native reference.
    """

    source_start = _vector(start, "start")
    source_end = _vector(end, "end")
    minimum = _vector(bounds_min, "bounds_min")
    maximum = _vector(bounds_max, "bounds_max")
    clipped = finite_segment(source_start, source_end, minimum, maximum)
    if clipped is None:
        return ()
    start_time, end_time = _segment_interval(source_start, source_end, minimum, maximum)  # type: ignore[misc]
    direction = tuple(source_end[axis] - source_start[axis] for axis in range(3))
    if all(abs(component) < 1.0e-12 for component in direction):
        if not include_endpoint:
            return ()
        cell = _initial_cell(clipped[0], direction, minimum, maximum)
        return (SegmentCell(cell, start_time, end_time, (0, 0, 0)),)

    cell = list(_initial_cell(clipped[0], direction, minimum, maximum))
    step = tuple(
        1 if component > 0.0 else -1 if component < 0.0 else 0
        for component in direction
    )
    epsilon = 1.0e-10
    records: list[SegmentCell] = []
    current_time = start_time
    entry_normal = [0, 0, 0]
    entry_point = clipped[0]
    for axis in range(3):
        if step[axis] == 0:
            continue
        boundary = minimum[axis] if step[axis] > 0 else maximum[axis]
        if abs(entry_point[axis] - boundary) <= 1.0e-8:
            entry_normal[axis] = -step[axis]

    while current_time < end_time - epsilon:
        next_times = []
        for axis in range(3):
            if step[axis] == 0:
                next_times.append(math.inf)
                continue
            boundary = cell[axis] + (1 if step[axis] > 0 else 0)
            next_times.append((boundary - source_start[axis]) / direction[axis])
        next_time = min(next_times)
        cell_exit = min(next_time, end_time)
        if cell_exit <= current_time + epsilon:
            break
        records.append(
            SegmentCell(
                tuple(cell),  # type: ignore[arg-type]
                current_time,
                cell_exit,
                tuple(entry_normal),  # type: ignore[arg-type]
            )
        )
        if next_time >= end_time - epsilon:
            break
        crossed = [
            axis for axis, candidate in enumerate(next_times)
            if abs(candidate - next_time) <= epsilon
        ]
        normal = [0, 0, 0]
        for axis in crossed:
            cell[axis] += step[axis]
            normal[axis] = -step[axis]
        current_time = next_time
        entry_normal = normal
    return tuple(records)


def raycast_voxel(
    asset: VoxelAsset,
    origin: Iterable[float],
    direction: Iterable[float],
    *,
    max_distance: float = 1.0e6,
) -> RayHit | None:
    """Return the first occupied voxel crossed by a local-space ray."""

    if not math.isfinite(max_distance) or max_distance < 0.0:
        raise ValueError("max_distance must be a finite non-negative value")
    if not asset.voxels:
        return None
    source_origin = _vector(origin, "origin")
    source_direction = _vector(direction, "direction")
    direction_length = math.sqrt(sum(component * component for component in source_direction))
    if direction_length < 1.0e-12:
        raise ValueError("direction must not be zero")
    unit_direction = tuple(component / direction_length for component in source_direction)
    grid = asset.grid_size
    minimum_cell, maximum_cell = asset.bounds  # type: ignore[misc]
    origin_voxel = tuple(component / grid for component in source_origin)
    direction_voxel = tuple(component / grid for component in unit_direction)
    bounds_minimum = tuple(float(component) for component in minimum_cell)
    bounds_maximum = tuple(float(component + 1) for component in maximum_cell)
    end_voxel = tuple(
        origin_voxel[axis] + direction_voxel[axis] * max_distance
        for axis in range(3)
    )
    for record in traverse_segment(
        origin_voxel,
        end_voxel,
        bounds_minimum,
        bounds_maximum,
        include_endpoint=True,
    ):
        palette_index = asset.voxels.get(record.cell)
        if palette_index is not None:
            return RayHit(
                record.cell,
                palette_index,
                record.t_enter * max_distance,
                record.normal,
            )
    return None


__all__ = ["RayHit", "SegmentCell", "finite_segment", "raycast_voxel", "traverse_segment"]
