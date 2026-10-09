# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic, policy-driven voxel resampling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Literal

from .voxel import Cell, VoxelAsset, VoxelSocket


Threshold = Literal["majority", "any", "full"]


@dataclass(frozen=True, slots=True)
class DecimationPolicy:
    """Controls occupancy selection and cells that must survive resampling."""

    factor: int = 2
    threshold: Threshold = "majority"
    protected: frozenset[Cell] = frozenset()
    protected_palette_index: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.factor, bool) or not isinstance(self.factor, int) or self.factor < 2:
            raise ValueError("decimation factor must be an integer >= 2")
        if self.threshold not in ("majority", "any", "full"):
            raise ValueError(f"unsupported decimation threshold: {self.threshold!r}")
        if isinstance(self.protected_palette_index, bool) or not isinstance(self.protected_palette_index, int):
            raise ValueError("protected_palette_index must be an integer")
        object.__setattr__(self, "protected", frozenset(tuple(cell) for cell in self.protected))

    def required_occupancy(self, block_volume: int) -> int:
        if self.threshold == "any":
            return 1
        if self.threshold == "full":
            return block_volume
        return (block_volume + 1) // 2


@dataclass(frozen=True, slots=True)
class DecimationReport:
    source_voxels: int
    target_voxels: int
    source_grid_size: float
    target_grid_size: float
    factor: int
    threshold: Threshold
    coarse_blocks: int
    mixed_material_blocks: int
    protected_cells: int
    protected_forced: int
    protected_cells_lost: int

    def to_dict(self) -> dict[str, int | float | str]:
        return asdict(self)


def _material_for_block(
    occupied: list[tuple[Cell, int]],
    protected: list[tuple[Cell, int]],
    default_palette_index: int,
) -> int:
    if protected:
        return min(index for _cell, index in protected)
    if not occupied:
        return default_palette_index
    counts: dict[int, int] = {}
    for _cell, index in occupied:
        counts[index] = counts.get(index, 0) + 1
    return min(counts, key=lambda index: (-counts[index], index))


def _resample_sockets(sockets: list[VoxelSocket], factor: int) -> list[VoxelSocket]:
    result: list[VoxelSocket] = []
    seen: set[tuple[Cell, Cell, str, int]] = set()
    for socket in sockets:
        position = tuple(axis // factor for axis in socket.position)
        key = (position, socket.normal, socket.tag, socket.rotation)
        if key in seen:
            continue
        seen.add(key)
        result.append(VoxelSocket(position, socket.normal, socket.tag, socket.rotation))
    return result


def decimate(asset: VoxelAsset, policy: DecimationPolicy = DecimationPolicy()) -> tuple[VoxelAsset, DecimationReport]:
    """Downsample a sparse asset with deterministic majority/material policies."""

    bounds = asset.bounds
    if bounds is None:
        empty = VoxelAsset(
            grid_size=asset.grid_size * policy.factor,
            palette=list(asset.palette),
            sockets=_resample_sockets(asset.sockets, policy.factor),
            provenance={**asset.provenance, "decimation_factor": str(policy.factor)},
        )
        return empty, DecimationReport(
            source_voxels=0,
            target_voxels=0,
            source_grid_size=asset.grid_size,
            target_grid_size=empty.grid_size,
            factor=policy.factor,
            threshold=policy.threshold,
            coarse_blocks=0,
            mixed_material_blocks=0,
            protected_cells=len(policy.protected),
            protected_forced=0,
            protected_cells_lost=0,
        )

    minimum, maximum = bounds
    block_volume = policy.factor ** 3
    required = policy.required_occupancy(block_volume)
    output_voxels: dict[Cell, int] = {}
    coarse_blocks = 0
    mixed_material_blocks = 0
    protected_forced = 0
    protected_present = policy.protected & set(asset.voxels)

    coarse_minimum = tuple(axis // policy.factor for axis in minimum)
    coarse_maximum = tuple(axis // policy.factor for axis in maximum)
    for coarse_cell in product(
        range(coarse_minimum[0], coarse_maximum[0] + 1),
        range(coarse_minimum[1], coarse_maximum[1] + 1),
        range(coarse_minimum[2], coarse_maximum[2] + 1),
    ):
        coarse_blocks += 1
        block_origin = tuple(axis * policy.factor for axis in coarse_cell)
        block_cells = [
            (block_origin[0] + dx, block_origin[1] + dy, block_origin[2] + dz)
            for dx, dy, dz in product(range(policy.factor), repeat=3)
        ]
        occupied = [(cell, asset.voxels[cell]) for cell in block_cells if cell in asset.voxels]
        protected = [(cell, asset.voxels[cell]) for cell in block_cells if cell in protected_present]
        if len({index for _cell, index in occupied}) > 1:
            mixed_material_blocks += 1
        if len(occupied) >= required or protected:
            if protected and len(occupied) < required:
                protected_forced += 1
            output_voxels[coarse_cell] = _material_for_block(
                occupied, protected, policy.protected_palette_index
            )

    output = VoxelAsset(
        grid_size=asset.grid_size * policy.factor,
        palette=list(asset.palette),
        voxels=output_voxels,
        sockets=_resample_sockets(asset.sockets, policy.factor),
        provenance={
            **asset.provenance,
            "decimation_factor": str(policy.factor),
            "decimation_threshold": policy.threshold,
        },
    )
    protected_cells_lost = sum(
        1 for cell in protected_present if tuple(axis // policy.factor for axis in cell) not in output.voxels
    )
    report = DecimationReport(
        source_voxels=len(asset.voxels),
        target_voxels=len(output.voxels),
        source_grid_size=asset.grid_size,
        target_grid_size=output.grid_size,
        factor=policy.factor,
        threshold=policy.threshold,
        coarse_blocks=coarse_blocks,
        mixed_material_blocks=mixed_material_blocks,
        protected_cells=len(policy.protected),
        protected_forced=protected_forced,
        protected_cells_lost=protected_cells_lost,
    )
    return output, report
