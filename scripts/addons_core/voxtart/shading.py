# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Whole-voxel shading and deterministic dithering algorithms.

Every operation in this module changes only a voxel's palette index.  It never
creates sub-voxel geometry, interpolated mesh colors, or fractional occupancy.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

from .palette import nearest_palette_index
from .voxel import Cell, Color, VoxelAsset


_NEIGHBORS: tuple[tuple[Cell, Cell], ...] = (
    ((-1, 0, 0), (-1, 0, 0)),
    ((1, 0, 0), (1, 0, 0)),
    ((0, -1, 0), (0, -1, 0)),
    ((0, 1, 0), (0, 1, 0)),
    ((0, 0, -1), (0, 0, -1)),
    ((0, 0, 1), (0, 0, 1)),
)
DITHER_PATTERNS = ("CHECKER", "BAYER2", "BAYER4", "ORDERED", "CIRCUIT", "NOISE")
DITHER_BAYER_SIZES = (2, 4, 8)
DITHER_AXES = ("XY", "XZ", "YZ")


@dataclass(frozen=True, slots=True)
class ShadingReport:
    """Stable summary shared by Blender operators and scripted callers."""

    operation: str
    considered: int
    changed: int
    surface_voxels: int
    skipped_protected: int

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


def _normalize(vector: Iterable[float], *, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    values = tuple(float(value) for value in vector)
    if len(values) != 3:
        raise ValueError("vectors must contain three values")
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1.0e-12:
        return fallback
    return tuple(value / length for value in values)  # type: ignore[return-value]


def _cell(value: Iterable[int]) -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError(f"voxel cells must contain three integers: {value!r}")
    return result  # type: ignore[return-value]


def _surface_cells(asset: VoxelAsset) -> tuple[Cell, ...]:
    occupied = set(asset.voxels)
    return tuple(
        cell
        for cell in sorted(occupied)
        if any(tuple(cell[axis] + offset[axis] for axis in range(3)) not in occupied for offset, _normal in _NEIGHBORS)
    )


def surface_cells(asset: VoxelAsset) -> tuple[Cell, ...]:
    """Return occupied cells exposed on at least one axis face."""

    return _surface_cells(asset)


def surface_normal(asset: VoxelAsset, cell: Iterable[int]) -> tuple[float, float, float]:
    """Return a normalized outward normal assembled from exposed voxel faces."""

    normalized = _cell(cell)
    if normalized not in asset.voxels:
        raise ValueError(f"cell is not occupied: {normalized!r}")
    occupied = asset.voxels
    normal = [0.0, 0.0, 0.0]
    for offset, face_normal in _NEIGHBORS:
        neighbor = tuple(normalized[axis] + offset[axis] for axis in range(3))
        if neighbor not in occupied:
            for axis in range(3):
                normal[axis] += face_normal[axis]
    return _normalize(normal, fallback=(0.0, 0.0, 1.0))


def _center_normal(asset: VoxelAsset, cell: Cell) -> tuple[float, float, float]:
    bounds = asset.bounds
    if bounds is None:
        return (0.0, 0.0, 1.0)
    minimum, maximum = bounds
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    return _normalize(
        tuple(cell[axis] - center[axis] for axis in range(3)),
        fallback=(0.0, 0.0, 1.0),
    )


def _illumination(
    normal: tuple[float, float, float],
    light_direction: tuple[float, float, float],
    ambient: float,
) -> float:
    light = _normalize(light_direction, fallback=(0.35, -0.45, 0.82))
    diffuse = max(0.0, sum(normal[axis] * light[axis] for axis in range(3)))
    return ambient + (1.0 - ambient) * diffuse


def _group_key(cell: Cell, group_size: int) -> tuple[int, int, int]:
    return tuple(cell[axis] // group_size for axis in range(3))  # type: ignore[return-value]


def _candidate_cells(
    asset: VoxelAsset,
    cells: Iterable[Cell] | None,
    *,
    surface_only: bool,
) -> tuple[Cell, ...]:
    available = set(_surface_cells(asset) if surface_only else asset.voxels)
    if cells is None:
        return tuple(sorted(available))
    requested = {_cell(cell) for cell in cells}
    return tuple(sorted(requested & available))


def _clamp_color(color: Sequence[float]) -> Color:
    if len(color) != 4:
        raise ValueError("colors must contain four channels")
    return tuple(max(0.0, min(1.0, float(channel))) for channel in color)  # type: ignore[return-value]


def shade_voxels(
    asset: VoxelAsset,
    *,
    cells: Iterable[Cell] | None = None,
    surface_only: bool = True,
    light_direction: Iterable[float] = (0.35, -0.45, 0.82),
    ambient: float = 0.35,
    strength: float = 0.8,
    levels: int = 4,
    group_size: int = 1,
    protected: Iterable[Cell] = (),
    allow_protected: bool = False,
) -> ShadingReport:
    """Shade occupied cells by quantizing lit colors back to whole palette entries.

    ``group_size`` makes neighboring voxel blocks share the same light level;
    the geometry and occupied-cell set remain unchanged.
    """

    if not 0.0 <= float(ambient) <= 1.0:
        raise ValueError("ambient must be in [0, 1]")
    if not 0.0 <= float(strength) <= 1.0:
        raise ValueError("strength must be in [0, 1]")
    if isinstance(levels, bool) or not isinstance(levels, int) or not 2 <= levels <= 64:
        raise ValueError("levels must be an integer in [2, 64]")
    if isinstance(group_size, bool) or not isinstance(group_size, int) or not 1 <= group_size <= 64:
        raise ValueError("group_size must be an integer in [1, 64]")

    selected = _candidate_cells(asset, cells, surface_only=surface_only)
    protected_cells = {_cell(cell) for cell in protected}
    surface_set = set(_surface_cells(asset))
    normals = {
        cell: surface_normal(asset, cell) if cell in surface_set else _center_normal(asset, cell)
        for cell in selected
    }
    light = tuple(float(value) for value in light_direction)
    group_illumination: dict[tuple[int, int, int], list[float]] = {}
    for cell in selected:
        normal = normals[cell]
        value = _illumination(normal, light, float(ambient))
        group_illumination.setdefault(_group_key(cell, group_size), []).append(value)
    group_values = {
        key: sum(values) / len(values)
        for key, values in group_illumination.items()
    }

    changed = 0
    skipped = 0
    surface_count = len(_surface_cells(asset))
    for cell in selected:
        if cell in protected_cells and not allow_protected:
            skipped += 1
            continue
        value = group_values[_group_key(cell, group_size)]
        value = round(value * (levels - 1)) / (levels - 1)
        factor = 1.0 + float(strength) * ((value - 0.5) * 1.4)
        source = asset.palette[asset.voxels[cell]]
        target = _clamp_color((source[0] * factor, source[1] * factor, source[2] * factor, source[3]))
        replacement = nearest_palette_index(target, asset.palette)
        if replacement != asset.voxels[cell]:
            asset.voxels[cell] = replacement
            changed += 1
    return ShadingReport("shade", len(selected), changed, surface_count, skipped)


def _plane_coordinates(group: tuple[int, int, int], axis: str) -> tuple[int, int]:
    if axis == "XY":
        return group[0], group[1]
    if axis == "XZ":
        return group[0], group[2]
    if axis == "YZ":
        return group[1], group[2]
    raise ValueError(f"unsupported dither axis: {axis!r}")


def _validate_bayer_size(size: int) -> int:
    if isinstance(size, bool) or not isinstance(size, int) or size not in DITHER_BAYER_SIZES:
        choices = ", ".join(str(value) for value in DITHER_BAYER_SIZES)
        raise ValueError(f"Bayer matrix size must be one of: {choices}")
    return size


def _bayer_value(group: tuple[int, int, int], size: int, axis: str) -> float:
    size = _validate_bayer_size(size)
    x, y = _plane_coordinates(group, axis)
    if size == 2:
        matrix = ((0, 2), (3, 1))
    elif size == 4:
        matrix = (
            (0, 8, 2, 10),
            (12, 4, 14, 6),
            (3, 11, 1, 9),
            (15, 7, 13, 5),
        )
    else:
        matrix = (
            (0, 32, 8, 40, 2, 34, 10, 42),
            (48, 16, 56, 24, 50, 18, 58, 26),
            (12, 44, 4, 36, 14, 46, 6, 38),
            (60, 28, 52, 20, 62, 30, 54, 22),
            (3, 35, 11, 43, 1, 33, 9, 41),
            (51, 19, 59, 27, 49, 17, 57, 25),
            (15, 47, 7, 39, 13, 45, 5, 37),
            (63, 31, 55, 23, 61, 29, 53, 21),
        )
    return (matrix[y % size][x % size] + 0.5) / (size * size)


def _noise_value(group: tuple[int, int, int]) -> float:
    value = (
        group[0] * 0x1F123BB5
        + group[1] * 0x5F356495
        + group[2] * 0x2C1B3C6D
        + 0x7FEB352D
    ) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x45D9F3B) & 0xFFFFFFFF
    value ^= value >> 16
    return (value & 0xFFFFFFFF) / 4294967296.0


def dither_value(
    group: Iterable[int],
    pattern: str = "BAYER4",
    axis: str = "XY",
    bayer_size: int = 4,
    noise_amount: float = 0.0,
    threshold_bias: float = 0.0,
) -> float:
    """Return a deterministic threshold in ``[0, 1)`` for one voxel group."""

    if not 0.0 <= float(noise_amount) <= 1.0:
        raise ValueError("noise_amount must be in [0, 1]")
    if not -1.0 <= float(threshold_bias) <= 1.0:
        raise ValueError("threshold_bias must be in [-1, 1]")
    normalized = _cell(group)
    pattern = str(pattern).upper()
    axis = str(axis).upper()
    if pattern == "CHECKER":
        threshold = 0.25 if sum(normalized) % 2 == 0 else 0.75
    elif pattern == "BAYER2":
        threshold = _bayer_value(normalized, 2, axis)
    elif pattern == "BAYER4":
        threshold = _bayer_value(normalized, 4, axis)
    elif pattern in {"ORDERED", "CIRCUIT"}:
        threshold = _bayer_value(normalized, _validate_bayer_size(bayer_size), axis)
        if pattern == "CIRCUIT" and noise_amount <= 0.0:
            noise_amount = 0.35
    elif pattern == "NOISE":
        threshold = _noise_value(normalized)
    else:
        raise ValueError(f"unsupported dither pattern: {pattern!r}")
    if pattern != "NOISE" and noise_amount > 0.0:
        threshold = (1.0 - noise_amount) * threshold + noise_amount * _noise_value(normalized)
    return max(0.0, min(1.0 - 1.0e-12, threshold + float(threshold_bias)))


def dither_voxels(
    asset: VoxelAsset,
    low_index: int,
    high_index: int,
    *,
    density: float = 0.5,
    pattern: str = "BAYER4",
    axis: str = "XY",
    bayer_size: int = 4,
    noise_amount: float = 0.0,
    threshold_bias: float = 0.0,
    group_size: int = 1,
    cells: Iterable[Cell] | None = None,
    surface_only: bool = True,
    source_indices: Iterable[int] | None = None,
    protected: Iterable[Cell] = (),
    allow_protected: bool = False,
) -> ShadingReport:
    """Dither between two palette entries, assigning one color per whole voxel.

    With ``group_size > 1`` the threshold is shared by a block of voxels, so a
    dither cluster is still composed exclusively of complete voxel cells.
    """

    if not 0.0 <= float(density) <= 1.0:
        raise ValueError("density must be in [0, 1]")
    if isinstance(group_size, bool) or not isinstance(group_size, int) or not 1 <= group_size <= 64:
        raise ValueError("group_size must be an integer in [1, 64]")
    pattern = str(pattern).upper()
    if pattern not in DITHER_PATTERNS:
        raise ValueError(f"unsupported dither pattern: {pattern!r}")
    axis = str(axis).upper()
    if axis not in DITHER_AXES:
        raise ValueError(f"unsupported dither axis: {axis!r}")
    if pattern in {"ORDERED", "CIRCUIT"}:
        bayer_size = _validate_bayer_size(bayer_size)
    if not 0.0 <= float(noise_amount) <= 1.0:
        raise ValueError("noise_amount must be in [0, 1]")
    if not -1.0 <= float(threshold_bias) <= 1.0:
        raise ValueError("threshold_bias must be in [-1, 1]")
    if not 0 <= int(low_index) < len(asset.palette) or not 0 <= int(high_index) < len(asset.palette):
        raise ValueError("dither palette index is outside the asset palette")
    low_index = int(low_index)
    high_index = int(high_index)

    selected = _candidate_cells(asset, cells, surface_only=surface_only)
    protected_cells = {_cell(cell) for cell in protected}
    source_filter = None if source_indices is None else {int(index) for index in source_indices}
    changed = 0
    skipped = 0
    for cell in selected:
        if source_filter is not None and asset.voxels[cell] not in source_filter:
            continue
        if cell in protected_cells and not allow_protected:
            skipped += 1
            continue
        group = _group_key(cell, group_size)
        replacement = high_index if float(density) > dither_value(
            group,
            pattern,
            axis,
            bayer_size=bayer_size,
            noise_amount=noise_amount,
            threshold_bias=threshold_bias,
        ) else low_index
        if replacement != asset.voxels[cell]:
            asset.voxels[cell] = replacement
            changed += 1
    return ShadingReport("dither", len(selected), changed, len(_surface_cells(asset)), skipped)


__all__ = [
    "DITHER_AXES",
    "DITHER_BAYER_SIZES",
    "DITHER_PATTERNS",
    "ShadingReport",
    "dither_value",
    "dither_voxels",
    "shade_voxels",
    "surface_cells",
    "surface_normal",
]
