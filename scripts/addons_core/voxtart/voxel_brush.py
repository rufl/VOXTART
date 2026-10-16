# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sparse voxel brush and selection algorithms for interactive editing."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from .voxel import Cell


BRUSH_SHAPES = ("VOXEL", "LINE", "BOX", "SPHERE", "CYLINDER", "FACE")
AXES = ("X", "Y", "Z")
FACE_NORMALS = (
    (-1, 0, 0),
    (1, 0, 0),
    (0, -1, 0),
    (0, 1, 0),
    (0, 0, -1),
    (0, 0, 1),
)


def _cell(value: Iterable[int], name: str = "cell") -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError(f"{name} must contain three integers: {value!r}")
    return result  # type: ignore[return-value]


def _radius(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
        raise ValueError("brush radius must be an integer in [1, 64]")
    return value


def _axis(value: str) -> int:
    normalized = str(value).upper()
    if normalized not in AXES:
        raise ValueError(f"unsupported brush axis: {value!r}")
    return AXES.index(normalized)


def line_cells(start: Iterable[int], end: Iterable[int]) -> tuple[Cell, ...]:
    """Return a deterministic, gap-free integer line between two cells."""

    first = _cell(start, "line start")
    last = _cell(end, "line end")
    distance = max(abs(last[axis] - first[axis]) for axis in range(3))
    if distance == 0:
        return (first,)
    points: list[Cell] = []
    for step in range(distance + 1):
        values = []
        for axis in range(3):
            value = first[axis] + (last[axis] - first[axis]) * step / distance
            values.append(math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5))
        point = tuple(values)  # type: ignore[assignment]
        if not points or points[-1] != point:
            points.append(point)
    return tuple(points)


def box_cells(
    minimum: Iterable[int],
    maximum: Iterable[int],
    *,
    hollow: bool = False,
) -> tuple[Cell, ...]:
    """Return an inclusive box, optionally keeping only its shell."""

    lower = _cell(minimum, "box minimum")
    upper = _cell(maximum, "box maximum")
    if any(lower[axis] > upper[axis] for axis in range(3)):
        raise ValueError(f"box minimum must not exceed maximum: {lower!r} > {upper!r}")
    cells = []
    for x in range(lower[0], upper[0] + 1):
        for y in range(lower[1], upper[1] + 1):
            for z in range(lower[2], upper[2] + 1):
                if hollow and all(lower[axis] < (x, y, z)[axis] < upper[axis] for axis in range(3)):
                    continue
                cells.append((x, y, z))
    return tuple(cells)


def sphere_cells(
    center: Iterable[int],
    radius: int,
    *,
    hollow: bool = False,
) -> tuple[Cell, ...]:
    """Return integer cells inside a sphere centered on a voxel cell."""

    origin = _cell(center, "sphere center")
    radius = _radius(radius)
    outer = radius * radius
    inner = max(0, radius - 1) ** 2
    cells = []
    for x in range(origin[0] - radius, origin[0] + radius + 1):
        for y in range(origin[1] - radius, origin[1] + radius + 1):
            for z in range(origin[2] - radius, origin[2] + radius + 1):
                distance = sum((coordinate - origin[axis]) ** 2 for axis, coordinate in enumerate((x, y, z)))
                if distance > outer or (hollow and distance < inner):
                    continue
                cells.append((x, y, z))
    return tuple(cells)


def cylinder_cells(
    center: Iterable[int],
    radius: int,
    height: int,
    *,
    axis: str = "Z",
    hollow: bool = False,
) -> tuple[Cell, ...]:
    """Return a centered integer cylinder along one grid axis."""

    origin = _cell(center, "cylinder center")
    radius = _radius(radius)
    if isinstance(height, bool) or not isinstance(height, int) or height < 1 or height > 128:
        raise ValueError("cylinder height must be an integer in [1, 128]")
    axis_index = _axis(axis)
    outer = radius * radius
    inner = max(0, radius - 1) ** 2
    half_low = (height - 1) // 2
    half_high = height // 2
    cells = []
    for axial in range(-half_low, half_high + 1):
        for u in range(-radius, radius + 1):
            for v in range(-radius, radius + 1):
                if u * u + v * v > outer or (hollow and u * u + v * v < inner):
                    continue
                coordinate = list(origin)
                coordinate[axis_index] += axial
                other_axes = [index for index in range(3) if index != axis_index]
                coordinate[other_axes[0]] += u
                coordinate[other_axes[1]] += v
                cells.append(tuple(coordinate))  # type: ignore[arg-type]
    return tuple(cells)


def brush_cells(
    center: Iterable[int],
    *,
    shape: str = "VOXEL",
    radius: int = 1,
    hollow: bool = False,
    height: int | None = None,
    axis: str = "Z",
) -> tuple[Cell, ...]:
    """Create a brush stamp centered on one grid cell."""

    normalized = str(shape).upper()
    if normalized == "VOXEL":
        return (_cell(center),)
    if normalized == "SPHERE":
        return sphere_cells(center, radius, hollow=hollow)
    if normalized == "CYLINDER":
        return cylinder_cells(center, radius, height or radius * 2 + 1, axis=axis, hollow=hollow)
    if normalized == "BOX":
        origin = _cell(center)
        extent = _radius(radius)
        return box_cells(
            tuple(axis_value - extent for axis_value in origin),
            tuple(axis_value + extent for axis_value in origin),
            hollow=hollow,
        )
    if normalized == "LINE":
        return line_cells(center, center)
    raise ValueError(f"unsupported brush shape: {shape!r}")


def dilate_cells(cells: Iterable[Cell], radius: int = 1) -> tuple[Cell, ...]:
    """Grow a sparse cell set by a spherical brush radius."""

    source = tuple(_cell(cell) for cell in cells)
    if not source:
        return ()
    result: set[Cell] = set()
    for cell in source:
        result.update(sphere_cells(cell, radius))
    return tuple(sorted(result))


def mirror_cells(
    cells: Iterable[Cell],
    bounds: tuple[Cell, Cell] | None,
    *,
    axes: Iterable[bool] = (False, False, False),
) -> tuple[Cell, ...]:
    """Return original and mirrored cells across selected occupied bounds."""

    source = {_cell(cell) for cell in cells}
    if not source:
        return ()
    flags = tuple(bool(value) for value in axes)
    if len(flags) != 3:
        raise ValueError("mirror axes must contain three flags")
    if not any(flags):
        return tuple(sorted(source))
    if bounds is None:
        minimum = tuple(min(cell[axis] for cell in source) for axis in range(3))
        maximum = tuple(max(cell[axis] for cell in source) for axis in range(3))
    else:
        minimum = _cell(bounds[0], "mirror minimum")
        maximum = _cell(bounds[1], "mirror maximum")
    result = set(source)
    for cell in source:
        mirrored = list(cell)
        for axis, enabled in enumerate(flags):
            if enabled:
                mirrored[axis] = minimum[axis] + maximum[axis] - cell[axis]
        result.add(tuple(mirrored))  # type: ignore[arg-type]
    return tuple(sorted(result))


def select_box(cells: Iterable[Cell], minimum: Iterable[int], maximum: Iterable[int]) -> tuple[Cell, ...]:
    """Select occupied cells inside an inclusive box."""

    lower = _cell(minimum, "selection minimum")
    upper = _cell(maximum, "selection maximum")
    if any(lower[axis] > upper[axis] for axis in range(3)):
        raise ValueError("selection minimum must not exceed maximum")
    return tuple(
        sorted(
            cell
            for cell in (_cell(value) for value in cells)
            if all(lower[axis] <= cell[axis] <= upper[axis] for axis in range(3))
        )
    )


def select_color(voxels: Mapping[Cell, int], cell: Iterable[int]) -> tuple[Cell, ...]:
    """Select the six-connected region sharing the clicked voxel color."""

    start = _cell(cell)
    target = voxels.get(start)
    if target is None:
        return ()
    selected = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for axis in range(3):
            for delta in (-1, 1):
                neighbor = list(current)
                neighbor[axis] += delta
                neighbor_tuple = tuple(neighbor)  # type: ignore[assignment]
                if neighbor_tuple in selected or voxels.get(neighbor_tuple) != target:
                    continue
                selected.add(neighbor_tuple)
                pending.append(neighbor_tuple)
    return tuple(sorted(selected))


def _normal(value: Iterable[int]) -> Cell:
    result = _cell(value, "face normal")
    if result not in FACE_NORMALS:
        raise ValueError(f"face normal must be axis-aligned: {value!r}")
    return result


def face_brush_cells(
    center: Iterable[int],
    normal: Iterable[int],
    radius: int = 1,
    *,
    depth: int = 1,
    direction: int = 1,
    hollow: bool = False,
) -> tuple[Cell, ...]:
    """Return a square face brush extruded along an axis-aligned normal."""

    origin = _cell(center, "face brush center")
    normal_cell = _normal(normal)
    radius = _radius(radius)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 128:
        raise ValueError("face brush depth must be an integer in [1, 128]")
    if direction not in (-1, 1):
        raise ValueError("face brush direction must be -1 or 1")
    normal_axis = next(index for index, value in enumerate(normal_cell) if value)
    tangent_axes = tuple(index for index in range(3) if index != normal_axis)
    extent = radius - 1
    cells = []
    for layer in range(depth):
        for first in range(-extent, extent + 1):
            for second in range(-extent, extent + 1):
                on_edge = abs(first) == extent or abs(second) == extent or layer in (0, depth - 1)
                if hollow and not on_edge:
                    continue
                coordinate = list(origin)
                coordinate[normal_axis] += normal_cell[normal_axis] * layer * direction
                coordinate[tangent_axes[0]] += first
                coordinate[tangent_axes[1]] += second
                cells.append(tuple(coordinate))  # type: ignore[arg-type]
    return tuple(cells)


def select_connected(voxels: Mapping[Cell, int], cell: Iterable[int]) -> tuple[Cell, ...]:
    """Select the six-connected occupied component containing a cell."""

    start = _cell(cell)
    if start not in voxels:
        return ()
    selected = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for axis in range(3):
            for delta in (-1, 1):
                neighbor = list(current)
                neighbor[axis] += delta
                normalized = tuple(neighbor)  # type: ignore[assignment]
                if normalized in selected or normalized not in voxels:
                    continue
                selected.add(normalized)
                pending.append(normalized)
    return tuple(sorted(selected))


def select_flat_surface(
    voxels: Mapping[Cell, int],
    cell: Iterable[int],
    normal: Iterable[int],
) -> tuple[Cell, ...]:
    """Select the connected exposed cells on the clicked flat grid plane."""

    start = _cell(cell)
    normal_cell = _normal(normal)
    if start not in voxels:
        return ()
    normal_axis = next(index for index, value in enumerate(normal_cell) if value)
    plane = start[normal_axis]

    def exposed(candidate: Cell) -> bool:
        neighbor = list(candidate)
        neighbor[normal_axis] += normal_cell[normal_axis]
        return tuple(neighbor) not in voxels

    if not exposed(start):
        return ()
    selected = {start}
    pending = [start]
    tangent_axes = tuple(index for index in range(3) if index != normal_axis)
    while pending:
        current = pending.pop()
        for axis in tangent_axes:
            for delta in (-1, 1):
                neighbor = list(current)
                neighbor[axis] += delta
                normalized = tuple(neighbor)  # type: ignore[assignment]
                if (
                    normalized in selected
                    or normalized not in voxels
                    or normalized[normal_axis] != plane
                    or not exposed(normalized)
                ):
                    continue
                selected.add(normalized)
                pending.append(normalized)
    return tuple(sorted(selected))


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    px, py = point
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[index - 1]
        if (y1 > py) != (y2 > py):
            crossing = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px <= crossing:
                inside = not inside
    return inside


def select_lasso(
    projected: Mapping[Cell, tuple[float, float]],
    polygon: Iterable[tuple[float, float]],
) -> tuple[Cell, ...]:
    """Select projected cells whose centers lie inside a lasso polygon."""

    outline = tuple((float(point[0]), float(point[1])) for point in polygon)
    if len(outline) < 3:
        return ()
    return tuple(sorted(cell for cell, point in projected.items() if _point_in_polygon(point, outline)))


def select_circle(
    projected: Mapping[Cell, tuple[float, float]],
    center: tuple[float, float],
    radius: float,
) -> tuple[Cell, ...]:
    """Select projected cells inside a screen-space circle."""

    radius = float(radius)
    if not math.isfinite(radius) or radius <= 0.0:
        return ()
    center_x, center_y = float(center[0]), float(center[1])
    radius_squared = radius * radius
    return tuple(
        sorted(
            cell
            for cell, point in projected.items()
            if (point[0] - center_x) ** 2 + (point[1] - center_y) ** 2 <= radius_squared
        )
    )


__all__ = [
    "AXES",
    "BRUSH_SHAPES",
    "FACE_NORMALS",
    "box_cells",
    "brush_cells",
    "cylinder_cells",
    "dilate_cells",
    "face_brush_cells",
    "line_cells",
    "mirror_cells",
    "select_box",
    "select_circle",
    "select_color",
    "select_connected",
    "select_flat_surface",
    "select_lasso",
    "sphere_cells",
]
