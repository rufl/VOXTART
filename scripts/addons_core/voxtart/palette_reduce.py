# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Deterministic palette reduction for sparse voxel assets.

This module implements a small weighted median-cut quantizer from first
principles. It is suitable for imported mesh colors and keeps all voxel
occupancy and grid coordinates unchanged while replacing palette indices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from .palette import MAX_PALETTE_COLORS, coerce_color, nearest_palette_index
from .voxel import Color, VoxelAsset


PALETTE_REDUCTION_METHODS = ("MEDIAN_CUT",)


@dataclass(frozen=True, slots=True)
class PaletteReductionReport:
    source_colors: int
    target_colors: int
    source_voxels: int
    target_voxels: int
    changed_voxels: int
    method: str

    @property
    def changed(self) -> bool:
        return self.source_colors != self.target_colors or self.changed_voxels > 0

    def to_dict(self) -> dict[str, int | str | bool]:
        return {**asdict(self), "changed": self.changed}


@dataclass(frozen=True, slots=True)
class _ColorPoint:
    color: Color
    weight: float
    source_index: int


def _target_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PALETTE_COLORS:
        raise ValueError(f"target palette size must be an integer in [1, {MAX_PALETTE_COLORS}]")
    return value


def _method(value: str) -> str:
    normalized = str(value or "").upper()
    if normalized not in PALETTE_REDUCTION_METHODS:
        raise ValueError(f"unsupported palette reduction method: {value!r}")
    return normalized


def _points(colors: Sequence[Color], weights: Sequence[float] | None) -> list[_ColorPoint]:
    if weights is not None and len(weights) != len(colors):
        raise ValueError("palette weights must match the color count")
    merged: dict[Color, tuple[float, int]] = {}
    for index, color in enumerate(colors):
        weight = 1.0 if weights is None else float(weights[index])
        if weight < 0.0 or not weight == weight or weight in (float("inf"), float("-inf")):
            raise ValueError("palette weights must be finite and non-negative")
        previous_weight, first_index = merged.get(color, (0.0, index))
        merged[color] = (previous_weight + weight, first_index)
    result = [
        _ColorPoint(color, weight if weight > 0.0 else 1.0, first_index)
        for color, (weight, first_index) in merged.items()
    ]
    result.sort(key=lambda point: point.source_index)
    return result


def _box_split(points: Sequence[_ColorPoint], indices: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    if len(indices) < 2:
        return None
    ranges = tuple(
        max(points[index].color[channel] for index in indices)
        - min(points[index].color[channel] for index in indices)
        for channel in range(4)
    )
    channel = max(range(4), key=lambda value: (ranges[value], -value))
    ordered = sorted(
        indices,
        key=lambda index: (
            points[index].color[channel],
            points[index].color[(channel + 1) % 4],
            points[index].color[(channel + 2) % 4],
            points[index].color[(channel + 3) % 4],
            points[index].source_index,
        ),
    )
    total = sum(points[index].weight for index in ordered)
    target = total * 0.5
    cumulative = 0.0
    split = 1
    best_distance = float("inf")
    for candidate in range(1, len(ordered)):
        cumulative += points[ordered[candidate - 1]].weight
        distance = abs(cumulative - target)
        if distance < best_distance:
            best_distance = distance
            split = candidate
    return tuple(ordered[:split]), tuple(ordered[split:])


def _centroid(points: Sequence[_ColorPoint], indices: Iterable[int]) -> Color:
    indices = tuple(indices)
    total = sum(points[index].weight for index in indices)
    return tuple(
        sum(points[index].color[channel] * points[index].weight for index in indices) / total
        for channel in range(4)
    )  # type: ignore[return-value]


def reduce_palette(
    colors: Iterable[Iterable[float]],
    max_colors: int,
    *,
    weights: Iterable[float] | None = None,
    method: str = "MEDIAN_CUT",
) -> list[Color]:
    """Return a deterministic weighted palette with at most ``max_colors`` entries."""

    target = _target_count(max_colors)
    normalized_method = _method(method)
    source = tuple(coerce_color(color) for color in colors)
    if not source:
        raise ValueError("palette contains no colors")
    weight_values = None if weights is None else tuple(float(value) for value in weights)
    points = _points(source, weight_values)
    if len(points) <= target:
        return [point.color for point in points]

    boxes: list[tuple[int, ...]] = [tuple(range(len(points)))]
    while len(boxes) < target:
        candidates = []
        for box_index, box in enumerate(boxes):
            split = _box_split(points, box)
            if split is None:
                continue
            ranges = tuple(
                max(points[index].color[channel] for index in box)
                - min(points[index].color[channel] for index in box)
                for channel in range(4)
            )
            candidates.append(
                (
                    max(ranges) * sum(points[index].weight for index in box),
                    max(ranges),
                    -min(points[index].source_index for index in box),
                    box_index,
                    split,
                )
            )
        _score, _range, _first, box_index, split = max(candidates, key=lambda item: item[:4])
        boxes[box_index:box_index + 1] = [split[0], split[1]]

    boxes.sort(key=lambda box: min(points[index].source_index for index in box))
    palette = [_centroid(points, box) for box in boxes]
    # Centroids can coincide after alpha/color weighting. Keep output valid and
    # deterministic even when the requested count cannot be reached exactly.
    result: list[Color] = []
    for color in palette:
        if color not in result:
            result.append(color)
    return result


def reduce_asset_palette(
    asset: VoxelAsset,
    max_colors: int,
    *,
    method: str = "MEDIAN_CUT",
) -> tuple[VoxelAsset, PaletteReductionReport]:
    """Reduce an asset palette while preserving coordinates, sockets, and scale."""

    target = _target_count(max_colors)
    normalized_method = _method(method)
    counts = [0] * len(asset.palette)
    for palette_index in asset.voxels.values():
        counts[palette_index] += 1
    if asset.voxels:
        source_indices = tuple(index for index, count in enumerate(counts) if count)
        source_colors = tuple(asset.palette[index] for index in source_indices)
        source_weights = tuple(float(counts[index]) for index in source_indices)
    else:
        source_indices = tuple(range(len(asset.palette)))
        source_colors = tuple(asset.palette)
        source_weights = None
    reduced = reduce_palette(
        source_colors,
        target,
        weights=source_weights,
        method=normalized_method,
    )
    mapping = {
        source_index: nearest_palette_index(asset.palette[source_index], reduced)
        for source_index in source_indices
    }
    voxels = {cell: mapping.get(index, 0) for cell, index in asset.voxels.items()}
    changed_voxels = sum(
        1 for cell, index in asset.voxels.items() if voxels[cell] != index
    )
    provenance = dict(asset.provenance)
    provenance["palette_reduction_method"] = normalized_method
    provenance["palette_reduction_colors"] = str(len(reduced))
    result = VoxelAsset(
        grid_size=asset.grid_size,
        palette=reduced,
        voxels=voxels,
        sockets=list(asset.sockets),
        provenance=provenance,
    )
    return result, PaletteReductionReport(
        source_colors=len(asset.palette),
        target_colors=len(result.palette),
        source_voxels=len(asset.voxels),
        target_voxels=len(result.voxels),
        changed_voxels=changed_voxels,
        method=normalized_method,
    )


__all__ = [
    "PALETTE_REDUCTION_METHODS",
    "PaletteReductionReport",
    "reduce_asset_palette",
    "reduce_palette",
]
