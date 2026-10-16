# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic rounded voxel blobs generated from a 2D lasso polygon."""

from __future__ import annotations

import math
from typing import Iterable

from .voxel import Cell


MAX_LASSO_BLOB_VOXELS = 1_000_000


def _polygon(value: Iterable[Iterable[float]]) -> tuple[tuple[float, float], ...]:
    points = tuple((float(point[0]), float(point[1])) for point in value)
    if len(points) < 3:
        raise ValueError("lasso blob needs at least three points")
    if any(not math.isfinite(axis) for point in points for axis in point):
        raise ValueError("lasso points must be finite")
    area = sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )
    if abs(area) < 1.0e-8:
        raise ValueError("lasso polygon has no area")
    return points


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        if (y1 > y) == (y2 > y):
            continue
        crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        if x < crossing_x:
            inside = not inside
    return inside


def _segment_distance(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    px, py = point
    ax, ay = first
    bx, by = second
    dx = bx - ax
    dy = by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= 1.0e-12:
        return math.hypot(px - ax, py - ay)
    amount = ((px - ax) * dx + (py - ay) * dy) / length_squared
    amount = max(0.0, min(1.0, amount))
    closest_x = ax + amount * dx
    closest_y = ay + amount * dy
    return math.hypot(px - closest_x, py - closest_y)


def _edge_distance(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> float:
    return min(
        _segment_distance(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )


def _mirror(cell: Cell, axis: int) -> Cell:
    mirrored = list(cell)
    mirrored[axis] = -mirrored[axis]
    return tuple(mirrored)  # type: ignore[return-value]


def rounded_lasso_voxels(
    polygon: Iterable[Iterable[float]],
    *,
    depth_voxels: int = 6,
    palette_index: int = 0,
    symmetry_axis: int | None = None,
) -> dict[Cell, int]:
    """Create a rounded, marching-friendly voxel volume under a lasso.

    Polygon coordinates are expressed in voxel-center units.  The lasso is
    filled in X/Y; the Z thickness grows smoothly with the distance from the
    polygon edge, producing a rounded blob instead of a flat extrusion.
    """

    polygon = _polygon(polygon)
    if isinstance(depth_voxels, bool) or not isinstance(depth_voxels, int) or depth_voxels < 1:
        raise ValueError("blob depth must be a positive integer")
    if symmetry_axis is not None and symmetry_axis not in (0, 1, 2):
        raise ValueError("symmetry axis must be 0, 1, 2, or None")
    if isinstance(palette_index, bool) or not isinstance(palette_index, int) or palette_index < 0:
        raise ValueError("palette index must be a non-negative integer")

    minimum_x = math.floor(min(point[0] for point in polygon))
    maximum_x = math.ceil(max(point[0] for point in polygon))
    minimum_y = math.floor(min(point[1] for point in polygon))
    maximum_y = math.ceil(max(point[1] for point in polygon))
    estimated_columns = max(0, maximum_x - minimum_x + 1) * max(0, maximum_y - minimum_y + 1)
    if estimated_columns > MAX_LASSO_BLOB_VOXELS // max(1, depth_voxels * 2 + 1):
        raise ValueError("lasso blob is too large; reduce the lasso or blob depth")

    voxels: dict[Cell, int] = {}
    for x in range(minimum_x, maximum_x + 1):
        for y in range(minimum_y, maximum_y + 1):
            center = (float(x), float(y))
            if not _point_in_polygon(center, polygon):
                continue
            edge_distance = _edge_distance(center, polygon)
            rounded_distance = min(float(depth_voxels), edge_distance)
            radius = math.sqrt(max(0.0, 2.0 * depth_voxels * rounded_distance - rounded_distance * rounded_distance))
            z_radius = max(0, math.ceil(radius))
            for z in range(-z_radius, z_radius + 1):
                cell = (x, y, z)
                voxels[cell] = palette_index
                if symmetry_axis is not None:
                    voxels[_mirror(cell, symmetry_axis)] = palette_index

    if not voxels:
        raise ValueError("lasso did not cover any voxel centers")
    return voxels
