# SPDX-FileCopyrightText: 2026 VOXTART contributors
#
# SPDX-License-Identifier: MIT

"""Blockbench .bbmodel and Bedrock geometry conversion helpers."""

from __future__ import annotations

import colorsys
import json
import math
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .palette import nearest_palette_index
from .voxel import Color, VoxelAsset


BLOCKBENCH_FORMAT_VERSION = "5.0"
MAX_BLOCKBENCH_ELEMENTS = 2_000_000
DEFAULT_BLOCKBENCH_UNITS_PER_VOXEL = 1.0


def _vec3(value: Iterable[Any], field: str) -> tuple[float, float, float]:
    result = tuple(float(axis) for axis in value)
    if len(result) != 3 or not all(math.isfinite(axis) for axis in result):
        raise ValueError(f"Blockbench {field} must contain three finite values")
    return result  # type: ignore[return-value]


def _color(value: Iterable[Any]) -> Color:
    values = tuple(float(channel) for channel in value)
    if len(values) == 3:
        values = (*values, 1.0)
    if len(values) != 4 or not all(math.isfinite(channel) for channel in values):
        raise ValueError("Blockbench colors must contain three or four finite channels")
    if any(channel > 1.0 for channel in values):
        values = tuple(channel / 255.0 for channel in values)
    if any(channel < 0.0 or channel > 1.0 for channel in values):
        raise ValueError("Blockbench colors must be in [0, 1] or [0, 255]")
    return values  # type: ignore[return-value]


def _indexed_color(index: int) -> Color:
    red, green, blue = colorsys.hsv_to_rgb((int(index) * 0.61803398875) % 1.0, 0.55, 0.9)
    return red, green, blue, 1.0


def _palette_from_payload(payload: Mapping[str, Any]) -> list[Color]:
    raw = payload.get("voxtart_palette", payload.get("palette", ()))
    if not isinstance(raw, (list, tuple)):
        return []
    palette = []
    for value in raw:
        try:
            palette.append(_color(value))
        except (TypeError, ValueError):
            continue
    return palette


def _element_color(element: Mapping[str, Any], palette: list[Color]) -> Color:
    value = element.get("color")
    if isinstance(value, int) and not isinstance(value, bool):
        index = int(value)
        if 0 <= index < len(palette):
            return palette[index]
        return _indexed_color(index)
    if isinstance(value, (list, tuple)):
        try:
            return _color(value)
        except (TypeError, ValueError):
            pass
    if isinstance(value, str):
        token = value.removeprefix("#")
        if len(token) in (6, 8):
            try:
                return _color(tuple(int(token[index:index + 2], 16) for index in range(0, len(token), 2)))
            except (TypeError, ValueError):
                pass
    return _indexed_color(len(palette))


def _rotate_point(
    point: tuple[float, float, float],
    pivot: tuple[float, float, float],
    rotation: tuple[float, float, float],
) -> tuple[float, float, float]:
    value = [point[index] - pivot[index] for index in range(3)]
    for axis, angle in enumerate(rotation):
        radians = math.radians(angle)
        sine = math.sin(radians)
        cosine = math.cos(radians)
        if axis == 0:
            value[1], value[2] = value[1] * cosine - value[2] * sine, value[1] * sine + value[2] * cosine
        elif axis == 1:
            value[0], value[2] = value[0] * cosine + value[2] * sine, -value[0] * sine + value[2] * cosine
        else:
            value[0], value[1] = value[0] * cosine - value[1] * sine, value[0] * sine + value[1] * cosine
    return tuple(value[index] + pivot[index] for index in range(3))  # type: ignore[return-value]


def _cube_bounds(
    origin: Iterable[Any],
    size: Iterable[Any],
    *,
    rotation: Iterable[Any] = (0.0, 0.0, 0.0),
    pivot: Iterable[Any] | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    origin = _vec3(origin, "cube origin")
    size = _vec3(size, "cube size")
    rotation = _vec3(rotation, "cube rotation")
    pivot = _vec3(pivot or origin, "cube pivot")
    opposite = tuple(origin[index] + size[index] for index in range(3))
    corners = [
        (origin[0] if x == 0 else opposite[0], origin[1] if y == 0 else opposite[1], origin[2] if z == 0 else opposite[2])
        for x in range(2) for y in range(2) for z in range(2)
    ]
    transformed = tuple(_rotate_point(corner, pivot, rotation) for corner in corners)
    minimum = tuple(min(point[index] for point in transformed) for index in range(3))
    maximum = tuple(max(point[index] for point in transformed) for index in range(3))
    return minimum, maximum  # type: ignore[return-value]


def _mesh_bounds(
    element: Mapping[str, Any],
    *,
    rotation: Iterable[Any] = (0.0, 0.0, 0.0),
    pivot: Iterable[Any] | None = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    raw_vertices = element.get("vertices")
    if isinstance(raw_vertices, Mapping):
        vertices = tuple(_vec3(value, "mesh vertex") for value in raw_vertices.values())
    elif isinstance(raw_vertices, (list, tuple)):
        vertices = tuple(_vec3(value, "mesh vertex") for value in raw_vertices)
    else:
        return None
    if not vertices:
        return None
    pivot = _vec3(pivot or (0.0, 0.0, 0.0), "mesh pivot")
    rotation = _vec3(rotation, "mesh rotation")
    transformed = tuple(_rotate_point(vertex, pivot, rotation) for vertex in vertices)
    minimum = tuple(min(vertex[index] for vertex in transformed) for index in range(3))
    maximum = tuple(max(vertex[index] for vertex in transformed) for index in range(3))
    return minimum, maximum


def _iter_elements(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_elements = payload.get("elements")
    if isinstance(raw_elements, list):
        return tuple(item for item in raw_elements if isinstance(item, Mapping))
    geometries = payload.get("minecraft:geometry")
    if not isinstance(geometries, list):
        return ()
    elements: list[Mapping[str, Any]] = []
    for geometry in geometries:
        if not isinstance(geometry, Mapping):
            continue
        for bone in geometry.get("bones", ()):
            if not isinstance(bone, Mapping):
                continue
            bone_rotation = bone.get("rotation", (0.0, 0.0, 0.0))
            bone_pivot = bone.get("pivot", (0.0, 0.0, 0.0))
            for cube in bone.get("cubes", ()):
                if not isinstance(cube, Mapping):
                    continue
                origin = cube.get("origin", cube.get("from"))
                size = cube.get("size")
                if origin is None or size is None:
                    continue
                elements.append({
                    "name": f"{bone.get('name', 'bone')} cube",
                    "from": origin,
                    "to": tuple(float(origin[index]) + float(size[index]) for index in range(3)),
                    "rotation": cube.get("rotation", bone_rotation),
                    "origin": cube.get("pivot", bone_pivot),
                    "color": cube.get("color", bone.get("color")),
                    "visibility": cube.get("visibility", True) and bone.get("visibility", True),
                })
    return tuple(elements)


def decode_blockbench(
    data: bytes | str | Mapping[str, Any],
    *,
    grid_size: float = 1.0,
    units_per_voxel: float = DEFAULT_BLOCKBENCH_UNITS_PER_VOXEL,
) -> VoxelAsset:
    """Decode axis-aligned and rotated Blockbench cube elements into voxels."""

    if isinstance(data, Mapping):
        payload = data
    else:
        try:
            payload = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid Blockbench JSON") from error
    if not isinstance(payload, Mapping):
        raise ValueError("Blockbench document must contain a JSON object")
    grid_size = float(grid_size)
    units_per_voxel = float(units_per_voxel)
    if not math.isfinite(grid_size) or grid_size <= 0.0:
        raise ValueError("Blockbench grid size must be positive")
    if not math.isfinite(units_per_voxel) or units_per_voxel <= 0.0:
        raise ValueError("Blockbench units per voxel must be positive")
    elements = _iter_elements(payload)
    if not elements:
        raise ValueError("Blockbench document contains no supported cube elements")
    if len(elements) > MAX_BLOCKBENCH_ELEMENTS:
        raise ValueError("Blockbench document contains too many elements")
    palette = _palette_from_payload(payload)
    voxels: dict[tuple[int, int, int], int] = {}
    for element in elements:
        if element.get("visibility", element.get("export", True)) is False:
            continue
        element_type = str(element.get("type", "cube")).lower()
        if element_type not in ("cube", "mesh", ""):
            continue
        rotation_value = element.get("rotation", (0.0, 0.0, 0.0))
        if isinstance(rotation_value, Mapping):
            angle = float(rotation_value.get("angle", 0.0))
            axis = str(rotation_value.get("axis", "y")).lower()
            axis_index = {"x": 0, "y": 1, "z": 2}.get(axis, 1)
            rotation = tuple(angle if index == axis_index else 0.0 for index in range(3))
            default_pivot = (0.0, 0.0, 0.0) if element_type == "mesh" else element.get("from", (0.0, 0.0, 0.0))
            pivot = rotation_value.get("origin", element.get("origin", default_pivot))
        else:
            rotation = rotation_value
            pivot = element.get("origin", (0.0, 0.0, 0.0) if element_type == "mesh" else element.get("from", (0.0, 0.0, 0.0)))
        if element_type == "mesh":
            bounds = _mesh_bounds(element, rotation=rotation, pivot=pivot)
            if bounds is None:
                continue
            minimum, maximum = bounds
        else:
            from_value = element.get("from")
            to_value = element.get("to")
            if from_value is None or to_value is None:
                continue
            from_point = _vec3(from_value, "element from")
            to_point = _vec3(to_value, "element to")
            minimum, maximum = _cube_bounds(
                from_point,
                tuple(to_point[index] - from_point[index] for index in range(3)),
                rotation=rotation,
                pivot=pivot,
            )
        palette_color = _element_color(element, palette)
        try:
            palette_index = palette.index(palette_color)
        except ValueError:
            palette.append(palette_color)
            palette_index = len(palette) - 1
        start = tuple(math.floor(minimum[index] / units_per_voxel) for index in range(3))
        stop = tuple(math.ceil(maximum[index] / units_per_voxel) for index in range(3))
        for x in range(start[0], stop[0]):
            for y in range(start[1], stop[1]):
                for z in range(start[2], stop[2]):
                    voxels[(x, y, z)] = palette_index
    if not palette:
        palette = [_indexed_color(0)]
    return VoxelAsset(
        grid_size=grid_size,
        palette=palette,
        voxels=voxels,
        provenance={
            "workflow": "blockbench_import",
            "model_format": str(payload.get("meta", {}).get("model_format", "unknown")) if isinstance(payload.get("meta"), Mapping) else "unknown",
        },
    )


def _merged_runs(asset: VoxelAsset) -> tuple[tuple[tuple[int, int, int], tuple[int, int, int], int], ...]:
    remaining = set(asset.voxels)
    runs = []
    while remaining:
        cell = min(remaining)
        palette_index = asset.voxels[cell]
        end_x = cell[0]
        while (end_x + 1, cell[1], cell[2]) in remaining and asset.voxels[(end_x + 1, cell[1], cell[2])] == palette_index:
            end_x += 1
        for x in range(cell[0], end_x + 1):
            remaining.discard((x, cell[1], cell[2]))
        runs.append((cell, (end_x + 1, cell[1] + 1, cell[2] + 1), palette_index))
    return tuple(runs)


def encode_blockbench(
    asset: VoxelAsset,
    *,
    units_per_voxel: float = DEFAULT_BLOCKBENCH_UNITS_PER_VOXEL,
    model_format: str = "free",
) -> bytes:
    """Encode a VOXTART asset as a cube-based generic .bbmodel document."""

    units_per_voxel = float(units_per_voxel)
    if not math.isfinite(units_per_voxel) or units_per_voxel <= 0.0:
        raise ValueError("Blockbench units per voxel must be positive")
    elements = []
    for index, (start, stop, palette_index) in enumerate(_merged_runs(asset)):
        minimum = [coordinate * units_per_voxel for coordinate in start]
        maximum = [coordinate * units_per_voxel for coordinate in stop]
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"voxtart:{index}:{start}:{stop}:{palette_index}"))
        elements.append({
            "name": f"Voxel Run {index + 1:05d}",
            "type": "cube",
            "uuid": identifier,
            "from": minimum,
            "to": maximum,
            "origin": minimum,
            "rotation": [0, 0, 0],
            "color": int(palette_index),
            "export": True,
            "visibility": True,
            "faces": {
                direction: {"uv": [0, 0, 16, 16], "texture": None}
                for direction in ("north", "east", "south", "west", "up", "down")
            },
        })
    payload = {
        "meta": {
            "format_version": BLOCKBENCH_FORMAT_VERSION,
            "model_format": str(model_format),
            "box_uv": False,
        },
        "name": "VOXTART Export",
        "resolution": {"width": 16, "height": 16},
        "elements": elements,
        "outliner": [element["uuid"] for element in elements],
        "textures": [],
        "voxtart_grid_size": asset.grid_size,
        "voxtart_palette": [list(color) for color in asset.palette],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class BlockbenchModel:
    """Convenience wrapper for model conversion callers."""

    asset: VoxelAsset
    source_format: str = "bbmodel"


__all__ = [
    "BLOCKBENCH_FORMAT_VERSION",
    "BlockbenchModel",
    "DEFAULT_BLOCKBENCH_UNITS_PER_VOXEL",
    "decode_blockbench",
    "encode_blockbench",
]
