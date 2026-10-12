# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic guide-volume refinement for sprite reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .reconstruct import ReconstructionResult
from .voxel import Cell, VoxelAsset

GuideMode = Literal["OBSERVED_ONLY", "ALLOW_HIDDEN"]


@dataclass(frozen=True, slots=True)
class GuidePriorResult:
    asset: VoxelAsset
    added_cells: tuple[Cell, ...]
    rejected_free_cells: tuple[Cell, ...]
    recolored_cells: tuple[Cell, ...]


def apply_guide_prior(
    result: ReconstructionResult,
    guide: VoxelAsset,
    *,
    mode: GuideMode = "ALLOW_HIDDEN",
) -> GuidePriorResult:
    """Merge a pre-voxelized mesh/template guide without overriding free evidence.

    ``guide`` is intentionally a VoxelAsset: the existing VOXTART dynamic
    voxelizer remains the mesh-to-grid boundary, while this function performs
    the SPRITEFORM-style evidence reconciliation.
    """

    if not isinstance(result, ReconstructionResult):
        raise TypeError("guide refinement expects a ReconstructionResult")
    if not isinstance(guide, VoxelAsset):
        raise TypeError("guide refinement expects a VoxelAsset guide")
    normalized_mode = str(mode).upper()
    if normalized_mode not in ("OBSERVED_ONLY", "ALLOW_HIDDEN"):
        raise ValueError("guide mode must be OBSERVED_ONLY or ALLOW_HIDDEN")
    free_cells = {cell for cell, evidence in result.evidence.cells if evidence.state == "FREE"}
    evidence_cells = set(cell for cell, _evidence in result.evidence.cells)
    asset = VoxelAsset(
        grid_size=result.asset.grid_size,
        palette=list(result.asset.palette),
        voxels=dict(result.asset.voxels),
        sockets=list(result.asset.sockets),
        provenance=dict(result.asset.provenance),
    )
    added: list[Cell] = []
    rejected: list[Cell] = []
    recolored: list[Cell] = []
    def guide_index(index: int) -> int:
        color = guide.palette[index]
        try:
            return asset.palette.index(color)
        except ValueError:
            asset.palette.append(color)
            return len(asset.palette) - 1

    for cell, palette_index in guide.iter_voxels():
        target_index = guide_index(palette_index)
        if cell in free_cells:
            rejected.append(cell)
            continue
        if normalized_mode == "OBSERVED_ONLY" and cell not in evidence_cells:
            continue
        existing = asset.voxels.get(cell)
        if existing is None:
            asset.voxels[cell] = target_index
            added.append(cell)
        elif existing != target_index:
            asset.voxels[cell] = target_index
            recolored.append(cell)
    asset.provenance.update({
        "guide_prior": "VOXEL_GUIDE",
        "guide_mode": normalized_mode,
        "guide_digest": guide.content_digest,
        "guide_added": str(len(added)),
        "guide_rejected_free": str(len(rejected)),
    })
    return GuidePriorResult(asset, tuple(sorted(added)), tuple(sorted(rejected)), tuple(sorted(recolored)))


__all__ = ["GuidePriorResult", "apply_guide_prior"]
