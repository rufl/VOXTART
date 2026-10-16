# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic image, depth, and voxel-surface comparison metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable, Sequence

from .voxel import Cell


@dataclass(frozen=True, slots=True)
class PixelMetrics:
    """Pixel agreement metrics with explicit missing and extra evidence."""

    source_pixels: int
    predicted_pixels: int
    intersection_pixels: int
    union_pixels: int
    missing_pixels: int
    extra_pixels: int
    color_mismatches: int

    @property
    def iou(self) -> float:
        return self.intersection_pixels / self.union_pixels if self.union_pixels else 1.0

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["iou"] = self.iou
        return value


def compare_indexed_views(
    source: Sequence[Sequence[int | None]],
    predicted: Sequence[Sequence[int | None]],
) -> PixelMetrics:
    """Compare equal-sized indexed images using visible-pixel IoU."""

    source_rows = tuple(tuple(row) for row in source)
    predicted_rows = tuple(tuple(row) for row in predicted)
    if (
        not source_rows
        or not source_rows[0]
        or not predicted_rows
        or not predicted_rows[0]
        or any(len(row) != len(source_rows[0]) for row in source_rows)
        or any(len(row) != len(predicted_rows[0]) for row in predicted_rows)
    ):
        raise ValueError("indexed views must contain rectangular pixels")
    if len(source_rows) != len(predicted_rows) or len(source_rows[0]) != len(predicted_rows[0]):
        raise ValueError("indexed views must have equal dimensions")
    source_pixels = predicted_pixels = intersection = union = missing = extra = mismatches = 0
    for source_row, predicted_row in zip(source_rows, predicted_rows):
        for source_pixel, predicted_pixel in zip(source_row, predicted_row):
            source_visible = source_pixel is not None
            predicted_visible = predicted_pixel is not None
            source_pixels += int(source_visible)
            predicted_pixels += int(predicted_visible)
            intersection += int(source_visible and predicted_visible)
            union += int(source_visible or predicted_visible)
            missing += int(source_visible and not predicted_visible)
            extra += int(predicted_visible and not source_visible)
            mismatches += int(source_visible and predicted_visible and source_pixel != predicted_pixel)
    return PixelMetrics(source_pixels, predicted_pixels, intersection, union, missing, extra, mismatches)


@dataclass(frozen=True, slots=True)
class DepthMetrics:
    """Finite paired depth errors; missing pairs are never treated as zero."""

    samples: int
    mae: float | None
    rmse: float | None
    max_error: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compare_depths(reference: Iterable[float], predicted: Iterable[float]) -> DepthMetrics:
    """Compute MAE/RMSE/max error for equally sized finite depth samples."""

    reference_values = tuple(float(value) for value in reference)
    predicted_values = tuple(float(value) for value in predicted)
    if len(reference_values) != len(predicted_values):
        raise ValueError("depth samples must have equal lengths")
    if any(not math.isfinite(value) for value in (*reference_values, *predicted_values)):
        raise ValueError("depth samples must be finite")
    if not reference_values:
        return DepthMetrics(0, None, None, None)
    errors = tuple(abs(left - right) for left, right in zip(reference_values, predicted_values))
    return DepthMetrics(
        len(errors),
        sum(errors) / len(errors),
        math.sqrt(sum(error * error for error in errors) / len(errors)),
        max(errors),
    )


@dataclass(frozen=True, slots=True)
class SurfaceDistanceMetrics:
    """Directed and symmetric cell-center distances; empty sides remain null."""

    reference_count: int
    predicted_count: int
    reference_to_predicted_mean: float | None
    reference_to_predicted_max: float | None
    predicted_to_reference_mean: float | None
    predicted_to_reference_max: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _cells(values: Iterable[Cell]) -> tuple[Cell, ...]:
    result = tuple(tuple(cell) for cell in values)
    if any(len(cell) != 3 or any(isinstance(axis, bool) or not isinstance(axis, int) for axis in cell) for cell in result):
        raise ValueError("surface cells must contain three integer coordinates")
    return tuple(sorted(set(result)))  # type: ignore[return-value]


def _directed_distances(
    source: tuple[Cell, ...],
    target: tuple[Cell, ...],
    scale: tuple[float, float, float],
) -> tuple[float, ...] | None:
    if not source or not target:
        return None
    return tuple(
        math.sqrt(
            min(
                sum(((cell[axis] - other[axis]) * scale[axis]) ** 2 for axis in range(3))
                for other in target
            )
        )
        for cell in source
    )


def compare_cell_surfaces(
    reference: Iterable[Cell],
    predicted: Iterable[Cell],
    *,
    cell_scale: Iterable[float] = (1.0, 1.0, 1.0),
) -> SurfaceDistanceMetrics:
    """Compare exposed cell centers with anisotropic Euclidean distances."""

    reference_cells = _cells(reference)
    predicted_cells = _cells(predicted)
    scale = tuple(float(value) for value in cell_scale)
    if len(scale) != 3 or any(not math.isfinite(value) or value <= 0.0 for value in scale):
        raise ValueError("cell_scale must contain three positive finite values")
    reference_distances = _directed_distances(reference_cells, predicted_cells, scale)
    predicted_distances = _directed_distances(predicted_cells, reference_cells, scale)

    def summary(values: tuple[float, ...] | None) -> tuple[float | None, float | None]:
        if values is None:
            return None, None
        return sum(values) / len(values), max(values)

    reference_mean, reference_max = summary(reference_distances)
    predicted_mean, predicted_max = summary(predicted_distances)
    return SurfaceDistanceMetrics(
        len(reference_cells),
        len(predicted_cells),
        reference_mean,
        reference_max,
        predicted_mean,
        predicted_max,
    )


__all__ = [
    "DepthMetrics",
    "PixelMetrics",
    "SurfaceDistanceMetrics",
    "compare_cell_surfaces",
    "compare_depths",
    "compare_indexed_views",
]
