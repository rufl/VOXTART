# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Pure placement math shared by the VOXTART asset placer."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable


PLACEMENT_MODES = ("FREE", "GRID", "SURFACE", "VERTEX", "BRUSH", "PATH")


@dataclass(frozen=True, slots=True)
class PlacementSettings:
    """Deterministic transform variation for repeated asset placement."""

    seed: int = 0
    random_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    random_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    random_scale: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _vector(value: Iterable[float], field: str) -> tuple[float, float, float]:
    result = tuple(float(axis) for axis in value)
    if len(result) != 3 or not all(math.isfinite(axis) for axis in result):
        raise ValueError(f"{field} must contain three finite values")
    return result  # type: ignore[return-value]


def snap_to_grid(point: Iterable[float], step: float) -> tuple[float, float, float]:
    """Snap a world point to a uniform grid while preserving its origin."""

    point = _vector(point, "point")
    step = float(step)
    if not math.isfinite(step) or step <= 0.0:
        raise ValueError("grid step must be a positive finite value")
    return tuple(round(axis / step) * step for axis in point)  # type: ignore[return-value]


def nearest_point(
    points: Iterable[Iterable[float]],
    target: Iterable[float],
) -> tuple[float, float, float] | None:
    """Return the closest candidate point, deterministically breaking ties."""

    target = _vector(target, "target")
    candidates = [_vector(point, "candidate point") for point in points]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda point: (
            sum((point[index] - target[index]) ** 2 for index in range(3)),
            point,
        ),
    )


def polyline_samples(
    points: Iterable[Iterable[float]],
    spacing: float,
    *,
    closed: bool = False,
) -> tuple[tuple[float, float, float], ...]:
    """Sample a polyline at even distances, including its first point."""

    source = [_vector(point, "polyline point") for point in points]
    if closed and len(source) > 1 and source[-1] != source[0]:
        source.append(source[0])
    if len(source) < 2:
        return tuple(source)
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing <= 0.0:
        raise ValueError("path spacing must be a positive finite value")

    segments: list[tuple[tuple[float, float, float], tuple[float, float, float], float]] = []
    total = 0.0
    for start, end in zip(source, source[1:]):
        length = math.sqrt(sum((end[index] - start[index]) ** 2 for index in range(3)))
        if length <= 1.0e-9:
            continue
        segments.append((start, end, length))
        total += length
    if not segments:
        return (source[0],)

    distances = [0.0]
    cursor = spacing
    while cursor < total - 1.0e-9:
        distances.append(cursor)
        cursor += spacing
    if not closed and distances[-1] < total - 1.0e-9:
        distances.append(total)

    result: list[tuple[float, float, float]] = []
    segment_index = 0
    segment_start_distance = 0.0
    for distance in distances:
        while (
            segment_index < len(segments) - 1
            and distance > segment_start_distance + segments[segment_index][2]
        ):
            segment_start_distance += segments[segment_index][2]
            segment_index += 1
        start, end, length = segments[segment_index]
        factor = min(1.0, max(0.0, (distance - segment_start_distance) / length))
        result.append(tuple(
            start[index] + (end[index] - start[index]) * factor
            for index in range(3)
        ))
    return tuple(result)


def disc_samples(
    center: Iterable[float],
    radius: float,
    count: int,
    *,
    seed: int = 0,
) -> tuple[tuple[float, float, float], ...]:
    """Generate deterministic, evenly distributed candidates in a horizontal disc."""

    center = _vector(center, "brush center")
    radius = float(radius)
    count = int(count)
    if not math.isfinite(radius) or radius < 0.0:
        raise ValueError("brush radius must be a finite non-negative value")
    if count < 1:
        return ()
    generator = random.Random(int(seed))
    result = []
    for _index in range(count):
        angle = generator.random() * math.tau
        distance = radius * math.sqrt(generator.random())
        result.append((
            center[0] + math.cos(angle) * distance,
            center[1] + math.sin(angle) * distance,
            center[2],
        ))
    return tuple(result)


def randomized_transform(
    location: Iterable[float],
    rotation: Iterable[float],
    scale: Iterable[float],
    *,
    index: int,
    settings: PlacementSettings,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Apply reproducible per-instance transform variation."""

    location = _vector(location, "location")
    rotation = _vector(rotation, "rotation")
    scale = _vector(scale, "scale")
    random_position = _vector(settings.random_position, "random position")
    random_rotation = _vector(settings.random_rotation, "random rotation")
    random_scale = _vector(settings.random_scale, "random scale")
    if any(axis < 0.0 for axis in random_position + random_rotation + random_scale):
        raise ValueError("randomization ranges must be non-negative")
    if any(axis <= 0.0 for axis in scale):
        raise ValueError("scale values must be positive")

    generator = random.Random(int(settings.seed) + int(index) * 1_000_003)
    varied_location = tuple(
        location[axis] + generator.uniform(-random_position[axis], random_position[axis])
        for axis in range(3)
    )
    varied_rotation = tuple(
        rotation[axis] + math.radians(generator.uniform(-random_rotation[axis], random_rotation[axis]))
        for axis in range(3)
    )
    varied_scale = tuple(
        max(0.001, scale[axis] * (1.0 + generator.uniform(-random_scale[axis], random_scale[axis])))
        for axis in range(3)
    )
    return varied_location, varied_rotation, varied_scale



def thumbnail_pixels(asset: object, width: int = 96, height: int = 96) -> tuple[float, ...]:
    """Render a small deterministic isometric preview from sparse voxel data."""

    width = int(width)
    height = int(height)
    if width < 8 or height < 8:
        raise ValueError("thumbnail dimensions must be at least 8 pixels")
    pixels = [0.0] * (width * height * 4)
    voxels = getattr(asset, "voxels", {})
    palette = getattr(asset, "palette", ())
    if not voxels or not palette:
        return tuple(pixels)

    projected = []
    for cell, palette_index in voxels.items():
        if len(cell) != 3:
            continue
        try:
            color = tuple(float(channel) for channel in palette[int(palette_index)])
        except (IndexError, TypeError, ValueError):
            continue
        if len(color) < 3:
            continue
        projected.append((
            float(cell[0] - cell[1]),
            float((cell[0] + cell[1]) * 0.5 - cell[2]),
            float(sum(cell)),
            color,
        ))
    if not projected:
        return tuple(pixels)

    minimum_x = min(item[0] for item in projected)
    maximum_x = max(item[0] for item in projected)
    minimum_y = min(item[1] for item in projected)
    maximum_y = max(item[1] for item in projected)
    span_x = max(maximum_x - minimum_x + 1.0, 1.0)
    span_y = max(maximum_y - minimum_y + 1.0, 1.0)
    scale = min((width - 8) / span_x, (height - 8) / span_y)
    half_width = max(1.0, scale * 0.52)
    half_height = max(1.0, scale * 0.30)
    center_x = (minimum_x + maximum_x) * 0.5
    center_y = (minimum_y + maximum_y) * 0.5
    for point_x, point_y, _depth, color in sorted(projected, key=lambda item: item[2]):
        screen_x = (point_x - center_x) * scale + width * 0.5
        screen_y = (point_y - center_y) * scale + height * 0.54
        minimum_pixel_x = max(0, int(math.floor(screen_x - half_width)))
        maximum_pixel_x = min(width - 1, int(math.ceil(screen_x + half_width)))
        minimum_pixel_y = max(0, int(math.floor(screen_y - half_height)))
        maximum_pixel_y = min(height - 1, int(math.ceil(screen_y + half_height)))
        for pixel_y in range(minimum_pixel_y, maximum_pixel_y + 1):
            for pixel_x in range(minimum_pixel_x, maximum_pixel_x + 1):
                distance = (
                    abs(pixel_x + 0.5 - screen_x) / half_width
                    + abs(pixel_y + 0.5 - screen_y) / half_height
                )
                if distance > 1.0:
                    continue
                index = (pixel_y * width + pixel_x) * 4
                pixels[index:index + 4] = (
                    max(0.0, min(1.0, color[0])),
                    max(0.0, min(1.0, color[1])),
                    max(0.0, min(1.0, color[2])),
                    max(0.0, min(1.0, color[3] if len(color) > 3 else 1.0)),
                )
    return tuple(pixels)

__all__ = [
    "PLACEMENT_MODES",
    "PlacementSettings",
    "disc_samples",
    "nearest_point",
    "polyline_samples",
    "randomized_transform",
    "snap_to_grid",
    "thumbnail_pixels",
]
