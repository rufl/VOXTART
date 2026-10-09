# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Deterministic grid ray traversal for voxel picking."""

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


def _vector(value: Iterable[float], name: str) -> tuple[float, float, float]:
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(math.isfinite(component) for component in result):
        raise ValueError(f"{name} must contain three finite values")
    return result  # type: ignore[return-value]


def _ray_interval(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> tuple[float, float] | None:
    entry = -math.inf
    exit = math.inf
    for axis in range(3):
        if abs(direction[axis]) < 1.0e-12:
            if origin[axis] < minimum[axis] or origin[axis] > maximum[axis]:
                return None
            continue
        near = (minimum[axis] - origin[axis]) / direction[axis]
        far = (maximum[axis] - origin[axis]) / direction[axis]
        if near > far:
            near, far = far, near
        entry = max(entry, near)
        exit = min(exit, far)
        if entry > exit:
            return None
    return entry, exit


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
    interval = _ray_interval(origin_voxel, direction_voxel, bounds_minimum, bounds_maximum)
    if interval is None:
        return None
    entry, exit = interval
    if exit < 0.0 or entry > max_distance:
        return None
    distance = max(0.0, entry)
    limit = min(max_distance, exit)
    epsilon = 1.0e-9
    sample_distance = min(distance + epsilon, limit)
    sample = tuple(
        origin_voxel[axis] + direction_voxel[axis] * sample_distance
        for axis in range(3)
    )
    cell = tuple(math.floor(coordinate) for coordinate in sample)
    step = tuple(
        1 if component > 0.0 else -1 if component < 0.0 else 0
        for component in direction_voxel
    )
    entry_normal = [0, 0, 0]
    if entry > 0.0:
        for axis in range(3):
            boundary = bounds_minimum[axis] if direction_voxel[axis] > 0.0 else bounds_maximum[axis]
            if abs(origin_voxel[axis] + direction_voxel[axis] * entry - boundary) < 1.0e-7:
                entry_normal[axis] = -step[axis]
                break
    normal = tuple(entry_normal)  # type: ignore[assignment]

    def hit(current: Cell, hit_distance: float, hit_normal: Cell) -> RayHit | None:
        palette_index = asset.voxels.get(current)
        if palette_index is None:
            return None
        return RayHit(current, palette_index, hit_distance, hit_normal)

    while distance <= limit + epsilon:
        current = tuple(cell)  # type: ignore[arg-type]
        result = hit(current, distance, normal)
        if result is not None:
            return result
        next_distance = math.inf
        next_axis = -1
        for axis in range(3):
            if step[axis] == 0:
                continue
            boundary = cell[axis] + (1 if step[axis] > 0 else 0)
            candidate = distance + epsilon + (boundary - sample[axis]) / direction_voxel[axis]
            if candidate < next_distance:
                next_distance = candidate
                next_axis = axis
        if next_axis < 0 or next_distance > limit + epsilon:
            break
        distance = max(distance, next_distance)
        cell = list(cell)
        cell[next_axis] += step[next_axis]
        cell = tuple(cell)
        sample = tuple(
            origin_voxel[axis] + direction_voxel[axis] * (distance + epsilon)
            for axis in range(3)
        )
        normal_vector = [0, 0, 0]
        normal_vector[next_axis] = -step[next_axis]
        normal = tuple(normal_vector)  # type: ignore[assignment]
    return None
