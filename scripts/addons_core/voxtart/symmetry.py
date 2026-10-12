# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic, missing-only symmetry for voxel candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .provenance import EvidenceMap
from .voxel import Cell, VoxelAsset

SymmetryAxis = Literal["X", "Y", "Z"]
_AXES = {"X": 0, "Y": 1, "Z": 2}


@dataclass(frozen=True, slots=True)
class SymmetryResult:
    asset: VoxelAsset
    added_cells: tuple[Cell, ...]
    skipped_free_cells: tuple[Cell, ...]
    conflicts: tuple[Cell, ...]


def mirror_cell(cell: Cell, axis: SymmetryAxis, plane: int = 0) -> Cell:
    normalized_axis = str(axis).upper()
    if normalized_axis not in _AXES:
        raise ValueError("symmetry axis must be X, Y, or Z")
    if isinstance(plane, bool) or not isinstance(plane, int):
        raise ValueError("symmetry plane must be an integer cell coordinate")
    index = _AXES[normalized_axis]
    mirrored = list(cell)
    mirrored[index] = 2 * plane - cell[index]
    return tuple(mirrored)  # type: ignore[return-value]


def apply_missing_only(
    asset: VoxelAsset,
    axis: SymmetryAxis,
    *,
    plane: int = 0,
    forbidden: Iterable[Cell] = (),
    evidence: EvidenceMap | None = None,
) -> SymmetryResult:
    """Mirror only absent cells; observed and free cells always win."""

    if not isinstance(asset, VoxelAsset):
        raise TypeError("symmetry expects a VoxelAsset")
    forbidden_cells = {tuple(cell) for cell in forbidden}
    if evidence is not None:
        forbidden_cells.update(
            cell for cell, record in evidence.cells if record.state == "FREE"
        )
    result = VoxelAsset(
        grid_size=asset.grid_size,
        palette=list(asset.palette),
        voxels=dict(asset.voxels),
        sockets=list(asset.sockets),
        provenance=dict(asset.provenance),
    )
    added: list[Cell] = []
    skipped: list[Cell] = []
    conflicts: list[Cell] = []
    for cell, palette_index in asset.iter_voxels():
        mirrored = mirror_cell(cell, axis, plane)
        if mirrored == cell:
            continue
        if mirrored in forbidden_cells:
            skipped.append(mirrored)
            continue
        existing = result.voxels.get(mirrored)
        if existing is None:
            result.voxels[mirrored] = palette_index
            added.append(mirrored)
        elif existing != palette_index:
            conflicts.append(mirrored)
    result.provenance.update({
        "symmetry_axis": str(axis).upper(),
        "symmetry_plane": str(plane),
        "symmetry_policy": "MISSING_ONLY",
        "symmetry_added": str(len(added)),
    })
    return SymmetryResult(result, tuple(sorted(set(added))), tuple(sorted(set(skipped))), tuple(sorted(set(conflicts))))


__all__ = ["SymmetryResult", "apply_missing_only", "mirror_cell"]
