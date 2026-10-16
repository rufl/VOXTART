# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic sparse-volume transforms inspired by voxel conversion tools.

The implementation is intentionally independent of any external voxel editor.
Transforms operate on integer cell centers, keep palette indices stable, and
return new assets instead of mutating their inputs.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from .voxel import Cell, VoxelAsset, VoxelSocket


TRANSFORM_AXES = ("X", "Y", "Z")
ROTATION_DEGREES = (0, 90, 180, 270)
MAX_CHUNK_SIZE = 256
Bounds = tuple[Cell, Cell]


def _cell(value: Iterable[int], field: str = "cell") -> Cell:
    result = tuple(value)
    if len(result) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in result):
        raise ValueError(f"{field} must contain three integers: {value!r}")
    return result  # type: ignore[return-value]


def _offset(value: Iterable[int]) -> Cell:
    return _cell(value, "offset")


def _axis(value: str) -> int:
    normalized = str(value or "").upper()
    if normalized not in TRANSFORM_AXES:
        raise ValueError(f"unsupported transform axis: {value!r}")
    return TRANSFORM_AXES.index(normalized)


def _degrees(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in ROTATION_DEGREES:
        choices = ", ".join(str(degree) for degree in ROTATION_DEGREES)
        raise ValueError(f"rotation degrees must be one of: {choices}")
    return value


def _bounds(value: Bounds | None) -> Bounds | None:
    if value is None:
        return None
    minimum = _cell(value[0], "minimum")
    maximum = _cell(value[1], "maximum")
    if any(minimum[axis] > maximum[axis] for axis in range(3)):
        raise ValueError(f"invalid transform bounds: {minimum!r} > {maximum!r}")
    return minimum, maximum


def _asset_bounds(asset: VoxelAsset, reference_bounds: Bounds | None) -> Bounds | None:
    normalized = _bounds(reference_bounds)
    if normalized is not None:
        return normalized
    return asset.bounds


def _inside(cell: Cell, bounds: Bounds) -> bool:
    minimum, maximum = bounds
    return all(minimum[axis] <= cell[axis] <= maximum[axis] for axis in range(3))


def _result(
    asset: VoxelAsset,
    voxels: Mapping[Cell, int],
    sockets: Iterable[VoxelSocket],
    operation: str,
    **metadata: object,
) -> VoxelAsset:
    provenance = dict(asset.provenance)
    provenance[f"volume_{operation}"] = "true"
    provenance.update({f"volume_{key}": str(value) for key, value in metadata.items()})
    return VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(voxels),
        sockets=list(sockets),
        provenance=provenance,
        palette_materials=asset.get_palette_materials(),
    )


def _mirror_cell(cell: Cell, bounds: Bounds, axis: int) -> Cell:
    minimum, maximum = bounds
    result = list(cell)
    result[axis] = minimum[axis] + maximum[axis] - cell[axis]
    return tuple(result)  # type: ignore[return-value]


def mirror_asset(
    asset: VoxelAsset,
    axis: str,
    *,
    reference_bounds: Bounds | None = None,
) -> VoxelAsset:
    """Reflect cells and sockets across the occupied bounding-box midpoint."""

    axis_index = _axis(axis)
    bounds = _asset_bounds(asset, reference_bounds)
    if bounds is None:
        return _result(asset, asset.voxels, asset.sockets, "mirror", axis=TRANSFORM_AXES[axis_index])
    voxels = {
        _mirror_cell(cell, bounds, axis_index): palette_index
        for cell, palette_index in asset.voxels.items()
    }
    sockets = []
    for socket in asset.sockets:
        normal = list(socket.normal)
        normal[axis_index] *= -1
        sockets.append(
            VoxelSocket(
                _mirror_cell(socket.position, bounds, axis_index),
                tuple(normal),  # type: ignore[arg-type]
                socket.tag,
                socket.rotation,
            )
        )
    return _result(asset, voxels, sockets, "mirror", axis=TRANSFORM_AXES[axis_index])


def _rotate_local(local: Cell, dimensions: Cell, axis: int, turns: int) -> Cell:
    x, y, z = local
    nx, ny, nz = dimensions
    if axis == 0:
        if turns == 1:
            return x, z, ny - 1 - y
        if turns == 2:
            return x, ny - 1 - y, nz - 1 - z
        return x, nz - 1 - z, y
    if axis == 1:
        if turns == 1:
            return z, y, nx - 1 - x
        if turns == 2:
            return nx - 1 - x, y, nz - 1 - z
        return nz - 1 - z, y, x
    if turns == 1:
        return ny - 1 - y, x, z
    if turns == 2:
        return nx - 1 - x, ny - 1 - y, z
    return y, nx - 1 - x, z


def _rotate_direction(normal: Cell, axis: int, turns: int) -> Cell:
    x, y, z = normal
    if axis == 0:
        if turns == 1:
            return x, z, -y
        if turns == 2:
            return x, -y, -z
        return x, -z, y
    if axis == 1:
        if turns == 1:
            return z, y, -x
        if turns == 2:
            return -x, y, -z
        return -z, y, x
    if turns == 1:
        return -y, x, z
    if turns == 2:
        return -x, -y, z
    return y, -x, z


def rotate_asset(
    asset: VoxelAsset,
    axis: str,
    degrees: int,
    *,
    reference_bounds: Bounds | None = None,
) -> VoxelAsset:
    """Rotate by a right angle while anchoring the bounding-box minimum.

    Anchoring the lower corner keeps every result on the integer voxel lattice,
    including rectangular volumes whose two rotated dimensions have different
    parity. The caller can translate afterward when a different pivot is needed.
    """

    axis_index = _axis(axis)
    degrees = _degrees(degrees)
    turns = (degrees // 90) % 4
    bounds = _asset_bounds(asset, reference_bounds)
    if turns == 0 or bounds is None:
        return _result(
            asset,
            asset.voxels,
            asset.sockets,
            "rotate",
            axis=TRANSFORM_AXES[axis_index],
            degrees=degrees,
        )
    minimum, maximum = bounds
    dimensions = tuple(maximum[index] - minimum[index] + 1 for index in range(3))

    def transform(cell: Cell) -> Cell:
        local = tuple(cell[index] - minimum[index] for index in range(3))
        rotated = _rotate_local(local, dimensions, axis_index, turns)
        return tuple(minimum[index] + rotated[index] for index in range(3))  # type: ignore[return-value]

    voxels = {transform(cell): palette_index for cell, palette_index in asset.voxels.items()}
    sockets = [
        VoxelSocket(
            transform(socket.position),
            _rotate_direction(socket.normal, axis_index, turns),
            socket.tag,
            socket.rotation,
        )
        for socket in asset.sockets
    ]
    return _result(
        asset,
        voxels,
        sockets,
        "rotate",
        axis=TRANSFORM_AXES[axis_index],
        degrees=degrees,
    )


def translate_asset(asset: VoxelAsset, offset: Iterable[int]) -> VoxelAsset:
    """Translate every cell and socket by an integer voxel offset."""

    normalized = _offset(offset)
    voxels = {
        tuple(cell[axis] + normalized[axis] for axis in range(3)): palette_index
        for cell, palette_index in asset.voxels.items()
    }
    sockets = [
        VoxelSocket(
            tuple(socket.position[axis] + normalized[axis] for axis in range(3)),
            socket.normal,
            socket.tag,
            socket.rotation,
        )
        for socket in asset.sockets
    ]
    return _result(asset, voxels, sockets, "translate", offset=normalized)


def crop_asset(
    asset: VoxelAsset,
    minimum: Iterable[int] | None = None,
    maximum: Iterable[int] | None = None,
    *,
    rebase: bool = False,
) -> VoxelAsset:
    """Keep a bounded region and optionally move its minimum cell to the origin."""

    if (minimum is None) != (maximum is None):
        raise ValueError("crop minimum and maximum must be supplied together")
    if minimum is None:
        bounds = asset.bounds
    else:
        bounds = _bounds((_cell(minimum, "minimum"), _cell(maximum, "maximum")))
    if bounds is None:
        return _result(asset, {}, (), "crop", rebase=bool(rebase))

    minimum_cell, maximum_cell = bounds
    shift = minimum_cell if rebase else (0, 0, 0)
    voxels = {
        tuple(cell[axis] - shift[axis] for axis in range(3)): palette_index
        for cell, palette_index in asset.voxels.items()
        if _inside(cell, bounds)
    }
    sockets = [
        VoxelSocket(
            tuple(socket.position[axis] - shift[axis] for axis in range(3)),
            socket.normal,
            socket.tag,
            socket.rotation,
        )
        for socket in asset.sockets
        if _inside(socket.position, bounds)
    ]
    return _result(
        asset,
        voxels,
        sockets,
        "crop",
        minimum=minimum_cell,
        maximum=maximum_cell,
        rebase=bool(rebase),
    )


def split_asset(asset: VoxelAsset, chunk_size: int = 32) -> dict[Cell, VoxelAsset]:
    """Split occupied cells into local-coordinate chunks keyed by world origin."""

    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(f"chunk size must be an integer in [1, {MAX_CHUNK_SIZE}]")
    groups: dict[Cell, dict[Cell, int]] = {}
    for cell, palette_index in sorted(asset.voxels.items()):
        origin = tuple(math.floor(cell[axis] / chunk_size) * chunk_size for axis in range(3))
        local = tuple(cell[axis] - origin[axis] for axis in range(3))
        groups.setdefault(origin, {})[local] = palette_index

    result: dict[Cell, VoxelAsset] = {}
    for origin in sorted(groups):
        local_voxels = groups[origin]
        local_sockets = [
            VoxelSocket(
                tuple(socket.position[axis] - origin[axis] for axis in range(3)),
                socket.normal,
                socket.tag,
                socket.rotation,
            )
            for socket in asset.sockets
            if tuple(math.floor(socket.position[axis] / chunk_size) * chunk_size for axis in range(3)) == origin
        ]
        result[origin] = _result(
            asset,
            local_voxels,
            local_sockets,
            "split",
            chunk_size=chunk_size,
            chunk_origin=origin,
        )
    return result


__all__ = [
    "Bounds",
    "MAX_CHUNK_SIZE",
    "ROTATION_DEGREES",
    "TRANSFORM_AXES",
    "crop_asset",
    "mirror_asset",
    "rotate_asset",
    "split_asset",
    "translate_asset",
]
