# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic volume deformation primitives for VOXTART blob assets."""

from __future__ import annotations

import math
from typing import Iterable

from .edit import EditReport, edit_asset
from .voxel import Cell, VoxelAsset


BLOFORMER_MODES = ("ADD", "SUBTRACT", "CARVE", "INFLATE", "ERODE", "SMOOTH")
BLOFORMER_SHAPES = ("SPHERE", "BOX", "CYLINDER")
BLOFORMER_AXES = ("X", "Y", "Z")
MAX_BLOFORMER_CELLS = 2_000_000


def _vector(value: Iterable[float], name: str) -> tuple[float, float, float]:
    result = tuple(float(axis) for axis in value)
    if len(result) != 3 or any(not math.isfinite(axis) for axis in result):
        raise ValueError(f"Bloformer {name} must contain three finite values")
    return result  # type: ignore[return-value]


def _axis(value: str) -> int:
    value = str(value or "Z").upper()
    if value not in BLOFORMER_AXES:
        raise ValueError(f"unsupported Bloformer axis: {value}")
    return BLOFORMER_AXES.index(value)


def _normalize_mode(value: str) -> str:
    value = str(value or "ADD").upper()
    if value not in BLOFORMER_MODES:
        raise ValueError(f"unsupported Bloformer mode: {value}")
    return value


def _normalize_shape(value: str) -> str:
    value = str(value or "SPHERE").upper()
    if value not in BLOFORMER_SHAPES:
        raise ValueError(f"unsupported Bloformer shape: {value}")
    return value


def _inside_shape(
    cell: Cell,
    center: tuple[float, float, float],
    radius: float,
    shape: str,
    axis: int,
) -> bool:
    offset = tuple(cell[index] - center[index] for index in range(3))
    if shape == "SPHERE":
        return sum(axis_value * axis_value for axis_value in offset) <= radius * radius
    if shape == "BOX":
        return all(abs(axis_value) <= radius for axis_value in offset)
    axial = offset[axis]
    radial = sum(offset[index] * offset[index] for index in range(3) if index != axis)
    return abs(axial) <= radius and radial <= radius * radius


def volume_cells(
    center: Iterable[float],
    radius: float,
    *,
    shape: str = "SPHERE",
    axis: str = "Z",
) -> frozenset[Cell]:
    """Return deterministic voxel cells covered by a primitive volume."""

    center = _vector(center, "center")
    radius = float(radius)
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("Bloformer radius must be finite and positive")
    shape = _normalize_shape(shape)
    axis_index = _axis(axis)
    extent = math.ceil(radius)
    span = extent * 2 + 3
    if span ** 3 > MAX_BLOFORMER_CELLS:
        raise ValueError("Bloformer volume is too large; reduce the radius")
    minimum = tuple(math.floor(center[index] - radius) for index in range(3))
    maximum = tuple(math.ceil(center[index] + radius) for index in range(3))
    cells: set[Cell] = set()
    for x in range(minimum[0], maximum[0] + 1):
        for y in range(minimum[1], maximum[1] + 1):
            for z in range(minimum[2], maximum[2] + 1):
                cell = (x, y, z)
                if _inside_shape(cell, center, radius, shape, axis_index):
                    cells.add(cell)
    return frozenset(cells)


def _neighbors(cell: Cell) -> tuple[Cell, ...]:
    result: list[Cell] = []
    for axis in range(3):
        for delta in (-1, 1):
            neighbor = list(cell)
            neighbor[axis] += delta
            result.append(tuple(neighbor))  # type: ignore[arg-type]
    return tuple(result)


def _shell_cells(volume: frozenset[Cell], thickness: float) -> frozenset[Cell]:
    layers = max(1, math.ceil(float(thickness)))
    remaining = set(volume)
    shell: set[Cell] = set()
    for _layer in range(layers):
        boundary = {
            cell
            for cell in remaining
            if any(neighbor not in remaining for neighbor in _neighbors(cell))
        }
        if not boundary:
            break
        shell.update(boundary)
        remaining.difference_update(boundary)
    return frozenset(shell)


def apply_bloformer(
    asset: VoxelAsset,
    *,
    center: Iterable[float] = (0.0, 0.0, 0.0),
    radius: float = 3.0,
    mode: str = "ADD",
    shape: str = "SPHERE",
    axis: str = "Z",
    thickness: float = 1.0,
    palette_index: int = 0,
    overwrite: bool = False,
    protected: Iterable[Cell] = (),
    allow_protected: bool = False,
) -> EditReport:
    """Apply one volume deformation to a voxel asset atomically.

    ``ADD`` places a primitive volume, ``SUBTRACT`` removes it, and ``CARVE``
    removes only its outside shell. ``INFLATE`` grows occupied cells into the
    brush, ``ERODE`` removes exposed cells, and ``SMOOTH`` applies a six-neighbor
    majority pass. All modes are bounded by the requested primitive volume.
    """

    mode = _normalize_mode(mode)
    shape = _normalize_shape(shape)
    center = _vector(center, "center")
    radius = float(radius)
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("Bloformer radius must be finite and positive")
    thickness = float(thickness)
    if not math.isfinite(thickness) or thickness <= 0.0:
        raise ValueError("Bloformer carve thickness must be finite and positive")
    if isinstance(palette_index, bool) or not isinstance(palette_index, int):
        raise ValueError("Bloformer palette index must be an integer")
    asset._palette_index(palette_index)
    brush = volume_cells(center, radius, shape=shape, axis=axis)
    source = dict(asset.voxels)

    if mode == "CARVE":
        targets = _shell_cells(brush, thickness)
    elif mode == "INFLATE":
        targets = frozenset(
            cell
            for cell in brush
            if cell not in source and any(neighbor in source for neighbor in _neighbors(cell))
        )
    elif mode == "ERODE":
        targets = frozenset(
            cell
            for cell in brush
            if cell in source and any(neighbor not in source for neighbor in _neighbors(cell))
        )
    else:
        targets = brush

    def mutate(transaction) -> None:
        if mode == "ADD":
            for cell in sorted(targets):
                transaction.add(cell, palette_index, overwrite=overwrite)
            return
        if mode in ("SUBTRACT", "CARVE", "ERODE"):
            for cell in sorted(targets):
                transaction.remove(cell)
            return
        if mode == "INFLATE":
            for cell in sorted(targets):
                transaction.add(cell, palette_index, overwrite=False)
            return
        for cell in sorted(targets):
            occupied = cell in source
            occupied_neighbors = sum(neighbor in source for neighbor in _neighbors(cell))
            if occupied and occupied_neighbors <= 2:
                transaction.remove(cell)
            elif not occupied and occupied_neighbors >= 4:
                transaction.add(cell, palette_index, overwrite=False)

    return edit_asset(
        asset,
        f"bloformer_{mode.lower()}_{shape.lower()}",
        mutate,
        protected=protected,
        allow_protected=allow_protected,
    )


__all__ = [
    "BLOFORMER_AXES",
    "BLOFORMER_MODES",
    "BLOFORMER_SHAPES",
    "MAX_BLOFORMER_CELLS",
    "apply_bloformer",
    "volume_cells",
]
