# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Geometric, non-semantic skeleton suggestions for voxel assets."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
from typing import Iterable

from .voxel import Cell, VoxelAsset

_NEIGHBORS = (
    (-1, 0, 0),
    (1, 0, 0),
    (0, -1, 0),
    (0, 1, 0),
    (0, 0, -1),
    (0, 0, 1),
)
_METHOD = "semantic6/manhattan6-empty/deepest-root/geodesic-farthest/coverage/v1"


def _neighbors(cell: Cell, occupied: set[Cell]) -> tuple[Cell, ...]:
    return tuple(
        (cell[0] + offset[0], cell[1] + offset[1], cell[2] + offset[2])
        for offset in _NEIGHBORS
        if (cell[0] + offset[0], cell[1] + offset[1], cell[2] + offset[2]) in occupied
    )


def _distance_from_empty(occupied: set[Cell]) -> dict[Cell, int]:
    boundary = sorted(cell for cell in occupied if len(_neighbors(cell, occupied)) < 6)
    distances = {cell: 1 for cell in boundary}
    queue = deque(boundary)
    while queue:
        cell = queue.popleft()
        for neighbor in _neighbors(cell, occupied):
            if neighbor not in distances:
                distances[neighbor] = distances[cell] + 1
                queue.append(neighbor)
    return distances


def _root_distances(root: Cell, occupied: set[Cell]) -> tuple[dict[Cell, int], dict[Cell, Cell | None]]:
    distances = {root: 0}
    parents: dict[Cell, Cell | None] = {root: None}
    queue = deque((root,))
    while queue:
        cell = queue.popleft()
        for neighbor in sorted(_neighbors(cell, occupied)):
            if neighbor not in distances:
                distances[neighbor] = distances[cell] + 1
                parents[neighbor] = cell
                queue.append(neighbor)
    return distances, parents


@dataclass(frozen=True, slots=True)
class SkeletonNode:
    cell: Cell
    parent: Cell | None
    radius: float
    confidence: float
    empty_distance: int
    geodesic_distance: int

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["cell"] = list(self.cell)
        value["parent"] = None if self.parent is None else list(self.parent)
        return value


@dataclass(frozen=True, slots=True)
class SkeletonGraph:
    nodes: tuple[SkeletonNode, ...]
    method: str = _METHOD

    def to_dict(self) -> dict[str, object]:
        return {"method": self.method, "nodes": [node.to_dict() for node in self.nodes]}


def derive_skeleton(asset: VoxelAsset, *, max_nodes: int = 256) -> SkeletonGraph:
    """Derive a deterministic geometric graph; it makes no anatomy claim."""

    if not isinstance(asset, VoxelAsset):
        raise TypeError("skeleton derivation expects a VoxelAsset")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes <= 0:
        raise ValueError("max_nodes must be a positive integer")
    occupied = set(asset.voxels)
    if not occupied:
        return SkeletonGraph(())
    empty_distances = _distance_from_empty(occupied)
    root = min(
        (cell for cell in occupied if cell in empty_distances),
        key=lambda cell: (-empty_distances[cell], cell),
    )
    geodesic, parents = _root_distances(root, occupied)
    selected: set[Cell] = {root}
    for cell in sorted(occupied):
        degree = len(_neighbors(cell, occupied))
        if degree != 2 or degree <= 1:
            selected.add(cell)
    ordered = sorted(selected, key=lambda cell: (geodesic.get(cell, math.inf), cell))
    if len(ordered) > max_nodes:
        ordered = ordered[:max_nodes]
    selected = set(ordered)
    nodes: list[SkeletonNode] = []
    for cell in ordered:
        parent = parents.get(cell)
        while parent is not None and parent not in selected:
            parent = parents.get(parent)
        distance = empty_distances.get(cell, 1)
        nodes.append(
            SkeletonNode(
                cell=cell,
                parent=parent,
                radius=asset.grid_size * max(0.5, (distance - 1.5) / math.sqrt(3.0)),
                confidence=distance / (distance + 1.0),
                empty_distance=distance,
                geodesic_distance=geodesic.get(cell, 0),
            )
        )
    return SkeletonGraph(tuple(nodes))


__all__ = ["SkeletonGraph", "SkeletonNode", "derive_skeleton"]
