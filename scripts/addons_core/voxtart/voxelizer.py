# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic postprocessing for mesh-to-voxel conversion."""

from __future__ import annotations

from typing import Mapping

from .voxel import Cell, VoxelAsset


VOXELIZER_MODES = ("SOLID", "SURFACE", "SHELL")
MAX_SHELL_THICKNESS = 64
_NEIGHBORS = (
    (-1, 0, 0),
    (1, 0, 0),
    (0, -1, 0),
    (0, 1, 0),
    (0, 0, -1),
    (0, 0, 1),
)


def _mode(mode: str) -> str:
    normalized = str(mode).upper()
    if normalized not in VOXELIZER_MODES:
        raise ValueError(f"unsupported voxelizer mode: {mode!r}")
    return normalized


def _thickness(thickness: int) -> int:
    result = int(thickness)
    if isinstance(thickness, bool) or result != thickness or not 1 <= result <= MAX_SHELL_THICKNESS:
        raise ValueError(
            f"voxelizer shell thickness must be an integer between 1 and {MAX_SHELL_THICKNESS}"
        )
    return result


def _erode(cells: set[Cell]) -> set[Cell]:
    return {
        cell
        for cell in cells
        if all(
            (cell[0] + offset[0], cell[1] + offset[1], cell[2] + offset[2]) in cells
            for offset in _NEIGHBORS
        )
    }


def filter_voxels(
    voxels: Mapping[Cell, int],
    *,
    mode: str = "SOLID",
    shell_thickness: int = 1,
) -> dict[Cell, int]:
    """Return solid, one-cell surface, or multi-cell shell occupancy."""

    normalized_mode = _mode(mode)
    thickness = _thickness(shell_thickness)
    source = dict(voxels)
    if normalized_mode == "SOLID":
        return source
    interior = set(source)
    for _ in range(1 if normalized_mode == "SURFACE" else thickness):
        interior = _erode(interior)
        if not interior:
            break
    return {
        cell: palette_index
        for cell, palette_index in source.items()
        if cell not in interior
    }


def apply_voxelizer_mode(
    asset: VoxelAsset,
    *,
    mode: str = "SOLID",
    shell_thickness: int = 1,
) -> VoxelAsset:
    """Copy an asset while applying a voxelizer occupancy mode."""

    normalized_mode = _mode(mode)
    thickness = _thickness(shell_thickness)
    result = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=filter_voxels(
            asset.voxels,
            mode=normalized_mode,
            shell_thickness=thickness,
        ),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
        palette_materials=asset.get_palette_materials(),
    )
    result.provenance.update({
        "voxelizer_mode": normalized_mode,
        "voxelizer_shell_thickness": str(thickness),
    })
    return result


__all__ = [
    "MAX_SHELL_THICKNESS",
    "VOXELIZER_MODES",
    "apply_voxelizer_mode",
    "filter_voxels",
]
